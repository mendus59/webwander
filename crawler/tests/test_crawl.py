"""End-to-end crawl with a fake network, checking the daily minimum is met."""

import json
from pathlib import Path

import wander.__main__ as cli
import wander.store as st
from wander.fetch import Page

FIX = Path(__file__).parent / "fixtures"
PAGES = ["toy.html", "synth.html", "retro.html", "saas.html", "parked.html"]


def fake_fetch(self, url, with_scripts=True):
    # Deterministically map each URL to one fixture, with a unique title so pages differ.
    name = PAGES[sum(map(ord, url)) % len(PAGES)]
    html = (FIX / name).read_text().replace("<h1>", f"<h1>{url} ", 1)
    return Page(url, 200, {"content-type": "text/html"}, html)


def test_crawl_meets_minimum(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.Fetcher, "fetch", fake_fetch)
    monkeypatch.setattr(st, "SEEN_PATH", tmp_path / "seen.txt")
    monkeypatch.setattr(st, "FRONTIER_PATH", tmp_path / "frontier.json")
    monkeypatch.setattr(st, "LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(st, "BACKLOG_PATH", tmp_path / "backlog.json")
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("\n".join(f"https://site{i}.example{i}.org/" for i in range(40)))
    monkeypatch.setattr(cli, "SEEDS_PATH", seeds)
    db = tmp_path / "db.json"
    db.write_text(json.dumps({"submissions": {"s1": {"url": "https://submitted.example.net", "uid": "u1"}}}))

    rc = cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "10"])
    assert rc == 0
    data = json.loads(db.read_text())
    proposed = [s for s in data["sites"].values() if s["status"] == "proposed"]
    assert len(proposed) >= 10
    assert data["submissions"] == {}
    assert any(s["source"] == "submission" for s in proposed)
    assert data["meta"]["stats"]["lastRunNew"] == len(proposed)
    assert all(s["categories"] is not None and 0 <= s["score"] <= 100 for s in proposed)

    # Second run must not re-propose anything already seen.
    rc = cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "10"])
    data2 = json.loads(db.read_text())
    assert set(data2["sites"]) >= set(data["sites"])
    urls = [s["url"] for s in data2["sites"].values()]
    assert len(urls) == len(set(urls))


def test_recheck_removes_spam_and_insecure(tmp_path, monkeypatch):
    def fetch(self, url, with_scripts=True):
        if "badcert" in url:
            return Page(url, 0, {}, "", error="insecure: invalid certificate")
        if "spammy" in url:
            html = "<html><title>Casino</title><body>casino betting slots payday loan</body></html>"
            return Page(url, 200, {"content-type": "text/html"}, html)
        if "down" in url:
            return Page(url, 0, {}, "", error="ConnectTimeout")
        return Page(url, 200, {"content-type": "text/html"}, (FIX / "toy.html").read_text())

    monkeypatch.setattr(cli.Fetcher, "fetch", fetch)
    db = tmp_path / "db.json"
    sites = {n: {"url": f"https://{n}.example.org/", "status": "approved"} for n in ("good", "badcert", "spammy", "down")}
    sites["oldhttp"] = {"url": "http://down-plain.example.org/", "status": "approved"}
    db.write_text(json.dumps({"sites": sites}))
    assert cli.main(["recheck", "--local", str(db), "--delay", "0"]) == 0
    out = json.loads(db.read_text())["sites"]
    assert out["good"]["status"] == "approved"
    assert out["down"]["status"] == "approved"  # temporarily unreachable is not removed
    assert out["badcert"]["status"] == "rejected" and out["badcert"]["autoReject"] == "insecure: invalid certificate"
    assert out["spammy"]["status"] == "rejected" and out["spammy"]["autoReject"] == "spam"
    assert out["oldhttp"]["status"] == "rejected" and out["oldhttp"]["autoReject"] == "insecure: not https"


def test_failed_and_resubmitted_submissions_stay_visible(tmp_path, monkeypatch):
    """A hand-added site that can't be fetched lands in 'rejected' with the reason, and
    an admin re-adding a rejected site puts it back in the review queue."""
    def fetch(self, url, with_scripts=True):
        if "dead" in url:
            return Page(url, 0, {}, "", error="ConnectTimeout")
        return Page(url, 200, {"content-type": "text/html"}, (FIX / "toy.html").read_text())

    monkeypatch.setattr(cli.Fetcher, "fetch", fetch)
    for name in ("SEEN_PATH", "FRONTIER_PATH", "LOG_PATH", "BACKLOG_PATH"):
        monkeypatch.setattr(st, name, tmp_path / name)
    monkeypatch.setattr(cli, "SEEDS_PATH", tmp_path / "none.txt")
    db = tmp_path / "db.json"
    db.write_text(json.dumps({"submissions": {
        "a": {"url": "https://dead-site.example", "uid": "admin", "autoApprove": True},
        "b": {"url": "https://toy-site.example", "uid": "admin", "autoApprove": False}}}))
    args = ["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "0"]
    assert cli.main(args) == 0
    data = json.loads(db.read_text())
    by_url = {s["url"]: s for s in data["sites"].values()}
    dead = by_url["https://dead-site.example/"]
    assert dead["status"] == "rejected" and dead["autoReject"] == "couldn't fetch: ConnectTimeout"
    assert data["submissions"] == {}

    toy_id = next(k for k, s in data["sites"].items() if "toy" in s["url"])
    data["sites"][toy_id]["status"] = "rejected"
    data["submissions"] = {"c": {"url": "https://toy-site.example", "uid": "admin", "autoApprove": False},
                           "d": {"url": "https://dead-site.example", "uid": "admin", "autoApprove": False}}
    db.write_text(json.dumps(data))
    assert cli.main(args) == 0
    data = json.loads(db.read_text())
    assert data["sites"][toy_id]["status"] == "proposed" and data["sites"][toy_id]["resubmitted"]
    assert sum(1 for s in data["sites"].values() if "dead" in s["url"]) == 1


def test_runs_record_crawler_version(tmp_path, monkeypatch):
    import wander
    monkeypatch.setattr(cli.Fetcher, "fetch", fake_fetch)
    for name in ("SEEN_PATH", "FRONTIER_PATH", "LOG_PATH", "BACKLOG_PATH"):
        monkeypatch.setattr(st, name, tmp_path / name)
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("\n".join(f"https://v{i}.example{i}.org/" for i in range(15)))
    monkeypatch.setattr(cli, "SEEDS_PATH", seeds)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    db = tmp_path / "db.json"
    assert cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "3"]) == 0
    data = json.loads(db.read_text())
    assert data["meta"]["stats"]["crawlerVersion"] == wander.__version__
    assert all(s["crawlerVersion"] == wander.__version__ for s in data["sites"].values())
    assert json.loads(st.LOG_PATH.read_text())[-1]["version"] == wander.__version__
    assert f"v{wander.__version__}" in summary.read_text()


def test_submissions_command_scores_queue_now(tmp_path, monkeypatch):
    """Nothing waits in a queue: the submissions command scores every pending URL into
    Proposed, and a URL that's already in the backlog moves to Proposed instead."""
    monkeypatch.setattr(cli.Fetcher, "fetch", fake_fetch)
    for name in ("SEEN_PATH", "FRONTIER_PATH", "LOG_PATH", "BACKLOG_PATH"):
        monkeypatch.setattr(st, name, tmp_path / name)
    from wander.urls import site_id
    held = "https://held.example.org/"
    db = tmp_path / "db.json"
    db.write_text(json.dumps({
        "sites": {site_id(held): {"url": held, "status": "backlog", "categories": ["toys"], "score": 70,
                                 "backlogAt": __import__("time").time()}},
        "submissions": {"a": {"url": "https://fresh-site.example", "uid": "u1"},
                        "b": {"url": held, "uid": "admin", "autoApprove": False}}}))
    assert cli.main(["submissions", "--local", str(db), "--delay", "0"]) == 0
    data = json.loads(db.read_text())
    assert data["submissions"] == {}
    by_url = {s["url"]: s for s in data["sites"].values()}
    assert by_url["https://fresh-site.example/"]["status"] == "proposed"
    assert by_url[held]["status"] == "proposed"
    assert len(data["sites"]) == 2


def test_one_site_per_domain_everywhere(tmp_path, monkeypatch):
    """A domain is never suggested twice: existing duplicates are cleaned up, other pages
    of a known domain are skipped by the crawl, and submissions don't add a second copy."""
    import time
    monkeypatch.setattr(cli.Fetcher, "fetch", fake_fetch)
    for name in ("SEEN_PATH", "FRONTIER_PATH", "LOG_PATH", "BACKLOG_PATH"):
        monkeypatch.setattr(st, name, tmp_path / name)
    seeds = tmp_path / "seeds.txt"
    pages = [f"https://dup{i}.example{i}.org/page{j}" for i in range(12) for j in range(3)]
    seeds.write_text("\n".join(pages + ["https://www.known.example/other-page"]))
    monkeypatch.setattr(cli, "SEEDS_PATH", seeds)
    now = time.time()
    db = tmp_path / "db.json"
    db.write_text(json.dumps({
        "sites": {
            # Older runs left three docs for one domain; the approved one must survive.
            "a": {"url": "https://known.example/", "registrableDomain": "known.example", "status": "approved", "score": 50},
            "b": {"url": "https://known.example/b", "registrableDomain": "known.example", "status": "proposed", "score": 90},
            "c": {"url": "https://known.example/c", "registrableDomain": "known.example", "status": "backlog",
                  "score": 80, "categories": ["toys"], "backlogAt": now},
            "d": {"url": "https://twice.example/", "registrableDomain": "twice.example", "status": "backlog",
                  "score": 60, "categories": ["toys"], "backlogAt": now},
            "e": {"url": "https://twice.example/x", "registrableDomain": "twice.example", "status": "rejected", "score": 70},
        },
        "submissions": {"s": {"url": "https://known.example/yet-another", "uid": "u1"}}}))
    assert cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "10",
                     "--min-score", "0", "--pool-factor", "10"]) == 0
    sites = json.loads(db.read_text())["sites"]
    domains = [cli.site_domain(s) for s in sites.values()]
    assert len(domains) == len(set(domains)), sorted(domains)
    assert sites["a"]["status"] == "approved" and "b" not in sites and "c" not in sites
    assert "d" in sites and "e" not in sites  # backlog beats rejected
    assert 5 <= sum(1 for d in domains if d.startswith("example")) <= 12  # at most one page per domain

    # A second run (and the admin's backlog batch) still never duplicates a domain.
    seeds.write_text("\n".join(f"https://dup{i}.example{i}.org/more{j}" for i in range(12) for j in range(3)))
    assert cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "5"]) == 0
    assert cli.main(["backlog", "--local", str(db), "--count", "5"]) == 0
    sites = json.loads(db.read_text())["sites"]
    domains = [cli.site_domain(s) for s in sites.values()]
    assert len(domains) == len(set(domains))
