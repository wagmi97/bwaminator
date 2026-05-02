"""
End-to-end orchestrator. Glues:

   watcher  ──tweet──▶  namer ──decision──▶  image_prep ──CDN url──▶  deploy.ts

Latency budget (target):
    detect ──▶ name :  1500 ms  (namer)
    name   ──▶ image:  1500 ms  (or near-zero with crop_source)
    image  ──▶ tx   :   400 ms  (template hydrate + sign)
    tx     ──▶ land :   600 ms  (NextBlock + slot time)
                       ─────
              total :  ~4 s   (matches the 7s observed on chadlon)
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Make the sibling packages importable (`src/watcher`, `src/namer`, …)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from images.image_prep import ImagePreparer  # noqa: E402
from namer.namer import Namer  # noqa: E402
from watcher.watcher import TweetEvent, TwitterApiIoStream  # noqa: E402


async def _upload_metadata_json(
    client: httpx.AsyncClient,
    name: str,
    symbol: str,
    description: str,
    image_url: str,
    twitter_url: str,
    image_prep: ImagePreparer,
) -> str:
    body = {
        "createdOn": "pump.fun",
        "name": name,
        "symbol": symbol,
        "description": description or "",
        "image": image_url,
        "twitter": twitter_url,
        "website": "",
    }
    key = f"{secrets.token_hex(8)}.json"
    image_prep.s3.put_object(
        Bucket=image_prep.bucket,
        Key=key,
        Body=json.dumps(body).encode(),
        ContentType="application/json",
        CacheControl="public, max-age=31536000",
        ACL="public-read",
    )
    return f"{image_prep.public_base}/{key}"


def _spawn_deploy(name: str, symbol: str, metadata_uri: str, snipe_sol: float) -> dict:
    """Shell out to the TS deployer. Keeping it as a subprocess means we can
    swap implementations (Rust, Go) without touching the Python pipeline."""
    env = os.environ.copy()
    env["DEPLOY_REQ"] = json.dumps(
        {"name": name, "symbol": symbol, "metadataUri": metadata_uri, "snipeSol": snipe_sol}
    )
    proc = subprocess.run(
        ["npx", "tsx", "src/deployer/deploy.ts"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


async def handle_tweet(
    ev: TweetEvent,
    namer: Namer,
    image_prep: ImagePreparer,
    snipe_sol: float,
) -> None:
    t0 = time.monotonic()

    image_url = ev.image_urls[0] if ev.image_urls else None
    decision = await namer.name(ev.text, image_url)
    t_named = time.monotonic()

    print(
        f"[+{(t_named - t0) * 1000:.0f}ms named] @{ev.author} {ev.id}: "
        f"deploy={decision.deploy} name={decision.name!r} sym={decision.symbol!r} "
        f"reason={decision.reason_skipped!r}"
    )
    if not decision.deploy:
        return

    img = await image_prep.prepare(
        source_url=image_url,
        strategy=decision.image_strategy or "crop_source",
        prompt=decision.image_prompt,
    )
    t_imaged = time.monotonic()

    async with httpx.AsyncClient() as c:
        meta_uri = await _upload_metadata_json(
            c,
            name=decision.name or "",
            symbol=decision.symbol or "",
            description=decision.description or "",
            image_url=img.url,
            twitter_url=f"https://x.com/{ev.author}/status/{ev.id}",
            image_prep=image_prep,
        )
    t_metad = time.monotonic()

    result = await asyncio.to_thread(
        _spawn_deploy, decision.name, decision.symbol, meta_uri, snipe_sol
    )
    t_deployed = time.monotonic()

    print(
        f"[deployed] mint={result.get('mint')} sig={result.get('signature')}\n"
        f"  name {(t_named - t0) * 1000:.0f}ms | "
        f"img {(t_imaged - t_named) * 1000:.0f}ms | "
        f"meta {(t_metad - t_imaged) * 1000:.0f}ms | "
        f"chain {(t_deployed - t_metad) * 1000:.0f}ms | "
        f"total {(t_deployed - t0) * 1000:.0f}ms | "
        f"tweet→deploy {(t_deployed - ev.created_at_unix) :.2f}s"
    )


async def main() -> None:
    load_dotenv()

    accounts = (os.getenv("WATCH_ACCOUNTS") or "").split(",")
    snipe_sol = float(os.getenv("SNIPE_SOL_AMOUNT") or "0.1")

    namer = Namer()
    image_prep = ImagePreparer(
        s3_endpoint=os.environ["S3_ENDPOINT"],
        s3_bucket=os.environ["S3_BUCKET"],
        s3_access_key=os.environ["S3_ACCESS_KEY"],
        s3_secret_key=os.environ["S3_SECRET_KEY"],
        public_base_url=os.environ["S3_PUBLIC_BASE_URL"],
        fal_key=os.getenv("FAL_KEY"),
    )

    stream = TwitterApiIoStream(os.environ["TWITTERAPI_IO_KEY"], accounts).stream()

    # Each tweet handled concurrently — a slow namer call on one tweet must
    # not delay the next.
    async for ev in stream:
        asyncio.create_task(handle_tweet(ev, namer, image_prep, snipe_sol))


if __name__ == "__main__":
    asyncio.run(main())
