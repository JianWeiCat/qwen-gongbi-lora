#!/usr/bin/env python3
"""Build DiffSynth edit-pair dataset files from accepted candidate reviews."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

from pipeline_common import (
    DEFAULT_DATASET_DIR,
    category_prompt,
    dataset_paths,
    load_json,
    read_jsonl,
    relative_to_dataset,
    sha256_file,
    write_jsonl,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/pipeline_gongbi_v1.json"))
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--output-images-dir-name", default="images")
    parser.add_argument("--prefix-width", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--clear-output", action="store_true")
    return parser.parse_args()


def latest_review_by_candidate(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for index, row in enumerate(rows):
        candidate_id = str(row.get("candidate_id", ""))
        if candidate_id:
            latest[candidate_id] = {**row, "_line": index}
    return latest


def save_png_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            image.convert("RGB").save(target, format="PNG")
    except Exception:
        shutil.copy2(source, target)


def main() -> None:
    args = parse_args()
    config = load_json(args.config)
    dataset_dir = args.dataset_dir or Path(config.get("dataset_dir", DEFAULT_DATASET_DIR))
    paths = dataset_paths(dataset_dir)
    final_images = dataset_dir / args.output_images_dir_name

    raw_assets = read_jsonl(paths["raw_manifest"])
    candidates = [item for item in read_jsonl(paths["candidates_manifest"]) if item.get("status") == "success"]
    reviews = latest_review_by_candidate(read_jsonl(paths["reviews_manifest"]))
    raw_by_id = {str(item.get("asset_id")): item for item in raw_assets}
    candidates_by_asset: dict[str, list[dict]] = {}
    for candidate in candidates:
        candidates_by_asset.setdefault(str(candidate.get("asset_id")), []).append(candidate)

    selected: list[tuple[dict, dict]] = []
    for asset_id, asset in raw_by_id.items():
        accepted = []
        for candidate in candidates_by_asset.get(asset_id, []):
            review = reviews.get(str(candidate.get("candidate_id")))
            if review and review.get("decision") == "accept":
                accepted.append((int(review.get("_line", 0)), candidate))
        if not accepted:
            continue
        accepted.sort(key=lambda pair: pair[0], reverse=True)
        selected.append((asset, accepted[0][1]))

    if args.limit is not None:
        selected = selected[: args.limit]
    if not selected:
        raise SystemExit("no accepted candidates found; review candidates first")

    if args.clear_output and final_images.exists():
        for path in final_images.iterdir():
            if path.is_file() and path.name != ".gitkeep":
                path.unlink()
    final_images.mkdir(parents=True, exist_ok=True)

    dataset_rows = []
    metadata = []
    for index, (asset, candidate) in enumerate(selected, start=1):
        pair_id = f"{index:0{args.prefix_width}d}"
        category = str(asset.get("category", "unknown"))
        input_target = final_images / f"{pair_id}_input.png"
        gongbi_target = final_images / f"{pair_id}_gongbi.png"
        source_path = dataset_dir / str(asset["image_path"])
        candidate_path = dataset_dir / str(candidate["image_path"])

        save_png_copy(source_path, input_target)
        save_png_copy(candidate_path, gongbi_target)

        prompt = category_prompt(config, category)
        metadata.append(
            {
                "image": relative_to_dataset(input_target, dataset_dir),
                "edit_image": relative_to_dataset(gongbi_target, dataset_dir),
                "prompt": prompt,
            }
        )
        dataset_rows.append(
            {
                "pair_id": pair_id,
                "category": category,
                "asset_id": asset.get("asset_id"),
                "candidate_id": candidate.get("candidate_id"),
                "prompt": prompt,
                "image": relative_to_dataset(input_target, dataset_dir),
                "edit_image": relative_to_dataset(gongbi_target, dataset_dir),
                "source_image_path": asset.get("image_path"),
                "candidate_image_path": candidate.get("image_path"),
                "source_url": asset.get("source_url"),
                "landing_url": asset.get("landing_url"),
                "license": asset.get("license"),
                "image_sha256": sha256_file(input_target),
                "edit_image_sha256": sha256_file(gongbi_target),
                "built_at": utc_now(),
            }
        )

    paths["metadata"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_jsonl(paths["dataset_manifest"], dataset_rows)
    print(f"built {len(dataset_rows)} pairs")
    print(f"metadata: {paths['metadata']}")
    print(f"dataset manifest: {paths['dataset_manifest']}")


if __name__ == "__main__":
    main()
