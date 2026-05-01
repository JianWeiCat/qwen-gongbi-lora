#!/usr/bin/env python3
"""Validate local paired-image metadata before remote training."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


REQUIRED_KEYS = {"image", "edit_image", "prompt"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("data/gongbi_v1"),
        help="Dataset directory containing metadata.json.",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=None,
        help="Metadata path. Defaults to dataset-dir/metadata.json.",
    )
    parser.add_argument(
        "--skip-image-open",
        action="store_true",
        help="Only check path existence and JSON fields.",
    )
    return parser.parse_args()


def load_metadata(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("metadata must be a JSON array")
    return data


def image_info(path: Path) -> tuple[int, int, str] | None:
    if Image is None:
        return None
    with Image.open(path) as image:
        image.load()
        return image.width, image.height, image.mode


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir
    metadata_path = args.metadata or dataset_dir / "metadata.json"
    if not metadata_path.exists():
        raise SystemExit(f"metadata not found: {metadata_path}")

    items = load_metadata(metadata_path)
    errors: list[str] = []
    size_pairs: Counter[str] = Counter()
    prompt_counter: Counter[str] = Counter()

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"item {index}: must be an object")
            continue

        missing = REQUIRED_KEYS - set(item)
        if missing:
            errors.append(f"item {index}: missing keys {sorted(missing)}")
            continue

        input_path = dataset_dir / item["image"]
        target_path = dataset_dir / item["edit_image"]
        prompt_counter[item["prompt"]] += 1

        for role, path in (("image", input_path), ("edit_image", target_path)):
            if not path.exists():
                errors.append(f"item {index}: {role} not found: {path}")
                continue
            if args.skip_image_open:
                continue
            if Image is None:
                errors.append("Pillow is not installed; run pip install pillow or use --skip-image-open")
                continue
            try:
                info = image_info(path)
                if info is not None:
                    size_pairs[f"{info[0]}x{info[1]}:{info[2]}"] += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"item {index}: failed to open {role} {path}: {exc}")

    if errors:
        print("dataset check failed")
        for error in errors[:50]:
            print(f"- {error}")
        if len(errors) > 50:
            print(f"- ... {len(errors) - 50} more errors")
        raise SystemExit(1)

    print(f"dataset ok: {len(items)} pairs")
    print(f"unique prompts: {len(prompt_counter)}")
    if size_pairs:
        print("top image sizes:")
        for size, count in size_pairs.most_common(10):
            print(f"- {size}: {count}")


if __name__ == "__main__":
    main()

