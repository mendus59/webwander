"""WebWander crawler: discovery, heuristic scoring and category tagging (no AI)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATEGORIES_PATH = ROOT / "site" / "data" / "categories.json"
DATA_DIR = ROOT / "data"
