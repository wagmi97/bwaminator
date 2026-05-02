"""
RunPod Serverless ComfyUI handler.

Maps the simplified {input: {prompt, ...}} request to a ComfyUI workflow.
Loads workflow.json, substitutes the prompt placeholder, queues it through
the local ComfyUI server, waits for the output, and returns base64 PNG.

Deploy as a RunPod Serverless template:

  Base image:        runpod/comfyui:latest (or build your own with the LoRAs
                     baked into /comfyui/models/loras/)
  Container start:   bash -lc "cd /comfyui && python main.py --listen 0.0.0.0 --port 8188 & python -u handler.py"
  Required models in /comfyui/models/:
    unet/flux1-schnell.safetensors
    clip/t5xxl_fp8_e4m3fn.safetensors
    clip/clip_l.safetensors
    vae/ae.safetensors
    loras/chroma_v2.safetensors
    loras/flux-uncensored-v2.safetensors

  Env:
    COMFY_URL=http://127.0.0.1:8188
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from pathlib import Path

import requests
import runpod  # type: ignore

COMFY = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
WORKFLOW_PATH = Path(__file__).parent / "comfyui_workflow.json"


def _wait_for_image(prompt_id: str, timeout_s: float = 60.0) -> bytes:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        h = requests.get(f"{COMFY}/history/{prompt_id}", timeout=5).json()
        if prompt_id not in h:
            time.sleep(0.2)
            continue
        outputs = h[prompt_id].get("outputs", {})
        for node_id, out in outputs.items():
            for img in out.get("images", []):
                r = requests.get(
                    f"{COMFY}/view",
                    params={
                        "filename": img["filename"],
                        "type": img.get("type", "output"),
                        "subfolder": img.get("subfolder", ""),
                    },
                    timeout=10,
                )
                r.raise_for_status()
                return r.content
        time.sleep(0.15)
    raise TimeoutError(f"comfyui timeout for {prompt_id}")


def handler(event: dict) -> dict:
    inp = event.get("input") or {}
    prompt = (inp.get("prompt") or "").strip()
    if not prompt:
        return {"error": "empty prompt"}
    seed = int(inp.get("seed") or int.from_bytes(os.urandom(4), "little"))

    workflow = json.loads(WORKFLOW_PATH.read_text())
    # Inject the prompt at node 6
    workflow["6"]["inputs"]["text"] = prompt
    # Randomize seed at node 9
    workflow["9"]["inputs"]["seed"] = seed

    client_id = str(uuid.uuid4())
    r = requests.post(
        f"{COMFY}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=10,
    )
    r.raise_for_status()
    prompt_id = r.json()["prompt_id"]

    img = _wait_for_image(prompt_id)
    return {"image_b64": base64.b64encode(img).decode()}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
