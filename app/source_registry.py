from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml


_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SOURCES_PATH = _ROOT / "sources.yaml"


def load_sources(path: Path | None = None) -> List[dict]:
    sources_path = path or _DEFAULT_SOURCES_PATH
    if not sources_path.exists():
        raise FileNotFoundError(f"sources.yaml not found: {sources_path}")

    with sources_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    sources = data.get("sources") or []
    if not isinstance(sources, list):
        raise ValueError("sources.yaml: 'sources' must be a list")
    return sources


def get_source_by_id(source_id: str, path: Path | None = None) -> Dict:
    for source in load_sources(path):
        if source.get("id") == source_id:
            return source
    raise KeyError(f"source_id not found: {source_id}")
