"""
Multi-source tweet ingestion. Race three independent paths and fire on whichever
sees a tweet first; dedupe by tweet_id so we don't fire twice on the same
event.

Why three:
  1. twitterapi.io WebSocket (commercial firehose; P50 251ms, P90 327ms,
     $0.15/1k tweets). Primary because it has the best latency.
  2. twscrape (self-hosted authenticated GraphQL polling at 1-2Hz with
     residential proxies). Hot redundancy; sometimes beats the WS due to
     Twitter's internal replication patterns.
  3. X API Pay-Per-Use (`/2/users/:id/tweets`) at 0.5Hz on the top ~10
     accounts. Cold backup + authoritative source-of-truth for gap fill.

Each source emits TweetEvent on a shared asyncio.Queue. The dedup gate
drops anything seen within the last N seconds.

If we win latency by even 200ms over bwam we'll typically land first; same
slot of arrival = same outcome, but we save 200-400ms compared to the
single-WS-only architecture and eliminate the WS-down failure mode.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
import websockets


@dataclass
class TweetEvent:
    id: str
    author: str
    text: str
    image_urls: list[str]
    created_at_unix: float
    detected_at_unix: float
    detected_by: str  # "ws" | "scrape" | "api"

    @property
    def detection_lag_s(self) -> float:
        return self.detected_at_unix - self.created_at_unix


def snowflake_to_unix(tid: int) -> float:
    return ((tid >> 22) + 1288834974657) / 1000


class _DedupGate:
    """Drop tweets we've already seen recently. Sources race in parallel; the
    first one through wins, the rest get suppressed."""

    def __init__(self, window_s: float = 60.0):
        self.window = window_s
        self._seen: dict[str, float] = {}

    def first_seen(self, tweet_id: str) -> bool:
        now = time.monotonic()
        # garbage-collect old entries on the cheap
        if len(self._seen) > 4096:
            cutoff = now - self.window
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        if tweet_id in self._seen:
            return False
        self._seen[tweet_id] = now
        return True


class TwitterApiIoWebSocket:
    """Primary: twitterapi.io WebSocket. Push-based; under 1s.

    Setup once via REST: POST /api/twitter/webhook/rule with
    {"value": "from:user1 OR from:user2 ...", "tag": "default"}
    Then connect WS with API key; receive {tweets:[...]} frames.
    """

    def __init__(self, api_key: str, ws_url: str, accounts: list[str]):
        self.api_key = api_key
        self.ws_url = ws_url
        self.accounts = [a.lstrip("@") for a in accounts]

    async def ensure_rule(self) -> None:
        rule = " OR ".join(f"from:{u}" for u in self.accounts)
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                "https://api.twitterapi.io/api/twitter/webhook/rule",
                headers={"X-API-Key": self.api_key},
                json={"value": rule, "tag": "watch"},
            )

    async def run(self, queue: asyncio.Queue[TweetEvent]) -> None:
        await self.ensure_rule()
        backoff = 0.5
        while True:
            try:
                async with websockets.connect(
                    f"{self.ws_url}?apiKey={self.api_key}",
                    ping_interval=15,
                    ping_timeout=10,
                ) as ws:
                    backoff = 0.5
                    async for raw in ws:
                        now = time.time()
                        msg = json.loads(raw)
                        for t in msg.get("tweets") or []:
                            ev = TweetEvent(
                                id=str(t["id"]),
                                author=t.get("author", {}).get("userName", ""),
                                text=t.get("text") or "",
                                image_urls=[
                                    m["media_url_https"]
                                    for m in t.get("extendedEntities", {}).get("media", [])
                                    if m.get("type") == "photo"
                                ],
                                created_at_unix=snowflake_to_unix(int(t["id"])),
                                detected_at_unix=now,
                                detected_by="ws",
                            )
                            await queue.put(ev)
            except Exception as e:  # noqa: BLE001
                print(f"[ws] reconnect after {backoff:.1f}s: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)


class TwscrapeFallback:
    """Hot redundancy. Polls user timelines at 1-2 Hz over a pool of
    authenticated cookie sessions + residential proxies. Sometimes beats the WS
    on individual events because of internal Twitter replication patterns."""

    def __init__(self, db_path: str, accounts: list[str], hz: float = 1.5):
        self.db_path = db_path
        self.accounts = [a.lstrip("@") for a in accounts]
        self.interval = 1 / hz

    async def run(self, queue: asyncio.Queue[TweetEvent]) -> None:
        # twscrape import is lazy because it's optional
        from twscrape import API  # type: ignore

        api = API(self.db_path)
        last_seen: dict[str, str] = {}
        while True:
            t0 = time.monotonic()
            for user in self.accounts:
                try:
                    async for tweet in api.user_tweets(user, limit=3):
                        if last_seen.get(user) == str(tweet.id):
                            break
                        last_seen[user] = str(tweet.id)
                        ev = TweetEvent(
                            id=str(tweet.id),
                            author=user,
                            text=tweet.rawContent or "",
                            image_urls=[
                                m.fullUrl for m in (tweet.media or []) if m.type == "photo"
                            ],
                            created_at_unix=snowflake_to_unix(int(tweet.id)),
                            detected_at_unix=time.time(),
                            detected_by="scrape",
                        )
                        await queue.put(ev)
                except Exception as e:  # noqa: BLE001
                    print(f"[scrape] {user}: {e}")
            await asyncio.sleep(max(0, self.interval - (time.monotonic() - t0)))


class XApiPpuFallback:
    """Cold backup. Polls X API PPU at 0.5Hz on top accounts. Mainly for gap
    fill; also a sanity check that the WS isn't silently broken."""

    def __init__(self, bearer: str, accounts: list[str], hz: float = 0.5):
        self.bearer = bearer
        self.accounts = [a.lstrip("@") for a in accounts]
        self.interval = 1 / hz
        self._user_id_cache: dict[str, str] = {}

    async def _user_id(self, c: httpx.AsyncClient, user: str) -> str | None:
        if user in self._user_id_cache:
            return self._user_id_cache[user]
        r = await c.get(
            f"https://api.twitter.com/2/users/by/username/{user}",
            headers={"Authorization": f"Bearer {self.bearer}"},
        )
        if r.status_code != 200:
            return None
        uid = r.json().get("data", {}).get("id")
        if uid:
            self._user_id_cache[user] = uid
        return uid

    async def run(self, queue: asyncio.Queue[TweetEvent]) -> None:
        last_seen: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=8) as c:
            while True:
                t0 = time.monotonic()
                for user in self.accounts:
                    uid = await self._user_id(c, user)
                    if not uid:
                        continue
                    try:
                        r = await c.get(
                            f"https://api.twitter.com/2/users/{uid}/tweets",
                            params={
                                "max_results": 5,
                                "tweet.fields": "created_at,attachments",
                                "expansions": "attachments.media_keys",
                                "media.fields": "url,type",
                            },
                            headers={"Authorization": f"Bearer {self.bearer}"},
                        )
                        if r.status_code != 200:
                            continue
                        data = r.json()
                        media_by_key = {
                            m["media_key"]: m
                            for m in data.get("includes", {}).get("media", [])
                        }
                        for t in data.get("data", []):
                            tid = t["id"]
                            if last_seen.get(user) == tid:
                                break
                            last_seen[user] = tid
                            urls = [
                                media_by_key[k]["url"]
                                for k in t.get("attachments", {}).get("media_keys", [])
                                if media_by_key.get(k, {}).get("type") == "photo"
                            ]
                            ev = TweetEvent(
                                id=tid,
                                author=user,
                                text=t.get("text") or "",
                                image_urls=urls,
                                created_at_unix=snowflake_to_unix(int(tid)),
                                detected_at_unix=time.time(),
                                detected_by="api",
                            )
                            await queue.put(ev)
                    except Exception as e:  # noqa: BLE001
                        print(f"[api] {user}: {e}")
                await asyncio.sleep(max(0, self.interval - (time.monotonic() - t0)))


async def race_sources(accounts: list[str]) -> AsyncIterator[TweetEvent]:
    """Compose all available sources, dedupe by tweet_id, yield deduped events."""
    queue: asyncio.Queue[TweetEvent] = asyncio.Queue()
    gate = _DedupGate()

    tasks: list[asyncio.Task[None]] = []
    if (key := os.getenv("TWITTERAPI_IO_KEY")) and (ws := os.getenv("TWITTERAPI_IO_WS")):
        tasks.append(asyncio.create_task(TwitterApiIoWebSocket(key, ws, accounts).run(queue)))
    if os.getenv("TWSCRAPE_DB_PATH"):
        tasks.append(
            asyncio.create_task(TwscrapeFallback(os.environ["TWSCRAPE_DB_PATH"], accounts).run(queue))
        )
    if bearer := os.getenv("X_API_BEARER_TOKEN"):
        tasks.append(asyncio.create_task(XApiPpuFallback(bearer, accounts).run(queue)))
    if not tasks:
        raise RuntimeError("no twitter sources configured")

    try:
        while True:
            ev = await queue.get()
            if gate.first_seen(ev.id):
                yield ev
    finally:
        for t in tasks:
            t.cancel()
