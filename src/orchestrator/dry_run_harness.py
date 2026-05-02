"""
Dry-run latency harness.

Fires synthetic TweetEvents through the full pipeline (gate → namer → image
→ metadata → deploy.ts in DRY_RUN mode) and emits per-stage timings. Use
this to validate the latency budget BEFORE going live with real money.

What it does NOT do:
  - Hit twitterapi.io / X API (we synthesize the events ourselves)
  - Submit any on-chain transaction (deploy.ts respects DRY_RUN=true)

What it DOES do:
  - Real namer call — incurs token cost (~$0.01 per scenario)
  - Real image fetch + crop + WEBP encode
  - Real metadata upload to your configured hosts (R2/Bunny/Pinata)
  - Real `npx tsx src/deployer/deploy.ts` invocation in DRY_RUN mode
    (builds the v0 tx, signs, but doesn't submit)

Usage:
    DRY_RUN=true python src/orchestrator/dry_run_harness.py [--scenarios 10]

Output: a CSV summary + per-stage histogram printed at exit.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from images.image_gen import build_default_pipeline  # noqa: E402
from namer.decision_filter import TopicDedup, TweetGate  # noqa: E402
from namer.namer import Namer  # noqa: E402
from watcher.multi_source import TweetEvent  # noqa: E402


# Synthetic scenarios chosen to exercise different code paths.
SCENARIOS: list[dict] = [
    # 1. Public figure with image — should deploy on most paths
    {
        "author": "elonmusk",
        "text": "in court today",
        "image_urls": ["https://pbs.twimg.com/media/EXAMPLE_ELON_COURT.jpg"],
        "expect_deploy": True,
        "expect_category": "meme",
    },
    # 2. Agent topic — should be classified as "agent"
    {
        "author": "OpenAI",
        "text": "introducing the new browsing agent",
        "image_urls": ["https://pbs.twimg.com/media/EXAMPLE_AGENT.jpg"],
        "expect_deploy": True,
        "expect_category": "agent",
    },
    # 3. Generic news, no anchor — should be REFUSED by the namer
    {
        "author": "WatcherGuru",
        "text": "JUST IN: Inflation comes in at 2.1%",
        "image_urls": [],
        "expect_deploy": False,
    },
    # 4. Reply (starts with @) — pre-namer gate refuses
    {
        "author": "elonmusk",
        "text": "@SamA you're cooked",
        "image_urls": [],
        "expect_deploy": False,
    },
    # 5. Image-only viral moment — should deploy with image gen if no source
    {
        "author": "AutismCapital",
        "text": "",
        "image_urls": ["https://pbs.twimg.com/media/EXAMPLE_VIRAL.jpg"],
        "expect_deploy": True,
        "expect_category": "meme",
    },
    # 6. Family-member posts a pet — common low-effort meme trigger
    {
        "author": "DonaldJTrumpJr",
        "text": "meet the new pup, his name is Lucky",
        "image_urls": ["https://pbs.twimg.com/media/EXAMPLE_DOG.jpg"],
        "expect_deploy": True,
        "expect_category": "meme",
    },
    # 7. Robot / autonomous-systems topic
    {
        "author": "BostonDynamics",
        "text": "Atlas now does parkour in heels",
        "image_urls": ["https://pbs.twimg.com/media/EXAMPLE_ATLAS.jpg"],
        "expect_deploy": True,
        "expect_category": "agent",
    },
    # 8. Ironic political moment
    {
        "author": "realDonaldTrump",
        "text": "TARIFFS ARE BACK!! the WORLD will pay!",
        "image_urls": [],
        "expect_deploy": True,
        "expect_category": "meme",
    },
]


@dataclass
class StageTiming:
    name: str
    samples_ms: list[float] = field(default_factory=list)

    def record(self, ms: float) -> None:
        self.samples_ms.append(ms)

    def summary(self) -> str:
        if not self.samples_ms:
            return f"  {self.name:18} (no samples)"
        s = sorted(self.samples_ms)
        p50 = s[len(s) // 2]
        p90 = s[int(len(s) * 0.9)] if len(s) > 1 else s[0]
        p99 = s[int(len(s) * 0.99)] if len(s) >= 100 else s[-1]
        return (
            f"  {self.name:18} n={len(s):3} "
            f"p50={p50:>7.0f}ms  p90={p90:>7.0f}ms  p99={p99:>7.0f}ms  "
            f"min={min(s):>6.0f}ms  max={max(s):>7.0f}ms"
        )


def _make_event(scen: dict, idx: int) -> TweetEvent:
    """Synthesize a TweetEvent. Use a fake snowflake ID anchored to NOW so
    the detection_lag computation reads as 0."""
    now = time.time()
    # Snowflake id whose decoded timestamp == now (within ms)
    TWEPOCH = 1288834974657
    fake_id = ((int(now * 1000) - TWEPOCH) << 22) | idx
    return TweetEvent(
        id=str(fake_id),
        author=scen["author"],
        text=scen["text"],
        image_urls=scen["image_urls"],
        created_at_unix=now,
        detected_at_unix=now,
        detected_by="harness",
    )


async def run_scenario(scen: dict, idx: int, ctx: dict, timings: dict[str, StageTiming]) -> dict:
    """Execute one scenario and return a result row."""
    ev = _make_event(scen, idx)
    t_start = time.monotonic()

    # We piggyback on _process from fast_main but capture timings via stdout
    # parsing is brittle; instead, reimplement the stage breakdown here so we
    # can time each piece independently. Keep behavior in lockstep with
    # fast_main._process.

    from images.multi_host_metadata import random_key, to_webp, upload_first
    from namer.prompt_rewriter import for_text_only_tweets, variants
    import httpx, json, asyncio as _asyncio
    import subprocess

    namer: Namer = ctx["namer"]
    gate: TweetGate = ctx["gate"]
    dedup: TopicDedup = ctx["dedup"]
    pipeline = ctx["image_pipeline"]

    # --- gate ---
    t0 = time.monotonic()
    g = gate.check(ev)
    timings["gate"].record((time.monotonic() - t0) * 1000)
    if not g.pass_through:
        return {"scenario": idx, "deployed": False, "stage_failed": "gate", "reason": g.reason}

    # --- namer ---
    t0 = time.monotonic()
    image_url = ev.image_urls[0] if ev.image_urls else None
    decision = await namer.name(ev.text, image_url if image_url else None)
    timings["namer"].record((time.monotonic() - t0) * 1000)
    if not decision.deploy:
        return {"scenario": idx, "deployed": False, "stage_failed": "namer", "reason": decision.reason_skipped}

    # --- dedup ---
    t0 = time.monotonic()
    is_dup, why = dedup.is_dup(decision.name or "")
    timings["dedup"].record((time.monotonic() - t0) * 1000)
    if is_dup:
        return {"scenario": idx, "deployed": False, "stage_failed": "dedup", "reason": why}

    # --- image acquisition ---
    t0 = time.monotonic()
    img_bytes = None
    if image_url:
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
                r = await c.get(image_url)
                if r.status_code == 200:
                    img_bytes = r.content
        except Exception:
            pass
    if img_bytes is None:
        seed = decision.image_prompt or for_text_only_tweets(
            decision.name or "", decision.symbol or "", decision.description or ""
        )
        for v in variants(seed):
            try:
                gr = await pipeline.generate(v.text)
                img_bytes = gr.bytes_
                break
            except Exception:
                continue
    timings["image"].record((time.monotonic() - t0) * 1000)
    if img_bytes is None:
        return {"scenario": idx, "deployed": False, "stage_failed": "image"}

    # --- metadata upload ---
    t0 = time.monotonic()
    webp = to_webp(img_bytes)
    img_asset = await upload_first(webp, random_key("webp"), "image/webp")
    timings["meta_image"].record((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    meta = {
        "createdOn": "pump.fun",
        "name": decision.name,
        "symbol": decision.symbol,
        "description": decision.description or "",
        "image": img_asset.url,
        "twitter": f"https://x.com/{ev.author}/status/{ev.id}",
        "website": "",
        "category": decision.category or "meme",
    }
    meta_asset = await upload_first(
        json.dumps(meta).encode(), random_key("json"), "application/json"
    )
    timings["meta_json"].record((time.monotonic() - t0) * 1000)

    # --- deploy.ts (DRY_RUN) ---
    t0 = time.monotonic()
    req = json.dumps({
        "name": decision.name,
        "symbol": decision.symbol,
        "metadataUri": meta_asset.url,
        "snipeSol": float(os.getenv("SNIPE_SOL_AMOUNT") or "0.1"),
        "strategy": decision.category or "meme",
    })
    proc = await _asyncio.create_subprocess_exec(
        "npx", "tsx", "src/deployer/deploy.ts",
        env={**os.environ, "DEPLOY_REQ": req, "DRY_RUN": "true"},
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    timings["deploy_dryrun"].record((time.monotonic() - t0) * 1000)
    deployed = proc.returncode == 0

    total_ms = (time.monotonic() - t_start) * 1000
    timings["TOTAL"].record(total_ms)

    return {
        "scenario": idx,
        "deployed": deployed,
        "name": decision.name,
        "symbol": decision.symbol,
        "category": decision.category,
        "confidence": decision.confidence,
        "expect_category": scen.get("expect_category"),
        "category_match": (decision.category == scen.get("expect_category")) if scen.get("expect_category") else None,
        "total_ms": round(total_ms, 0),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1, help="run each scenario N times for stable percentiles")
    ap.add_argument("--out", default="data/dry_run_report.csv")
    args = ap.parse_args()

    load_dotenv()
    os.environ.setdefault("DRY_RUN", "true")

    ctx = {
        "namer": Namer(),
        "dedup": TopicDedup(),
        "gate": TweetGate(),
        "image_pipeline": build_default_pipeline(),
    }
    timings = {n: StageTiming(n) for n in ("gate", "namer", "dedup", "image", "meta_image", "meta_json", "deploy_dryrun", "TOTAL")}
    rows: list[dict] = []
    for rep in range(args.repeat):
        for i, scen in enumerate(SCENARIOS):
            try:
                r = await run_scenario(scen, i + rep * 100, ctx, timings)
            except Exception as e:  # noqa: BLE001
                r = {"scenario": i + rep * 100, "error": str(e)[:120]}
            rows.append(r)
            print(f"[{rep}.{i}] {r}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r.keys()}))
        w.writeheader()
        w.writerows(rows)

    print("\n=== STAGE TIMINGS ===")
    for t in timings.values():
        print(t.summary())
    deployed = sum(1 for r in rows if r.get("deployed"))
    print(f"\ndeployed: {deployed}/{len(rows)}  ({100 * deployed / max(1, len(rows)):.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
