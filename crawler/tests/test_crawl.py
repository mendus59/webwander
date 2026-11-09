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
