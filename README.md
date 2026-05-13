```
██████╗ ██╗    ██╗ █████╗ ███╗   ███╗██╗███╗   ██╗ █████╗ ████████╗ ██████╗ ██████╗
██╔══██╗██║    ██║██╔══██╗████╗ ████║██║████╗  ██║██╔══██╗╚══██╔══╝██╔═══██╗██╔══██╗
██████╔╝██║ █╗ ██║███████║██╔████╔██║██║██╔██╗ ██║███████║   ██║   ██║   ██║██████╔╝
██╔══██╗██║███╗██║██╔══██║██║╚██╔╝██║██║██║╚██╗██║██╔══██║   ██║   ██║   ██║██╔══██╗
██████╔╝╚███╔███╔╝██║  ██║██║ ╚═╝ ██║██║██║ ╚████║██║  ██║   ██║   ╚██████╔╝██║  ██║
╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝
═══════════════════════════ hasta la vista, bwam ═══════════════════════════════════
```

Twitter-narrative pump.fun deploy bot. Watches a curated list of high-signal X accounts, names a coin from each tweet, and ships it on-chain in single-digit seconds. 

## Stack

- **watcher** (Python) — race three Twitter sources, dedupe by tweet id
- **namer** (Python) — vision-capable model proposes name + ticker + image strategy; refuses low-quality narratives
- **images** (Python) — Cloudflare Workers AI → fal.ai → Replicate → RunPod ComfyUI fallback chain
- **deployer** (TypeScript) — durable nonce + pre-grinded mint pool + pump.fun `CreateV2` + raced multi-relayer landing

## Quick start

```bash
cp .env.example .env      # fill in the keys you have
pip install -e .          # python deps (or: uv sync)
npm install               # ts deps

npx tsx src/deployer/setup_nonce.ts 8    # one-time: create durable nonce pool
npx tsx src/deployer/build_alt.ts        # one-time: publish address lookup table
npx tsx src/deployer/mint_pool.ts &      # background: grind "...pump"-suffix keypairs

python src/orchestrator/fast_main.py     # go
```

Set `DRY_RUN=true` to build + sign txs without submitting.

## Research

[`research/FINDINGS.md`](research/FINDINGS.md) — reverse-engineering of two competitor stacks (autonomous bot `bwamJzzt…` vs human-driven `j7tracker.io` SaaS), the on-chain instruction layout, latency budget, and the levers we pull to beat them.

## Layout

```
src/
  watcher/        multi-source twitter ingestion
  namer/          decision filter + naming + prompt rewrite
  images/         gen + multi-host metadata upload
  orchestrator/   glue (main, fast_main, dry_run_harness)
  deployer/       pump.fun bindings + relayer race + setup helpers
runpod/           uncensored image-gen worker (ComfyUI on RunPod)
data/             on-chain research dumps
research/         findings doc
```
