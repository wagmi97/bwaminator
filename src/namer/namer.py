"""
Namer. Given a tweet (text + image), return a coin name + ticker +
description + a yes/no decision on whether to fire.

Outputs a strict JSON schema and refuses to fire on:
  - generic / non-meme tweets ("good morning", thread continuations)
  - politically toxic content that won't trade
  - tweets without enough visual or character anchor

Latency target: <1.5s on a vision-capable model.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass

import anthropic
import httpx


# Prompt is the entire IP. Tune this. The reference bot we're trying to beat
# fires duplicate names like "GOONER", "Dragoncoin", "memcoin" repeatedly —
# this prompt explicitly refuses that kind of low-effort output.
SYSTEM_PROMPT = """You are a meme-coin naming engine for a pump.fun deploy bot. \
You see a single tweet (text + maybe an image) and decide whether it is \
"deployable narrative material" — and if so, you propose a name + ticker.

Your output is fed straight into an on-chain transaction. There is no human \
review. Wrong outputs cost real money. Be ruthless.

# When to deploy

Deploy ONLY when the tweet has at least ONE of:
- a recognizable named character (Elon, Trump, a streamer, a CEO)
- a specific event the meme can anchor to (court date, beef, gaffe)
- a strong unique visual (a face, an object, a moment frozen in time)
- a quotable phrase that "wants" to become a coin name

REFUSE (deploy=false) for:
- replies, threads, quote-tweets without new context
- opinions / takes / commentary without a visual or character anchor
- generic news without meme legs
- tweets where you cannot picture a holder saying "the coin from the X tweet"
- topics already saturated this week (don't be the 5th GOONER)

# Naming taste

Crypto-native, not corporate. Lower-case ironic > title-case proper noun.
Portmanteaus, puns, in-jokes, deliberate misspellings ALL good. Two-word \
names beat three. Names that read like a friend's nickname for the topic \
beat names that read like a press release.

GOOD names: chadlon, pepetrump, sigmasam, gooncoin, niggernomics (joke), \
weatherguy, courtchad, rippedlon, sadtman, lockedin, doneso, baby boba.

BAD names (refuse to output): "memcoin", "for profit coin", "ABC Token", \
"$EVENT Token", "OfficialX", "TheY", anything that's just the subject's \
name in caps (ELONCOIN, TRUMPCOIN), or generic words ("dragon", "war", \
"gooner") that have no specific tweet anchor.

# Ticker taste

4-8 chars, all caps. Ideally a portmanteau or pun, NOT initials.
GOOD: CHADLON, PEPRUMP, GIGABASED, RIPLON, SADTMAN.
BAD: TWEETCOIN, ELONTOKEN, ABC, TC.

# Topic classification (deploy strategy)

Set "category" to one of:
  - "agent"  — when the tweet's subject is autonomous-software topics: \
               agents, robots, autonomous systems, model labs, model \
               products, or character-style chatbot personas. These coins \
               get pump.fun-funded token incentives ("buybacks") if \
               curated; we want them flagged so pump.fun's discovery picks \
               them up.
  - "meme"   — everything else (people, events, jokes, IRL chaos)

The category controls on-chain flags (cashback off for agent, on for meme) \
and the metadata category tag.

# Output

Return strict JSON only, no prose, no code fence:

{
  "deploy": true | false,
  "reason_skipped": null | "one terse sentence",
  "name": "...",
  "symbol": "...",
  "description": "ONE ironic sentence, under 140 chars (or empty)",
  "image_strategy": "crop_source" | "generate" | "use_source",
  "image_prompt": null | "for Flux Schnell if generate",
  "category": "meme" | "agent",
  "confidence": 0.0-1.0
}

If confidence is below 0.65, set deploy=false even if everything else looks \
fine. The orchestrator's bias is toward NOT firing — better to miss than \
to ship slop.
"""


@dataclass
class NamingResult:
    deploy: bool
    name: str | None
    symbol: str | None
    description: str | None
    image_strategy: str | None
    image_prompt: str | None
    reason_skipped: str | None
    category: str | None
    confidence: float
    raw: dict


class Namer:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.client = anthropic.AsyncAnthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self.model = model or os.environ["NAMER_MODEL"]

    async def name(self, tweet_text: str, image_url: str | None = None) -> NamingResult:
        content: list[dict] = [{"type": "text", "text": tweet_text or "(no text)"}]
        if image_url:
            img_bytes = await _fetch_bytes(image_url)
            content.insert(
                0,
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": _guess_media_type(image_url),
                        "data": base64.b64encode(img_bytes).decode(),
                    },
                },
            )

        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=400,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # cache the static prompt
                }
            ],
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        # tolerate code-fence wrap
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[1].rsplit("\n", 1)[0]
        data = json.loads(text)
        return NamingResult(
            deploy=bool(data.get("deploy")),
            name=data.get("name"),
            symbol=data.get("symbol"),
            description=data.get("description") or "",
            image_strategy=data.get("image_strategy"),
            image_prompt=data.get("image_prompt"),
            reason_skipped=data.get("reason_skipped"),
            category=data.get("category", "meme"),
            confidence=float(data.get("confidence", 0.0)),
            raw=data,
        )


async def _fetch_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


def _guess_media_type(url: str) -> str:
    u = url.lower().split("?")[0]
    if u.endswith(".png"):
        return "image/png"
    if u.endswith(".webp"):
        return "image/webp"
    if u.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


# ---- demo ----
async def _demo() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    n = Namer()
    r = await n.name(
        tweet_text="In court today",
        image_url="https://pbs.twimg.com/media/SOME-ELON-COURT-IMAGE.jpg",
    )
    print(json.dumps(r.raw, indent=2))


if __name__ == "__main__":
    asyncio.run(_demo())
