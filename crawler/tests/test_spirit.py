"""The site's spirit: discovering the creative, educational, fun, obscure, artistic and weird.

Each fixture below stands for one kind of site WebWander exists to surface. They must all
clear the auto-proposal threshold, even after the database already holds the others,
and a typical commercial/listicle page must not. If a scoring change breaks this file,
the change is working against the point of the site.
"""

import random
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

# Text-first pages with no interactivity at all: each needs its own vocabulary so
# they don't look like near-duplicates of one another.
LONG = {
    "philosophy.html": "Sisyphus pushes the stone uphill forever and perhaps the pushing itself is the whole point. "
        "Consciousness notices the absurd yet refuses despair while morality becomes a daily craft. Camus wrote "
        "that one must imagine him happy; Nietzsche asked whether we could will every moment again. Stoic "
        "teachers practised attention, Zen monks swept courtyards, and Kant worried about duty. Free will "
        "may be an illusion, a useful story, or the strangest fact about living creatures who wonder why "
        "anything exists rather than nothing at all, and whether meaning grows from habits, friendships, "
        "gardens, unfinished letters and stubborn kindness toward strangers.",
    "manifesto.html": "Libraries lend without watching while the surveillance internet watches without lending anything. "
        "Democracy needs commons where citizens read unobserved, argue freely, borrow strange books and change "
        "their minds. Advertising platforms harvest attention, sell profiles to brokers and nudge elections. "
        "Privacy is not secrecy; it is room to think. Fund librarians, protect encryption, publish budgets, "
        "audit algorithms, break monopolies, pay authors fairly, open archives, teach children to question "
        "propaganda, and treat knowledge as infrastructure like roads, water, schools and parks.",
    "provocative.html": "Comfort is overrated, boredom is sacred, and most dinner parties would improve with one heretical "
        "guest saying forbidden things aloud before dessert. Productivity gurus sell anxiety wrapped in "
        "pastel planners. Nostalgia lies beautifully. Grown adults should climb trees, quit hobbies they "
        "hate, admit envy, laugh during solemn ceremonies, question sacred cows, eat cereal for supper, "
        "write terrible poetry deliberately, argue with their heroes, defend unfashionable bands, and "
        "confess that silence frightens them more than criticism ever could.",
    "french.html": "Cette carte postale montre une plage bretonne vers mille neuf cent douze. Au dos, une tante "
        "écrit que la pluie ne cesse jamais mais que les crêpes restent excellentes. Une autre représente "
        "Lyon sous la neige, une troisième un phare normand, une quatrième des vendangeuses souriantes. "
        "Chaque timbre raconte une époque, chaque écriture penchée garde un secret de famille, chaque "
        "tache de café rappelle une cuisine disparue, un facteur pressé, un dimanche tranquille.",
}

SPIRIT = {
    "fun/interactive toy": ("toy.html", "https://ann.neocities.org/toys/blobs/"),
    "music": ("synth.html", "https://synth.example.net/"),
    "educational long-form": ("essay.html", "https://ciechan.example.org/~ann/notes/physics/waves/2024/"),
    "artistic/obscure/weird": ("netart.html", "https://doors.example.art/"),
    "retro/personal": ("retro.html", "https://starla.neocities.org/"),
    "programming": ("code.html", "https://lisp.example.dev/"),
    # Widened scope: ideas, politics, provocation and the non-English web are all welcome.
    "philosophical": ("philosophy.html", "https://absurd.example.org/essays/on-the-absurd/"),
    "political": ("manifesto.html", "https://library-manifesto.example.net/"),
    "provocative/edgy": ("provocative.html", "https://unpopular.example.zone/"),
    "non-English": ("french.html", "https://cartes.example.fr/"),
}

EXPECTED_TAG = {"philosophical": "philosophy", "political": "politics",
                "provocative/edgy": "provocative", "non-English": "world"}


def evaluate(name, url, corpus):
    html = (FIX / name).read_text().replace("ESSAY_BODY", ESSAY)
    if name in LONG:
        words = LONG[name].replace(";", "").replace(",", "").split()
        rnd = random.Random(name)  # varied but deterministic prose, so it isn't keyword stuffing
        html = html.replace("LONG_BODY", " ".join(" ".join(rnd.sample(words, 12)) + "." for _ in range(100)))
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
    if kind in EXPECTED_TAG:
        assert EXPECTED_TAG[kind] in cats, (kind, cats)


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
