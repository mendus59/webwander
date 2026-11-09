from pathlib import Path

import pytest

from wander.extract import extract, frameable
from wander.score import Corpus, score
from wander.simhash import similarity, simhash
from wander.tagger import tag
from wander.urls import normalize, registrable_domain

FIX = Path(__file__).parent / "fixtures"


def run(name, url, corpus=None, headers=None):
    f = extract(url, (FIX / name).read_text(), headers or {})
    fp = simhash(" ".join([f["title"], f["description"], f["headings"], f["text"]]))
    return f, score(f, fp, corpus or Corpus()), tag(f)[0]


def test_creative_toy_scores_high():
    f, res, tags = run("toy.html", "https://blobs.example.io/")
    assert res.reject is None
    assert res.score >= 70, res
    assert "toys" in tags


def test_saas_scores_low():
    f, res, _ = run("saas.html", "https://acme.example.com/")
    assert res.score < 35, res
    assert res.breakdown["independence"] < 10


def test_toy_beats_saas():
    _, toy, _ = run("toy.html", "https://blobs.example.io/")
    _, saas, _ = run("saas.html", "https://acme.example.com/")
    assert toy.score - saas.score >= 40


def test_parked_domain_rejected():
    _, res, _ = run("parked.html", "https://coolthing.net/")
    assert res.reject == "parked domain"


def test_big_platform_rejected():
    _, res, _ = run("toy.html", "https://www.youtube.com/watch")
    assert res.reject == "big platform"


def test_retro_tags():
    f, res, tags = run("retro.html", "https://starla.neocities.org/")
    assert "retro" in tags
    assert "indieweb" in tags
    assert f["personal_host"]
    assert "https://melonland.net/" in f["external_links"]


def test_synth_is_music():
    _, res, tags = run("synth.html", "https://synth.example.net/")
    assert tags[0] == "music"
    assert "makes sound" in res.reasons


def test_duplicate_and_domain_reduce_novelty():
    f, first, _ = run("toy.html", "https://blobs.example.io/")
    corpus = Corpus()
    corpus.add(simhash(" ".join([f["title"], f["description"], f["headings"], f["text"]])), "example.io")
    _, again, _ = run("toy.html", "https://blobs.example.io/other", corpus)
    assert again.breakdown["novelty"] < first.breakdown["novelty"] - 20
    assert "near-duplicate of a known site" in again.reasons


def test_simhash_similarity():
    a = simhash("the quick brown fox jumps over the lazy dog near the river bank today")
    b = simhash("the quick brown fox jumps over the lazy dog near the river bank tonight")
    c = simhash("synthesizers make wonderful noises when you twist every knob available")
    assert similarity(a, b) > similarity(a, c)


@pytest.mark.parametrize("raw,expected", [
    ("example.com", "https://example.com/"),
    ("http://www.Example.com/index.html?utm_source=x&b=2", "http://example.com/?b=2"),
    ("https://example.com/path/#section", "https://example.com/path"),
    ("https://example.com/#/app-route", "https://example.com/#/app-route"),
    ("mailto:hi@example.com", None),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_registrable_domain():
    assert registrable_domain("https://foo.github.io/bar") == "foo.github.io"
    assert registrable_domain("https://a.b.example.co.uk/") == "example.co.uk"
    assert registrable_domain("https://sub.neal.fun/") == "neal.fun"


def test_frameable():
    assert frameable({}, "https://x.com/")
    assert not frameable({}, "http://x.com/")
    assert not frameable({"x-frame-options": "DENY"}, "https://x.com/")
    assert not frameable({"content-security-policy": "frame-ancestors 'self'"}, "https://x.com/")
    assert frameable({"content-security-policy": "frame-ancestors *"}, "https://x.com/")
