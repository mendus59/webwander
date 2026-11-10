"""Screenshots for the saved-sites card view: never keep a blank picture."""

import functools
import http.server
import io
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from wander import shots

FIXTURES = Path(__file__).parent / "fixtures" / "shots"


def png(draw=None, bg="white", size=(1280, 800)):
    img = Image.new("RGB", size, bg)
    if draw:
        draw(ImageDraw.Draw(img))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def test_flat_page_is_blank():
    assert shots.is_blank(png())
    assert shots.is_blank(png(bg="#111"))


def test_spinner_on_white_is_blank():
    assert shots.is_blank(png(lambda d: d.ellipse((620, 380, 660, 420), outline="#ccc", width=3)))


def test_text_page_is_not_blank():
    def text(d):
        for y in range(60, 700, 22):
            d.rectangle((190, y, 1090, y + 10), fill="#333")
    assert not shots.is_blank(png(text))


def test_dark_art_page_is_not_blank():
    def art(d):
        for i in range(40):
            d.ellipse((i * 30, i * 18, i * 30 + 120, i * 18 + 120), fill=(i * 6, 80, 200 - i * 4))
    assert not shots.is_blank(png(art, bg="black"))


def site(i, **kw):
    return dict({"id": f"s{i}", "url": f"https://s{i}.example/", "status": "approved",
                 "categories": ["art"], "approvedAt": i}, **kw)


def test_plan_skips_mature_and_prunes():
    sites = [site(1), site(2, categories=["mature"]), site(3, status="rejected"), site(4)]
    manifest = {"s2": {"f": "s2.webp", "t": 0}, "s3": {"f": "s3.webp", "t": 0}, "s4": {"f": "s4.webp", "t": 0}}
    todo, remove = shots.plan(sites, set(), manifest, now=1e9, limit=10)
    assert [s["id"] for s in todo] == ["s1"]
    assert remove == ["s2", "s3"]   # mature and no-longer-approved lose their screenshot


def test_plan_puts_saved_first_and_retries_old_blanks():
    sites = [site(i) for i in range(1, 6)]
    manifest = {"s4": {"blank": True, "t": 1e9 - 86400}, "s5": {"blank": True, "t": 0}}
    todo, _ = shots.plan(sites, {"s1"}, manifest, now=1e9, limit=10)
    assert [s["id"] for s in todo] == ["s1", "s5", "s3", "s2"]   # saved, then newest; s4 blank too recently


# ---------------------------------------------------------------- real browser

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def server():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(FIXTURES))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def page():
    try:
        p = playwright.sync_playwright().start()
        browser = p.chromium.launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no browser: {e}")
    ctx = browser.new_context(viewport=shots.VIEWPORT)
    yield ctx.new_page()
    browser.close()
    p.stop()


def test_waits_for_late_content(server, page):
    shot = shots.capture(page, f"{server}/late.html", steps=(1.0, 2.0, 3.0))
    assert shot.note == "ok" and shot.attempts > 1
    assert not shots.is_blank(shot.image)


def test_plain_page_first_try(server, page):
    shot = shots.capture(page, f"{server}/text.html", steps=(1.0,))
    assert shot.note == "ok" and shot.attempts == 1


def test_gives_up_on_blank_page(server, page):
    shot = shots.capture(page, f"{server}/never.html", steps=(0.5, 0.5))
    assert shot.image is None and shot.note == "blank"
