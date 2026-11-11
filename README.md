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
  can suggest a site for review. **Saved sites** can be shown as a list or as cards with a
  picture of each page, and filtered by tag and by date added (today, past 7/30/365 days or a
  custom range), newest or oldest first. Mature saves stay hidden unless the mature setting is on.
- **Screenshots** (`crawler/wander/shots.py`, `.github/workflows/screenshots.yml`, daily): a
  headless browser photographs approved sites for the card view, saved sites first. Pages that
  draw their content with JavaScript are given time: it waits for the network, fonts and images,
  then checks the picture and keeps waiting (and nudges lazy content with a scroll) while it is
  still one flat colour. A page that stays blank gets no screenshot; the card shows the page's
  own preview image or a generated card with its icon, name and topics instead, so a card is
  never empty. Mature sites are never captured. Pictures live on the `screenshots` branch (kept
  at one commit) and the Pages deploy copies them into `site/shots/`.
- **Admin** (`site/admin.html`): review queue sorted by score, with the score breakdown,
  reasons, auto-detected categories (editable), preview, approve/reject, bulk actions and
  hand-adding URLs. **Every site has exactly one status, so it shows in exactly one tab:**
  Proposed, Approved, Rejected (with the auto-check reason) or Backlog (good sites held back
  for balance). **Propose a balanced batch** on the Backlog tab moves the next N backlog sites
  (category-balanced, already scored) to Proposed with no crawl (`python -m wander backlog
  --count N` does the same). Sites added by hand or suggested by visitors show at the top of
  Proposed while they're scored; with the Run crawler token set up, the **Score submissions**
  workflow scores them within a minute or two (`python -m wander submissions`), otherwise
  the next daily crawl does. A URL that can't be fetched lands in Rejected with the reason.
- **Run crawler** in the admin bar starts the daily-crawl workflow straight from the panel and
  shows its live status. It needs a one-time GitHub fine-grained token (this repo only,
  Actions: read and write), which is kept in that browser's localStorage and only sent to
  api.github.com. The ↗ button next to it still opens the workflow on GitHub.
  Its **Kind of content** picker targets that one run: leave it unspecified for the usual
  balanced crawl, or pick tags and combine them with **Any (OR)** or **All (AND)**. The run
  then searches for those topics and only proposes or backlogs sites with matching tags;
  other good finds aren't marked as seen, so a later run can still pick them up
  (`python -m wander crawl --tags music,art --match all`). The daily crawl is unchanged.
- **Crawler** (`crawler/`): Python, run by `.github/workflows/crawl.yml` every day. It
  always proposes at least 10 never-before-suggested sites (`--min-new`). If too few clear
  the score threshold it fills up with the best of the rest and marks them "below threshold".
  **Each run is category-balanced:** it evaluates a pool of up to 4× more good candidates
  than it needs, then picks so every category gets roughly the same number
  (`crawler/wander/balance.py`), preferring categories the database has fewest of. Discovery
  searches are steered toward those rarest categories too. Good sites that didn't fit a run
  are kept, already scored, as `status: "backlog"` sites and offered first in later runs (no
  re-crawl needed; entries expire after 30 days, max 500).

**Crawler version:** `crawler/wander/__init__.py` holds `__version__` (bump it whenever the
crawler's behaviour changes). Every run prints it with the commit id, shows it in the Actions
run summary and the bot's commit message (`crawl: 2026-11-10 (crawler v1.13.0, 325a51d)`),
saves it in `data/crawl-log.json` and `meta/stats`, and stamps it on each site it scores. The
admin panel shows the last run's version and each site's. `python -m wander --version` prints it.

**One site per domain, everywhere.** A domain (e.g. `neal.fun`, or `ann.neocities.org` on
shared hosts) is only ever in the database once, in exactly one status. The crawl skips any
page whose domain is already there (and takes only one page per new domain), submissions
and seeds of a known domain don't add a copy, and every run removes leftover duplicates from
older versions, keeping approved > proposed > backlog > rejected, then the best score.

## Scoring without AI

`crawler/wander/score.py` gives every page a 0-100 score from plain heuristics:

| Part | Points | What it looks at |
|---|---|---|
| Interactivity | 0-30 | `<canvas>`, WebGL, Web Audio, mouse/touch/keyboard handlers, inputs, maps, charts (also inside same-origin JS bundles) |
| Media | 0-10 | photo galleries, artwork, films and music: images (incl. lazy-loaded and `<picture>`), `<video>`/`<audio>`, and Vimeo/YouTube/Bandcamp/SoundCloud embeds. Media-rich pages never count as "empty" or "very little content", and image alt text feeds the tagger |
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

**Sites behind bot checks** (Cloudflare, Vercel, DDoS-Guard, Sucuri, Imperva, DataDome,
PerimeterX, AWS WAF, Akamai) are never worked around: the crawler doesn't solve challenges
or disguise itself. Instead it scores the page from its latest Wayback Machine copy (within
3 years), keeping the live https/certificate checks, and marks it "🛡 Cloudflare · archived
copy" in the admin panel. With no archived copy, up to `--max-unscanned` (default 5) such
sites per run go to Proposed with a "?" score and no tags, for review by hand; the rest are
tried again on later runs. A plain 403 or an ordinary error page is still skipped.

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
.github/workflows/    pages.yml, crawl.yml (daily), recheck.yml (weekly), seed.yml (once), test.yml
```
