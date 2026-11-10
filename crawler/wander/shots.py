"""Page screenshots for the "card" view of a user's saved sites.

Screenshots are taken with a headless browser (Playwright) by
.github/workflows/screenshots.yml and kept on the `screenshots` branch, which the
Pages deploy copies into site/shots/. site/shots/manifest.json tells the site which
sites have one.

Many fun sites draw themselves with JavaScript (toys, canvases, single-page apps),
so a screenshot taken the moment the page "loads" is often an empty white box. To
avoid that, capture() waits for the network to settle, fonts and images to finish,
then checks the picture: if it is (nearly) one flat colour it waits longer, nudges
lazy content by scrolling, and tries again. A page that is still blank after all
that gets no screenshot; the site then falls back to the page's own preview image or
a generated preview card, never a blank tile.

Mature sites are never screenshotted (the repo and the site are public).
"""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

VIEWPORT = {"width": 1280, "height": 800}
THUMB_SIZE = (640, 400)
# Extra waits (seconds) between attempts while the page is still blank.
SETTLE_STEPS = (1.0, 2.5, 4.0, 6.0)
BLANK_RETRY_DAYS = 14   # try blank pages again after this long
MATURE = "mature"


# ---------------------------------------------------------------- blank detection

def blank_ratio(image_bytes: bytes) -> float:
    """Share (0-1) of the picture covered by its single most common colour.
    A white page with only a spinner is ~0.999; a real page is far lower."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize((160, 100), Image.Resampling.BILINEAR)
    # Bucket colours (16 levels per channel) so anti-aliasing and gradients
    # of the same background still count as "the same colour".
    img = img.point(lambda v: v & 0xF0)
    colors = img.getcolors(maxcolors=160 * 100)
    return max(count for count, _ in colors) / (160 * 100)


def is_blank(image_bytes: bytes, threshold: float = 0.985) -> bool:
    return blank_ratio(image_bytes) >= threshold


def to_thumbnail(png: bytes) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=72, method=6)
    return out.getvalue()


# ---------------------------------------------------------------- capture

@dataclass
class Shot:
    image: bytes | None   # webp thumbnail, or None if the page stayed blank / failed
    note: str             # "ok", "blank", or an error message
    attempts: int = 0


_SETTLE_JS = """async () => {
  try { await document.fonts.ready; } catch (e) {}
  const imgs = [...document.images].filter((i) => !i.complete).slice(0, 40);
  await Promise.race([
    Promise.all(imgs.map((i) => new Promise((r) => { i.onload = i.onerror = r; }))),
    new Promise((r) => setTimeout(r, 4000)),
  ]);
}"""


def capture(page, url: str, steps=SETTLE_STEPS, nav_timeout_ms: int = 25000) -> Shot:
    """Load `url` in a Playwright page and return a non-blank thumbnail if possible."""
    try:
        page.goto(url, wait_until="load", timeout=nav_timeout_ms)
    except Exception as e:  # noqa: BLE001 - slow pages: keep whatever has rendered so far
        if "Timeout" not in type(e).__name__ and "Timeout" not in str(e):
            return Shot(None, f"error: {str(e).splitlines()[0][:120]}")
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001 - sites that poll forever never go idle
        pass
    try:
        page.evaluate(_SETTLE_JS)
    except Exception:  # noqa: BLE001
        pass

    attempts = 0
    for i, wait in enumerate((0.0, *steps)):
        if wait:
            time.sleep(wait)
        if i == 2:
            # Lazy-loaded content often waits for a scroll before it appears.
            try:
                page.mouse.wheel(0, 600)
                time.sleep(0.6)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(0.4)
            except Exception:  # noqa: BLE001
                pass
        attempts += 1
        try:
            png = page.screenshot(type="png", timeout=15000)
        except Exception as e:  # noqa: BLE001
            return Shot(None, f"error: {str(e).splitlines()[0][:120]}", attempts)
        if not is_blank(png):
            return Shot(to_thumbnail(png), "ok", attempts)
    return Shot(None, "blank", attempts)


# ---------------------------------------------------------------- manifest

def load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=0, sort_keys=True))


def needs_capture(entry: dict | None, now: float, force: bool = False) -> bool:
    if force or not entry:
        return True
    if entry.get("f"):
        return False
    return now - entry.get("t", 0) > BLANK_RETRY_DAYS * 86400


def plan(sites: list[dict], saved_ids: set[str], manifest: dict, now: float,
         limit: int, force: bool = False) -> tuple[list[dict], list[str]]:
    """Decide what to capture and what to drop.

    Returns (to_capture, to_remove). Saved sites come first (people are looking at
    those), then the newest approved. Sites that are no longer approved, or are now
    tagged mature, lose their screenshot."""
    keep = {s["id"]: s for s in sites
            if s.get("status") == "approved" and MATURE not in (s.get("categories") or [])}
    to_remove = sorted(i for i in manifest if i not in keep)
    todo = [s for s in keep.values() if needs_capture(manifest.get(s["id"]), now, force)]
    todo.sort(key=lambda s: (s["id"] not in saved_ids, -_ts(s.get("approvedAt"))))
    return todo[:limit], to_remove


def _ts(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if hasattr(v, "timestamp"):
        return v.timestamp()
    return 0.0


def run(sites: list[dict], saved_ids: set[str], out_dir: Path, limit: int = 120,
        max_minutes: float = 40, force: bool = False, log=print) -> dict:
    """Capture screenshots into out_dir (the checked-out `screenshots` branch)."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    now = time.time()
    todo, remove = plan(sites, saved_ids, manifest, now, limit, force)

    for sid in remove:
        entry = manifest.pop(sid)
        if entry.get("f"):
            (out_dir / entry["f"]).unlink(missing_ok=True)
    log(f"Screenshots: {len(todo)} to capture, {len(remove)} removed")

    stats = {"ok": 0, "blank": 0, "error": 0, "removed": len(remove)}
    deadline = now + max_minutes * 60
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--autoplay-policy=user-gesture-required"])
        try:
            for s in todo:
                if time.time() > deadline:
                    log("  time budget used up; the rest waits for the next run")
                    break
                ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1, color_scheme="light")
                ctx.set_default_timeout(20000)
                page = ctx.new_page()
                page.on("dialog", lambda d: d.dismiss())
                try:
                    shot = capture(page, s["url"])
                finally:
                    ctx.close()
                entry = {"t": int(time.time())}
                if shot.image:
                    name = f"{s['id']}.webp"
                    (out_dir / name).write_bytes(shot.image)
                    entry["f"] = name
                    stats["ok"] += 1
                else:
                    old = manifest.get(s["id"], {})
                    if old.get("f"):
                        (out_dir / old["f"]).unlink(missing_ok=True)
                    entry["blank"] = True
                    stats["blank" if shot.note == "blank" else "error"] += 1
                manifest[s["id"]] = entry
                log(f"  [{shot.note}] {s['url']} ({shot.attempts} tries)")
        finally:
            browser.close()
    save_manifest(manifest_path, manifest)
    return stats
