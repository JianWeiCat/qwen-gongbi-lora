#!/usr/bin/env python3
"""Generate DiffSynth edit-pair metadata for Qwen-Image-Edit-2511 LoRA."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_PROMPT = (
    "Convert the input image into traditional Chinese gongbi painting style, "
    "preserving the original composition, subject details, and spatial structure."
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("data/gongbi_v1"),
        help="Dataset directory containing images/ and output metadata.json.",
    )
    parser.add_argument(
        "--images-dir-name",
        default="images",
        help="Image folder name under dataset-dir.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Metadata output path. Defaults to dataset-dir/metadata.json.",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt written to every metadata item.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation.",
    )
    return parser.parse_args()


def collect_pairs(images_dir: Path) -> list[tuple[str, Path, Path]]:
    inputs: dict[str, Path] = {}
    targets: dict[str, Path] = {}

    for path in sorted(images_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        stem = path.stem
        if stem.endswith("_input"):
            pair_id = stem[: -len("_input")]
            inputs[pair_id] = path
        elif stem.endswith("_gongbi"):
            pair_id = stem[: -len("_gongbi")]
            targets[pair_id] = path

    missing_targets = sorted(set(inputs) - set(targets))
    missing_inputs = sorted(set(targets) - set(inputs))
    if missing_targets or missing_inputs:
        messages = []
        if missing_targets:
            messages.append(f"missing *_gongbi files for: {', '.join(missing_targets[:20])}")
        if missing_inputs:
            messages.append(f"missing *_input files for: {', '.join(missing_inputs[:20])}")
        raise SystemExit("; ".join(messages))

    return [(pair_id, inputs[pair_id], targets[pair_id]) for pair_id in sorted(inputs)]


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir
    images_dir = dataset_dir / args.images_dir_name
    output = args.output or dataset_dir / "metadata.json"

    if not images_dir.exists():
        raise SystemExit(f"images directory not found: {images_dir}")

    items = []
    for _, input_path, target_path in collect_pairs(images_dir):
        items.append(
            {
                "image": input_path.relative_to(dataset_dir).as_posix(),
                "edit_image": target_path.relative_to(dataset_dir).as_posix(),
                "prompt": args.prompt,
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(items, ensure_ascii=False, indent=args.indent) + "\n", encoding="utf-8")
    print(f"wrote {len(items)} items to {output}")


if __name__ == "__main__":
    main()
