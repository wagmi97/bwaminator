# RunPod Serverless ComfyUI image with Flux Schnell + CHROMA + uncensored LoRA
# pre-baked. Build:
#   docker build -t myreg/memecoin-comfy:latest .
#   docker push myreg/memecoin-comfy:latest
# then point your RunPod template at this image.

FROM runpod/comfyui:latest

# Pre-download model weights so cold-start doesn't have to fetch them.
# Replace URLs with your own mirror for reliability; HuggingFace public URLs
# work but rate-limit on cold start.
ARG HF_TOKEN

RUN mkdir -p /comfyui/models/unet /comfyui/models/clip /comfyui/models/vae /comfyui/models/loras

# Flux Schnell unet (FP8 quantized — fits A6000 easily)
RUN curl -fL --header "Authorization: Bearer ${HF_TOKEN}" \
    -o /comfyui/models/unet/flux1-schnell.safetensors \
    https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors

# CLIP encoders + VAE
RUN curl -fL --header "Authorization: Bearer ${HF_TOKEN}" \
    -o /comfyui/models/clip/t5xxl_fp8_e4m3fn.safetensors \
    https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors \
 && curl -fL --header "Authorization: Bearer ${HF_TOKEN}" \
    -o /comfyui/models/clip/clip_l.safetensors \
    https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors \
 && curl -fL --header "Authorization: Bearer ${HF_TOKEN}" \
    -o /comfyui/models/vae/ae.safetensors \
    https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors

# Uncensored LoRAs — replace with whichever community drops you trust.
# CHROMA: Flux-compatible reduced-filtering fork; check Civitai.red for
# current redistribution URLs. Flux-Uncensored-V2: secondary stack.
COPY loras/chroma_v2.safetensors          /comfyui/models/loras/chroma_v2.safetensors
COPY loras/flux-uncensored-v2.safetensors /comfyui/models/loras/flux-uncensored-v2.safetensors

# RunPod handler
RUN pip install --no-cache-dir runpod requests
COPY handler.py             /comfyui/handler.py
COPY comfyui_workflow.json  /comfyui/comfyui_workflow.json

# RunPod expects handler.py at /
WORKDIR /comfyui

# Start ComfyUI in background then the runpod handler
CMD bash -lc "python main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch & exec python -u handler.py"
