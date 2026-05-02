"""
Twitter ingestion. Three pluggable backends, ranked by latency:

  1. twitterapi.io  — reverse-engineered firehose, ~0.5s tweet→event, paid.
  2. Nitter         — public scrape, free but flaky.
  3. Tweepy / v2    — last-resort polling on bearer token, 30–60s lag.

Emits TweetEvent objects to an asyncio.Queue. Downstream consumers
(namer.py) pull from the queue and decide whether to fire a deploy.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx


@dataclass
class TweetEvent:
    id: str
    author: str
    text: str
    image_urls: list[str]
    created_at_unix: float
    detected_at_unix: float

    @property
    def detection_lag_s(self) -> float:
        return self.detected_at_unix - self.created_at_unix


class TwitterApiIoStream:
    """twitterapi.io is the practical firehose for sub-2s latency. Polls a
    user-timeline endpoint at high frequency through a CDN edge.

    NOTE: their stream API is even better if you have it; the polling
    fallback below is good enough to demonstrate the shape.
    """

    BASE = "https://api.twitterapi.io"

    def __init__(self, api_key: str, accounts: list[str], poll_hz: float = 2.0):
        self.api_key = api_key
        self.accounts = [a.lstrip("@") for a in accounts]
        self.poll_interval = 1 / poll_hz
        self._last_seen: dict[str, str] = {}

    async def stream(self) -> AsyncIterator[TweetEvent]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                t0 = time.monotonic()
                results = await asyncio.gather(
                    *(self._poll_user(client, u) for u in self.accounts),
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, Exception):
                        continue
                    for ev in r:
                        yield ev
                await asyncio.sleep(max(0.0, self.poll_interval - (time.monotonic() - t0)))

    async def _poll_user(self, client: httpx.AsyncClient, user: str) -> list[TweetEvent]:
        r = await client.get(
            f"{self.BASE}/twitter/user/last_tweets",
            params={"userName": user, "limit": 5},
            headers={"X-API-Key": self.api_key},
        )
        r.raise_for_status()
        out: list[TweetEvent] = []
        now = time.time()
        for t in r.json().get("data", []):
            tid = t["id"]
            if self._last_seen.get(user) == tid:
                break
            self._last_seen[user] = tid
            images = [m["media_url_https"] for m in t.get("media", []) if m.get("type") == "photo"]
            out.append(
                TweetEvent(
                    id=tid,
                    author=user,
                    text=t.get("text", ""),
                    image_urls=images,
                    created_at_unix=_snowflake_to_unix(int(tid)),
                    detected_at_unix=now,
                )
            )
        return list(reversed(out))  # oldest-first


def _snowflake_to_unix(tid: int) -> float:
    """Twitter snowflake IDs encode ms since 2010-11-04."""
    TWEPOCH = 1288834974657
    return ((tid >> 22) + TWEPOCH) / 1000


# ---- runnable demo ----
async def _demo() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    accounts = (os.getenv("WATCH_ACCOUNTS") or "elonmusk").split(",")
    stream = TwitterApiIoStream(os.environ["TWITTERAPI_IO_KEY"], accounts).stream()
    async for ev in stream:
        print(f"[{ev.detection_lag_s:.2f}s lag] @{ev.author}: {ev.text[:80]}  imgs={len(ev.image_urls)}")


if __name__ == "__main__":
    asyncio.run(_demo())
