"""WebWander crawler CLI.

  python -m wander crawl  [--min-new 10] [--local db.json]   daily discovery run
  python -m wander seed   [--approve] [--local db.json]      import crawler/seeds.txt
  python -m wander score  URL [URL ...]                      explain one URL's score/tags
  python -m wander recheck [--local db.json]                 reject live sites that became spam/insecure
"""

from __future__ import annotations

import argparse
import os
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import ROOT, __version__, build, version_string
from . import store as st
from .balance import category_counts, select_balanced, topics
from .fetch import Fetcher
from .pipeline import evaluate
from .score import BLOCKED_DOMAINS, MIN_SCORE, Corpus
from .simhash import from_hex
from .sources import gather, rarest_categories, search_terms_for
from .urls import identity, normalize, registrable_domain, site_id

SEEDS_PATH = ROOT / "crawler" / "seeds.txt"


def log(msg: str) -> None:
    print(msg, flush=True)


def url_key(norm: str) -> str:
    return hashlib.sha1(identity(norm).encode()).hexdigest()[:16]


def open_store(args):
    return st.LocalStore(args.local) if args.local else st.FirestoreStore()


def build_corpus(sites: list[dict]) -> Corpus:
    corpus = Corpus()
    for s in sites:
        if s.get("status") in ("approved", "proposed"):
            corpus.add(from_hex(s.get("simhash", "")), s.get("registrableDomain") or registrable_domain(s["url"]))
    return corpus


def compute_stats(sites: list[dict]) -> dict:
    by_status = Counter(s.get("status") for s in sites)
    by_cat = Counter(c for s in sites if s.get("status") == "approved" for c in s.get("categories", []))
    return {"approved": by_status["approved"], "proposed": by_status["proposed"],
            "rejected": by_status["rejected"], "categories": dict(by_cat)}


def write_summary(markdown: str) -> None:
    """Show a short report on the GitHub Actions run page (no-op elsewhere)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(markdown + "\n")


def failed_submission_doc(sub: dict, ev) -> dict | None:
    norm = normalize(sub.get("url", ""))
    if not norm or not norm.startswith(("http://", "https://")):
        return None
    norm = ev.norm if ev.norm.startswith("https://") else norm
    domain = norm.split("/")[2]
    return {"id": site_id(norm), "url": norm, "domain": domain,
            "registrableDomain": registrable_domain(norm), "title": domain, "description": "",
            "categories": [], "score": 0, "scoreBreakdown": {}, "reasons": [],
            "source": "submission", "submittedBy": sub.get("uid", ""), "status": "rejected",
            "autoReject": f"couldn't fetch: {ev.error or 'unknown error'}"}


# ---------------------------------------------------------------- crawl

def cmd_crawl(args) -> int:
    started = time.time()
    store = open_store(args)
    fetcher = Fetcher(delay=args.delay)
    sites = store.load_sites()
    known_ids = {s["id"] for s in sites}
    by_id = {s["id"]: s for s in sites}
    corpus = build_corpus(sites)
    seen = st.load_seen()
    frontier = st.load_frontier()
    frontier_urls = {f["url"] for f in frontier}
    log(f"Loaded {len(sites)} sites, {len(seen)} seen URLs, {len(frontier)} frontier links")

    new_docs: list[dict] = []

    def remember_links(ev) -> None:
        if not ev.features or ev.result is None or ev.result.reject or ev.result.score < 40:
            return
        for link in ev.features["external_links"][:15]:
            if url_key(link) in seen or link in frontier_urls:
                continue
            if registrable_domain(link) in BLOCKED_DOMAINS:
                continue
            frontier.append({"url": link, "parentScore": ev.result.score, "from": ev.norm})
            frontier_urls.add(link)

    # 1. User/admin submissions always get scored and queued for review.
    for sub in store.load_submissions():
        ev = evaluate(sub["url"], fetcher, corpus, source="submission")
        if ev.doc and ev.doc["id"] not in known_ids:
            approve = bool(sub.get("autoApprove"))
            doc = dict(ev.doc, status="approved" if approve else "proposed",
                       submittedBy=sub.get("uid", ""), autoReject=ev.result.reject)
            if approve:
                doc["approvedBy"] = sub.get("uid", "")
            store.upsert_site(doc)
            new_docs.append(doc)
            known_ids.add(doc["id"])
            corpus.add(from_hex(doc["simhash"]), doc["registrableDomain"])
            log(f"  submission {ev.norm} -> {ev.result.score}")
        elif ev.doc:
            # Already in the database. An admin re-adding a rejected site wants another look.
            prev = by_id.get(ev.doc["id"], {})
            if "autoApprove" in sub and prev.get("status") == "rejected":
                store.upsert_site(dict(ev.doc, status="proposed", resubmitted=True,
                                       autoReject=ev.result.reject, submittedBy=sub.get("uid", "")))
                log(f"  submission {ev.norm}: rejected before, back in the review queue")
        else:
            # Couldn't be fetched (insecure, unreachable, bad URL). Keep a record in
            # "rejected" so whoever added it can see why, instead of it vanishing.
            log(f"  submission {sub['url']} failed: {ev.error}")
            failed = failed_submission_doc(sub, ev)
            if failed and failed["id"] not in known_ids:
                store.upsert_site(failed)
                known_ids.add(failed["id"])
        store.delete_submission(sub["id"])
        seen.add(url_key(ev.norm))
        remember_links(ev)

    # 2. Discovery: build a pool of good candidates (bigger than we need), steering
    #    searches toward the categories the database has fewest of.
    db_counts = Counter(c for s in sites if s.get("status") in ("approved", "proposed")
                        for c in topics(s))
    rare = rarest_categories(db_counts, 4)
    log(f"Rarest categories, searched for first: {', '.join(rare)}")
    candidates = gather(SEEDS_PATH, frontier, use_network=not args.offline,
                        extra_queries=search_terms_for(rare))
    log(f"{len(candidates)} candidates gathered")
    used_frontier = set()
    fetched = 0
    # Start from the backlog: good sites found earlier that didn't fit that run's balance.
    backlog = [d for d in st.load_backlog() if d["id"] not in known_ids]
    known_ids.update(d["id"] for d in backlog)
    pool: list[dict] = list(backlog)  # >= min_score, not rejected
    log(f"{len(backlog)} sites waiting in the backlog")
    runners_up: list[dict] = []  # scored but under threshold, used to guarantee --min-new
    pool_target = len(backlog) + args.max_new * args.pool_factor
    for url, source in candidates:
        if len(pool) >= pool_target or fetched >= args.max_fetch:
            break
        if time.time() - started > args.max_minutes * 60:
            log("time budget reached")
            break
        norm = normalize(url)
        if not norm:
            continue
        if source == "link":
            used_frontier.add(url)
        key = url_key(norm)
        dom = registrable_domain(norm)
        if key in seen or site_id(norm) in known_ids or dom in BLOCKED_DOMAINS:
            continue
        seen.add(key)
        fetched += 1
        ev = evaluate(norm, fetcher, corpus, source=source)
        seen.add(url_key(ev.norm))
        if not ev.doc:
            log(f"  [skip] {norm}: {ev.error}")
            continue
        if ev.doc["id"] in known_ids:
            continue
        remember_links(ev)
        if ev.result.reject:
            log(f"  [reject] {ev.norm}: {ev.result.reject}")
            continue
        known_ids.add(ev.doc["id"])
        if ev.result.score >= args.min_score:
            pool.append(dict(ev.doc))
            log(f"  [good {ev.result.score:3d}] {ev.norm}  {ev.doc['categories']}")
        else:
            runners_up.append(dict(ev.doc))
            log(f"  [low  {ev.result.score:3d}] {ev.norm}")

    # 3. Choose a category-balanced set from the pool.
    run_domains = {d["registrableDomain"] for d in new_docs}
    chosen = select_balanced(pool, max(0, args.max_new - len(new_docs)), db_counts, run_domains)
    # 4. Guarantee the daily minimum with the best of the rest (still balanced).
    if len(new_docs) + len(chosen) < args.min_new:
        taken = run_domains | {d["registrableDomain"] for d in chosen}
        fill = select_balanced(runners_up, args.min_new - len(new_docs) - len(chosen),
                               db_counts + Counter(c for d in chosen for c in topics(d)), taken)
        chosen += [dict(d, belowThreshold=True) for d in fill]
    for d in chosen:
        doc = {k: v for k, v in d.items() if not k.startswith("_")}
        doc["status"] = "proposed"
        store.upsert_site(doc)
        new_docs.append(doc)
        corpus.add(from_hex(doc["simhash"]), doc["registrableDomain"])
        log(f"  [NEW {doc['score']:3d}{' fill' if doc.get('belowThreshold') else ''}] {doc['url']}  {doc['categories']}")
    frontier = [f for f in frontier if f["url"] not in used_frontier]
    frontier_urls = {f["url"] for f in frontier}
    # Good candidates that didn't fit this run's balance are kept, already scored, for later runs.
    chosen_ids = {d["id"] for d in chosen}
    leftovers = [d for d in pool if d["id"] not in chosen_ids]
    st.save_backlog(leftovers)
    log(f"{len(leftovers)} good sites kept in the backlog for later runs")
    run_categories = category_counts([d for d in new_docs])
    log(f"Categories this run: {run_categories}")

    all_sites = sites + new_docs
    stats = compute_stats(all_sites)
    stats.update(lastRun=datetime.now(timezone.utc).isoformat(timespec="seconds"), lastRunNew=len(new_docs),
                 lastRunCategories=run_categories, crawlerVersion=__version__, crawlerBuild=build())
    store.write_stats(stats)
    store.save()
    st.save_seen(seen)
    st.save_frontier(frontier)
    st.append_log({"at": stats["lastRun"], "version": __version__, "build": build(), "fetched": fetched, "new": len(new_docs),
                   "minutes": round((time.time() - started) / 60, 1), "categories": run_categories,
                   "sites": [d["url"] for d in new_docs]})
    log(f"Done: fetched {fetched}, proposed {len(new_docs)} new sites, frontier {len(frontier)}")
    write_summary(f"## Daily crawl {version_string()}\n\n"
                  f"Proposed **{len(new_docs)}** new sites (fetched {fetched}).\n\n"
                  + "".join(f"- {c}: {n}\n" for c, n in sorted(run_categories.items())))
    if len(new_docs) < args.min_new:
        log(f"WARNING: only {len(new_docs)} new sites (wanted {args.min_new}). Add more seeds or raise --max-fetch.")
        return 2 if args.strict else 0
    return 0


# ---------------------------------------------------------------- seed

def cmd_seed(args) -> int:
    store = open_store(args)
    fetcher = Fetcher(delay=args.delay)
    sites = store.load_sites()
    known_ids = {s["id"] for s in sites}
    corpus = build_corpus(sites)
    seen = st.load_seen()
    path = Path(args.file) if args.file else SEEDS_PATH
    urls = [l.split("#")[0].strip() for l in path.read_text().splitlines()]
    added = []
    for url in filter(None, urls):
        ev = evaluate(url, fetcher, corpus, source="seed")
        seen.add(url_key(ev.norm))
        if not ev.doc:
            log(f"  [skip] {url}: {ev.error}")
            continue
        if ev.doc["id"] in known_ids:
            continue
        if ev.result.reject and not args.force:
            log(f"  [reject] {ev.norm}: {ev.result.reject}")
            continue
        status = "approved" if args.approve else "proposed"
        doc = dict(ev.doc, status=status)
        if args.approve:
            doc["approvedBy"] = "seed"
        store.upsert_site(doc)
        known_ids.add(doc["id"])
        added.append(doc)
        corpus.add(from_hex(doc["simhash"]), doc["registrableDomain"])
        log(f"  [{status} {ev.result.score:3d}] {ev.norm}  {doc['categories']}")
    stats = compute_stats(sites + added)
    store.write_stats(stats)
    store.save()
    st.save_seen(seen)
    log(f"Seeded {len(added)} sites")
    return 0


# ---------------------------------------------------------------- recheck

RECHECK_REASONS = ("spam", "insecure")


def cmd_recheck(args) -> int:
    """Re-fetch approved/proposed sites and reject any that are now spam or insecure
    (no https, bad/expired certificate, downgrade redirect, miner...)."""
    store = open_store(args)
    fetcher = Fetcher(delay=args.delay)
    sites = [s for s in store.load_sites() if s.get("status") in ("approved", "proposed")]
    removed = []
    for s in sites:
        ev = evaluate(s["url"], fetcher, Corpus())
        reason = ev.error if ev.error and ev.error.startswith("insecure") else (ev.result.reject if ev.result else None)
        if ev.error and not reason and s["url"].startswith("http://"):
            reason = "insecure: not https"  # stored as http and no working https version
        if reason and reason.startswith(RECHECK_REASONS):
            store.upsert_site({"id": s["id"], "status": "rejected", "autoReject": reason, "previousStatus": s["status"]})
            removed.append((s["url"], reason))
            log(f"  [removed] {s['url']}: {reason}")
        elif ev.error:
            log(f"  [unreachable, kept] {s['url']}: {ev.error}")
    store.save()
    log(f"Rechecked {len(sites)} sites, removed {len(removed)}")
    return 0


# ---------------------------------------------------------------- score

def cmd_score(args) -> int:
    store = st.LocalStore(args.local) if args.local else None
    corpus = build_corpus(store.load_sites()) if store else Corpus()
    fetcher = Fetcher(delay=0)
    for url in args.urls:
        ev = evaluate(url, fetcher, corpus)
        if not ev.doc:
            print(json.dumps({"url": url, "error": ev.error}))
            continue
        out = {k: ev.doc[k] for k in ("url", "title", "score", "scoreBreakdown", "reasons",
                                      "categories", "categoryScores", "frameable")}
        out["reject"] = ev.result.reject
        print(json.dumps(out, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="wander")
    p.add_argument("--version", action="version", version=f"wander {version_string()}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="daily discovery run")
    c.add_argument("--min-new", type=int, default=10, help="always propose at least this many")
    c.add_argument("--max-new", type=int, default=25)
    c.add_argument("--max-fetch", type=int, default=300)
    c.add_argument("--max-minutes", type=float, default=45)
    c.add_argument("--min-score", type=int, default=MIN_SCORE)
    c.add_argument("--pool-factor", type=int, default=4,
                   help="evaluate up to max-new x this many good candidates, then pick a balanced set")
    c.add_argument("--strict", action="store_true", help="exit non-zero if min-new not met")
    c.add_argument("--offline", action="store_true", help="only seeds + frontier, no external APIs")

    s = sub.add_parser("seed", help="import a list of URLs")
    s.add_argument("file", nargs="?")
    s.add_argument("--approve", action="store_true", help="mark seeds approved immediately")
    s.add_argument("--force", action="store_true", help="add even if auto-rejected")

    sub.add_parser("recheck", help="re-fetch live sites and reject spam/insecure ones")

    sc = sub.add_parser("score", help="explain the score for URLs")
    sc.add_argument("urls", nargs="+")

    for sp in (c, s, sc, sub.choices["recheck"]):
        sp.add_argument("--local", help="use a local JSON file instead of Firestore")
        sp.add_argument("--delay", type=float, default=0.5)

    args = p.parse_args(argv)
    log(f"WebWander crawler {version_string()}")
    return {"crawl": cmd_crawl, "seed": cmd_seed, "score": cmd_score, "recheck": cmd_recheck}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
