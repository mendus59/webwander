"""Storage backends: Firestore (production) and a local JSON file (dry runs / tests).

Firestore layout (see firebase/firestore.rules):
  sites/{id}         every scored site; status = proposed | approved | rejected
  submissions/{id}   URLs added by users or the admin, waiting for the crawler to score
  meta/stats         counters shown on the site
Crawl bookkeeping that doesn't need to be in Firestore (seen URLs, crawl frontier)
lives in data/ in the repo, which keeps Firestore reads low.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

from . import DATA_DIR


class LocalStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.data.setdefault("sites", {})
        self.data.setdefault("submissions", {})
        self.data.setdefault("meta", {})

    def load_sites(self) -> list[dict]:
        return [dict(v, id=k) for k, v in self.data["sites"].items()]

    def upsert_site(self, doc: dict) -> None:
        doc = dict(doc)
        doc_id = doc.pop("id")
        existing = self.data["sites"].setdefault(doc_id, {})
        if not existing:
            existing.update(discoveredAt=time.time(), rand=random.random())
        existing.update(doc)

    def load_submissions(self) -> list[dict]:
        return [dict(v, id=k) for k, v in self.data["submissions"].items()]

    def load_shot_targets(self) -> list[dict]:
        return [s for s in self.load_sites() if s.get("status") == "approved"]

    def load_saved_ids(self) -> set[str]:
        return set(self.data.get("saved", []))

    def delete_submission(self, sub_id: str) -> None:
        self.data["submissions"].pop(sub_id, None)

    def write_stats(self, stats: dict) -> None:
        self.data["meta"]["stats"] = stats

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=1, default=str))


class FirestoreStore:
    def __init__(self):
        import firebase_admin
        from firebase_admin import credentials, firestore

        raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if raw:
            cred = credentials.Certificate(json.loads(raw))
        else:  # GOOGLE_APPLICATION_CREDENTIALS path
            cred = credentials.ApplicationDefault()
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        self.fs = firestore
        self.db = firestore.client()

    def load_sites(self) -> list[dict]:
        fields = ["url", "domain", "registrableDomain", "simhash", "status", "categories"]
        return [dict(d.to_dict(), id=d.id) for d in self.db.collection("sites").select(fields).stream()]

    def upsert_site(self, doc: dict) -> None:
        doc = dict(doc)
        doc_id = doc.pop("id")
        ref = self.db.collection("sites").document(doc_id)
        if not ref.get().exists:
            doc["discoveredAt"] = self.fs.SERVER_TIMESTAMP
            doc["rand"] = random.random()
        ref.set(doc, merge=True)

    def load_submissions(self) -> list[dict]:
        return [dict(d.to_dict(), id=d.id) for d in self.db.collection("submissions").stream()]

    def load_shot_targets(self) -> list[dict]:
        fields = ["url", "status", "categories", "approvedAt"]
        q = self.db.collection("sites").where("status", "==", "approved").select(fields)
        return [dict(d.to_dict(), id=d.id) for d in q.stream()]

    def load_saved_ids(self) -> set[str]:
        """Ids of sites anyone has saved (users/{uid}/saved/{siteId})."""
        return {d.id for d in self.db.collection_group("saved").select([]).stream()}

    def delete_submission(self, sub_id: str) -> None:
        self.db.collection("submissions").document(sub_id).delete()

    def write_stats(self, stats: dict) -> None:
        stats = dict(stats, updatedAt=self.fs.SERVER_TIMESTAMP)
        self.db.collection("meta").document("stats").set(stats, merge=True)

    def save(self) -> None:
        pass


# ---- repo-file bookkeeping -------------------------------------------------

SEEN_PATH = DATA_DIR / "seen-urls.txt"
FRONTIER_PATH = DATA_DIR / "frontier.json"
LOG_PATH = DATA_DIR / "crawl-log.json"
BACKLOG_PATH = DATA_DIR / "backlog.json"
BACKLOG_MAX = 500
BACKLOG_DAYS = 30
FRONTIER_MAX = 3000


def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    return {line.strip() for line in SEEN_PATH.read_text().splitlines() if line.strip()}


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text("\n".join(sorted(seen)) + "\n")


def load_frontier() -> list[dict]:
    if not FRONTIER_PATH.exists():
        return []
    return json.loads(FRONTIER_PATH.read_text())


def save_frontier(items: list[dict]) -> None:
    # Keep the most promising links (from high-scoring parents), drop the rest.
    items = sorted(items, key=lambda x: x.get("parentScore", 0), reverse=True)[:FRONTIER_MAX]
    FRONTIER_PATH.write_text(json.dumps(items, indent=0))


def append_log(entry: dict, keep: int = 60) -> None:
    log = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
    log.append(entry)
    LOG_PATH.write_text(json.dumps(log[-keep:], indent=1))


def load_backlog() -> list[dict]:
    """Good, already-scored sites that didn't fit an earlier run's category balance.
    They are offered first in later runs instead of being re-crawled; entries older
    than BACKLOG_DAYS are dropped (the page may have changed)."""
    if not BACKLOG_PATH.exists():
        return []
    cutoff = time.time() - BACKLOG_DAYS * 86400
    return [d for d in json.loads(BACKLOG_PATH.read_text()) if d.get("_backlogAt", 0) >= cutoff]


def save_backlog(docs: list[dict]) -> None:
    now = time.time()
    docs = [dict(d, _backlogAt=d.get("_backlogAt", now)) for d in docs]
    docs = sorted(docs, key=lambda d: d.get("score", 0), reverse=True)[:BACKLOG_MAX]
    BACKLOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKLOG_PATH.write_text(json.dumps(docs, indent=0))
