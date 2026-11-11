"""Polite HTTP fetching: robots.txt, size limits, timeouts."""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import requests

USER_AGENT = "WebWanderBot/1.0 (+https://github.com/mendus59/webwander)"
MAX_PAGE_BYTES = 2_000_000
MAX_SCRIPT_BYTES = 600_000
MAX_SCRIPTS = 4
TIMEOUT = (6, 12)


@dataclass
class Page:
    url: str  # final URL after redirects
    status: int
    headers: dict
    html: str
    scripts: list[str] = field(default_factory=list)  # bodies of a few same-origin scripts
    elapsed: float = 0.0
    error: str | None = None
    tls: dict = field(default_factory=dict)  # {"version": "TLSv1.3", "days_left": 80}
    archived_at: str | None = None  # date of the Wayback Machine copy this page came from
    bot_wall: str | None = None  # "Cloudflare", "DDoS-Guard"...: a bot check stopped us reading the page

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300


# Bot checks / web application firewalls that answer a crawler with a challenge page
# instead of the site: (name, vendor headers, markers unique to the vendor's challenge
# page, generic markers that only count together with the vendor's header).
BOT_WALLS = [
    ("Cloudflare", {"cf-mitigated": "challenge", "server": "cloudflare"},
     ["cf-chl", "challenge-platform", "cf-browser-verification", "cf-error-details"],
     ["just a moment", "attention required", "enable javascript and cookies"]),
    ("Vercel", {"x-vercel-mitigated": "challenge"}, ["vercel security checkpoint"], []),
    ("DDoS-Guard", {"server": "ddos-guard"}, ["ddos-guard"], ["checking your browser"]),
    ("Sucuri", {"x-sucuri-id": "", "server": "sucuri"}, ["sucuri website firewall"], ["access denied"]),
    ("Imperva", {"x-iinfo": ""}, ["_incapsula_resource", "incapsula incident"], []),
    ("DataDome", {"x-datadome": ""}, ["captcha-delivery.com"], ["datadome"]),
    ("PerimeterX", {}, ["px-captcha", "perimeterx"], []),
    ("AWS WAF", {"x-amzn-waf-action": ""}, ["awswaf", "aws-waf-token"], []),
    ("Akamai", {"server": "akamaighost"}, [], ["access denied", "reference #"]),
]
BLOCK_STATUSES = {401, 403, 405, 429, 503}


def _vendor_header(headers: dict, wanted: dict) -> bool:
    return any(k in headers and v in headers[k].lower() for k, v in wanted.items())


def detect_bot_wall(status: int, headers: dict, html: str) -> str | None:
    """Name of the bot check that answered instead of the site, or None.

    A plain 403 from a server, or an ordinary error page served through a CDN, is not
    a bot wall: that needs the vendor's challenge page (or its "challenge" header).
    A 200 counts only when the whole page is a short challenge page.
    """
    body = html[:30000].lower()
    blocked = status in BLOCK_STATUSES
    short = len(html) < 30000
    for name, wanted, strong, weak in BOT_WALLS:
        vendor = _vendor_header(headers, wanted)
        if any(v == "challenge" and headers.get(k, "").lower() == "challenge" for k, v in wanted.items()):
            return name
        if any(m in body for m in strong) and (blocked or short):
            return name
        if vendor and blocked and any(m in body for m in weak):
            return name
    return None


class Fetcher:
    def __init__(self, delay: float = 0.5):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "en;q=0.9,*;q=0.5",
        })
        self.delay = delay
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=TIMEOUT)
                if r.status_code >= 400:
                    rp = None  # no robots.txt: everything allowed
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def _get_limited(self, url: str, limit: int) -> requests.Response:
        r = self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True)
        chunks, size = [], 0
        for chunk in r.iter_content(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                break
        r._content = b"".join(chunks)[:limit]
        return r

    def fetch(self, url: str, with_scripts: bool = True) -> Page:
        if not self.allowed(url):
            return Page(url, 0, {}, "", error="blocked by robots.txt")
        start = time.time()
        if not url.startswith("https://"):
            return Page(url, 0, {}, "", error="insecure: not https")
        try:
            r = self._get_limited(url, MAX_PAGE_BYTES)  # requests verifies certificates by default
        except requests.exceptions.SSLError:
            return Page(url, 0, {}, "", error="insecure: invalid certificate")
        except requests.RequestException as e:
            return Page(url, 0, {}, "", error=f"{type(e).__name__}")
        if not r.url.startswith("https://") or any(not h.url.startswith("https://") for h in r.history[1:]):
            return Page(r.url, r.status_code, {}, "", error="insecure: redirects to http")
        headers = {k.lower(): v for k, v in r.headers.items()}
        ctype = headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype:
            return Page(r.url, r.status_code, headers, "", error=f"not html ({ctype[:40]})")
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        page = Page(r.url, r.status_code, headers, r.text, elapsed=time.time() - start)
        page.bot_wall = detect_bot_wall(r.status_code, headers, r.text)
        if page.bot_wall:
            page.error = f"bot protection ({page.bot_wall})"
            page.tls = tls_info(urlsplit(page.url).hostname or "")
            time.sleep(self.delay)
            return page
        if with_scripts and page.ok:
            page.scripts = self._same_origin_scripts(page)
        if page.ok:
            page.tls = tls_info(urlsplit(page.url).hostname or "")
        time.sleep(self.delay)
        return page

    def archived(self, url: str, max_age_days: int = 3 * 365) -> Page | None:
        """The Wayback Machine's latest copy of a page, for sites whose bot check stops us
        reading the live page. Uses the public availability API and the raw ("id_")
        snapshot, so it never touches the site itself. None when there's no recent copy."""
        try:
            r = self.session.get("https://archive.org/wayback/available", params={"url": url}, timeout=TIMEOUT)
            snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        except (requests.RequestException, ValueError):
            return None
        ts = snap.get("timestamp", "")
        if not snap.get("available") or not str(snap.get("status", "200")).startswith("2") or len(ts) < 8:
            return None
        taken = datetime.strptime(ts[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - taken).days > max_age_days:
            return None
        raw = f"https://web.archive.org/web/{ts}id_/{url}"
        try:
            a = self._get_limited(raw, MAX_PAGE_BYTES)
        except requests.RequestException:
            return None
        if not a.ok or "html" not in a.headers.get("content-type", ""):
            return None
        a.encoding = a.encoding if a.encoding and a.encoding.lower() != "iso-8859-1" else a.apparent_encoding
        page = Page(url, 200, {}, a.text)
        page.archived_at = taken.date().isoformat()
        time.sleep(self.delay)
        return page

    def _same_origin_scripts(self, page: Page) -> list[str]:
        """Fetch a few same-origin JS bundles: many creative sites are SPA shells whose
        interactivity (canvas, WebGL, WebAudio) only shows up in the bundle."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(page.html, "lxml")
        origin = urlsplit(page.url).netloc
        bodies = []
        for tag in soup.find_all("script", src=True)[:12]:
            src = urljoin(page.url, tag["src"])
            if urlsplit(src).netloc != origin:
                continue
            try:
                r = self._get_limited(src, MAX_SCRIPT_BYTES)
                if r.ok:
                    bodies.append(r.text)
            except requests.RequestException:
                continue
            if len(bodies) >= MAX_SCRIPTS:
                break
        return bodies


def tls_info(host: str, port: int = 443) -> dict:
    """Negotiated TLS version and days until the certificate expires.
    Best effort: returns {} when a direct socket isn't possible (e.g. behind a proxy)."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=6) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
                version = tls.version()
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
        return {"version": version, "days_left": (expires - datetime.now(timezone.utc)).days}
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return {}
