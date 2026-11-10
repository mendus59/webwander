"""Pick a category-balanced set of sites from a pool of scored candidates.

Each crawl evaluates more good candidates than it needs, then chooses the final
set greedily: at every step it takes the candidate whose least-covered category has
the fewest picks so far (in this run, plus a gentle nudge from what the database
already holds), breaking ties by score. The result is roughly the same number of
sites per category instead of whatever the discovery sources happened to return.
"""

from __future__ import annotations

from collections import Counter

from .simhash import from_hex, similarity

NEAR_DUPLICATE = 0.9
IGNORED = {"mature"}  # an overlay tag, not a topic to balance on


def topics(doc: dict) -> list[str]:
    cats = [c for c in doc.get("categories") or [] if c not in IGNORED]
    return cats or ["_uncategorised"]


def select_balanced(pool: list[dict], k: int, db_counts: Counter | None = None,
                    taken_domains: set[str] | None = None) -> list[dict]:
    """pool: site docs (need categories, score, registrableDomain, simhash). Returns <= k docs."""
    db_counts = db_counts or Counter()
    db_total = sum(db_counts.values()) or 1
    run_counts: Counter = Counter()
    domains = set(taken_domains or ())
    chosen: list[dict] = []
    remaining = sorted(pool, key=lambda d: d["score"], reverse=True)

    def need(doc: dict) -> tuple:
        # Lowest run count among the doc's topics first; the database share is a small
        # tie-breaker so categories that are rare overall win ties; then higher score.
        cats = topics(doc)
        run_min = min(run_counts[c] for c in cats)
        db_share = min(db_counts[c] for c in cats) / db_total
        return (run_min, db_share, -doc["score"])

    while remaining and len(chosen) < k:
        remaining.sort(key=need)
        doc = remaining.pop(0)
        if doc["registrableDomain"] in domains:
            continue
        fp = from_hex(doc.get("simhash", ""))
        if fp and any(similarity(fp, from_hex(c.get("simhash", ""))) >= NEAR_DUPLICATE for c in chosen):
            continue
        chosen.append(doc)
        domains.add(doc["registrableDomain"])
        for c in topics(doc):
            run_counts[c] += 1
    return chosen


def category_counts(docs: list[dict]) -> dict[str, int]:
    return dict(Counter(c for d in docs for c in topics(d)))
