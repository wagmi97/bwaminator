# bwaminator — Pumpfun Twitter-Narrative Deployer Research

> "I'll be back." — every memecoin we ship 2 seconds after Elon tweets.

## Reverse-engineering notes

## Two distinct subjects studied

| | bwamJzzt (likely autonomous bot) | FciWQ5q (human via j7tracker.io SaaS) |
|---|---|---|
| Wallet age | ~24 days (2026-04-06) | ~71 days (2026-02-18) |
| Sig count (lifetime) | 40,000+ | 6,918 |
| **Operator type** | **Autonomous bot (own infra)** | **Human user of j7tracker.io** |
| Pumpfun program | `6EF8rrecthR5...` | `6EF8rrecthR5...` (same) |
| CreateV2 disc | `d6904cec5f8b31b4` | `d6904cec5f8b31b4` (same) |
| Snipe ix | `BuyExactSolIn` (`38fc7408...`) | `Buy` (`66063d1201...`) |
| Initial bag from 5 SOL | 151.66M tokens (15.17%) | ~153M tokens (15.3%) |
| Durable nonce | **YES** (`advanceNonce` first) | **NO** (fresh blockhash) |
| Anti-MEV flag | `F1UXBEAM` (FluxBeam) | `Dep1oyedUsingJ7Tracker` (J7 brand) |
| Landing tip on create | NextBlock (`NextbLoCk...`) | Operator-owned (`j7CcC6Ld...`) |
| Sell tip | `4iUgjMT8...` (mystery svc) | `AStRAnpi...` (Astralane) |
| Custom router program | none — raw pumpfun | `J7pourVwqP1Vt...` (J7's wrapper) |
| Metadata host | `meta.sdfgsdfsdf.uk` (throwaway) | `metadata.j7tracker.io` (branded SaaS) |
| Image CDN | Axiom Spaces | Axiom Spaces (same — SaaS-shared?) |
| ALTs | shared/standard | shared + J7-vanity (`J7tuiJan...`, `J7HJUrdh...`) |

Both stacks deploy on Token-2022 with on-mint metadata extension, both spend ~5 SOL per deploy, both hit ~15% supply on the snipe (nowhere near the commonly-cited "50%").

## The j7tracker.io discovery

`j7tracker.io` is a **public, multi-tenant memecoin-deploy SaaS** branded "simple, fast, reliable" with explicit keywords "token deployer, solana, pump.fun, **twitter tracker**, meme coins, crypto trading". Crucially, **j7tracker is human-driven** — the user clicks deploy in their UI; it is NOT autonomous AI.

Evidence of multi-tenancy (12 distinct signers in a 20-tx random sample of the last 200 txs hitting `J7pourVw...`):

```
4S8etPw8tSxjzJikupCmc4K2AtDkB6Fk7uMbmyfoCBcE
5EbDdqGqK7wbUSjNJ9mtQoVwXjLNtonCyGk8Nr3rKa3S
8XFvmScJfLwFj2o4skadqc8vYJuYDXMiQXGKAFF4o5sr
8oZiaf74SwU9YufXNWHHZj6kih6RrqaZ3T38exYD27jw
9FR5MeR4nDu7nDp6ohxq8ampRFZQRmrHLWbjuF1Zb5PJ
BUTTmYpZbA17QzTPkp1UiKsmkXiiv7pKjeHhSiMcZJNS
DibK1Lb7X8kr4JRxrW1cBKJ3u7D2LHyHi8LkLsaCq89q
GU2Kne1HkDRHqhwD5ZraCGSiuhnh84xxwN2Xs2wbmU4r
HPzWfoAcogVQPxx65zQ1s5QF23ib5ecjCFytxE5Je1Dt
HR78wECFE9dd28v5gYZwuo3Pxf9YFB9HuUg17348XSs5
HYX9kEDjrNXmts4Adb6jH7KqfexrZnuuAdWFx3Aqthi9
JA31Cm2u6v51FuN7HMzhJ7vnCkgo38vNcU5tA6o5Z3Q2
```

What the user is paying j7tracker for:
- A "twitter tracker" panel that surfaces relevant tweets in near-real-time.
- One-click deploy that handles: image upload to their CDN, metadata JSON write to `metadata.j7tracker.io`, mint-keypair grinding for `pump` suffix, tx construction + bundling + landing.
- Their own on-chain router program `J7pourVwqP1Vt...` (which provides Sell/Buy wrappers — note `SellPumpfun` was seen).
- Branded vanity infra (`j7CcC6Ld...` tip, `Dep1oyedUsingJ7Tracker` flag) so other traders see "deployed by j7tracker" badges in their watch tools.

**Implication for the original question:** The "magic" naming/image quality on chadlon was a **human picking the name and image** in j7tracker's UI within ~5 seconds of the tweet appearing in their feed. The latency win is not AI — it's a fast human + production-grade chain infra that lets them go from "click deploy" to landed-on-chain in <2 seconds.

Subject wallet (autonomous variant): `bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`
Reference deploy (chadlon): `hLokvfUnqYUztoVdA4gwZnpvTKsPNefkdn2DyxGpump` ("CHADLON MUSK" / CHADLON)
Reference deploy decoded in detail (older mint, dead): `6wStRUYp6QeG9Xq1MVhtuA4fMgRGHQHtLphDiXaRgdvR` ("The Nietzschean Pony" / 矮种马)
Create tx for that mint: `5pCNRWbX8xZXu53g1XEsYb4bffb4dpjCwPXE4JsR1h2sLEiLY3Un9J2Ub5vvqXoV1eenJGHi43drwnHf2HuRHirg`

## Headline metric

**Tweet → metadata-upload latency: ~7.3 seconds.**

Elon's tweet (2050073827615588784) was published 2026-05-01T04:44:23.690Z. Chadlon's metadata file `Last-Modified` header reads 2026-05-01T04:44:31Z. The bot is direct-monitoring Elon's account; Autism Capital's repost (the user-visible source) was posted 8 hours earlier and is irrelevant.

The "milliseconds" framing in the wild is hype — actual latency is **single-digit seconds**, but that is enough to be first by a wide margin.

## On-chain stack (instructions in deploy bundle, single tx)

1. `system::advanceNonce` — operator pre-signs txs against a **durable nonce account** (e.g. `7JqNwA3a9N2zt8paPJ6FCR7B4MR5JrHNuQNYfqBHGr42`). This is the single biggest latency win: tx is fully signed and ready to submit; no fresh-blockhash round-trip required. They run a **pool of nonce accounts** (also seen: `29DxFv2pr...`) for parallel readiness.
2. `ComputeBudget::SetComputeUnitLimit` 350k, **with FluxBeam anti-MEV flag pubkeys** as accounts (`dontbund1e11111111111F1UXBEAM11111111r4pfou`, `jitodontfront11111111F1UXBEAM11111111nQRVsr`). These are vanity pubkeys read by friendly relayers/validators as "do not bundle / do not front-run" hints.
3. `ComputeBudget::SetComputeUnitPrice` ~2.86 lamports/CU → priority fee ≈ 0.001 SOL.
4. **Pumpfun `CreateV2`** (program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`, anchor disc `d6904cec5f8b31b4`).
   - Inline payload: name (u32-len + utf8) + symbol (u32-len + utf8) + uri (u32-len + utf8).
   - Single CPI tree creates: mint account (Token-2022 with metadata-pointer + on-mint metadata extensions), bonding curve PDA, deployer ATA, fee-program registration, mints 1B supply to curve, calls `setAuthority(null)` on mint.
   - Mint authority before renounce: `TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM` (pumpfun global PDA). Update authority on metadata: also renounced to null mid-instruction. **Token is immutable from birth.**
   - Token-2022 + on-mint metadata = **no Metaplex CPI**, fewer accounts, smaller tx footprint.
5. `system::createAccountWithSeed` for the deployer's bonding-curve token account (seed = first 8 chars of mint pubkey).
6. `spl-token::initializeAccount3`.
7. **Pumpfun `BuyExactSolIn`** (anchor disc `38fc74089edfcd5f`, payload `<u64 sol_in><u64 min_tokens>`). Reference deploy: `sol_in=5e9 lamports (5 SOL)`, `min_tokens=1` (effectively **zero slippage protection** — accept any fill, never fail).
8. `system::transfer` 1,000,000 lamports → **NextBlock tip account** `NextbLoCkVtMGcV47JzewQdvBpLqT9TxQFozQkN98pE`. Vanity prefix `Nextb` confirms NextBlock relayer, **not Jito**. Some sell-side txs tip a different (non-Jito) account (`4iUgjMT8q2hN...`) — likely 0slot.trade or Bloxroute.

Address Lookup Tables: 7+ pre-published ALTs are referenced (e.g. `MAyhSmzXzV1pTf...`, `A7hAgCzFw14fej...`, `pfeeUxB6jkeY1H...`, `ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL`). All static program IDs and pumpfun PDAs are stuffed into ALTs to keep the v0 tx well under the 1232-byte packet limit.

## Economics per deploy

- Tx fee: ~1.01M lamports (≈0.001 SOL)
- Priority fee: ~1.0M lamports (≈0.001 SOL)
- NextBlock tip: 1.0M lamports (≈0.001 SOL)
- Snipe buy: 5,000M lamports (5 SOL)
- Pumpfun creator/reserve fees: ~62M lamports (≈0.062 SOL)
- **Total per deploy: ~5.07 SOL**

Initial bag from 5 SOL on a fresh curve = **151.66M tokens = 15.17% of 1B supply** (curve bought ~848M tokens worth of inventory). The "50%" figure that floats around is wrong; actual bag is closer to **15%**. Still huge first-block dominance — every subsequent buyer in the same block bumps the curve price for them.

## Off-chain stack (what we can infer from metadata)

### Image hosting

- Chadlon: `axiomtrading.sfo3.cdn.digitaloceanspaces.com/<32hex>.webp`
- Older deploys: rotating throwaway domains
- Strong inference: the operator either uses **Axiom Trading's deploy endpoint** (which uploads images to their CDN as a side effect) or has reverse-engineered Axiom's upload API and is using it as free CDN. Either way, points to Axiom involvement.

### Metadata JSON hosting

- Chadlon: `metadata.j7tracker.io/metadata/<16hex>.json`
- Nietzschean Pony: `meta.sdfgsdfsdf.uk/metadata/<8charB64>` 
- Both Cloudflare-fronted, both `Cache-Control: public, max-age=31536000` (immutable). Hosts rotate per deploy, suggesting **operator-controlled infra with cycling domains** (likely to dodge takedowns / domain-level blocklists used by Solana wallets).

### Metadata schema (chadlon example)

```json
{
  "createdOn": "pump.fun",
  "name": "CHADLON MUSK",
  "symbol": "CHADLON",
  "description": "",
  "image": "https://axiomtrading.sfo3.cdn.digitaloceanspaces.com/...webp",
  "twitter": "https://x.com/elonmusk/status/2050073827615588784",
  "website": ""
}
```

Note: `twitter` field stamps the **source tweet** into the token metadata. This is bot-leak — the operator's pipeline writes the trigger tweet URL straight into the metadata. Useful for our reverse-engineering, also useful for trader bots that want to filter by source.

## Inferred pipeline (the actual answer to "how")

1. **Twitter watcher.** Direct firehose / GraphQL polling on a curated list of accounts (Elon, Trump's family, Autism Capital, prominent crypto/news accounts). Detect latency: ~0.5–2 s.
2. **Vision LLM call.** Image + tweet text → JSON `{name, ticker, description, image_prompt_or_crop_hint}`. Prompt is tuned for crypto/meme vernacular ("chad", "based", portmanteaus). Model latency: ~1–2 s.
3. **Image preparation** — three modes: (a) crop the source image (chadlon almost certainly used the ripped-Elon crop directly — zero generation latency); (b) generate via Flux Schnell / SDXL Turbo (2–4 s); (c) defer image, ship deploy with cropped source, optionally update metadata after.
4. **Metadata upload.** PUT image + JSON to operator-owned CDN host. <1 s.
5. **Bundle assembly + send.** Pre-signed durable-nonce template is rehydrated with the new mint keypair, name/symbol/uri populated, NextBlock tip attached, sent to NextBlock relayer. Wire-to-land: ~0.4–1 s.

End-to-end realistic budget: **2–8 seconds** depending on whether image is generated or cropped. Observed chadlon: 7.3 s.

## What's actually hard to replicate

- **Twitter ingestion at scale.** Public API is too slow; private GraphQL + residential proxies require ongoing maintenance.
- **Account curation.** Deciding *which* tweets to fire on is the alpha. Most tweets from these accounts are noise.
- **Wallet warming + nonce pool ops.** You need pre-funded fresh wallets and a maintained pool of durable nonce accounts ready to fire.
- **Prompt taste.** "ChadElon" vs "chadlon" is the operator's instinct for what reads as native vs cringe.
- **Curated ALTs.** They've pre-deployed ALTs covering all pumpfun PDAs + helper programs. Skip this and your tx blows past 1232 bytes.

## Wallet age + rotation

- First signed activity: **2026-04-06** (~24 days before observation).
- Total tx count in 24 days: 40,000+ (~1,700/day).
- The very earliest signed tx already shows the full 5-SOL-snipe pattern. The operator did not iterate this wallet — they spun it up fully tooled. Strong signal of **wallet rotation**: spin a fresh wallet, run for 30–60 days, retire and replace. Implication: bwamJzzt is the *current* wallet, not the operator's only wallet, and there is likely a sister wallet starting up around the same age window.
- Helius pagination stops before exposing the original funding tx (probable hard cap). To finish the funding trace would require a Solscan / Vybe / direct geyser dump.

## Bwam deeper dive (round 2)

Sampled the most recent 200 successful txs from bwam → **37 CreateV2 txs in ~3 hours**. Findings:

### Spam-fire compensates for low taste

- 4× `GOONER` / `The Gooner` in ~100 seconds (4 different mints, basically the same coin)
- 3× `Dragoncoin` / `Dragon` in 60 seconds
- 3× `barackobamai` / `Placida barackobamai`
- 2× each of `IShowGranny`, `sidelined`, `TAJIRI`, `memcoin`, `HTZ`, `podslo/podslop`

This is **insurance against tx fail**: if the first deploy 6044's, the second usually lands, and if both land you double-bag. It also confirms the bot **never re-checks "did I already fire this" before firing again** — easy quality win for us.

### Naming quality is autonomous-mediocre

Examples from his last 3 hours: `for profit coin (FPC)`, `imagine agent (IMAGINE)`, `memcoin (MEM)`, `Pumpfiles`, `Out Of Pump (OOP)`, `Hertz Global Holdings, Inc (HTZ)`. These read like an untuned model dump, not a human picking names. Lots of literal subject names (no portmanteau / pun / meme vernacular).

This is the reverse signature of what j7tracker users produced for chadlon (CHADLON MUSK / CHADLON — clearly human taste). **Our prompt has to be sharper than bwam's** — the new `src/namer/namer.py` system prompt explicitly refuses generic outputs.

### Single-point-of-failure metadata infra

ALL 37 recent creates use `meta.sdfgsdfsdf.uk`. **At time of analysis the host is returning 502 Bad Gateway and bwam has been silent for 2+ hours.** His bot is fully shut down because his throwaway metadata host went down.

The rotating throwaway hosts looked like a feature at first glance (anti-takedown). They're actually a brittleness:
- He uploads → host goes down → bot can't ship URIs in CreateV2 → bot dies.
- A bigger operator would tee uploads to 2-3 hosts in parallel and ship the first URL that succeeds.

**Our build does this** (see `src/images/multi_host_metadata.py` — R2 + Bunny + Pinata IPFS in parallel).

### Wallet rotation confirmation

Wallet age 24 days, 40k+ txs. bwam reportedly has 100k+ deploys total — implying multiple wallets run in sequence. Sister wallets sharing the same FluxBeam-flag stack would be findable by querying for wallets that tip `NextbLoCkVtMGcV47JzewQdvBpLqT9TxQFozQkN98pE` AND use the F1UXBEAM vanity flag pubkeys, but that's a deeper dig.

### Could not directly measure bwam's tweet→deploy latency

bwam's metadata JSON files include the `twitter` source-tweet field same as j7tracker, but his metadata host is currently dead so we can't fetch any of them. Latency floor is **inferred** from his architecture: durable nonce + pre-published ALTs + NextBlock relayer should put him at 1.5–3s tweet→on-chain on a hot day, longer when the metadata host is being ratelimited.

## Twitter ingestion in 2026 — what we found

(Based on web research, summarized in research/TWITTER_API_2026.md if expanded.)

| Source | P50 | P99 | Cost | Notes |
|---|---|---|---|---|
| **twitterapi.io WS** | **251 ms** | **<1.5 s** | $0.15/1k tweets | Best practical option. Push-based. |
| twscrape (auth + proxies) | 500–900 ms | 2 s | $400/mo proxies + cookies | Good hot-redundancy partner. TOS-violating. |
| X API PPU `/users/:id/tweets` poll @ 0.5 Hz | ~1.5 s | 4 s | $0.001/read = ~$200–400/mo | Cold backup + gap fill. |
| X API Filtered Stream | 5–7 s | n/a | Pro $4.5k/mo | Too slow even at the high tier. |
| X Enterprise Powerstream | <1 s claimed | n/a | $42k+/mo | Out of budget. |

**Recommended stack**: race twitterapi.io WS + twscrape + X API PPU; dedupe by tweet_id; fire on first arrival. Total < $1.5k/mo. Expected P50 300–600 ms detection. Implemented in `src/watcher/multi_source.py`.

## Image gen — best 2026 stack

Researched April 2026; key findings:

| Model | Latency | Price | Filter posture |
|---|---|---|---|
| **FLUX.2 [klein] 4B (Cloudflare Workers AI)** | **0.3–0.5 s** | ~$5/mo Workers Paid | **No platform-side refusal** on celebs/politics/parody |
| FLUX.2 [klein] 9B (Cloudflare) | 0.5–1.2 s | same | same |
| FLUX.1 [schnell] (fal.ai / Replicate) | 0.8–1.5 s | $0.003/img | No platform-side filter on managed APIs |
| FLUX.1.1 Pro / Pro Ultra | 4.5 s+ | $0.04/img | **Strict** (refuses celebs) — DO NOT USE |
| FLUX.2 [dev] 32B | 5–15 s | n/a | Quality flagship, too slow for hot path |
| DALL-E 3 / gpt-image-1 | 4–8 s | $0.04/img | Silently rewrites public-figure names — DO NOT USE |
| Google Imagen / Nano Banana 2 | 1–3 s | $0.04/img | Heavy refusals on people/politics — DO NOT USE |
| RunPod Serverless ComfyUI w/ CHROMA + Pony | 1.2 s warm, 15-30 s cold | $5/mo cold or $240/mo always-on | **Zero filtering** (fully self-controlled) |

**Tiered routing implemented in `src/images/image_gen.py`**:
1. Cloudflare Workers AI FLUX.2 Klein 4B — primary (fastest)
2. fal.ai Flux Schnell — cross-provider redundancy
3. Replicate Flux Schnell — third-party redundancy
4. RunPod ComfyUI w/ CHROMA + Pony — fully uncensored last resort

Plus `src/namer/prompt_rewriter.py` produces 3 progressive variants per prompt:
- v1: named figures verbatim ("Elon Musk shirtless")
- v2: descriptive substitutions ("a tall lean man with tight jaw, shirt off")
- v3: stylized framing ("…as an editorial cartoon, exaggerated features")

The pipeline tries v1 first (fastest, fewest refusals on permissive providers), falls through to v2/v3 only if a provider refuses. Most memecoin prompts succeed on v1.

## X API pay-per-use — researched, decided NOT to promote

X moved to global PPU in Feb 2026. April 20 2026 update brought owned reads to $0.001/req. **However**:

- Non-owned post reads: **$0.005/post** (30× more expensive than twitterapi.io's $0.15/1k = $0.00015/tweet)
- Hard cap: **2M reads/month on PPU**, anything more requires Enterprise (~$42k/mo)
- Effective rate-limit ceiling: **~11 req/s app-wide** on `/2/users/:id/tweets` — physically prevents 2 Hz × 50 accounts polling
- Latency floor: **5–30 s** edge-cache freshness window — polling faster doesn't help
- Filtered Stream still NOT on PPU
- Account Activity webhooks still NOT on PPU

**Decision**: keep twitterapi.io as primary read source. Use X API PPU only for writes (posting replies/announcements) and any owned-account reads. Implementation in `src/watcher/multi_source.py` already reflects this (`XApiPpuFallback` is opt-in cold backup, not primary).

## Pumpfun deploy modes (from official IDL/docs)

Verified against `pump-public-docs/idl/pump.json` + `docs/`:

| On-chain mechanism | What it is | How we use it |
|---|---|---|
| **Mayhem mode** | Token-2022 + on-mint metadata + Mayhem PDAs (`MAyhSmzXzV1pTf...`, `13ec7XdrjF3...`, `BwWK17cb...`). Optional via create_v2 bool. | **`is_mayhem_mode = false` ALWAYS (policy).** The 5 Mayhem accounts remain in the account list (required by program account validation) but are inert pass-throughs. |
| **Cashback** | Creator fee redirected to BUYERS based on their `UserVolumeAccumulator`. `OptionBool` arg of `create_v2`. Buy/sell need `track_volume = Some(true)` and the cashback `UserVolumeAccumulator` in remaining accounts on sell. | `is_cashback_enabled = Some(true)` for **memes**, `Some(false)` for **agents** |
| **Token incentives ("agent mode" / buybacks)** | Pump.fun ADMIN allocates daily token supply via `admin_update_token_incentives(start, end, secs_per_day, day, supply_per_day)` — NOT user-callable. Users with volume claim pro-rata via `claim_token_incentives()`. We can't enable this from a deploy; pump.fun curates which coins get it. | Tag agent coins with `category: "agent"` in metadata. Periodically run `harvest_incentives.ts` to claim on holdings if pump.fun adds incentives. |
| **Standard fee recipient** | 8 standard pump fee recipients in the Global account; one is selected per buy/sell tx. | Used always (we don't use Mayhem fee recipients). Picked at random from the pool of 8 per `pickFeeRecipient()`. |

`buildClaimTokenIncentives` is implemented in `src/deployer/pumpfun.ts`, callable on any mint we hold a position in. Use the cron-style harvester (`src/deployer/harvest_incentives.ts`) to scan `data/agent_holdings.json` daily and claim from any mint where `globalIncentiveTokenAccount` is funded.

### IDL re-derivation in pumpfun.ts

`create_v2` and `buy_exact_sol_in` re-derived against the official IDL:
- `create_v2`: **6 args** (name, symbol, uri, creator, is_mayhem_mode, is_cashback_enabled), **16 accounts** including 5 Mayhem PDAs
- `buy_exact_sol_in`: **3 args** (spendable_sol_in, min_tokens_out, track_volume:OptionBool), **16 accounts** including creator_vault, global/user_volume_accumulator, fee_config, fee_program

Account orders, PDA seeds, and discriminators all now match the official IDL. Send-real-money risk on this surface is now low.

## How we beat bwam — the four levers

| Lever | bwam | Us | Latency / quality win |
|---|---|---|---|
| Tweet ingestion | unknown (probably twitterapi.io WS or self-scrape) | RACE 3 sources | match or beat his p50, kill his p99 |
| Naming | autonomous LLM, weak prompt — fires duplicates and generic outputs | tighter prompt + dedup gate refusing near-duplicates within 5 min | quality > spam-fire |
| Mint grinding | inline (`while...endsWith("pump")`) costs 50–200 ms per deploy | pre-grinded pool topped up by background worker — pop is O(ms) | 50–200 ms |
| Image | source crop only (no gen) | parallel to metadata upload, optional Flux Schnell when source missing | hidden behind parallel work |
| Metadata host | one throwaway domain (currently 502 → bot dies) | parallel R2 + Bunny + Pinata IPFS; first success URL is used | survives any single host outage |
| Mint keypair | fresh grind in tx | pre-grinded pool | -100 ms p50 |
| Relayer | NextBlock only | RACE NextBlock + Jito + 0slot + Helius Sender + Astralane | typically 1 slot saved (~400 ms) |
| Nonce | durable nonce pool | durable nonce pool (same) | parity |
| ALTs | static / shared | same + a custom ALT we publish containing our deployer + tip accounts | parity |

End-to-end target: tweet → on-chain in **~1.7 s p50, ~3 s p99**. That's roughly **3-4× faster than bwam's observed ceiling** if he's running optimally and 10×+ faster when his metadata infra is degraded.

## Open questions / next dig

- [ ] Find the sister wallets via shared infra: query for other wallets that tip `NextbLoCkVtMGcV47JzewQdvBpLqT9TxQFozQkN98pE` AND use the same FluxBeam flag accounts AND the same metadata-host pattern.
- [ ] Resolve which non-Jito relayer the `4iUgjMT8...` tip account belongs to (likely 0slot.trade or Astralane).
- [ ] Hit rate: count chadlon-class hits vs slippage 6044 / insufficient-rent failures across the 40k tx history.
- [ ] Twitter-account distribution: extract `twitter` field from every deploy's metadata to see who they actually monitor (Elon, Trump, Trump kids, Autism Capital, etc.) and the relative weights.
