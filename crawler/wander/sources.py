"""Free discovery sources. Each returns a list of (url, source_name) candidates.

All of these are public and need no API key. Any source that fails (rate limit,
blocked IP, changed format) is skipped so one bad source never stops a run.
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

import requests

from .fetch import USER_AGENT

HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 15

HN_QUERIES = ["interactive", "toy", "game", "generator", "simulation", "visualization", "synth",
              "explorable", "made a website", "weird", "art", "map", "puzzle", "browser"]


def _log(msg: str) -> None:
    print(f"  [sources] {msg}", file=sys.stderr)


def seeds(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    lines = [l.split("#")[0].strip() for l in path.read_text().splitlines()]
    return [(l, "seed") for l in lines if l]


def hacker_news(limit_per_query: int = 30) -> list[tuple[str, str]]:
    """Show HN posts with some community traction, via the free Algolia API."""
    out = []
    for q in random.sample(HN_QUERIES, k=min(6, len(HN_QUERIES))):
        try:
            r = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": q, "tags": "show_hn", "numericFilters": "points>15",
                        "hitsPerPage": limit_per_query},
                headers=HEADERS, timeout=TIMEOUT,
            )
            r.raise_for_status()
            out += [(h["url"], "hn") for h in r.json().get("hits", []) if h.get("url")]
        except Exception as e:  # noqa: BLE001
            _log(f"hn '{q}' failed: {e}")
    return out


def reddit(sub: str = "InternetIsBeautiful") -> list[tuple[str, str]]:
    out = []
    for listing in ("top.json?t=month&limit=100", "new.json?limit=100"):
        try:
            r = requests.get(f"https://www.reddit.com/r/{sub}/{listing}", headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            for child in r.json()["data"]["children"]:
                d = child["data"]
                if not d.get("is_self") and d.get("url") and d.get("score", 0) >= 10:
                    out.append((d["url"], f"reddit:{sub}"))
        except Exception as e:  # noqa: BLE001
            _log(f"reddit {sub} {listing} failed: {e}")
    return out


def lobsters() -> list[tuple[str, str]]:
    out = []
    for tag in ("show", "art", "games", "visualization", "graphics"):
        try:
            r = requests.get(f"https://lobste.rs/t/{tag}.json", headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            out += [(s["url"], "lobsters") for s in r.json() if s.get("url") and s.get("score", 0) >= 5]
        except Exception as e:  # noqa: BLE001
            _log(f"lobsters {tag} failed: {e}")
    return out


def wiby(n: int = 25) -> list[tuple[str, str]]:
    """wiby.me/surprise redirects to a random page from its small-web index."""
    out = []
    for _ in range(n):
        try:
            r = requests.get("https://wiby.me/surprise/", headers=HEADERS, timeout=TIMEOUT)
            m = re.search(r"URL='?([^'\">]+)", r.text, re.I)
            if m:
                out.append((m.group(1), "wiby"))
        except Exception as e:  # noqa: BLE001
            _log(f"wiby failed: {e}")
            break
    return out


def frontier(items: list[dict], n: int = 150) -> list[tuple[str, str]]:
    """Outbound links found on good pages during earlier crawls."""
    return [(i["url"], "link") for i in items[:n]]


def gather(seed_path: Path, frontier_items: list[dict], use_network: bool = True) -> list[tuple[str, str]]:
    groups = [seeds(seed_path), frontier(frontier_items)]
    if use_network:
        groups += [hacker_news(), reddit(), lobsters(), wiby()]
    for g in groups:
        random.shuffle(g)
    # Interleave so no single source dominates a run.
    out, i = [], 0
    while any(i < len(g) for g in groups):
        for g in groups:
            if i < len(g):
                out.append(g[i])
        i += 1
    return out
