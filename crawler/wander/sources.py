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
              "explorable", "made a website", "weird", "art", "map", "puzzle", "browser",
              "essay", "manifesto", "philosophy", "zine", "poetry", "archive"]
# Regular (non "Show HN") stories: essays and ideas pages people found worth discussing.
HN_STORY_QUERIES = ["essay", "philosophy", "manifesto", "politics", "ethics", "society", "rant",
                    "controversial", "thought experiment", "personal website", "history of",
                    "why i", "in defense of", "against", "the case for", "language", "culture"]
LOBSTERS_TAGS = ["show", "art", "games", "visualization", "graphics", "philosophy", "culture",
                 "law", "person", "science", "historical", "practices"]


def _log(msg: str) -> None:
    print(f"  [sources] {msg}", file=sys.stderr)


def seeds(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    lines = [l.split("#")[0].strip() for l in path.read_text().splitlines()]
    return [(l, "seed") for l in lines if l]


def rarest_categories(counts, n: int = 4) -> list[str]:
    """Category ids the database holds the fewest of (Mature is never targeted)."""
    from .tagger import load_categories
    ids = [c["id"] for c in load_categories() if c["id"] != "mature"]
    return sorted(ids, key=lambda c: (counts.get(c, 0), random.random()))[:n]


def search_terms_for(category_ids: list[str], per_category: int = 2) -> list[str]:
    """A few keywords per category, used as extra discovery searches. A category can list
    its own "search" terms (Mature has no tagging keywords, but can still be searched for)."""
    from .tagger import load_categories
    by_id = {c["id"]: c for c in load_categories()}
    terms = []
    for cid in category_ids:
        kws = by_id[cid].get("search") or by_id[cid]["keywords"]
        if kws:
            terms += [kws[0]] + random.sample(kws[1:], k=min(per_category - 1, len(kws) - 1))
    return terms


def hacker_news(limit_per_query: int = 30, extra_queries: list[str] | None = None) -> list[tuple[str, str]]:
    """Show HN posts with some community traction, via the free Algolia API."""
    out = []
    queries = list(extra_queries or []) + random.sample(HN_QUERIES, k=min(4, len(HN_QUERIES)))
    for q in queries:
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
    for q in random.sample(HN_STORY_QUERIES, k=min(4, len(HN_STORY_QUERIES))):
        try:
            r = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": q, "tags": "story", "numericFilters": "points>40",
                        "hitsPerPage": limit_per_query},
                headers=HEADERS, timeout=TIMEOUT,
            )
            r.raise_for_status()
            out += [(h["url"], "hn") for h in r.json().get("hits", []) if h.get("url")]
        except Exception as e:  # noqa: BLE001
            _log(f"hn story '{q}' failed: {e}")
    return out


# ---- topic searches, used by tag-targeted runs to find many pages about the chosen tags

def hn_topic(queries: list[str], per_query: int = 30) -> list[tuple[str, str]]:
    """Hacker News stories and Show HNs about each topic (lower bar than the general feed)."""
    out = []
    for q in queries:
        for tags, points in (("show_hn", 5), ("story", 20)):
            try:
                r = requests.get("https://hn.algolia.com/api/v1/search",
                                 params={"query": q, "tags": tags, "numericFilters": f"points>{points}",
                                         "hitsPerPage": per_query}, headers=HEADERS, timeout=TIMEOUT)
                r.raise_for_status()
                out += [(h["url"], "hn-topic") for h in r.json().get("hits", []) if h.get("url")]
            except Exception as e:  # noqa: BLE001
                _log(f"hn topic '{q}' failed: {e}")
    return out


def marginalia(queries: list[str], per_query: int = 40) -> list[tuple[str, str]]:
    """Marginalia Search: a search engine for the small, non-commercial web (public API key)."""
    out = []
    for q in queries:
        try:
            r = requests.get(f"https://api.marginalia.nu/public/search/{requests.utils.quote(q)}",
                             params={"count": per_query}, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            out += [(h["url"], "marginalia") for h in r.json().get("results", []) if h.get("url")]
        except Exception as e:  # noqa: BLE001
            _log(f"marginalia '{q}' failed: {e}")
    return out


def wiby_search(queries: list[str]) -> list[tuple[str, str]]:
    """Wiby's search over its hand-curated index of simple, old-school personal pages."""
    out = []
    for q in queries:
        try:
            r = requests.get("https://wiby.me/json/", params={"q": q}, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            out += [(h["URL"], "wiby") for h in r.json() if isinstance(h, dict) and h.get("URL")]
        except Exception as e:  # noqa: BLE001
            _log(f"wiby search '{q}' failed: {e}")
    return out


def topic_search(queries: list[str]) -> list[tuple[str, str]]:
    """Pages about the given topics from every search source, interleaved."""
    return _interleave([hn_topic(queries), marginalia(queries), wiby_search(queries)])


def keyword_rank(items: list[tuple[str, str]], keywords: list[str]) -> list[tuple[str, str]]:
    """Put links whose URL mentions one of the keywords first (keeps order otherwise)."""
    words = [re.sub(r"[^a-z0-9]+", "", k.lower()) for k in keywords]
    words = [w for w in words if len(w) >= 3]

    def hits(url: str) -> int:
        u = re.sub(r"[^a-z0-9]+", "", url.lower())
        return sum(w in u for w in words)
    return sorted(items, key=lambda it: -hits(it[0]))


def _interleave(groups: list[list]) -> list:
    out, i = [], 0
    while any(i < len(g) for g in groups):
        for g in groups:
            if i < len(g):
                out.append(g[i])
        i += 1
    return out


def lobsters() -> list[tuple[str, str]]:
    out = []
    for tag in LOBSTERS_TAGS:
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


def kagi_small_web(n: int = 60) -> list[tuple[str, str]]:
    """Recent posts from Kagi's public Small Web feed: thousands of personal sites and blogs."""
    try:
        r = requests.get("https://kagi.com/api/v1/smallweb/feed/", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        urls = re.findall(r"<link[^>]+href=[\"']([^\"']+)[\"']", r.text)
        urls = [u for u in urls if u.startswith("http") and "kagi.com" not in u]
        # The feed lists individual (often everyday) posts; the site's front page is the
        # better thing to suggest, so that's what gets evaluated.
        roots = list(dict.fromkeys(re.sub(r"^(https?://[^/]+).*$", r"\1/", u) for u in urls))
        return [(u, "smallweb") for u in random.sample(roots, k=min(n, len(roots)))]
    except Exception as e:  # noqa: BLE001
        _log(f"kagi small web failed: {e}")
        return []


def frontier(items: list[dict], n: int = 150) -> list[tuple[str, str]]:
    """Outbound links found on good pages during earlier crawls."""
    return [(i["url"], "link") for i in items[:n]]


def frontier_for(items: list[dict], wanted: list[str], keywords: list[str], n: int = 600) -> list[tuple[str, str]]:
    """Frontier links for a targeted run: links found on pages that had the chosen tags
    first, then links whose URL mentions a topic keyword, then the rest."""
    def rank(i: dict) -> tuple:
        return (-len(set(i.get("parentCats") or []) & set(wanted)),)
    ordered = sorted(items, key=rank)
    tagged = [(i["url"], "link") for i in ordered if set(i.get("parentCats") or []) & set(wanted)]
    rest = keyword_rank([(i["url"], "link") for i in ordered if not set(i.get("parentCats") or []) & set(wanted)], keywords)
    return (tagged + rest)[:n]


def gather(seed_path: Path, frontier_items: list[dict], use_network: bool = True,
           extra_queries: list[str] | None = None, wanted: list[str] | None = None,
           keywords: list[str] | None = None) -> list[tuple[str, str]]:
    if wanted:
        # Targeted run: topic searches and on-topic frontier links come first, then the
        # usual sources (whose pages are kept only if they turn out to match).
        first = [frontier_for(frontier_items, wanted, keywords or extra_queries or [])]
        if use_network:
            first.insert(0, topic_search(extra_queries or []))
        lead = _interleave(first)
        lead_urls = {u for u, _ in lead}
        rest = gather(seed_path, [], use_network, extra_queries=None)
        return lead + [c for c in rest if c[0] not in lead_urls]
    groups = [seeds(seed_path), frontier(frontier_items)]
    if use_network:
        groups += [hacker_news(extra_queries=extra_queries), lobsters(), wiby(), kagi_small_web()]
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
