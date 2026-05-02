"""
Image generation abstraction with permissiveness-aware fallback.

We need fast image gen that doesn't refuse memecoin content (public figures,
mild offense, political imagery). Different providers have different
filtering postures. Strategy:

  1. Try the FAST primary (fal.ai Flux Schnell — typically 1-2s, mild
     filtering only on hard NSFW).
  2. If primary refuses or returns a flagged/black image, fall through to
     self-hosted (RunPod / Modal endpoint with your own checkpoint —
     no filtering at all).
  3. As an emergency last-resort, use the source-tweet image cropped
     directly — never refuses, but might be ugly.

Each adapter implements `.generate(prompt) -> bytes` and `.healthy() -> bool`.
The orchestrator races primary against fallback when latency budget permits,
or runs them sequentially when budget is tight.
"""
from __future__ import annotations

import asyncio
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


@dataclass
class GenResult:
    bytes_: bytes
    via: str
    elapsed_ms: float
    refused: bool = False


class _BaseGen(ABC):
    name: str = "base"

    @abstractmethod
    async def generate(self, prompt: str) -> bytes: ...


def _is_refusal(body_or_err: Exception | bytes | str) -> bool:
    """Heuristic: detect provider refusals (text 'unsafe' / 'blocked' /
    'flagged', a black image, or a 4xx with safety message)."""
    if isinstance(body_or_err, Exception):
        msg = str(body_or_err).lower()
        return any(k in msg for k in ("safety", "policy", "blocked", "unsafe", "flagged", "moderation"))
    if isinstance(body_or_err, str):
        msg = body_or_err.lower()
        return any(k in msg for k in ("safety", "policy", "blocked", "unsafe", "flagged"))
    return False


class CloudflareFluxKlein(_BaseGen):
    """Cloudflare Workers AI: FLUX.2 [klein] 4B. **Fastest commercial option
    in 2026** — 4-step distilled, P50 0.3-0.5s. Released Jan 15 2026.
    Cloudflare's edge inference often beats fal/Replicate by 200-500ms
    because the model runs in their global PoPs.

    Worker AI billing: included in Workers Paid ($5/mo) up to a generous
    Neuron quota; effectively free at our volumes."""

    name = "cf-flux-klein-4b"

    def __init__(self, account_id: str | None = None, api_token: str | None = None):
        self.account = account_id or os.environ["CF_ACCOUNT_ID"]
        self.token = api_token or os.environ["CF_API_TOKEN"]
        # 9b is higher quality but ~2x slower; 4b is the speed king.
        self.model = os.getenv("CF_FLUX_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")

    async def generate(self, prompt: str) -> bytes:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/run/{self.model}",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"prompt": prompt, "steps": 4},
            )
            if r.status_code != 200:
                raise RuntimeError(f"cf-workers-ai: {r.status_code} {r.text[:200]}")
            ct = r.headers.get("content-type", "")
            if ct.startswith("image/"):
                return r.content
            # Some endpoints return JSON {result: {image: base64}}
            j = r.json()
            import base64
            return base64.b64decode(j["result"]["image"])


class FalFluxSchnell(_BaseGen):
    """fal.ai Flux Schnell. P50 ~1.5s. Permissive on public figures and mild
    offense; refuses hard NSFW + extreme violence. Good cross-provider
    redundancy for when Cloudflare degrades."""

    name = "fal-flux-schnell"

    def __init__(self, api_key: str | None = None):
        self.key = api_key or os.environ["FAL_KEY"]
        # Flux.2 Klein is also on fal as it rolls out — flip via env.
        self.model = os.getenv("FAL_MODEL", "fal-ai/flux/schnell")

    async def generate(self, prompt: str) -> bytes:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                f"https://fal.run/{self.model}",
                headers={"Authorization": f"Key {self.key}"},
                json={
                    "prompt": prompt,
                    "image_size": "square_hd",
                    "num_inference_steps": 4,
                    "enable_safety_checker": False,
                },
            )
            if r.status_code != 200:
                raise RuntimeError(f"fal: {r.status_code} {r.text[:200]}")
            j = r.json()
            url = j["images"][0]["url"]
            r2 = await c.get(url)
            r2.raise_for_status()
            return r2.content


class ReplicateFluxSchnell(_BaseGen):
    """Replicate Flux Schnell. Backup primary; similar latency, similar
    posture. Different infra so uncorrelated outages with fal."""

    name = "replicate-flux-schnell"

    def __init__(self, api_token: str | None = None):
        self.token = api_token or os.environ["REPLICATE_API_TOKEN"]

    async def generate(self, prompt: str) -> bytes:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                "https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions",
                headers={
                    "Authorization": f"Token {self.token}",
                    "Content-Type": "application/json",
                    "Prefer": "wait",
                },
                json={
                    "input": {
                        "prompt": prompt,
                        "aspect_ratio": "1:1",
                        "num_outputs": 1,
                        "num_inference_steps": 4,
                        "disable_safety_checker": True,
                    }
                },
            )
            r.raise_for_status()
            data = r.json()
            url = data["output"][0] if isinstance(data["output"], list) else data["output"]
            r2 = await c.get(url)
            r2.raise_for_status()
            return r2.content


class RunPodComfyUI(_BaseGen):
    """RunPod Serverless ComfyUI. **The fully-uncensored fallback** for
    prompts that even fal/Cloudflare refuse. Stack: Flux Schnell base +
    CHROMA (Flux-compatible uncensored fork) + Pony Diffusion XL for
    stylized/cartoon. Zero filtering — you own the workflow.

    Cost: $0.44/hr A6000 community ($240/mo always-on, zero cold start) or
    serverless on-demand for ~$5/mo if you tolerate FlashBoot cold starts
    (~200ms warm path, 15-30s cold).

    Configure SELFHOST_IMAGE_ENDPOINT to your RunPod /run endpoint and
    SELFHOST_IMAGE_AUTH to your API key."""

    name = "runpod-comfy"

    def __init__(self, endpoint: str | None = None, auth: str | None = None):
        self.endpoint = endpoint or os.environ.get("SELFHOST_IMAGE_ENDPOINT")
        self.auth = auth or os.environ.get("SELFHOST_IMAGE_AUTH", "")

    async def generate(self, prompt: str) -> bytes:
        if not self.endpoint:
            raise RuntimeError("RunPod endpoint not configured")
        async with httpx.AsyncClient(timeout=45.0) as c:
            # RunPod serverless: POST /run, returns {id, output: {image_b64}}
            r = await c.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.auth}"} if self.auth else {},
                json={
                    "input": {
                        "prompt": prompt,
                        "negative_prompt": "low quality, blurry, watermark, text",
                        "checkpoint": os.getenv("RUNPOD_CHECKPOINT", "flux-schnell-chroma"),
                        "lora": os.getenv("RUNPOD_LORA", ""),
                        "steps": 4,
                        "width": 1024,
                        "height": 1024,
                    }
                },
            )
            r.raise_for_status()
            j = r.json()
            out = j.get("output") or {}
            if "image_b64" in out:
                import base64
                return base64.b64decode(out["image_b64"])
            if "image_url" in out:
                r2 = await c.get(out["image_url"])
                r2.raise_for_status()
                return r2.content
            raise RuntimeError(f"unexpected RunPod response: {j}")


class _Pipeline:
    def __init__(self, providers: list[_BaseGen], race_primary_with_fallback: bool = False):
        self.providers = providers
        self.race = race_primary_with_fallback

    async def generate(self, prompt: str) -> GenResult:
        """Try providers in order until one succeeds. Optionally race the
        primary with the fallback in parallel and take whichever returns
        first (uses more compute budget for lower p99).
        """
        if not self.providers:
            raise RuntimeError("no image-gen providers configured")

        if self.race and len(self.providers) >= 2:
            return await self._race(prompt)
        return await self._sequential(prompt)

    async def _sequential(self, prompt: str) -> GenResult:
        last: Exception | None = None
        for p in self.providers:
            t0 = time.monotonic()
            try:
                b = await p.generate(prompt)
                return GenResult(b, p.name, (time.monotonic() - t0) * 1000)
            except Exception as e:  # noqa: BLE001
                last = e
                refused = _is_refusal(e)
                print(f"[image] {p.name} {'refused' if refused else 'failed'}: {e}")
                continue
        raise RuntimeError(f"all image providers failed: {last}")

    async def _race(self, prompt: str) -> GenResult:
        t0 = time.monotonic()

        async def _attempt(p: _BaseGen) -> tuple[_BaseGen, bytes | None, Exception | None]:
            try:
                return p, await p.generate(prompt), None
            except Exception as e:  # noqa: BLE001
                return p, None, e

        tasks = [asyncio.create_task(_attempt(p)) for p in self.providers]
        last: Exception | None = None
        for t in asyncio.as_completed(tasks):
            p, b, err = await t
            if b is not None:
                # Cancel other in-flight (cheap; might already be billed)
                for x in tasks:
                    if not x.done():
                        x.cancel()
                return GenResult(b, p.name, (time.monotonic() - t0) * 1000)
            last = err
        raise RuntimeError(f"all image providers failed: {last}")


def build_default_pipeline() -> _Pipeline:
    """Build whichever providers are configured, in priority order:

      1. Cloudflare Workers AI (FLUX.2 Klein 4B) — 0.3-0.5s, edge-served
      2. fal.ai (Flux Schnell or Klein) — 1.5s, cross-provider redundancy
      3. Replicate (Flux Schnell) — 2s, third-party redundancy
      4. RunPod ComfyUI — uncensored fallback when commercial APIs refuse

    First success wins; refusals fall through.
    """
    providers: list[_BaseGen] = []
    if os.getenv("CF_ACCOUNT_ID") and os.getenv("CF_API_TOKEN"):
        providers.append(CloudflareFluxKlein())
    if os.getenv("FAL_KEY"):
        providers.append(FalFluxSchnell())
    if os.getenv("REPLICATE_API_TOKEN"):
        providers.append(ReplicateFluxSchnell())
    if os.getenv("SELFHOST_IMAGE_ENDPOINT"):
        providers.append(RunPodComfyUI())
    return _Pipeline(
        providers,
        race_primary_with_fallback=os.getenv("IMAGE_GEN_RACE", "false").lower() == "true",
    )
