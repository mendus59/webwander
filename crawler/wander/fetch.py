"""Polite HTTP fetching: robots.txt, size limits, timeouts."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
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

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300


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
        try:
            r = self._get_limited(url, MAX_PAGE_BYTES)
        except requests.RequestException as e:
            return Page(url, 0, {}, "", error=f"{type(e).__name__}")
        headers = {k.lower(): v for k, v in r.headers.items()}
        ctype = headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype:
            return Page(r.url, r.status_code, headers, "", error=f"not html ({ctype[:40]})")
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        page = Page(r.url, r.status_code, headers, r.text, elapsed=time.time() - start)
        if with_scripts and page.ok:
            page.scripts = self._same_origin_scripts(page)
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
