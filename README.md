# 🧭 WebWander

Wander the fun, weird and creative corners of the web, one click at a time. A small,
personal take on the old StumbleUpon idea, with no social features: no feeds, no
followers, no comments.

**The spirit:** discovering every part of the Internet, especially the creative, educational,
fun, obscure, artistic and weird, and also the provocative, philosophical, political, edgy,
suggestive and challenging. Every scoring rule serves that, and
[`crawler/tests/test_spirit.py`](crawler/tests/test_spirit.py) guards it: a toy, a synth, a
long explorable essay, an obscure net-art page, a retro homepage, a programming walkthrough,
a philosophy essay, a political manifesto, a contrarian rant and a French-language collection
must all clear the proposal threshold, and an ad-heavy listicle must not.

**Live site:** <https://mendus59.github.io/webwander/> · **Setup:** [SETUP.md](SETUP.md)

## How it works

```
 discovery sources ──► crawler (GitHub Actions, daily) ──► Firestore "proposed"
 (HN, 10 subreddits,         fetch → extract → tag → score        │
  Lobsters, wiby, Kagi                                     admin.html review
  small web, links, seeds)                                       │
                                                       Firestore "approved"
                                                                  │
                                              index.html: Wander ➜ (GitHub Pages)
```

- **Site** (`site/`): plain HTML/CSS/JS on GitHub Pages. Firebase Auth (Google sign-in)
  and Firestore. Anyone can wander. Signing in remembers what you've seen, what you
  liked or disliked (which nudges what you're shown), and your saved sites. Signed-in users
  can suggest a site for review.
- **Admin** (`site/admin.html`): review queue sorted by score, with the score breakdown,
  reasons, auto-detected categories (editable), preview, approve/reject, bulk actions and
  hand-adding URLs. Tabs cover every place a site can be: Proposed, Approved, Rejected (with
  the auto-check reason), Queued (submissions waiting to be scored) and Backlog (good sites
  held back for balance, which you can queue or approve). A hand-added URL that can't be
  fetched shows up under Rejected with the reason instead of disappearing.
- **Crawler** (`crawler/`): Python, run by `.github/workflows/crawl.yml` every day. It
  always proposes at least 10 never-before-suggested sites (`--min-new`). If too few clear
  the score threshold it fills up with the best of the rest and marks them "below threshold".
  **Each run is category-balanced:** it evaluates a pool of up to 4× more good candidates
  than it needs, then picks so every category gets roughly the same number
  (`crawler/wander/balance.py`), preferring categories the database has fewest of. Discovery
  searches are steered toward those rarest categories too. Good sites that didn't fit a run
  are kept, already scored, in `data/backlog.json` and offered first in later runs (no
  re-crawl needed; entries expire after 30 days, max 500).

**Crawler version:** `crawler/wander/__init__.py` holds `__version__` (bump it whenever the
crawler's behaviour changes). Every run prints it with the commit id, shows it in the Actions
run summary and the bot's commit message (`crawl: 2026-11-10 (crawler v1.9.0, 325a51d)`),
saves it in `data/crawl-log.json` and `meta/stats`, and stamps it on each site it scores. The
admin panel shows the last run's version and each site's. `python -m wander --version` prints it.

## Scoring without AI

`crawler/wander/score.py` gives every page a 0-100 score from plain heuristics:

| Part | Points | What it looks at |
|---|---|---|
| Interactivity | 0-30 | `<canvas>`, WebGL, Web Audio, mouse/touch/keyboard handlers, inputs, maps, charts (also inside same-origin JS bundles) |
| Independence | 0-20 | ad networks, trackers, cookie banners, third-party script count, personal hosts (neocities, github.io, tilde) |
| Craft | 0-15 | title, description, icon, https, playful wording, healthy vocabulary |
| Novelty | -10 to 35 | 64-bit SimHash of the page text vs. every site already in the DB, and how many sites share the domain |
| Security | -21 to 5 | HSTS and CSP headers; mixed http content, password forms and obfuscated scripts count against |
| Penalties | ≤ 0 | commercial language ("free trial", "pricing"...), empty pages, deep article paths |

Only five kinds of page are rejected outright: social media and big platforms (YouTube,
Reddit, news sites...), pages without content (parked domains, error pages, login walls,
empty pages), spam (spam terms, keyword stuffing, link farms), and insecure pages. Nothing is
rejected for its subject, opinions or language; non-English pages get the 🌍 Around the World
tag. **Security is required:** only https pages with a valid, unexpired certificate
and TLS 1.2+ are accepted (no http fallback, no redirects down to http), and pages loading
crypto-miners are rejected. A weekly **Recheck sites** workflow re-fetches every live site and
moves any that have become spam or insecure to "rejected". There is no content filter:
obviously explicit pages get the 🔞 **Mature** tag instead (unambiguous terms, age gates or
`rating=adult` labels; ambiguous words like "naked" alone never trigger it). Mature sites are
hidden on the site unless a viewer turns on "Show mature content" in Settings, which is off by
default and asks them to confirm they're 18 or older. Gambling/spam terms lower the score.

**Categories** come from `site/data/categories.json`: each category has keywords (weighted
by where they appear: title > description > headings > body) plus structural signals
(e.g. Web Audio → Music, a map library → Maps, `<marquee>` → Retro). Edit that file to add
or tune categories; the site and crawler both read it.

## Running the crawler locally

```sh
cd crawler
pip install -r requirements.txt
python -m pytest -q                                   # offline tests
python -m wander score https://neal.fun               # explain one site's score
python -m wander crawl --local db.local.json          # full run against a local JSON "database"
FIREBASE_SERVICE_ACCOUNT="$(cat key.json)" python -m wander crawl   # against Firestore
```

## Layout

```
site/                 GitHub Pages site (index.html, admin.html, js/, css/, data/categories.json)
crawler/wander/       fetch.py, extract.py, tagger.py, score.py, simhash.py, sources.py, store.py
crawler/seeds.txt     starter list of hand-picked sites
crawler/tests/        pytest suite with HTML fixtures
firebase/             Firestore security rules and indexes
data/                 crawl bookkeeping committed by the bot (seen URLs, link frontier, backlog, run log)
.github/workflows/    pages.yml, crawl.yml (daily), recheck.yml (weekly), seed.yml (once), test.yml
```
