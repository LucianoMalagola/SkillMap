"""
SkillMap: which database a command talks to.

    url = database_url("dev")     # or "main"

Lookup order (first non-empty wins; values are never printed):
    dev   DATABASE_URL_DEV, then DATABASE_URL (the local .env default, which points at dev)
    main  DATABASE_URL_MAIN only. There is deliberately no fallback, so a local command
          can't hit production by accident: main is written by the GitHub Actions workflow.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ("dev", "main")
_ENV_NAMES = {"dev": ("DATABASE_URL_DEV", "DATABASE_URL"), "main": ("DATABASE_URL_MAIN",)}


def load_env() -> None:
    """Load .env if present. Variables already in the environment win (GitHub secrets)."""
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def database_url(target: str) -> str:
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r} (expected one of {TARGETS})")
    load_env()
    for name in _ENV_NAMES[target]:
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise RuntimeError(f"no connection string for target '{target}': set {' or '.join(_ENV_NAMES[target])}")
