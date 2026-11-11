"""Fetch -> extract -> tag -> score for a single URL."""

from __future__ import annotations

from dataclasses import dataclass

from . import __version__
from .extract import extract
from .fetch import Fetcher, detect_bot_wall
from .score import Corpus, Result, score
from .simhash import simhash, to_hex
from .tagger import tag
from .urls import normalize, registrable_domain, site_id


@dataclass
class Evaluation:
    url: str
    norm: str
    result: Result | None
    doc: dict | None
    features: dict | None
    error: str | None = None


def unscanned_doc(url: str, wall: str, source: str) -> dict:
    """A site whose bot check stopped us and that has no archived copy: listed for an
    admin to look at by hand, with no score or tags."""
    domain = url.split("/")[2]
    return {
        "id": site_id(url), "url": url, "domain": domain, "registrableDomain": registrable_domain(url),
        "title": domain, "description": "", "image": "", "categories": [], "categoryScores": {},
        "score": 0, "scoreBreakdown": {}, "reasons": [f"couldn't scan: {wall} bot check, no archived copy"],
        "nearestSimilarity": 0.0, "simhash": "", "frameable": False, "source": source,
        "crawlerVersion": __version__, "unscanned": True, "scanBlocked": wall,
    }


def evaluate(url: str, fetcher: Fetcher, corpus: Corpus, source: str = "", use_archive: bool = True) -> Evaluation:
    """Fetch, extract, tag and score one URL.

    When a bot check (Cloudflare and the like) answers instead of the site, we never try
    to get past it. The page is scored from its latest Wayback Machine copy instead, or,
    when there isn't one, returned unscanned so an admin can review it by hand.
    """
    norm = normalize(url)
    if not norm:
        return Evaluation(url, url, None, None, None, "bad url")
    if norm.startswith("http://"):
        norm = "https://" + norm[len("http://"):]  # only secure sites are suggested
    page = fetcher.fetch(norm)
    wall = getattr(page, "bot_wall", None)
    archived = None
    if wall:
        final = normalize(page.url) or norm
        archived = fetcher.archived(final) if use_archive and hasattr(fetcher, "archived") else None
        if archived and detect_bot_wall(200, {}, archived.html):
            archived = None  # the archive only caught the challenge page too
        if not archived:
            doc = unscanned_doc(final, wall, source)
            return Evaluation(url, final, Result(0, {}, list(doc["reasons"])), doc, None)
        # Security comes from the live response (https, certificate, headers); content
        # from the archived copy.
        f = extract(final, archived.html, page.headers, [], tls=page.tls)
    elif not page.ok:
        return Evaluation(url, norm, None, None, None, page.error or f"http {page.status}")
    else:
        final = normalize(page.url) or norm
        f = extract(final, page.html, page.headers, page.scripts, tls=page.tls)
    fp = simhash(" ".join([f["title"], f["description"], f["headings"], f["text"]]))
    categories, cat_scores = tag(f)
    res = score(f, fp, corpus, categories)
    doc = {
        "id": site_id(final),
        "url": final,
        "domain": f["domain"],
        "registrableDomain": f["registrable_domain"],
        "title": f["title"] or f["domain"],
        "description": f["description"],
        "image": f["image"],
        "categories": categories,
        "categoryScores": cat_scores,
        "score": res.score,
        "scoreBreakdown": res.breakdown,
        "reasons": res.reasons,
        "nearestSimilarity": res.nearest_similarity,
        "simhash": to_hex(fp),
        "frameable": f["frameable"],
        "source": source,
        "crawlerVersion": __version__,
    }
    if wall:
        doc.update(scanBlocked=wall, scoredFrom="archive", archivedAt=archived.archived_at, frameable=False)
        res.reasons.insert(0, f"{wall} bot check: scored from the Wayback Machine copy of {archived.archived_at}")
        doc["reasons"] = res.reasons
    return Evaluation(url, final, res, doc, f)
