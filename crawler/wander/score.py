"""Uniqueness / fun scoring. Deterministic heuristics only.

The score (0-100) is the sum of five parts:

  interactivity  0-30  canvas, WebGL, WebAudio, pointer/keyboard handlers, inputs
  independence   0-20  no ads, few trackers, no cookie walls, personal hosting
  craft          0-15  has a title/description/icon, https, playful wording, long-form writing
  novelty        0-35  text fingerprint far from everything in the DB, new domain
  security       -21-5 HSTS/CSP headers, mixed content, password forms, obfuscated JS
  penalties      <=0   commercial language, empty pages, deep article paths

Only a few kinds of page are rejected outright: social media and big platforms, pages
without content (parked domains, error pages, login walls, empty pages), spam, and insecure
pages (no https / invalid or expired certificates / outdated TLS, crypto-miners). Nothing is
rejected for its subject, opinions or language: provocative, political, philosophical and
non-English pages are all welcome, and explicit ones get the Mature tag.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .simhash import similarity

BLOCKED_DOMAINS = {
    "google.com", "youtube.com", "youtu.be", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "reddit.com", "linkedin.com", "pinterest.com", "amazon.com", "ebay.com",
    "wikipedia.org", "wikimedia.org", "medium.com", "substack.com", "github.com", "gitlab.com",
    "apple.com", "microsoft.com", "news.ycombinator.com", "ycombinator.com", "lobste.rs",
    "nytimes.com", "theguardian.com", "bbc.co.uk", "bbc.com", "cnn.com", "washingtonpost.com",
    "forbes.com", "bloomberg.com", "reuters.com", "wsj.com", "theverge.com", "techcrunch.com",
    "wired.com", "arstechnica.com", "npmjs.com", "pypi.org", "stackoverflow.com", "discord.com",
    "discord.gg", "t.me", "telegram.org", "patreon.com", "ko-fi.com", "buymeacoffee.com",
    "producthunt.com", "apps.apple.com", "play.google.com", "chrome.google.com", "docs.google.com",
    "drive.google.com", "dropbox.com", "notion.so", "notion.site", "archive.org", "web.archive.org",
    "imgur.com", "giphy.com", "spotify.com", "soundcloud.com", "twitch.tv", "vimeo.com",
    "bit.ly", "goo.gl", "tinyurl.com", "mastodon.social", "bsky.app", "threads.net", "wiby.me",
}

MIN_SCORE = 45  # default threshold for automatically proposing a site


@dataclass
class Corpus:
    """What's already known, so novelty can be judged against it."""
    simhashes: list[int] = field(default_factory=list)
    domain_counts: Counter = field(default_factory=Counter)

    def add(self, simhash: int, domain: str) -> None:
        if simhash:
            self.simhashes.append(simhash)
        self.domain_counts[domain] += 1


@dataclass
class Result:
    score: int
    breakdown: dict
    reasons: list[str]
    reject: str | None = None
    nearest_similarity: float = 0.0


def reject_reason(f: dict) -> str | None:
    dom = f["registrable_domain"]
    if dom in BLOCKED_DOMAINS or f["domain"] in BLOCKED_DOMAINS:
        return "big platform"
    if f["parked"]:
        return "parked domain"
    if f["error_page"]:
        return "error page"
    if f["login_wall"]:
        return "login wall"
    if f["word_count"] < 3 and f["n_canvas"] == 0 and f["n_scripts_external"] == 0:
        return "empty page"
    if f["spam_score"] >= 2:
        return "spam"
    if not f["https"]:
        return "insecure: not https"
    if f["crypto_miner"]:
        return "insecure: crypto-miner script"
    if f["tls_version"] in ("SSLv3", "TLSv1", "TLSv1.1"):
        return f"insecure: outdated {f['tls_version']}"
    if f["cert_days_left"] is not None and f["cert_days_left"] < 0:
        return "insecure: expired certificate"
    return None


def score(f: dict, simhash: int, corpus: Corpus, categories: list[str] | None = None) -> Result:
    reasons: list[str] = []
    rej = reject_reason(f)

    # --- interactivity
    inter = 0
    if f["n_canvas"]:
        inter += 8; reasons.append("uses canvas")
    if f["webgl"]:
        inter += 7; reasons.append("WebGL / 3D")
    if f["web_audio"]:
        inter += 6; reasons.append("makes sound")
    if f["pointer_events"] >= 2:
        inter += 5; reasons.append("reacts to mouse/touch")
    if f["keyboard_events"] >= 2:
        inter += 4; reasons.append("keyboard controls")
    if f["n_inputs"] >= 3:
        inter += 3
    if f["n_svg"] >= 8:
        inter += 2
    if f["map_library"] or f["chart_library"]:
        inter += 3; reasons.append("maps/charts")
    inter = min(30, inter)

    # --- independence
    indep = 18
    if f["ad_networks"]:
        indep -= 8 * f["ad_networks"]; reasons.append(f"{f['ad_networks']} ad network(s)")
    if f["trackers"] > 1:
        indep -= 2 * (f["trackers"] - 1)
    if f["cookie_banner"]:
        indep -= 4; reasons.append("cookie banner")
    if f["n_scripts_third_party"] > 8:
        indep -= 3
    if f["personal_host"]:
        indep += 4; reasons.append("personal/indie host")
    if f["html_bytes"] < 150_000 and not f["n_scripts_third_party"] and not f["trackers"] and not f["ad_networks"]:
        indep += 3; reasons.append("lightweight small-web page")
    indep = max(0, min(20, indep))

    # --- craft
    craft = 0
    craft += 2 if f["title"] else 0
    craft += 2 if f["description"] else 0
    craft += 1 if f["has_icon"] else 0
    craft += 2 if f["https"] else 0
    craft += min(6, 2 * f["delight_hits"])
    if f["delight_hits"] >= 2:
        reasons.append("playful wording")
    craft += 2 if 0.35 <= f["unique_word_ratio"] <= 0.9 and f["word_count"] > 30 else 0
    if f["word_count"] >= 800 and not f["commerce_hits"] and not f["spam_hits"]:
        craft += 3; reasons.append("long-form writing")
    if f["lorem"]:
        craft -= 10
    craft = max(0, min(15, craft))

    # --- novelty
    nearest = max((similarity(simhash, h) for h in corpus.simhashes), default=0.5) if simhash else 0.5
    text_novelty = round(25 * max(0.0, min(1.0, (0.88 - nearest) / 0.3)))
    if nearest >= 0.9:
        reasons.append("near-duplicate of a known site")
    same_domain = corpus.domain_counts.get(f["registrable_domain"], 0)
    domain_novelty = {0: 10, 1: 5, 2: 2}.get(same_domain, -10)
    if same_domain == 0:
        reasons.append("new domain")
    novelty = text_novelty + domain_novelty

    # --- security (on top of the hard rejects for no https / bad certs / miners)
    sec = 0
    if f["hsts"]:
        sec += 2
    if f["csp"]:
        sec += 1
    if f["mixed_content"]:
        sec -= 5; reasons.append(f"mixed content x{f['mixed_content']}")
    else:
        sec += 2
    if f["password_inputs"]:
        sec -= 5; reasons.append("asks for a password")
    if f["obfuscated_js"]:
        sec -= 8; reasons.append("obfuscated script")
    if f["cert_days_left"] is not None and f["cert_days_left"] < 7:
        sec -= 3; reasons.append("certificate about to expire")

    # --- penalties
    pen = 0
    if f["commerce_hits"]:
        pen -= min(30, 5 * f["commerce_hits"]); reasons.append(f"commercial language x{f['commerce_hits']}")
    if f["word_count"] < 20 and not (f["n_canvas"] or f["webgl"]):
        pen -= 15; reasons.append("very little content")
    if f["spam_hits"]:
        pen -= 10; reasons.append("a spam/gambling term")
    # Deep paths are normal for obscure personal projects (/~ann/toys/thing), so only
    # very deep ones (usually CMS article URLs) cost anything. Long pages are not
    # penalised: long-form explainers and essays are exactly what we want.
    if f["path_depth"] > 6:
        pen -= f["path_depth"] - 6

    if categories is not None and not [c for c in categories if c != "mature"]:
        pen -= 5; reasons.append("no clear theme")

    total = max(0, min(100, inter + indep + craft + novelty + sec + pen))
    breakdown = {"interactivity": inter, "independence": indep, "craft": craft,
                 "novelty": novelty, "security": sec, "penalties": pen}
    return Result(int(total), breakdown, reasons, rej, round(nearest, 3))
