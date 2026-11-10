"""Fetch -> extract -> tag -> score for a single URL."""

from __future__ import annotations

from dataclasses import dataclass

from .extract import extract
from .fetch import Fetcher
from .score import Corpus, Result, score
from .simhash import simhash, to_hex
from .tagger import tag
from .urls import normalize, site_id


@dataclass
class Evaluation:
    url: str
    norm: str
    result: Result | None
    doc: dict | None
    features: dict | None
    error: str | None = None


def evaluate(url: str, fetcher: Fetcher, corpus: Corpus, source: str = "") -> Evaluation:
    norm = normalize(url)
    if not norm:
        return Evaluation(url, url, None, None, None, "bad url")
    if norm.startswith("http://"):
        norm = "https://" + norm[len("http://"):]  # only secure sites are suggested
    page = fetcher.fetch(norm)
    if not page.ok:
        return Evaluation(url, norm, None, None, None, page.error or f"http {page.status}")
    final = normalize(page.url) or norm
    f = extract(final, page.html, page.headers, page.scripts, tls=page.tls)
    fp = simhash(" ".join([f["title"], f["description"], f["headings"], f["text"]]))
    categories, cat_scores = tag(f)
    res = score(f, fp, corpus)
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
    }
    return Evaluation(url, final, res, doc, f)
