"""
Multi-host metadata uploader. Uploads to N hosts in parallel, returns the
first success URL. Falls back to the next on individual failures.

Why: bwam's bot is currently DOWN because his single metadata host
(meta.sdfgsdfsdf.uk) returns 502. A bot whose CRITICAL FAILURE MODE is
"DNS goes down" is not engineered for production.

Hosts (in priority order):
  1. Cloudflare R2 — primary. Cheapest, fastest, 99.99% SLA.
  2. BunnyCDN Storage — secondary. Different infra, different DNS, different
     billing relationship — uncorrelated outages.
  3. Pinata IPFS — tertiary. Decentralized; URL works even if Pinata's
     gateway goes down (any IPFS gateway can resolve the CID).

We upload to all three in parallel; return the first success. The deploy
goes out as soon as we have any URL — others continue uploading
asynchronously and provide redundancy.
"""
from __future__ import annotations

import asyncio
import io
import os
import secrets
from dataclasses import dataclass
from typing import Awaitable, Callable

import boto3
import httpx
from PIL import Image


@dataclass
class HostedAsset:
    url: str
    via: str
    elapsed_ms: float


class _R2Host:
    name = "r2"

    def __init__(self):
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY"],
            aws_secret_access_key=os.environ["R2_SECRET_KEY"],
        )
        self.bucket = os.environ["R2_BUCKET"]
        self.base = os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")

    async def put(self, key: str, body: bytes, content_type: str) -> str:
        # boto3 is sync — run in thread to keep event loop free
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl="public, max-age=31536000",
        )
        return f"{self.base}/{key}"


class _BunnyHost:
    name = "bunny"

    def __init__(self):
        self.zone = os.environ["BUNNY_STORAGE_ZONE"]
        self.key = os.environ["BUNNY_ACCESS_KEY"]
        self.base = os.environ["BUNNY_PUBLIC_BASE_URL"].rstrip("/")

    async def put(self, key: str, body: bytes, content_type: str) -> str:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.put(
                f"https://storage.bunnycdn.com/{self.zone}/{key}",
                content=body,
                headers={"AccessKey": self.key, "Content-Type": content_type},
            )
            r.raise_for_status()
        return f"{self.base}/{key}"


class _PinataHost:
    name = "pinata"

    def __init__(self):
        self.jwt = os.environ["PINATA_JWT"]

    async def put(self, key: str, body: bytes, content_type: str) -> str:
        # Pinata pinning API; key isn't used (CID is content-addressed).
        async with httpx.AsyncClient(timeout=20) as c:
            files = {"file": (key, body, content_type)}
            r = await c.post(
                "https://api.pinata.cloud/pinning/pinFileToIPFS",
                headers={"Authorization": f"Bearer {self.jwt}"},
                files=files,
            )
            r.raise_for_status()
            cid = r.json()["IpfsHash"]
        return f"https://gateway.pinata.cloud/ipfs/{cid}"


def _available_hosts():
    hosts = []
    if all(os.getenv(k) for k in ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_PUBLIC_BASE_URL")):
        hosts.append(_R2Host())
    if all(os.getenv(k) for k in ("BUNNY_STORAGE_ZONE", "BUNNY_ACCESS_KEY", "BUNNY_PUBLIC_BASE_URL")):
        hosts.append(_BunnyHost())
    if os.getenv("PINATA_JWT"):
        hosts.append(_PinataHost())
    return hosts


async def upload_first(body: bytes, key: str, content_type: str) -> HostedAsset:
    """Upload to all configured hosts in parallel; resolve with the first
    success. The remaining uploads keep running in the background for
    redundancy."""
    hosts = _available_hosts()
    if not hosts:
        raise RuntimeError("no metadata hosts configured")

    import time

    t0 = time.monotonic()
    tasks = {asyncio.create_task(h.put(key, body, content_type)): h for h in hosts}
    pending = set(tasks.keys())
    last_err: Exception | None = None
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for d in done:
            host = tasks[d]
            try:
                url = d.result()
                # Schedule the rest to continue silently as redundancy
                for p in pending:
                    p.add_done_callback(lambda _f: None)
                return HostedAsset(url=url, via=host.name, elapsed_ms=(time.monotonic() - t0) * 1000)
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[metadata] {host.name} failed: {e}")
    raise RuntimeError(f"all metadata hosts failed: {last_err}")


def to_webp(b: bytes, max_side: int = 1024) -> bytes:
    im = Image.open(io.BytesIO(b)).convert("RGB")
    im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=85, method=4)
    return out.getvalue()


def random_key(ext: str) -> str:
    return f"{secrets.token_hex(16)}.{ext}"
