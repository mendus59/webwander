"""Fetch-level security: certificate errors and downgrade redirects never reach scoring."""

import requests

from wander.fetch import Fetcher
from wander.pipeline import evaluate
from wander.score import Corpus


class FakeResponse:
    def __init__(self, url, history=()):
        self.url, self.history, self.status_code = url, list(history), 200
        self.headers = {"content-type": "text/html"}
        self.encoding = "utf-8"
        self.text = "<html><title>x</title><body>hello</body></html>"


def make_fetcher(monkeypatch, behaviour):
    f = Fetcher(delay=0)
    monkeypatch.setattr(f, "allowed", lambda url: True)
    monkeypatch.setattr(f, "_get_limited", lambda url, limit: behaviour(url))
    return f


def test_invalid_certificate(monkeypatch):
    def boom(url):
        raise requests.exceptions.SSLError("certificate verify failed")
    ev = evaluate("https://bad-cert.example.org/", make_fetcher(monkeypatch, boom), Corpus())
    assert ev.doc is None and ev.error == "insecure: invalid certificate"


def test_redirect_to_http(monkeypatch):
    ev = evaluate("https://downgrade.example.org/",
                  make_fetcher(monkeypatch, lambda url: FakeResponse("http://downgrade.example.org/")), Corpus())
    assert ev.doc is None and ev.error == "insecure: redirects to http"


def test_http_url_upgraded_to_https(monkeypatch):
    seen = []

    def ok(url):
        seen.append(url)
        return FakeResponse(url)
    f = make_fetcher(monkeypatch, ok)
    monkeypatch.setattr("wander.fetch.tls_info", lambda host: {})
    ev = evaluate("http://plain.example.org/", f, Corpus())
    assert seen[0].startswith("https://")
    assert ev.doc and ev.doc["url"].startswith("https://")
