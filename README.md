# 🧭 WebWander

Wander the fun, weird and creative corners of the web, one click at a time. A small,
personal take on the old StumbleUpon idea, with no social features: no feeds, no
followers, no comments.

**Live site:** <https://mendus59.github.io/webwander/> · **Setup:** [SETUP.md](SETUP.md)

## How it works

```
 discovery sources ──► crawler (GitHub Actions, daily) ──► Firestore "proposed"
 (HN, Reddit, Lobsters,      fetch → extract → tag → score        │
  wiby, links found on                                     admin.html review
  good pages, seeds)                                              │
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
  hand-adding URLs.
- **Crawler** (`crawler/`): Python, run by `.github/workflows/crawl.yml` every day. It
  always proposes at least 10 never-before-suggested sites (`--min-new`). If too few clear
  the score threshold it fills up with the best of the rest and marks them "below threshold".

## Scoring without AI

`crawler/wander/score.py` gives every page a 0-100 score from plain heuristics:

| Part | Points | What it looks at |
|---|---|---|
| Interactivity | 0-30 | `<canvas>`, WebGL, Web Audio, mouse/touch/keyboard handlers, inputs, maps, charts (also inside same-origin JS bundles) |
| Independence | 0-20 | ad networks, trackers, cookie banners, third-party script count, personal hosts (neocities, github.io, tilde) |
| Craft | 0-15 | title, description, icon, https, playful wording, healthy vocabulary |
| Novelty | -10 to 35 | 64-bit SimHash of the page text vs. every site already in the DB, and how many sites share the domain |
| Penalties | ≤ 0 | commercial language ("free trial", "pricing"...), empty pages, deep article paths |

Pages are rejected outright if they're parked domains, error pages, login walls, spam/adult,
non-English, or big platforms (YouTube, Reddit, news sites...).

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
data/                 crawl bookkeeping committed by the bot (seen URLs, link frontier, run log)
.github/workflows/    pages.yml, crawl.yml (daily), seed.yml (once), test.yml
```
