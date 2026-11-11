"""WebWander crawler: discovery, heuristic scoring and category tagging (no AI)."""

import os
import subprocess
from functools import lru_cache
from pathlib import Path

# Bump this whenever the crawler's behaviour changes (scoring, rejects, categories,
# sources...). Every run records it, so you can tell which version proposed a site.
__version__ = "1.16.0"

ROOT = Path(__file__).resolve().parents[2]
CATEGORIES_PATH = ROOT / "site" / "data" / "categories.json"
DATA_DIR = ROOT / "data"


@lru_cache(maxsize=1)
def build() -> str:
    """Short commit id of the code that is running ('' if unknown)."""
    sha = os.environ.get("GITHUB_SHA", "")
    if not sha:
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001
            sha = ""
    return sha[:7]


def version_string() -> str:
    """e.g. "v1.9.0 (325a51d)"."""
    return f"v{__version__}" + (f" ({build()})" if build() else "")
