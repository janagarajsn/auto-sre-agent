"""
Prompt loader — reads .md files from the prompts directory.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt '{name}.md' not found in {_PROMPTS_DIR}")
    return path.read_text(encoding="utf-8").strip()
