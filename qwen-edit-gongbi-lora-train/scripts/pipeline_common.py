#!/usr/bin/env python3
"""Shared helpers for the gongbi LoRA data pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_DATASET_DIR = Path("data/gongbi_v1")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"config must be a JSON object: {path}")
    return data


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise SystemExit(f"{path}:{line_number}: JSONL item must be an object")
            rows.append(item)
    return rows


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str, max_length: int = 48) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return (value or "item")[:max_length]


def safe_suffix(path_or_url: str, fallback: str = ".jpg") -> str:
    suffix = Path(path_or_url.split("?", 1)[0]).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return suffix
    return fallback


def image_info(path: Path) -> tuple[int, int, str]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Pillow is required; run pip install pillow") from exc

    with Image.open(path) as image:
        image.load()
        return image.width, image.height, image.mode


def relative_to_dataset(path: Path, dataset_dir: Path) -> str:
    return path.resolve().relative_to(dataset_dir.resolve()).as_posix()


def dataset_paths(dataset_dir: Path) -> dict[str, Path]:
    return {
        "dataset": dataset_dir,
        "raw_images": dataset_dir / "raw" / "images",
        "candidate_images": dataset_dir / "candidates" / "images",
        "manifests": dataset_dir / "manifests",
        "final_images": dataset_dir / "images",
        "raw_manifest": dataset_dir / "manifests" / "raw_assets.jsonl",
        "candidates_manifest": dataset_dir / "manifests" / "candidates.jsonl",
        "reviews_manifest": dataset_dir / "manifests" / "reviews.jsonl",
        "dataset_manifest": dataset_dir / "manifests" / "dataset_manifest.jsonl",
        "metadata": dataset_dir / "metadata.json",
    }


def category_prompt(config: dict[str, Any], category: str) -> str:
    for item in config.get("categories", []):
        if item.get("name") == category:
            prompt = item.get("prompt") or config.get("default_prompt")
            if prompt:
                return str(prompt)
    prompt = config.get("default_prompt")
    if prompt:
        return str(prompt)
    return (
        "Convert the input image into traditional Chinese gongbi painting style, "
        "preserving composition, subject identity, and fine structural details."
    )


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise SystemExit(f"path is outside allowed root: {path}")
    return resolved
