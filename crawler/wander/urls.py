"""URL normalisation and identity helpers."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "s", "si",
}


def normalize(url: str) -> str | None:
    """Return a canonical form of a URL, or None if it is not a crawlable web URL."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        if re.match(r"^[a-zA-Z][\w+.-]*:(?!\d)", url):  # mailto:, javascript:, ...
            return None
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    for suffix in ("/index.html", "/index.htm", "/index.php"):
        if path.endswith(suffix):
            path = path[: -len(suffix) + 1]
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS)
    )
    # Hash-routed single page apps keep their fragment ("#/thing"), anchors are dropped.
    fragment = parts.fragment if parts.fragment.startswith("/") or parts.fragment.startswith("!") else ""
    return urlunsplit((parts.scheme, host, path, query, fragment))


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def registrable_domain(url: str) -> str:
    """Rough eTLD+1 (good enough for de-duplicating sites by owner)."""
    host = domain_of(url)
    parts = host.split(".")
    shared_hosts = ("github.io", "neocities.org", "netlify.app", "vercel.app", "glitch.me",
                    "pages.dev", "itch.io", "tumblr.com", "substack.com", "wordpress.com",
                    "blogspot.com", "carrd.co", "surge.sh", "web.app", "firebaseapp.com")
    for shared in shared_hosts:
        if host.endswith("." + shared):
            return ".".join(parts[-(shared.count(".") + 2):])
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def identity(norm_url: str) -> str:
    """Scheme-less form, so http:// and https:// versions are the same site."""
    return norm_url.split("://", 1)[-1]


def site_id(norm_url: str) -> str:
    return hashlib.sha1(identity(norm_url).encode()).hexdigest()[:20]
