"""Sites behind bot checks (Cloudflare and the like) are never worked around. They're
scored from their Wayback Machine copy, or else listed unscanned for review by hand."""

import json
from pathlib import Path

import wander.__main__ as cli
import wander.store as st
from wander.fetch import Page, detect_bot_wall

FIX = Path(__file__).parent / "fixtures"
CF_CHALLENGE = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
                "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'></script>"
                "Enable JavaScript and cookies to continue</body></html>")


def test_detects_bot_walls_but_not_ordinary_errors():
    assert detect_bot_wall(403, {"server": "cloudflare", "cf-mitigated": "challenge"}, CF_CHALLENGE) == "Cloudflare"
    assert detect_bot_wall(503, {"server": "cloudflare"}, CF_CHALLENGE) == "Cloudflare"
    assert detect_bot_wall(429, {"x-vercel-mitigated": "challenge"}, "") == "Vercel"
    assert detect_bot_wall(403, {"server": "ddos-guard"}, "<title>DDoS-Guard</title>") == "DDoS-Guard"
    assert detect_bot_wall(403, {}, "<div id='px-captcha'></div>") == "PerimeterX"
    assert detect_bot_wall(403, {"server": "nginx"}, "<h1>403 Forbidden</h1>") is None
    assert detect_bot_wall(404, {"server": "cloudflare"}, "<h1>Not found</h1>") is None
    assert detect_bot_wall(200, {"server": "cloudflare"}, (FIX / "toy.html").read_text()) is None


def guarded_fetch(self, url, with_scripts=True):
    if "guarded" in url:
        return Page(url, 403, {"server": "cloudflare", "cf-mitigated": "challenge", "content-type": "text/html",
                               "strict-transport-security": "max-age=1"},
                    CF_CHALLENGE, error="bot protection (Cloudflare)", bot_wall="Cloudflare")
    if "forbidden" in url:
        return Page(url, 403, {"server": "nginx"}, "<h1>403 Forbidden</h1>")
    html = (FIX / "toy.html").read_text().replace("<h1>", f"<h1>{url} ", 1)
    return Page(url, 200, {"content-type": "text/html"}, html)


def fake_archive(self, url, max_age_days=1095):
    if "archived" not in url:
        return None
    html = (FIX / "synth.html").read_text()
    page = Page(url, 200, {}, html)
    page.archived_at = "2026-05-01"
    return page


def setup(tmp_path, monkeypatch, seeds):
    monkeypatch.setattr(cli.Fetcher, "fetch", guarded_fetch)
    monkeypatch.setattr(cli.Fetcher, "archived", fake_archive)
    for name in ("SEEN_PATH", "FRONTIER_PATH", "LOG_PATH", "BACKLOG_PATH"):
        monkeypatch.setattr(st, name, tmp_path / name)
    path = tmp_path / "seeds.txt"
    path.write_text("\n".join(seeds))
    monkeypatch.setattr(cli, "SEEDS_PATH", path)
    return tmp_path / "db.json"


def test_bot_walled_sites_reach_review(tmp_path, monkeypatch):
    seeds = ["https://guarded-archived.example/", "https://forbidden.example/"] + [
        f"https://guarded{i}.example{i}.net/" for i in range(8)]
    db = setup(tmp_path, monkeypatch, seeds)
    db.write_text(json.dumps({"submissions": {"s": {"url": "https://guarded-sub.example.org", "uid": "u1"}}}))
    assert cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "0",
                     "--min-score", "0", "--max-unscanned", "3"]) == 0
    sites = json.loads(db.read_text())["sites"].values()
    by_domain = {s["domain"]: s for s in sites}

    archived = by_domain["guarded-archived.example"]
    assert archived["status"] == "proposed" and archived["scanBlocked"] == "Cloudflare"
    assert archived["scoredFrom"] == "archive" and archived["archivedAt"] == "2026-05-01"
    assert archived["score"] > 0 and archived["categories"] and archived["frameable"] is False

    sub = by_domain["guarded-sub.example.org"]  # a hand-added site isn't rejected for its bot check
    assert sub["status"] == "proposed" and sub["unscanned"] and sub["scanBlocked"] == "Cloudflare"

    unscanned = [s for s in sites if s.get("unscanned") and s["source"] != "submission"]
    assert len(unscanned) == 3 and all(s["status"] == "proposed" for s in unscanned)
    assert "forbidden.example" not in by_domain  # an ordinary 403 is still skipped

    # The ones over this run's limit are tried again next run, and nothing is listed twice.
    assert cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "0",
                     "--min-score", "0", "--max-unscanned", "3"]) == 0
    sites = list(json.loads(db.read_text())["sites"].values())
    assert sum(1 for s in sites if s.get("unscanned") and s["source"] != "submission") == 6
    domains = [s["registrableDomain"] for s in sites]
    assert len(domains) == len(set(domains))


def test_recheck_keeps_bot_walled_sites(tmp_path, monkeypatch):
    db = setup(tmp_path, monkeypatch, [])
    db.write_text(json.dumps({"sites": {"g": {"url": "https://guarded-archived.example/", "status": "approved"}}}))
    assert cli.main(["recheck", "--local", str(db), "--delay", "0"]) == 0
    assert json.loads(db.read_text())["sites"]["g"]["status"] == "approved"


class FakeResp:
    def __init__(self, data=None, text="", ok=True):
        self._data, self.text, self.ok = data, text, ok
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"

    def json(self):
        return self._data


def test_wayback_copy_lookup(monkeypatch):
    from datetime import datetime, timezone
    from wander.fetch import Fetcher
    f = Fetcher(delay=0)
    recent = datetime.now(timezone.utc).strftime("%Y%m%d120000")
    asked = []

    def avail(ts):
        return lambda url, params=None, timeout=None: FakeResp(
            {"archived_snapshots": {"closest": {"available": True, "status": "200", "timestamp": ts}}})
    monkeypatch.setattr(f.session, "get", avail(recent))
    monkeypatch.setattr(f, "_get_limited", lambda url, limit: asked.append(url) or FakeResp(text="<html>hi</html>"))
    page = f.archived("https://guarded.example/")
    assert page.html == "<html>hi</html>" and page.archived_at
    assert asked == [f"https://web.archive.org/web/{recent}id_/https://guarded.example/"]

    monkeypatch.setattr(f.session, "get", avail("20150101000000"))
    assert f.archived("https://guarded.example/") is None  # too old to judge the site by
