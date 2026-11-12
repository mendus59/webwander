"""A manual crawl can ask for particular kinds of content: tags combined with OR or AND."""

import json
from pathlib import Path

import pytest

import wander.__main__ as cli
import wander.store as st
from wander.urls import normalize
from test_crawl import FIX, PAGES, fake_fetch
from wander.fetch import Page  # toy -> toys+science, synth -> music+games, retro -> retro+indieweb


def run(tmp_path, monkeypatch, *extra, backlog=None):
    monkeypatch.setattr(cli.Fetcher, "fetch", fake_fetch)
    for name in ("SEEN_PATH", "FRONTIER_PATH", "LOG_PATH", "BACKLOG_PATH"):
        monkeypatch.setattr(st, name, tmp_path / name)
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("\n".join(f"https://t{i}.example{i}.org/" for i in range(60)))
    monkeypatch.setattr(cli, "SEEDS_PATH", seeds)
    db = tmp_path / "db.json"
    if not db.exists():
        db.write_text(json.dumps({"sites": backlog or {}}))
    assert cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "0",
                     "--min-score", "0", "--pool-factor", "10", *extra]) == 0
    return json.loads(db.read_text()), db


def proposed(data):
    return [s for s in data["sites"].values() if s["status"] in ("proposed", "backlog")]


def test_or_keeps_sites_with_any_chosen_tag(tmp_path, monkeypatch):
    data, _ = run(tmp_path, monkeypatch, "--tags", "music,retro", "--match", "any")
    got = proposed(data)
    assert got and all({"music", "retro"} & set(s["categories"]) for s in got)
    assert any("music" in s["categories"] for s in got) and any("retro" in s["categories"] for s in got)
    assert data["meta"]["stats"]["lastRunTags"] == ["music", "retro"]


def test_and_needs_every_chosen_tag(tmp_path, monkeypatch):
    data, _ = run(tmp_path, monkeypatch, "--tags", "music,games", "--match", "all")
    got = proposed(data)
    assert got and all({"music", "games"} <= set(s["categories"]) for s in got)
    other = tmp_path / "other"
    other.mkdir()
    data, _ = run(other, monkeypatch, "--tags", "music,retro", "--match", "all")
    assert proposed(data) == []  # no page is both


def test_skipped_sites_stay_for_a_later_run(tmp_path, monkeypatch):
    old = {"b1": {"url": "https://old-toy.example/", "registrableDomain": "old-toy.example", "status": "backlog",
                  "categories": ["toys"], "score": 70, "backlogAt": 4102444800}}
    data, db = run(tmp_path, monkeypatch, "--tags", "music", backlog=old)
    assert data["sites"]["b1"]["status"] == "backlog"  # other kinds in the backlog are left alone
    seen = (tmp_path / "SEEN_PATH").read_text()
    for i in range(60):
        url = f"https://t{i}.example{i}.org/"
        if PAGES[sum(map(ord, url)) % len(PAGES)] in ("toy.html", "retro.html"):
            assert cli.url_key(normalize(url)) not in seen  # good, but not music: not marked as seen
        elif PAGES[sum(map(ord, url)) % len(PAGES)] == "synth.html":
            assert cli.url_key(normalize(url)) in seen
    data, _ = run(tmp_path, monkeypatch)  # the next normal run picks up the other kinds
    kinds = {c for s in proposed(data) for c in s["categories"]}
    assert {"toys", "retro"} <= kinds
    domains = [s["registrableDomain"] for s in data["sites"].values()]
    assert len(domains) == len(set(domains))


def test_unknown_tag_is_an_error():
    with pytest.raises(SystemExit, match="unknown tag"):
        cli.parse_tags("music, nope")
    assert cli.parse_tags(" Music ;art,music") == ["music", "art"]


def test_targeted_discovery_puts_topic_pages_first(monkeypatch):
    from wander import sources
    calls = []
    monkeypatch.setattr(sources, "hn_topic", lambda q: calls.append(("hn", q)) or [("https://hn-hit.example/", "hn-topic")])
    monkeypatch.setattr(sources, "marginalia", lambda q: [("https://small-web.example/", "marginalia")])
    monkeypatch.setattr(sources, "wiby_search", lambda q: [("https://old-page.example/", "wiby")])
    for name in ("hacker_news", "lobsters", "wiby", "kagi_small_web"):
        monkeypatch.setattr(sources, name, lambda *a, **k: [("https://general.example/", "general")])
    frontier = [{"url": "https://from-music-page.example/", "parentCats": ["music"]},
                {"url": "https://unrelated.example/", "parentCats": ["games"]},
                {"url": "https://my-synth-lab.example/", "parentCats": []}]
    out = sources.gather(Path("/nonexistent"), frontier, use_network=True, extra_queries=["synth"],
                         wanted=["music"], keywords=["synth", "music"])
    urls = [u for u, _ in out]
    assert calls == [("hn", ["synth"])]
    assert set(urls[:3]) >= {"https://hn-hit.example/", "https://small-web.example/"}
    assert urls.index("https://from-music-page.example/") < urls.index("https://my-synth-lab.example/") \
        < urls.index("https://unrelated.example/") < urls.index("https://general.example/")


def test_mature_can_be_searched_for():
    from wander.sources import search_terms_for
    terms = search_terms_for(["mature"], per_category=4)
    assert len(terms) == 4 and "erotica" in terms


def test_frontier_remembers_parent_tags(tmp_path, monkeypatch):
    def fetch(self, url, with_scripts=True):
        html = (FIX / "synth.html").read_text().replace("</body>", '<a href="https://linked-site.example/">x</a></body>')
        return Page(url, 200, {"content-type": "text/html"}, html.replace("<h1>", f"<h1>{url} ", 1))
    monkeypatch.setattr(cli.Fetcher, "fetch", fetch)
    for name in ("SEEN_PATH", "FRONTIER_PATH", "LOG_PATH", "BACKLOG_PATH"):
        monkeypatch.setattr(st, name, tmp_path / name)
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("https://one.example1.org/")
    monkeypatch.setattr(cli, "SEEDS_PATH", seeds)
    db = tmp_path / "db.json"
    db.write_text("{}")
    cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0", "--min-new", "0"])
    front = json.loads((tmp_path / "FRONTIER_PATH").read_text())
    link = next(f for f in front if f["url"].startswith("https://linked-site.example"))
    assert "music" in link["parentCats"]
