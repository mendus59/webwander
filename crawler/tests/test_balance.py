import json
from collections import Counter
from pathlib import Path

import wander.__main__ as cli
import wander.store as st
from wander.balance import category_counts, select_balanced
from wander.fetch import Page

FIX = Path(__file__).parent / "fixtures"


def doc(i, cats, score=60):
    return {"id": f"d{i}", "url": f"https://s{i}.example.org/", "categories": cats, "score": score,
            "registrableDomain": f"s{i}.example.org", "simhash": f"{(i * 0x9E3779B97F4A7C15) & (2**64 - 1):016x}"}


def test_skewed_pool_comes_out_balanced():
    # 30 high-scoring toys vs 3 lower-scoring sites each of music, retro, programming.
    pool = [doc(i, ["toys"], 90) for i in range(30)]
    pool += [doc(100 + i, ["music"], 50) for i in range(3)]
    pool += [doc(200 + i, ["retro"], 50) for i in range(3)]
    pool += [doc(300 + i, ["programming"], 50) for i in range(3)]
    counts = category_counts(select_balanced(pool, 12))
    assert counts == {"toys": 3, "music": 3, "retro": 3, "programming": 3}


def test_score_breaks_ties_within_category():
    pool = [doc(1, ["art"], 50), doc(2, ["art"], 80), doc(3, ["music"], 70)]
    picked = select_balanced(pool, 2)
    assert {d["id"] for d in picked} == {"d2", "d3"}


def test_database_rarity_breaks_ties():
    pool = [doc(1, ["games"], 70), doc(2, ["history"], 70)]
    picked = select_balanced(pool, 1, db_counts=Counter(games=50, history=2))
    assert picked[0]["categories"] == ["history"]


def test_skips_duplicate_domains_and_near_duplicates():
    a = doc(1, ["art"], 90)
    same_domain = dict(doc(2, ["music"], 80), registrableDomain=a["registrableDomain"])
    near_dup = dict(doc(3, ["games"], 70), simhash=a["simhash"])
    picked = select_balanced([a, same_domain, near_dup, doc(4, ["maps"], 40)], 4)
    assert [d["id"] for d in picked] == ["d1", "d4"]


def test_mature_is_not_a_balancing_topic():
    pool = [doc(1, ["art", "mature"], 90), doc(2, ["art"], 80), doc(3, ["music"], 40)]
    assert category_counts(select_balanced(pool, 2)) == {"art": 1, "music": 1}


def test_crawl_run_is_balanced_and_keeps_leftovers(tmp_path, monkeypatch):
    # The network mostly returns toy pages; a few music/retro/programming pages.
    def fetch(self, url, with_scripts=True):
        n = int(url.split("site")[1].split(".")[0])
        name = "synth.html" if n % 10 == 1 else "retro.html" if n % 10 == 2 else "code.html" if n % 10 == 3 else "toy.html"
        html = (FIX / name).read_text().replace("<h1>", f"<h1>page {n} " + " ".join(f"q{n}z{j}" for j in range(120)) + " ", 1)
        return Page(url, 200, {"content-type": "text/html"}, html)

    monkeypatch.setattr(cli.Fetcher, "fetch", fetch)
    monkeypatch.setattr(st, "SEEN_PATH", tmp_path / "seen.txt")
    monkeypatch.setattr(st, "FRONTIER_PATH", tmp_path / "frontier.json")
    monkeypatch.setattr(st, "LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(st, "BACKLOG_PATH", tmp_path / "backlog.json")
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("\n".join(f"https://site{i}.example{i}.org/" for i in range(60)))
    monkeypatch.setattr(cli, "SEEDS_PATH", seeds)
    db = tmp_path / "db.json"
    assert cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0",
                     "--min-new", "10", "--max-new", "12", "--min-score", "0"]) == 0
    new = [dict(s, id=k) for k, s in json.loads(db.read_text())["sites"].items() if s["status"] == "proposed"]
    counts = Counter(c for s in new for c in s["categories"] if c in ("toys", "music", "retro", "programming"))
    assert counts["music"] >= 3 and counts["retro"] >= 3 and counts["programming"] >= 3
    assert counts["toys"] <= 4
    log = json.loads((tmp_path / "log.json").read_text())[-1]
    assert log["categories"]
    # Unpicked good candidates are kept, already scored, in the backlog...
    backlog = json.loads((tmp_path / "backlog.json").read_text())
    assert backlog and all(b["score"] >= 0 and b["categories"] for b in backlog)
    first_ids = {s["id"] for s in new}
    assert not first_ids & {b["id"] for b in backlog}

    # ...and the next run presents them without fetching them again.
    fetched = []
    monkeypatch.setattr(cli.Fetcher, "fetch", lambda self, url, with_scripts=True: fetched.append(url) or
                        Page(url, 404, {}, "", error="http 404"))
    assert cli.main(["crawl", "--offline", "--local", str(db), "--delay", "0",
                     "--min-new", "10", "--max-new", "12", "--min-score", "0"]) == 0
    sites = [dict(s, id=k) for k, s in json.loads(db.read_text())["sites"].items()]
    second = [s for s in sites if s["status"] == "proposed" and s["id"] not in first_ids]
    assert len(second) >= 10
    assert {s["id"] for s in second} <= {b["id"] for b in backlog}
    assert not any(k.startswith("_") for s in second for k in s)
