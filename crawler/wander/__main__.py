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
from urllib.parse import urlsplit
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
            "rejected": by_status["rejected"], "backlog": by_status["backlog"], "categories": dict(by_cat)}


def merge_sites(sites: list[dict], changed: list[dict], removed: set[str] = frozenset()) -> list[dict]:
    """The site list after this run's writes, for stats."""
    merged = {s["id"]: s for s in sites}
    for d in changed:
        merged[d["id"]] = {**merged.get(d["id"], {}), **d}
    return [s for i, s in merged.items() if i not in removed]


STATUS_RANK = {"approved": 0, "proposed": 1, "backlog": 2, "rejected": 3}


def site_domain(s: dict) -> str:
    return s.get("registrableDomain") or registrable_domain(s.get("url", ""))


def dedupe_by_domain(store, sites: list[dict]) -> set[str]:
    """One site per domain, everywhere. If a domain has several site docs (from older
    crawler versions), keep the most useful one (approved > proposed > backlog > rejected,
    then the best score) and delete the rest. Returns the deleted ids; `sites` is updated."""
    by_dom: dict[str, list[dict]] = {}
    for s in sites:
        if s.get("status") in STATUS_RANK:
            by_dom.setdefault(site_domain(s), []).append(s)
    removed: set[str] = set()
    for dom, group in by_dom.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda s: (STATUS_RANK[s["status"]], -(s.get("score") or 0)))
        for extra in group[1:]:
            store.delete_site(extra["id"])
            removed.add(extra["id"])
            log(f"  [dedupe] {dom}: removed {extra['status']} {extra.get('url')} (kept {group[0].get('url')})")
    sites[:] = [s for s in sites if s["id"] not in removed]
    return removed


def known_domains_of(sites: list[dict]) -> dict[str, dict]:
    return {site_domain(s): s for s in sites if s.get("status") in STATUS_RANK}


def load_backlog(store, sites: list[dict], known_ids: set[str]) -> list[dict]:
    """Backlog sites (status "backlog"), minus expired ones, which are deleted. Moves the
    old data/backlog.json into the database the first time it runs."""
    domains = set(known_domains_of(sites))
    for d in st.take_legacy_backlog():
        if d["id"] not in known_ids and site_domain(d) not in domains:
            domains.add(site_domain(d))
            store.upsert_site(dict(d, status="backlog"))
            sites.append(dict(d, status="backlog"))
            known_ids.add(d["id"])
    cutoff = time.time() - st.BACKLOG_DAYS * 86400
    backlog = []
    for s in sites:
        if s.get("status") != "backlog":
            continue
        if (s.get("backlogAt") or 0) < cutoff:
            store.delete_site(s["id"])  # stale: the page may have changed
            s["status"] = None
        else:
            backlog.append(s)
    return backlog


def trim_backlog(store, backlog: list[dict]) -> set[str]:
    """Keep only the BACKLOG_MAX best-scoring backlog sites; returns the removed ids."""
    extra = sorted(backlog, key=lambda d: d.get("score", 0), reverse=True)[st.BACKLOG_MAX:]
    for d in extra:
        store.delete_site(d["id"])
    return {d["id"] for d in extra}


def process_submissions(store, fetcher, corpus, known_ids, by_id, seen, remember_links=None,
                        domains: dict[str, dict] | None = None) -> list[dict]:
    """Score every pending submission and put it in Proposed (or Approved when the admin
    asked). Every submission is removed afterwards, so nothing waits in a queue. A domain
    that is already in the database is never added a second time."""
    domains = known_domains_of(list(by_id.values())) if domains is None else domains
    new_docs = []
    for sub in store.load_submissions():
        ev = evaluate(sub["url"], fetcher, corpus, source="submission")
        approve = bool(sub.get("autoApprove"))
        prev = (by_id.get(ev.doc["id"]) or domains.get(ev.doc["registrableDomain"])) if ev.doc else None
        if ev.doc and not prev:
            doc = dict(ev.doc, status="approved" if approve else "proposed",
                       submittedBy=sub.get("uid", ""), autoReject=ev.result.reject)
            if approve:
                doc["approvedBy"] = sub.get("uid", "")
            store.upsert_site(doc)
            new_docs.append(doc)
            known_ids.add(doc["id"])
            by_id[doc["id"]] = doc
            domains[doc["registrableDomain"]] = doc
            corpus.add(from_hex(doc["simhash"]), doc["registrableDomain"])
            log(f"  submission {ev.norm} -> {ev.result.score}")
        elif ev.doc:
            # The site (or another page of its domain) is already in the database, so it
            # isn't added again. A backlog site moves to the queue (or straight to
            # approved); an admin re-adding a rejected site wants another look.
            if prev.get("status") == "backlog" or ("autoApprove" in sub and prev.get("status") == "rejected"):
                status = "approved" if approve else "proposed"
                update = dict(ev.doc) if prev["id"] == ev.doc["id"] else {"id": prev["id"]}
                update.update(status=status, resubmitted=prev.get("status") == "rejected",
                              submittedBy=sub.get("uid", ""))
                if prev["id"] == ev.doc["id"]:
                    update["autoReject"] = ev.result.reject
                store.upsert_site(update)
                log(f"  submission {ev.norm}: {prev.get('url')} was {prev.get('status')}, now {status}")
                prev["status"] = status
            else:
                log(f"  submission {ev.norm}: domain already listed ({prev.get('status')}: {prev.get('url')})")
        else:
            # Couldn't be fetched (insecure, unreachable, bad URL). Keep a record in
            # "rejected" so whoever added it can see why, instead of it vanishing.
            log(f"  submission {sub['url']} failed: {ev.error}")
            failed = failed_submission_doc(sub, ev)
            if failed and failed["id"] not in known_ids and failed["registrableDomain"] not in domains:
                store.upsert_site(failed)
                known_ids.add(failed["id"])
                domains[failed["registrableDomain"]] = failed
        store.delete_submission(sub["id"])
        seen.add(url_key(ev.norm))
        if remember_links:
            remember_links(ev)
    return new_docs


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

def parse_tags(raw: str | None) -> list[str]:
    """Category ids from "art, music" (unknown ids are an error, so typos don't silently
    turn a targeted run into an empty one)."""
    from .tagger import load_categories
    wanted = [t.strip().lower() for t in (raw or "").replace(";", ",").split(",") if t.strip()]
    known = {c["id"] for c in load_categories()}
    unknown = [t for t in wanted if t not in known]
    if unknown:
        raise SystemExit(f"unknown tag(s): {', '.join(unknown)} (known: {', '.join(sorted(known))})")
    return list(dict.fromkeys(wanted))


def topic_keywords(wanted: list[str]) -> list[str]:
    from .tagger import load_categories
    by_id = {c["id"]: c for c in load_categories()}
    return [k for cid in wanted for k in (by_id[cid].get("search") or []) + by_id[cid]["keywords"]]


def tag_filter(wanted: list[str], match: str):
    """A predicate for "has any of these tags" (OR) or "has all of them" (AND)."""
    if not wanted:
        return lambda doc: True
    want = set(wanted)
    if match == "all":
        return lambda doc: want <= set(doc.get("categories") or [])
    return lambda doc: bool(want & set(doc.get("categories") or []))


def cmd_crawl(args) -> int:
    started = time.time()
    store = open_store(args)
    fetcher = Fetcher(delay=args.delay)
    sites = store.load_sites()
    known_ids = {s["id"] for s in sites}
    # Start from the backlog: good sites found earlier that didn't fit that run's balance.
    backlog = load_backlog(store, sites, known_ids)
    deduped = dedupe_by_domain(store, sites)
    backlog = [d for d in backlog if d["id"] not in deduped]
    domains = known_domains_of(sites)  # every domain already in the database, any status
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
            frontier.append({"url": link, "parentScore": ev.result.score, "from": ev.norm,
                             "parentCats": list(ev.doc.get("categories") or [])})
            frontier_urls.add(link)

    # 1. User/admin submissions always get scored and put in the review queue.
    new_docs += process_submissions(store, fetcher, corpus, known_ids, by_id, seen, remember_links, domains)

    # 2. Discovery: build a pool of good candidates (bigger than we need), steering
    #    searches toward the categories the database has fewest of.
    db_counts = Counter(c for s in sites if s.get("status") in ("approved", "proposed")
                        for c in topics(s))
    wanted = parse_tags(args.tags)
    wants = tag_filter(wanted, args.match)
    if wanted:
        # A targeted run: search for the chosen kinds of content and keep only sites that
        # carry those tags (any of them, or all of them). Mature has no search terms.
        joiner = " AND " if args.match == "all" else " OR "
        log(f"Targeted run: only sites tagged {joiner.join(wanted)}")
        # Up to ~24 topic searches, spread over the chosen tags.
        queries = search_terms_for(wanted, per_category=max(3, 24 // len(wanted)))
        keywords = topic_keywords(wanted)
        log(f"Topic searches: {', '.join(queries)}")
    else:
        rare = rarest_categories(db_counts, 4)
        log(f"Rarest categories, searched for first: {', '.join(rare)}")
        queries, keywords = search_terms_for(rare), None
    candidates = gather(SEEDS_PATH, frontier, use_network=not args.offline, extra_queries=queries,
                        wanted=wanted or None, keywords=keywords)
    log(f"{len(candidates)} candidates gathered")
    used_frontier = set()
    fetched = 0
    pool: list[dict] = [d for d in backlog if wants(d)]  # >= min_score, not rejected
    log(f"{len(backlog)} sites waiting in the backlog" + (f", {len(pool)} with the chosen tags" if wanted else ""))
    runners_up: list[dict] = []  # scored but under threshold, used to guarantee --min-new
    unscanned: list[dict] = []  # behind a bot check and never archived: for review by hand
    off_topic = 0  # good pages skipped because a targeted run wanted other tags
    pool_target = len(pool) + args.max_new * args.pool_factor
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
        host = norm.split("/")[2].removeprefix("www.")
        if key in seen or site_id(norm) in known_ids or dom in BLOCKED_DOMAINS or host in BLOCKED_DOMAINS or dom in domains:
            continue  # seen before, or its domain is already in the database
        seen.add(key)
        fetched += 1
        ev = evaluate(norm, fetcher, corpus, source=source)
        seen.add(url_key(ev.norm))
        if not ev.doc:
            log(f"  [skip] {norm}: {ev.error}")
            continue
        if ev.doc["id"] in known_ids or ev.doc["registrableDomain"] in domains:
            continue  # redirected to a site/domain we already have
        remember_links(ev)
        domains[ev.doc["registrableDomain"]] = ev.doc  # one page per domain, even within a run
        if ev.result.reject:
            log(f"  [reject] {ev.norm}: {ev.result.reject}")
            continue
        if wanted and (ev.doc.get("unscanned") or not wants(ev.doc)):
            # Not what this run is for. Forget it so a later run can still pick it up.
            domains.pop(ev.doc["registrableDomain"], None)
            seen.discard(key)
            seen.discard(url_key(ev.norm))
            off_topic += 1
            continue
        if ev.doc.get("unscanned"):
            # Behind a bot check with no archived copy: list it for review by hand, a few
            # per run (only front pages: an unscanned article or thread isn't worth a look).
            # The rest are tried again on a later run.
            if urlsplit(ev.norm).path.strip("/") or urlsplit(ev.norm).query:
                log(f"  [unscanned, skipped] {ev.norm}: not a front page")
                continue
            if len(unscanned) < args.max_unscanned:
                unscanned.append(dict(ev.doc))
                known_ids.add(ev.doc["id"])
                log(f"  [unscanned] {ev.norm}: {ev.doc['scanBlocked']} bot check")
            else:
                domains.pop(ev.doc["registrableDomain"], None)
                seen.discard(key)
                seen.discard(url_key(ev.norm))
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
        corpus.add(from_hex(doc.get("simhash", "")), doc["registrableDomain"])
        log(f"  [NEW {doc['score']:3d}{' fill' if doc.get('belowThreshold') else ''}] {doc['url']}  {doc['categories']}")
    for d in unscanned:
        doc = dict(d, status="proposed")
        store.upsert_site(doc)
        new_docs.append(doc)
        log(f"  [NEW   ? unscanned] {doc['url']}  ({doc['scanBlocked']})")
    frontier = [f for f in frontier if f["url"] not in used_frontier]
    frontier_urls = {f["url"] for f in frontier}
    # Good candidates that didn't fit this run's balance are kept, already scored, for later
    # runs: they go in the database with status "backlog" (one place per site).
    chosen_ids = {d["id"] for d in chosen}
    old_ids = {d["id"] for d in backlog}
    leftovers = [d for d in pool if d["id"] not in chosen_ids]
    added_backlog = [dict(d, status="backlog", backlogAt=time.time()) for d in leftovers if d["id"] not in old_ids]
    for d in added_backlog:
        store.upsert_site({k: v for k, v in d.items() if not k.startswith("_")})
    trimmed = trim_backlog(store, leftovers)
    log(f"{len(leftovers) - len(trimmed)} good sites kept in the backlog for later runs")
    run_categories = category_counts([d for d in new_docs])
    log(f"Categories this run: {run_categories}")

    stats = compute_stats(merge_sites(sites, new_docs + added_backlog, trimmed | deduped))
    stats.update(lastRunTags=wanted, lastRunMatch=args.match if wanted else "", lastRun=datetime.now(timezone.utc).isoformat(timespec="seconds"), lastRunNew=len(new_docs),
                 lastRunCategories=run_categories, crawlerVersion=__version__, crawlerBuild=build())
    store.write_stats(stats)
    store.save()
    st.save_seen(seen)
    st.save_frontier(frontier)
    st.append_log({"at": stats["lastRun"], "version": __version__, "build": build(), "fetched": fetched, "new": len(new_docs),
                   "minutes": round((time.time() - started) / 60, 1), "categories": run_categories,
                   "sites": [d["url"] for d in new_docs],
                   **({"tags": wanted, "match": args.match} if wanted else {})})
    log(f"Done: fetched {fetched}, proposed {len(new_docs)} new sites, frontier {len(frontier)}"
        + (f", skipped {off_topic} without the chosen tags" if wanted else ""))
    target = (f"Only sites tagged **{(' AND ' if args.match == 'all' else ' OR ').join(wanted)}** "
              f"({off_topic} others skipped for a later run).\n\n") if wanted else ""
    write_summary(f"## {'Targeted' if wanted else 'Daily'} crawl {version_string()}\n\n" + target +
                  f"Proposed **{len(new_docs)}** new sites (fetched {fetched}).\n\n"
                  + "".join(f"- {c}: {n}\n" for c, n in sorted(run_categories.items())))
    if len(new_docs) < args.min_new:
        log(f"WARNING: only {len(new_docs)} new sites (wanted {args.min_new}). Add more seeds or raise --max-fetch.")
        return 2 if args.strict else 0
    return 0


# ---------------------------------------------------------------- seed

def cmd_backlog(args) -> int:
    """Propose a category-balanced batch straight from the backlog, without crawling."""
    store = open_store(args)
    sites = store.load_sites()
    backlog = load_backlog(store, sites, {s["id"] for s in sites})
    deduped = dedupe_by_domain(store, sites)
    backlog = [d for d in backlog if d["id"] not in deduped]
    db_counts = Counter(c for s in sites if s.get("status") in ("approved", "proposed")
                        for c in topics(s))
    chosen = select_balanced(backlog, args.count, db_counts)
    for d in chosen:
        store.upsert_site({"id": d["id"], "status": "proposed"})
        log(f"  [proposed {d['score']:3d}] {d['url']}  {d['categories']}")
    moved = [dict(d, status="proposed") for d in chosen]
    store.write_stats(compute_stats(merge_sites(sites, moved)))
    store.save()
    st.append_log({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version": __version__,
                   "build": build(), "fromBacklog": True, "new": len(chosen),
                   "categories": category_counts(chosen), "sites": [d["url"] for d in chosen]})
    log(f"Proposed {len(chosen)} sites from the backlog; {len(backlog) - len(chosen)} left")
    return 0


def cmd_submissions(args) -> int:
    """Score pending submissions only (hand-added URLs and visitor suggestions)."""
    store = open_store(args)
    fetcher = Fetcher(delay=args.delay)
    sites = store.load_sites()
    known_ids = {s["id"] for s in sites}
    load_backlog(store, sites, known_ids)  # moves an old data/backlog.json into the database
    dedupe_by_domain(store, sites)
    by_id = {s["id"]: s for s in sites}
    seen = st.load_seen()
    new_docs = process_submissions(store, fetcher, build_corpus(sites), known_ids, by_id, seen)
    store.write_stats(compute_stats(merge_sites(sites, new_docs)))
    store.save()
    st.save_seen(seen)
    write_summary(f"## Scored submissions {version_string()}\n\n{len(new_docs)} new site(s) added.\n")
    log(f"Scored submissions: {len(new_docs)} new site(s)")
    return 0


def cmd_seed(args) -> int:
    store = open_store(args)
    fetcher = Fetcher(delay=args.delay)
    sites = store.load_sites()
    dedupe_by_domain(store, sites)
    known_ids = {s["id"] for s in sites}
    domains = known_domains_of(sites)
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
        if ev.doc["id"] in known_ids or ev.doc["registrableDomain"] in domains:
            continue  # this site or its domain is already in the database
        if ev.result.reject and not args.force:
            log(f"  [reject] {ev.norm}: {ev.result.reject}")
            continue
        status = "approved" if args.approve else "proposed"
        doc = dict(ev.doc, status=status)
        if args.approve:
            doc["approvedBy"] = "seed"
        store.upsert_site(doc)
        known_ids.add(doc["id"])
        domains[doc["registrableDomain"]] = doc
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
        ev = evaluate(s["url"], fetcher, Corpus(), use_archive=False)
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


# ---------------------------------------------------------------- screenshots

def cmd_shots(args) -> int:
    """Screenshot approved sites for the saved-sites card view (see wander/shots.py)."""
    from . import shots

    store = open_store(args)
    sites = store.load_shot_targets()
    saved = store.load_saved_ids()
    stats = shots.run(sites, saved, Path(args.out), limit=args.limit,
                      max_minutes=args.max_minutes, force=args.force, log=log)
    log(f"Screenshots done: {stats}")
    write_summary(f"### Screenshots\n\n{stats['ok']} captured, {stats['blank']} stayed blank "
                  f"(fallback preview used), {stats['error']} failed, {stats['removed']} removed.\n")
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
    c.add_argument("--tags", default="",
                   help="comma-separated category ids: this run only keeps sites with these tags")
    c.add_argument("--match", choices=["any", "all"], default="any",
                   help="with --tags: any = OR (default), all = AND")
    c.add_argument("--max-unscanned", type=int, default=5,
                   help="sites behind a bot check (no archived copy) to list for review per run")
    c.add_argument("--pool-factor", type=int, default=4,
                   help="evaluate up to max-new x this many good candidates, then pick a balanced set")
    c.add_argument("--strict", action="store_true", help="exit non-zero if min-new not met")
    c.add_argument("--offline", action="store_true", help="only seeds + frontier, no external APIs")

    s = sub.add_parser("seed", help="import a list of URLs")
    s.add_argument("file", nargs="?")
    s.add_argument("--approve", action="store_true", help="mark seeds approved immediately")
    s.add_argument("--force", action="store_true", help="add even if auto-rejected")

    sub.add_parser("recheck", help="re-fetch live sites and reject spam/insecure ones")

    sh = sub.add_parser("shots", help="screenshot approved sites for the saved-sites card view")
    sh.add_argument("--out", required=True, help="folder holding the screenshots + manifest.json")
    sh.add_argument("--limit", type=int, default=120)
    sh.add_argument("--max-minutes", type=float, default=40)
    sh.add_argument("--force", action="store_true", help="recapture sites that already have one")

    b = sub.add_parser("backlog", help="propose a balanced batch from the backlog, no crawling")
    b.add_argument("--count", type=int, default=10)

    su = sub.add_parser("submissions", help="score pending submissions now (no discovery)")

    sc = sub.add_parser("score", help="explain the score for URLs")
    sc.add_argument("urls", nargs="+")

    for sp in (c, s, sc, sh, b, su, sub.choices["recheck"]):
        sp.add_argument("--local", help="use a local JSON file instead of Firestore")
        sp.add_argument("--delay", type=float, default=0.5)

    args = p.parse_args(argv)
    log(f"WebWander crawler {version_string()}")
    return {"crawl": cmd_crawl, "seed": cmd_seed, "score": cmd_score, "recheck": cmd_recheck,
            "shots": cmd_shots, "backlog": cmd_backlog, "submissions": cmd_submissions}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
