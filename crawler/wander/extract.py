"""Turn raw HTML into a flat dictionary of features. Pure function, no network."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .urls import domain_of, normalize, registrable_domain

AD_NETWORKS = [
    "googlesyndication", "doubleclick.net", "adsbygoogle", "amazon-adsystem", "taboola",
    "outbrain", "criteo", "adnxs", "media.net", "pubmatic", "rubiconproject", "adthrive",
    "mediavine", "ezoic", "propellerads", "popads", "carbonads",
]
TRACKERS = [
    "google-analytics.com", "googletagmanager", "gtag(", "fbq(", "connect.facebook.net",
    "hotjar", "segment.com/analytics", "mixpanel", "hs-scripts", "hubspot", "intercom",
    "clarity.ms", "fullstory", "heap-", "amplitude", "tiktok.com/i18n/pixel", "linkedin.com/px",
]
COOKIE_BANNERS = ["onetrust", "cookiebot", "cookieconsent", "cookie-consent", "cookielaw",
                  "quantcast", "trustarc", "didomi", "usercentrics", "we use cookies"]
COMMERCE_PHRASES = [
    "add to cart", "add to bag", "checkout", "free trial", "start your free", "pricing",
    "buy now", "shop now", "limited time", "% off", "per month", "/mo", "book a demo",
    "request a demo", "get started for free", "enterprise", "subscribe now", "sign up free",
    "our customers", "trusted by", "case studies", "affiliate", "sponsored", "coupon",
]
PARKED_PHRASES = [
    "domain is for sale", "buy this domain", "this domain may be for sale", "domain for sale",
    "parked free", "parkingcrew", "sedo.com", "hugedomains", "dan.com", "afternic",
    "domain has expired", "is parked", "godaddy.com/domainsearch",
]
ERROR_PHRASES = ["404 not found", "page not found", "403 forbidden", "access denied",
                 "site can't be reached", "account suspended", "coming soon", "under maintenance",
                 "just a moment...", "attention required! | cloudflare", "enable javascript and cookies"]
LOGIN_WALL_PHRASES = ["log in to continue", "sign in to continue", "login required",
                      "you must be logged in", "create an account to"]
# Explicit-content detection. Nothing is filtered: these only decide the "Mature" tag.
# Strong terms are unambiguous; weak ones ("naked", "explicit") also appear in innocent
# pages, so they need company. Hits in the title/description count double.
EXPLICIT_STRONG = ["porn", "porno", "pornography", "pornographic", "xxx", "nsfw", "hentai", "onlyfans",
                   "sex cam", "sex cams", "camgirl", "camgirls", "bdsm", "fetish", "erotica", "escort",
                   "escorts", "milf", "you must be 18", "are you over 18", "18 years of age or older",
                   "18 years or older", "adults only", "adult content", "sexually explicit"]
EXPLICIT_WEAK = ["nude", "nudes", "nudity", "naked", "erotic", "explicit", "sexy", "hardcore", "18+",
                 "uncensored", "lingerie", "sex"]
SPAM_WORDS = ["casino", "betting", "gambling", "slots", "viagra", "cialis", "cbd oil", "payday loan",
              "crypto presale", "airdrop", "forex signals", "binary options", "essay writing service",
              "buy followers", "seo services", "backlinks", "replica watches", "weight loss pills",
              "make money online", "get rich quick", "work from home", "loan approval", "bonus code"]
MINER_RE = re.compile(r"coinhive|coin-hive|cryptonight|crypto-loot|cryptoloot|webminer|jsecoin|coinimp|minero\.cc|webmine\.pro")
OBFUSCATED_JS_RE = re.compile(r"eval\(\s*(unescape|atob|function\(p,a,c,k,e)|document\.write\(\s*unescape")
STOPWORDS = set("""the a an and or of to in is it for on with as at by be this that are was from you your we our
i my me he she they them his her its not but if so do can all will has have had were been more one""".split())
EXPLICIT_LABEL_RE = re.compile(r"rta-5042-1996-1400-1577-rta|<meta[^>]+name=[\"']?rating[\"']?[^>]+content=[\"']?(adult|mature|rta)", re.I)


def _term_hits(text: str, terms: list[str]) -> int:
    return sum(len(re.findall(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text)) for t in terms)


def explicit_score(title: str, description: str, body: str, raw_html: str) -> int:
    head = (title + " " + description).lower()
    body = body[:8000].lower()
    strong = 4 * _term_hits(head, EXPLICIT_STRONG) + 2 * min(5, _term_hits(body, EXPLICIT_STRONG))
    if EXPLICIT_LABEL_RE.search(raw_html):
        strong += 10
    if not strong:
        return 0  # weak words alone ("naked mole rat") are never enough
    return strong + 2 * _term_hits(head, EXPLICIT_WEAK) + min(3, _term_hits(body, EXPLICIT_WEAK))
DELIGHT_WORDS = [
    "fun", "toy", "experiment", "experiments", "playful", "made with", "handmade", "hand-made",
    "weird", "delightful", "a little", "tiny", "silly", "explore", "interactive", "try",
    "click", "wander", "curious", "whimsical", "strange", "beautiful", "procedural", "generative",
    "for fun", "side project", "just for fun", "made this", "i made", "labor of love",
]
PERSONAL_HOSTS = ["neocities.org", "github.io", "tilde.", "sdf.org", "glitch.me", "itch.io",
                  "carrd.co", "nekoweb.org", "bearblog.dev", "omg.lol", "envs.net", "w3.org/People"]

WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _count_any(haystack: str, needles: list[str]) -> int:
    return sum(1 for n in needles if n in haystack)


def frameable(headers: dict, url: str) -> bool:
    """Can this page be shown inside an iframe on an https page?"""
    if not url.startswith("https://"):
        return False
    xfo = headers.get("x-frame-options", "").lower()
    if "deny" in xfo or "sameorigin" in xfo:
        return False
    csp = headers.get("content-security-policy", "").lower()
    m = re.search(r"frame-ancestors([^;]*)", csp)
    if m:
        sources = m.group(1).split()
        if "*" not in sources and not any("github.io" in s for s in sources):
            return False
    return True


def spam_score(words: list[str], spam_term_hits: int, n_external_links: int) -> int:
    """Spam terms + keyword stuffing + link farms. 2 or more means spam."""
    score = spam_term_hits
    content = [w for w in words if w not in STOPWORDS and len(w) > 2]
    if len(content) >= 150:
        top = max(content.count(w) for w in set(content[:3000]))
        if top / len(content) > 0.06:
            score += 2  # one word is over 6% of the page: keyword stuffing
    if n_external_links > 150 or (n_external_links > 60 and len(words) < 300):
        score += 2  # page is mostly outbound links: link farm
    return score


def mixed_content(soup, page_url: str) -> int:
    """Insecure http:// subresources on an https page."""
    if not page_url.startswith("https://"):
        return 0
    n = 0
    for tag in soup.find_all(["script", "img", "iframe", "audio", "video", "source", "embed"], src=True):
        n += tag["src"].strip().lower().startswith("http://")
    for tag in soup.find_all("link", href=True):
        if "stylesheet" in " ".join(tag.get("rel", [])).lower():
            n += tag["href"].strip().lower().startswith("http://")
    return n


def extract(url: str, html: str, headers: dict | None = None, scripts: list[str] | None = None,
            tls: dict | None = None) -> dict:
    headers = headers or {}
    tls = tls or {}
    scripts = scripts or []
    soup = BeautifulSoup(html, "lxml")
    raw_lower = html.lower()

    inline_js = "\n".join(s.get_text() for s in soup.find_all("script") if not s.get("src"))
    script_srcs = [s["src"] for s in soup.find_all("script", src=True)]
    all_js = (inline_js + "\n" + "\n".join(scripts)).lower()
    code_lower = raw_lower + "\n" + all_js

    title = _text(soup.title)[:200]
    og = soup.find("meta", property="og:title")
    og_title = og.get("content", "") if og else ""
    desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or soup.find("meta", property="og:description")
    description = (desc_tag.get("content", "") if desc_tag else "").strip()[:400]
    icon = any("icon" in " ".join(link.get("rel", [])).lower() for link in soup.find_all("link"))
    og_image = soup.find("meta", property="og:image")

    headings = " ".join(_text(h) for h in soup.find_all(["h1", "h2", "h3"])[:30])[:2000]

    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    body_text = _text(soup.body or soup)[:60000]
    words = WORD_RE.findall(body_text.lower())

    # Re-parse for element counts (we removed svg/script above).
    full = BeautifulSoup(html, "lxml")
    count = lambda name: len(full.find_all(name))  # noqa: E731

    page_domain = domain_of(url)
    own = registrable_domain(url)
    external_links, internal_links = set(), 0
    for a in full.find_all("a", href=True):
        href = urljoin(url, a["href"])
        if not href.startswith("http"):
            continue
        n = normalize(href)
        if not n:
            continue
        if registrable_domain(n) == own:
            internal_links += 1
        else:
            external_links.add(n)

    pointer_events = len(re.findall(r"mousemove|pointermove|touchstart|pointerdown|mousedown|ondrag|draggable", code_lower))
    keyboard_events = len(re.findall(r"keydown|keyup|keypress|arrowleft|arrowright|keycode", code_lower))

    features = {
        "url": url,
        "domain": page_domain,
        "registrable_domain": own,
        "status": None,
        "title": title or og_title,
        "description": description,
        "headings": headings,
        "text": body_text,
        "word_count": len(words),
        "unique_word_ratio": (len(set(words)) / len(words)) if words else 0.0,
        "html_bytes": len(html),
        "lang": (full.html.get("lang", "") if full.html else "")[:10].lower(),
        "has_icon": icon,
        "image": og_image.get("content", "") if og_image else "",
        # element counts
        "n_canvas": count("canvas") + (1 if "getcontext(" in all_js else 0),
        "n_svg": count("svg"),
        "n_img": count("img"),
        "n_video": count("video"),
        "n_audio": count("audio"),
        "n_iframe": count("iframe"),
        "n_forms": count("form"),
        "n_inputs": count("input") + count("textarea") + count("select") + count("button"),
        "n_tables": count("table"),
        "n_code_blocks": len(full.find_all("pre")) + len([c for c in full.find_all("code") if len(c.get_text()) > 40]),
        "n_scripts_external": len(script_srcs),
        "n_scripts_third_party": sum(1 for s in script_srcs if s.startswith("http") and registrable_domain(s) != own),
        "n_internal_links": internal_links,
        "n_external_links": len(external_links),
        "external_links": sorted(external_links)[:200],
        # structural signals
        "webgl": bool(re.search(r"webgl|three(\.min)?\.js|threejs|pixi|babylon|regl|getcontext\(['\"]webgl", code_lower)),
        "web_audio": bool(re.search(r"audiocontext|webkitaudiocontext|tone(\.min)?\.js|howler|createoscillator", code_lower)),
        "pointer_events": pointer_events,
        "keyboard_events": keyboard_events,
        "map_library": bool(re.search(r"leaflet|mapbox|openlayers|maps\.googleapis|maplibre|cesium", code_lower)),
        "chart_library": bool(re.search(r"\bd3(\.v\d)?(\.min)?\.js|d3js|chart\.js|plotly|vega|highcharts|echarts|observablehq", code_lower)),
        "code_highlighter": bool(re.search(r"prism(\.min)?\.(js|css)|highlight(\.min)?\.js|hljs|shiki|pygments|language-[a-z]+", code_lower)),
        "code_editor": bool(re.search(r"codemirror|monaco-editor|ace\.js|ace-builds|class=\"[^\"]*\brepl\b", code_lower)),
        "marquee_or_blink": bool(full.find(["marquee", "blink"])) or "bgcolor=" in raw_lower,
        "personal_host": any(h in page_domain for h in PERSONAL_HOSTS) or "/~" in url,
        # commercial / quality signals
        "ad_networks": _count_any(code_lower, AD_NETWORKS),
        "trackers": _count_any(code_lower, TRACKERS),
        "cookie_banner": _count_any(code_lower, COOKIE_BANNERS) > 0,
        "commerce_hits": _count_any((body_text + " " + title).lower(), COMMERCE_PHRASES),
        "parked": _count_any(raw_lower, PARKED_PHRASES) > 0,
        "error_page": _count_any((title + " " + body_text[:600]).lower(), ERROR_PHRASES) > 0,
        "login_wall": _count_any(body_text[:3000].lower(), LOGIN_WALL_PHRASES) > 0,
        "explicit_score": explicit_score(title or og_title, description, body_text, html[:20000]),
        "spam_hits": (spam_hits := _count_any((title + " " + description + " " + body_text[:5000]).lower(), SPAM_WORDS)),
        "spam_score": spam_score(words, spam_hits, len(external_links)),
        # security
        "hsts": "strict-transport-security" in headers,
        "csp": "content-security-policy" in headers,
        "mixed_content": mixed_content(full, url),
        "password_inputs": len(full.find_all("input", attrs={"type": re.compile("^password$", re.I)})),
        "crypto_miner": bool(MINER_RE.search(code_lower)),
        "obfuscated_js": bool(OBFUSCATED_JS_RE.search(all_js)),
        "tls_version": tls.get("version", ""),
        "cert_days_left": tls.get("days_left"),
        "delight_hits": _count_any((title + " " + description + " " + headings + " " + body_text[:4000]).lower(), DELIGHT_WORDS),
        "lorem": "lorem ipsum" in body_text.lower(),
        "frameable": frameable(headers, url),
        "https": url.startswith("https://"),
        "path_depth": len([p for p in urlsplit(url).path.split("/") if p]),
    }
    return features
