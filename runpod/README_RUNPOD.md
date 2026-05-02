# RunPod ComfyUI uncensored fallback

This directory holds the pieces to deploy the **fully uncensored image-gen fallback** that the orchestrator falls through to when fal.ai / Cloudflare refuse a prompt. You do not need this for the happy path — most memecoin prompts work fine on the commercial primaries.

## What's here

- `comfyui_workflow.json` — ComfyUI graph: Flux Schnell + CHROMA LoRA + flux-uncensored-v2 LoRA → 1024×1024 → resized to 768×768
- `handler.py` — RunPod Serverless handler that maps `{input: {prompt}}` to the workflow and returns base64 PNG
- `Dockerfile` — bakes Flux Schnell + the LoRAs into the image so cold-start doesn't have to download them

## Build + push

```bash
docker build --build-arg HF_TOKEN=$HF_TOKEN -t YOUR_REGISTRY/memecoin-comfy:latest .
docker push YOUR_REGISTRY/memecoin-comfy:latest
```

## Deploy on RunPod

1. RunPod console → Serverless → New Endpoint
2. Container image: `YOUR_REGISTRY/memecoin-comfy:latest`
3. GPU: A6000 48GB Community (`$0.44/hr`) or RTX 4090 (`$0.34/hr`, slightly slower for Flux)
4. Idle timeout: 5 min (FlashBoot keeps ~48% of cold-paths under 200ms)
5. Max workers: start with 1, scale up as needed
6. Note the endpoint URL: `https://api.runpod.ai/v2/{endpoint_id}/run`
7. Generate an API key in the RunPod console

## Wire into the orchestrator

In `.env`:

```bash
SELFHOST_IMAGE_ENDPOINT=https://api.runpod.ai/v2/{endpoint_id}/runsync
SELFHOST_IMAGE_AUTH={your_runpod_api_key}
```

`/runsync` waits for completion (good for our use case — we want the result inline). For long-running variants use `/run` + poll `/status`.

## Latency expectations

| Path | Latency |
|---|---|
| Always-on worker (warm) | 1.0–1.5 s |
| FlashBoot cold start (~48% of cold paths) | ~200 ms boot + 1.0–1.5 s gen = 1.2–1.7 s |
| Standard cold start | 15–30 s boot + 1.0–1.5 s gen = 16–32 s |

If you can't tolerate the 16–32s tail, run **one always-on worker** at ~$240/mo. For a deploy bot that fires 50–200×/day, on-demand is fine.

## LoRA sources (April 2026)

The uncensored LoRA ecosystem moved to **Civitai.red** in April 2026 (Civitai split into `.com` (filtered) and `.red` (less-filtered) per their April 2026 policy shift). Current URLs (verify before pulling):

- **CHROMA** — Flux-compatible uncensored fork: search Civitai.red
- **flux-uncensored-v2** — secondary stack: search Civitai.red
- **Pony Diffusion XL** — for stylized/cartoon variants: SDXL-base, swap the workflow if you want this style

Drop the .safetensors files into `runpod/loras/` before `docker build`.

## Why this exists at all

fal.ai Flux Schnell and Cloudflare Workers AI Flux.2 Klein both have **no platform-side content filter** on the open-weights models. They will generate "Trump-as-lion-king" and "shirtless Elon at court" without complaint. The cases where they refuse are typically:
- Hard NSFW (illegal-content adjacent)
- Specific character names that have been individually blacklisted (rare)
- Very specific copyrighted IP (Disney/Pokemon by name)

For those cases, RunPod with our own checkpoints is the escape hatch. Set `IMAGE_GEN_RACE=true` in `.env` if you want to fire the primary AND fallback in parallel and take whichever returns first — uses ~2× compute budget but kills the p99.
