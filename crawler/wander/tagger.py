"""Keyword + structural-signal category tagger. Deterministic, no AI.

Each category in site/data/categories.json lists keywords and structural signals.
A keyword hit is weighted by where it appears (title > description > headings > body),
body hits are capped so long pages don't drown everything, and signals add fixed weights.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache

from . import CATEGORIES_PATH

WEIGHTS = {"title": 4.0, "description": 2.5, "headings": 1.5, "body": 0.6}
BODY_CAP = 3  # max counted body occurrences per keyword
THRESHOLD = 4.0
MAX_TAGS = 3


@lru_cache(maxsize=1)
def load_categories() -> list[dict]:
    with open(CATEGORIES_PATH) as f:
        cats = json.load(f)["categories"]
    for c in cats:
        c["_patterns"] = [
            re.compile(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])") for k in c["keywords"]
        ]
    return cats


def signal_values(f: dict) -> dict[str, bool]:
    return {
        "canvas": f["n_canvas"] > 0,
        "webgl": f["webgl"],
        "web_audio": f["web_audio"],
        "audio_tag": f["n_audio"] > 0,
        "video_tag": f["n_video"] > 0,
        "keyboard_controls": f["keyboard_events"] >= 2,
        "pointer_handlers": f["pointer_events"] >= 2,
        "map_library": f["map_library"],
        "chart_library": f["chart_library"],
        "svg_heavy": f["n_svg"] >= 8,
        "forms": f["n_inputs"] >= 3,
        "marquee_or_blink": f["marquee_or_blink"],
        "table_layout": f["n_tables"] >= 3,
        "long_text": f["word_count"] >= 1500,
        "many_images": f["n_img"] >= 20,
        "personal_host": f["personal_host"],
        "code_blocks": f["n_code_blocks"] >= 3,
        "code_highlighter": f["code_highlighter"],
        "code_editor": f["code_editor"],
    }


def category_scores(f: dict) -> dict[str, float]:
    fields = {
        "title": f["title"].lower(),
        "description": f["description"].lower(),
        "headings": f["headings"].lower(),
        "body": f["text"][:20000].lower(),
    }
    signals = signal_values(f)
    # Normalise body hits by length so a 10k-word page doesn't win on volume alone.
    body_norm = 1.0 / max(1.0, math.log10(max(10, f["word_count"])) - 1.0)
    scores = {}
    for cat in load_categories():
        s = 0.0
        for pat in cat["_patterns"]:
            for field, text in fields.items():
                if not text:
                    continue
                if field == "body":
                    n = min(BODY_CAP, len(pat.findall(text)))
                    s += n * WEIGHTS["body"] * body_norm
                elif pat.search(text):
                    s += WEIGHTS[field]
        for sig, w in cat.get("signals", {}).items():
            if signals.get(sig):
                s += w
        scores[cat["id"]] = round(s, 2)
    return scores


def tag(f: dict) -> tuple[list[str], dict[str, float]]:
    scores = category_scores(f)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    tags = [cid for cid, s in ranked if s >= THRESHOLD][:MAX_TAGS]
    if not tags and ranked and ranked[0][1] > 0:
        tags = [ranked[0][0]]
    return tags, {k: v for k, v in ranked if v > 0}
