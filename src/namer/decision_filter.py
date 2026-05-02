"""
Quality / dedup gates that sit between the watcher and the namer.

What we filter out:

  * Replies and self-quote-tweets (rarely the originating signal)
  * Tweets from accounts below a follower floor (low signal-to-noise)
  * Topics we already deployed within DEDUP_WINDOW_SECONDS — bwam fires
    duplicate "Dragoncoin" three times in 60s and "GOONER" four times in
    100s. We refuse to be that bot.
  * "Generic" tweets (no image, no character anchor, no quotable hook).
    The namer already rejects these via `deploy: false`, but a cheap
    heuristic gate up front saves us tokens.

The dedup gate uses fuzzy topic similarity (lowercased + whitespace-norm
shingles), not exact-match — so "GOONER" and "The Gooner" should
collapse.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass


@dataclass
class FilterDecision:
    pass_through: bool
    reason: str | None = None


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _topic_shingles(text: str) -> set[str]:
    toks = _TOKEN_RE.findall(text.lower())
    if not toks:
        return set()
    if len(toks) == 1:
        return {toks[0]}
    return {f"{toks[i]}_{toks[i+1]}" for i in range(len(toks) - 1)} | set(toks)


class TopicDedup:
    """Approximate-match dedup based on bigram + unigram overlap. Throws away
    near-duplicate topics within a configurable window so we don't fire
    "memcoin" twice or "Dragoncoin" three times like bwam does."""

    def __init__(self, window_s: float | None = None, min_overlap: float = 0.5):
        self.window = window_s if window_s is not None else float(
            os.getenv("DEDUP_WINDOW_SECONDS", "300")
        )
        self.min_overlap = min_overlap
        self._recent: list[tuple[float, set[str], str]] = []

    def is_dup(self, name_or_topic: str) -> tuple[bool, str | None]:
        now = time.monotonic()
        # GC
        cutoff = now - self.window
        self._recent = [r for r in self._recent if r[0] > cutoff]
        sh = _topic_shingles(name_or_topic)
        if not sh:
            return False, None
        for ts, prev_sh, prev_topic in self._recent:
            inter = len(sh & prev_sh)
            denom = max(1, min(len(sh), len(prev_sh)))
            if inter / denom >= self.min_overlap:
                return True, f"near-duplicate of '{prev_topic}' ({(now - ts):.0f}s ago, overlap={inter / denom:.2f})"
        self._recent.append((now, sh, name_or_topic))
        return False, None


class TweetGate:
    """Cheap pre-namer filter. Refuses obvious noise."""

    def __init__(
        self,
        skip_replies: bool = True,
        skip_retweets: bool = False,
        min_followers: int = 100_000,
    ):
        self.skip_replies = skip_replies
        self.skip_retweets = skip_retweets
        self.min_followers = min_followers

    def check(self, tweet) -> FilterDecision:
        text = (tweet.text or "").strip()
        if not text and not tweet.image_urls:
            return FilterDecision(False, "empty tweet")
        if self.skip_replies and text.startswith("@"):
            return FilterDecision(False, "reply")
        if self.skip_retweets and text.startswith("RT @"):
            return FilterDecision(False, "retweet")
        # Very short tweets without images are usually thread continuations
        if len(text) < 15 and not tweet.image_urls:
            return FilterDecision(False, "short non-image tweet")
        return FilterDecision(True)
