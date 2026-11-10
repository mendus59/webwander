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


def test_programming_tag():
    f, res, tags = run("code.html", "https://lisp.example.dev/")
    assert tags[0] == "programming"
    assert f["n_code_blocks"] >= 3 and f["code_highlighter"] and f["code_editor"]


@pytest.mark.parametrize("name", ["toy.html", "saas.html", "retro.html", "synth.html"])
def test_programming_not_overtagged(name):
    _, _, tags = run(name, "https://x.example.org/")
    assert "programming" not in tags


def test_explicit_site_tagged_mature_not_rejected():
    f, res, tags = run("explicit.html", "https://gallery.example.net/")
    assert res.reject is None
    assert "mature" in tags


def test_innocent_words_not_mature():
    for name in ["molerat.html", "toy.html", "code.html", "retro.html"]:
        _, _, tags = run(name, "https://x.example.org/")
        assert "mature" not in tags, name


def test_mature_keeps_normal_tags():
    from wander.tagger import tag
    f, _, _ = run("synth.html", "https://synth.example.net/")
    f["explicit_score"] = 20
    tags, _ = tag(f)
    assert tags[0] == "music" and tags[-1] == "mature"


def _toy(url="https://x.example.org/", swap=("", ""), headers=None, tls=None):
    html = (FIX / "toy.html").read_text().replace(*swap) if swap[0] else (FIX / "toy.html").read_text()
    f = extract(url, html, headers or {}, tls=tls)
    return f, score(f, simhash(f["text"]), Corpus())


def test_spam_terms_rejected():
    _, res = _toy(swap=("Made with love", "Best online casino, betting slots and payday loan deals"))
    assert res.reject == "spam"


def test_single_spam_term_only_penalised():
    _, res = _toy(swap=("Made with love", "Not a casino"))
    assert res.reject is None and res.breakdown["penalties"] <= -10


def test_keyword_stuffing_is_spam():
    stuffed = " ".join(["cheap shoes best cheap shoes online cheap shoes deals"] * 40)
    f, res = _toy(swap=("Made with love", stuffed))
    assert res.reject == "spam"


def test_link_farm_is_spam():
    links = "".join(f'<a href="https://site{i}.example.com/">s{i}</a>' for i in range(160))
    _, res = _toy(swap=("</body>", links + "</body>"))
    assert res.reject == "spam"


def test_http_page_rejected():
    _, res = _toy(url="http://x.example.org/")
    assert res.reject == "insecure: not https"


def test_crypto_miner_rejected():
    _, res = _toy(swap=("</body>", '<script src="https://coinhive.com/lib/coinhive.min.js"></script></body>'))
    assert res.reject == "insecure: crypto-miner script"


def test_outdated_tls_and_expired_cert_rejected():
    assert _toy(tls={"version": "TLSv1", "days_left": 90})[1].reject == "insecure: outdated TLSv1"
    assert _toy(tls={"version": "TLSv1.3", "days_left": -2})[1].reject == "insecure: expired certificate"


def test_security_signals():
    _, good = _toy(headers={"strict-transport-security": "max-age=1", "content-security-policy": "default-src 'self'"})
    _, mixed = _toy(swap=("</body>", '<img src="http://cdn.example.org/a.png"></body>'))
    _, pw = _toy(swap=("</body>", '<input type="password"></body>'))
    assert good.breakdown["security"] == 5
    assert mixed.breakdown["security"] < 0 and "mixed content x1" in mixed.reasons
    assert "asks for a password" in pw.reasons
    assert good.score > mixed.score
