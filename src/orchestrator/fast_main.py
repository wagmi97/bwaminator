"""
Hot-path orchestrator. Targets sub-3-second tweet→on-chain.

Pipeline:

  raced ws + scrape + api  ──tweet──▶  cheap gate  ──pass──▶  namer
                                                                  │
                                                  parallel        │
              ┌───────────────────────────────────────────────────┤
              ▼                                                   ▼
   image_prep (crop or gen)                           pop pre-grinded mint kp
              │                                                   │
              ▼                                                   │
  upload metadata to all 3 hosts (first wins)                     │
              │                                                   │
              └────────────────────┬──────────────────────────────┘
                                   ▼
                        build v0 tx with N tips
                                   │
                                   ▼
                race-send to ALL relayers (first wins)
                                   │
                                   ▼
                            log latencies

Latency budget (target, Apr 2026):
  ws detect:       250 ms (twitterapi.io WS p50)
  filter+gate:     5 ms
  namer:           900 ms (vision-capable model with prompt-cached system)
  image gen:       400 ms (Cloudflare FLUX.2 Klein 4B) when source missing
                   0 ms     when source image cropped (fast path)
  meta+mint:       parallel; meta upload ~150 ms (R2 first to win)
  tx build+sign:   30 ms (template hydrate; durable nonce, no blockhash fetch)
  relayer race:    400 ms (1-slot win expected)
                   ────
  total p50 (cropped source path):   ~1.7 s
  total p50 (image gen path):        ~2.3 s
  total p99:                         ~3.5 s

bwam observed at ~7 s end-to-end (when his stack is up). Realistic improvement:
3-4× faster on healthy days, ∞× faster when his metadata host is down.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from images.image_gen import build_default_pipeline  # noqa: E402
from images.multi_host_metadata import HostedAsset, random_key, to_webp, upload_first  # noqa: E402
from namer.decision_filter import TopicDedup, TweetGate  # noqa: E402
from namer.namer import Namer  # noqa: E402
from namer.prompt_rewriter import for_text_only_tweets, variants  # noqa: E402
from watcher.multi_source import TweetEvent, race_sources  # noqa: E402


async def _fetch_image_bytes(url: str | None) -> bytes | None:
    if not url:
        return None
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
        try:
            r = await c.get(url)
            r.raise_for_status()
            return r.content
        except Exception:
            return None


async def _process(ev: TweetEvent, ctx: dict) -> None:
    t = {"detect": ev.detected_at_unix, "tweet_pub": ev.created_at_unix}
    t["start"] = time.time()

    gate: TweetGate = ctx["gate"]
    g = gate.check(ev)
    if not g.pass_through:
        print(f"[skip] {ev.author}: {g.reason}")
        return

    namer: Namer = ctx["namer"]
    image_url = ev.image_urls[0] if ev.image_urls else None
    decision = await namer.name(ev.text, image_url)
    t["named"] = time.time()

    if not decision.deploy:
        print(f"[skip-namer] @{ev.author}: {decision.reason_skipped}")
        return

    dedup: TopicDedup = ctx["dedup"]
    is_dup, why = dedup.is_dup(decision.name or "")
    if is_dup:
        print(f"[skip-dup] {decision.name}: {why}")
        return

    # Image acquisition strategy ordered by latency:
    #   1. crop_source / use_source: source tweet has an image — use it
    #   2. generate: namer said "no usable source", call image gen
    #      with progressive prompt variants until one returns
    img_bytes: bytes | None = None
    chosen_image_strategy = decision.image_strategy or "crop_source"
    if image_url and chosen_image_strategy in ("crop_source", "use_source"):
        img_bytes = await _fetch_image_bytes(image_url)

    if img_bytes is None:
        # Fall through to gen with rewritten prompts
        gen = ctx["image_pipeline"]
        prompt_seed = decision.image_prompt or for_text_only_tweets(
            decision.name or "", decision.symbol or "", decision.description or ""
        )
        for v in variants(prompt_seed):
            try:
                gr = await gen.generate(v.text)
                img_bytes = gr.bytes_
                print(f"[image] generated via {gr.via} ({v.style_tag}) {gr.elapsed_ms:.0f}ms")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[image] {v.style_tag} variant failed: {e}")

    if img_bytes is None:
        print(f"[skip] no image available for {decision.name}")
        return

    webp = to_webp(img_bytes)
    img_asset_t = asyncio.create_task(
        upload_first(webp, random_key("webp"), "image/webp")
    )
    img_asset: HostedAsset = await img_asset_t
    t["img_uploaded"] = time.time()

    meta = {
        "createdOn": "pump.fun",
        "name": decision.name,
        "symbol": decision.symbol,
        "description": decision.description or "",
        "image": img_asset.url if img_asset else "",
        "twitter": f"https://x.com/{ev.author}/status/{ev.id}",
        "website": "",
        # Pump.fun's discovery surfaces "agent" coins separately and curates
        # them for token-incentive (buyback) funding. Tag accordingly.
        "category": decision.category or "meme",
    }
    meta_asset = await upload_first(
        json.dumps(meta).encode(), random_key("json"), "application/json"
    )
    t["meta_uploaded"] = time.time()

    # Hand off to TS deployer
    req = json.dumps(
        {
            "name": decision.name,
            "symbol": decision.symbol,
            "metadataUri": meta_asset.url,
            "snipeSol": float(os.getenv("SNIPE_SOL_AMOUNT") or "0.1"),
            # "meme" → cashback ON; "agent" → cashback OFF (creator keeps
            # fees, pump.fun may add buyback incentives separately).
            "strategy": decision.category or "meme",
        }
    )
    proc = await asyncio.create_subprocess_exec(
        "npx",
        "tsx",
        "src/deployer/deploy.ts",
        env={**os.environ, "DEPLOY_REQ": req},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    t["chain"] = time.time()
    if proc.returncode != 0:
        print(f"[deploy-fail] {decision.name}: {err.decode()[:200]}")
        return
    last = out.decode().strip().splitlines()[-1]
    result = json.loads(last)

    # Append to holdings tracker so the periodic harvester
    # (src/deployer/harvest_incentives.ts) knows what to scan for buybacks.
    if result.get("mint") and decision.category == "agent":
        path = Path("data/agent_holdings.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(path.read_text()) if path.exists() else []
        existing.append({
            "mint": result["mint"],
            "strategy": "agent",
            "first_buy_sig": result.get("signature"),
            "name": decision.name,
            "symbol": decision.symbol,
            "deployed_at_unix": time.time(),
        })
        path.write_text(json.dumps(existing, indent=2))

    print(
        f"[deployed] @{ev.author} {decision.symbol:10} mint={result.get('mint')[:12]}.. via={result.get('via')} cat={decision.category}\n"
        f"  src={ev.detected_by} pub→detect={(t['detect']-t['tweet_pub'])*1000:.0f}ms "
        f"detect→named={(t['named']-t['detect'])*1000:.0f}ms "
        f"img={(t['img_uploaded']-t['named'])*1000:.0f}ms "
        f"meta={(t['meta_uploaded']-t['img_uploaded'])*1000:.0f}ms "
        f"chain={(t['chain']-t['meta_uploaded'])*1000:.0f}ms\n"
        f"  pub→on-chain={(t['chain']-t['tweet_pub']):.2f}s"
    )


async def main() -> None:
    load_dotenv()

    accounts = (os.getenv("WATCH_ACCOUNTS") or "").split(",")
    ctx = {
        "namer": Namer(),
        "dedup": TopicDedup(),
        "gate": TweetGate(
            skip_replies=os.getenv("SKIP_REPLIES", "true").lower() == "true",
            skip_retweets=os.getenv("SKIP_RETWEETS", "false").lower() == "true",
            min_followers=int(os.getenv("MIN_AUTHOR_FOLLOWERS", "100000")),
        ),
        "image_pipeline": build_default_pipeline(),
    }

    async for ev in race_sources(accounts):
        # Each tweet handled fully in parallel — never block the
        # watcher loop on a slow downstream.
        asyncio.create_task(_process(ev, ctx))


if __name__ == "__main__":
    asyncio.run(main())
