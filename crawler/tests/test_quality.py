"""Quality: suggestions should be *places to wander into*, each with something worth
discovering. A forum thread, a news story, an everyday dated post or an SEO page for an
AI app is one item inside a bigger site, or nothing at all, so it must not be proposed.
(The kinds of site the spirit tests protect must still be; see test_spirit.py.)"""

from pathlib import Path

import pytest

from test_spirit import full_corpus
from wander.extract import extract
from wander.score import MIN_SCORE, score
from wander.simhash import simhash
from wander.tagger import tag

Q = Path(__file__).parent / "fixtures" / "quality"
JUNK = {
    "everyday dated post": ("diary-post.html", "https://jo.example.net/2026/11/10/a-rainy-tuesday/"),
    "news article": ("news-article.html", "https://dailyledger.example.com/news/2026/11/10/council-parking/"),
    "forum thread": ("forum-thread.html", "https://oldcams.example.org/forum/viewtopic.php?t=19015"),
    "AI generator SEO page": ("ai-generator.html", "https://couple.example.com/nsfw-ai-image-generator"),
}


def run(name, url):
    f = extract(url, (Q / name).read_text(), {"strict-transport-security": "max-age=1"})
    fp = simhash(" ".join([f["title"], f["description"], f["headings"], f["text"]]))
    cats, _ = tag(f)
    return f, cats, score(f, fp, full_corpus(None), cats)


@pytest.mark.parametrize("kind", JUNK)
def test_low_quality_pages_are_not_proposed(kind):
    _, _, res = run(*JUNK[kind])
    assert res.reject or res.score < MIN_SCORE, (kind, res.score, res.breakdown, res.reasons)


def test_ai_generator_seo_is_spam():
    assert run(*JUNK["AI generator SEO page"])[2].reject == "spam"


def test_personal_site_front_page_beats_its_own_post():
    _, _, home = run("blog-home.html", "https://jo.example.net/")
    _, _, post = run(*JUNK["everyday dated post"])
    assert home.reject is None and home.score >= MIN_SCORE, (home.score, home.breakdown, home.reasons)
    assert "a whole site to explore" in home.reasons
    assert home.score - post.score >= 30


def test_page_kinds():
    f, _, res = run(*JUNK["forum thread"])
    assert f["forum_thread"] and "a forum/discussion thread, not a site" in res.reasons
    f, _, _ = run(*JUNK["news article"])
    assert f["news_article"] and f["dated_post"]
    f, _, _ = run("blog-home.html", "https://jo.example.net/")
    assert f["is_root"] and not (f["forum_thread"] or f["news_article"] or f["dated_post"])
    # A long essay is not a forum thread just because it talks about "discourse".
    essay = "<html><body><p>" + "public discourse matters " * 50 + "</p></body></html>"
    assert not extract("https://ideas.example.org/essays/2024/discourse/", essay, {})["forum_thread"]


def test_passing_mentions_dont_make_a_game_or_toy():
    """Strict categories need a structural signal or a prominent keyword."""
    html = ("<html><head><title>On city life</title></head><body><p>"
            + "We play with ideas, click through news, and draw conclusions about the game of politics. " * 20
            + "</p></body></html>")
    cats, _ = tag(extract("https://essays.example.org/", html, {}))
    assert not {"games", "toys", "generators", "simulation"} & set(cats), cats
