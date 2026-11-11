"""A manual crawl can ask for particular kinds of content: tags combined with OR or AND."""

import json

import pytest

import wander.__main__ as cli
import wander.store as st
from wander.urls import normalize
from test_crawl import PAGES, fake_fetch  # toy -> toys+science, synth -> music+games, retro -> retro+indieweb


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
