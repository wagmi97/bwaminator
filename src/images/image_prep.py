"""
Image preparation. Three strategies:

  use_source   — upload the source image as-is (fastest, sometimes ugliest)
  crop_source  — auto-crop to the dominant subject (fastest+best for portraits)
  generate     — Flux Schnell via fal.ai (~2s, prettiest, but adds latency)

Returns the URL of the uploaded image on the operator-controlled CDN.
"""
from __future__ import annotations

import asyncio
import io
import os
import secrets
import time
from dataclasses import dataclass

import boto3
import httpx
from PIL import Image


@dataclass
class PreparedImage:
    url: str
    bytes_uploaded: int
    elapsed_ms: float


class ImagePreparer:
    def __init__(
        self,
        s3_endpoint: str,
        s3_bucket: str,
        s3_access_key: str,
        s3_secret_key: str,
        public_base_url: str,
        fal_key: str | None = None,
    ):
        self.public_base = public_base_url.rstrip("/")
        self.bucket = s3_bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
        )
        self.fal_key = fal_key

    async def prepare(self, source_url: str | None, strategy: str, prompt: str | None) -> PreparedImage:
        t0 = time.monotonic()
        if strategy == "generate":
            if not self.fal_key:
                strategy = "use_source"  # fall back
            else:
                img_bytes = await self._flux_schnell(prompt or "a memecoin meme image, vibrant, viral")
        if strategy == "crop_source":
            assert source_url, "crop_source needs a source_url"
            img_bytes = _center_crop_square(await _fetch_bytes(source_url))
        elif strategy == "use_source":
            assert source_url, "use_source needs a source_url"
            img_bytes = await _fetch_bytes(source_url)
        elif strategy == "generate":
            pass  # already filled
        else:
            raise ValueError(f"unknown strategy {strategy!r}")

        key = f"{secrets.token_hex(16)}.webp"
        webp = _to_webp(img_bytes)
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=webp,
            ContentType="image/webp",
            CacheControl="public, max-age=31536000",
            ACL="public-read",
        )
        return PreparedImage(
            url=f"{self.public_base}/{key}",
            bytes_uploaded=len(webp),
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )

    async def _flux_schnell(self, prompt: str) -> bytes:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://fal.run/fal-ai/flux/schnell",
                headers={"Authorization": f"Key {self.fal_key}"},
                json={"prompt": prompt, "image_size": "square_hd", "num_inference_steps": 4},
            )
            r.raise_for_status()
            img_url = r.json()["images"][0]["url"]
            r2 = await c.get(img_url)
            r2.raise_for_status()
            return r2.content


async def _fetch_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


def _to_webp(b: bytes, max_side: int = 1024) -> bytes:
    im = Image.open(io.BytesIO(b)).convert("RGB")
    im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=85, method=4)
    return out.getvalue()


def _center_crop_square(b: bytes) -> bytes:
    im = Image.open(io.BytesIO(b)).convert("RGB")
    w, h = im.size
    s = min(w, h)
    im2 = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
    out = io.BytesIO()
    im2.save(out, format="WEBP", quality=88, method=4)
    return out.getvalue()
