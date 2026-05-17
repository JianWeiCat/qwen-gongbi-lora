#!/usr/bin/env python3
"""Import local source images into the raw asset manifest."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from pipeline_common import (
    DEFAULT_DATASET_DIR,
    IMAGE_SUFFIXES,
    append_jsonl,
    dataset_paths,
    image_info,
    read_jsonl,
    relative_to_dataset,
    sha256_file,
    slugify,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--category", required=True)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--source-label", default="manual")
    parser.add_argument("--min-width", type=int, default=1024)
    parser.add_argument("--min-height", type=int, default=1024)
    return parser.parse_args()


def list_images(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def main() -> None:
    args = parse_args()
    if not args.input_dir.exists():
        raise SystemExit(f"input dir not found: {args.input_dir}")

    paths = dataset_paths(args.dataset_dir)
    raw_manifest = paths["raw_manifest"]
    raw_images_dir = paths["raw_images"] / slugify(args.category)
    raw_images_dir.mkdir(parents=True, exist_ok=True)

    existing = read_jsonl(raw_manifest)
    seen_sha = {str(item.get("sha256")) for item in existing}
    imported = 0
    skipped = 0

    for input_path in list_images(args.input_dir):
        try:
            width, height, mode = image_info(input_path)
        except Exception as exc:  # noqa: BLE001
            print(f"skip unreadable {input_path}: {exc}")
            skipped += 1
            continue
        if width < args.min_width or height < args.min_height:
            print(f"skip small {input_path}: {width}x{height}")
            skipped += 1
            continue

        digest = sha256_file(input_path)
        if digest in seen_sha:
            print(f"skip duplicate {input_path}")
            skipped += 1
            continue

        stem = slugify(input_path.stem)
        asset_id = f"{slugify(args.category)}_{stem}_{digest[:10]}"
        output = raw_images_dir / f"{asset_id}{input_path.suffix.lower()}"
        shutil.copy2(input_path, output)

        record = {
            "asset_id": asset_id,
            "category": args.category,
            "source": args.source_label,
            "source_url": f"manual://{input_path.name}",
            "landing_url": None,
            "license": "manual-review-required",
            "license_version": None,
            "creator": None,
            "title": input_path.stem,
            "image_path": relative_to_dataset(output, args.dataset_dir),
            "sha256": digest,
            "width": width,
            "height": height,
            "mode": mode,
            "query": None,
            "collected_at": utc_now(),
        }
        append_jsonl(raw_manifest, record)
        seen_sha.add(digest)
        imported += 1
        print(f"imported {asset_id}: {width}x{height}")

    print(f"done, imported {imported}, skipped {skipped}")


if __name__ == "__main__":
    main()
