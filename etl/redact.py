"""
SkillMap: remove API credentials from text before it is written to disk.

Some APIs echo credentials back in their responses: Adzuna puts the app_id in every
ad's redirect_url (utm_source=...). Raw files are uploaded as GitHub Actions artifacts,
which anyone can download from a public repo, so extractors pass every response body
through redact() before saving it.

    text, n = redact(response.text)     # n = how many values were replaced

Values are read from the environment (loaded from .env by the extractors). They are
replaced by "[REDACTED:<NAME>]" and never printed.
"""

from __future__ import annotations

import os

SECRET_ENV_VARS = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY", "THEIRSTACK_API_KEY")
MIN_SECRET_LENGTH = 6   # never "redact" something so short it could match ordinary text


def _secrets(names: tuple[str, ...]) -> list[tuple[str, str]]:
    found = [(name, os.getenv(name, "").strip()) for name in names]
    # longest first, so a value that contains another is replaced whole
    return sorted([(n, v) for n, v in found if len(v) >= MIN_SECRET_LENGTH], key=lambda nv: -len(nv[1]))


def redact(text: str, names: tuple[str, ...] = SECRET_ENV_VARS) -> tuple[str, int]:
    """Replace every known credential value in text. Returns (clean_text, replacements)."""
    count = 0
    for name, value in _secrets(names):
        hits = text.count(value)
        if hits:
            count += hits
            text = text.replace(value, f"[REDACTED:{name}]")
    return text, count


def contains_secret(text: str, names: tuple[str, ...] = SECRET_ENV_VARS) -> bool:
    return any(value in text for _, value in _secrets(names))


def scrub_tree(root, names: tuple[str, ...] = SECRET_ENV_VARS) -> tuple[int, int]:
    """Last line of defence before raw files leave the machine (GitHub artifact): redact any
    credential value found in any file under root, in place. Returns (files_scanned,
    files_fixed). Values are never printed."""
    from pathlib import Path
    scanned = fixed = 0
    for path in Path(root).rglob("*"):
        if not path.is_file():
            continue
        scanned += 1
        text = path.read_bytes().decode("utf-8", errors="surrogateescape")
        clean, n = redact(text, names)
        if n:
            path.write_bytes(clean.encode("utf-8", errors="surrogateescape"))
            fixed += 1
    return scanned, fixed
