"""64-bit SimHash over word shingles, used to measure how different a page is from
everything already in the database (near-duplicates share most bits)."""

import hashlib
import re

WORD_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = set("""a an and are as at be by for from has have i in is it its of on or that the this
to was were will with you your we our my me us they them he she his her not but if so do can all""".split())


def _hash64(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")


def simhash(text: str, shingle: int = 3) -> int:
    words = [w for w in WORD_RE.findall(text.lower()) if w not in STOPWORDS]
    if not words:
        return 0
    if len(words) < shingle:
        tokens = words
    else:
        tokens = [" ".join(words[i:i + shingle]) for i in range(len(words) - shingle + 1)]
    v = [0] * 64
    for t in tokens[:20000]:
        h = _hash64(t)
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def similarity(a: int, b: int) -> float:
    """1.0 = identical fingerprints, ~0.5 = unrelated text."""
    return 1.0 - bin(a ^ b).count("1") / 64.0


def to_hex(h: int) -> str:
    return f"{h:016x}"


def from_hex(s: str) -> int:
    return int(s, 16) if s else 0
