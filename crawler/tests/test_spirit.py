"""The site's spirit: discovering the creative, educational, fun, obscure, artistic and weird.

Each fixture below stands for one kind of site WebWander exists to surface. They must all
clear the auto-proposal threshold, even after the database already holds the others,
and a typical commercial/listicle page must not. If a scoring change breaks this file,
the change is working against the point of the site.
"""

from pathlib import Path

import pytest

from wander.extract import extract
from wander.score import MIN_SCORE, Corpus, score
from wander.simhash import simhash
from wander.tagger import tag

FIX = Path(__file__).parent / "fixtures"

ESSAY = " ".join(
    f"Paragraph {i}: when wind blows across water it pushes tiny ripples, which grow into waves "
    f"that carry energy for {i * 7} kilometres; near shore the seabed slows the bottom of wave {i}, "
    f"the crest outruns it and topples. Try the slider to change depth {i} and watch what happens."
    for i in range(1, 45)
)

SPIRIT = {
    "fun/interactive toy": ("toy.html", "https://ann.neocities.org/toys/blobs/"),
    "music": ("synth.html", "https://synth.example.net/"),
    "educational long-form": ("essay.html", "https://ciechan.example.org/~ann/notes/physics/waves/2024/"),
    "artistic/obscure/weird": ("netart.html", "https://doors.example.art/"),
    "retro/personal": ("retro.html", "https://starla.neocities.org/"),
    "programming": ("code.html", "https://lisp.example.dev/"),
}


def evaluate(name, url, corpus):
    html = (FIX / name).read_text().replace("ESSAY_BODY", ESSAY)
    f = extract(url, html, {"strict-transport-security": "max-age=1"})
    fp = simhash(" ".join([f["title"], f["description"], f["headings"], f["text"]]))
    cats, _ = tag(f)
    return f, fp, cats, score(f, fp, corpus, cats)


def full_corpus(exclude):
    corpus = Corpus()
    for kind, (name, url) in SPIRIT.items():
        if kind != exclude:
            f, fp, _, _ = evaluate(name, url, Corpus())
            corpus.add(fp, f["registrable_domain"])
    return corpus


@pytest.mark.parametrize("kind", SPIRIT)
def test_spirit_sites_are_proposed(kind):
    name, url = SPIRIT[kind]
    _, _, cats, res = evaluate(name, url, full_corpus(kind))
    assert res.reject is None, (kind, res.reject)
    assert res.score >= MIN_SCORE, (kind, res.score, res.breakdown, res.reasons)
    assert cats, kind


def test_long_essays_and_deep_paths_not_penalised():
    _, _, _, res = evaluate("essay.html", SPIRIT["educational long-form"][1], Corpus())
    assert res.breakdown["penalties"] == 0
    assert "long-form writing" in res.reasons


def test_commercial_listicle_is_not_proposed():
    _, _, _, res = evaluate("news.html", "https://best-apps.example.com/2026/11/10/top-10-productivity/", Corpus())
    assert res.score < MIN_SCORE, (res.score, res.breakdown)


def test_spirit_beats_commercial_by_a_wide_margin():
    worst_spirit = min(evaluate(n, u, Corpus())[3].score for n, u in SPIRIT.values())
    commercial = evaluate("news.html", "https://best-apps.example.com/", Corpus())[3].score
    assert worst_spirit - commercial >= 30
