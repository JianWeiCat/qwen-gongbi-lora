#!/usr/bin/env python3
"""Generate gongbi-style candidates with Doubao SeedEdit through Volcengine Ark."""

from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any

from pipeline_common import (
    DEFAULT_DATASET_DIR,
    append_jsonl,
    category_prompt,
    dataset_paths,
    image_info,
    load_json,
    read_jsonl,
    relative_to_dataset,
    safe_suffix,
    sha256_file,
    slugify,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/pipeline_gongbi_v1.json"))
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Maximum new candidates to generate.")
    parser.add_argument("--asset-id", default=None, help="Generate candidates only for one asset.")
    parser.add_argument("--mock", action="store_true", help="Copy source images instead of calling the API.")
    parser.add_argument(
        "--image-input",
        choices=("local-b64", "source-url"),
        default="local-b64",
        help="How to send the source image to the provider.",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args()


def file_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def nested_getattr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def first_response_data(response: Any) -> Any:
    data = nested_getattr(response, "data")
    if isinstance(data, list) and data:
        return data[0]
    return None


def download_output(item: Any, output: Path, timeout: int) -> str | None:
    import requests

    url = nested_getattr(item, "url")
    b64_json = nested_getattr(item, "b64_json")
    output.parent.mkdir(parents=True, exist_ok=True)
    if b64_json:
        output.write_bytes(base64.b64decode(str(b64_json)))
        return None
    if not url:
        raise RuntimeError("provider response has neither url nor b64_json")
    with requests.get(str(url), timeout=timeout, stream=True) as response:
        response.raise_for_status()
        with output.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return str(url)


def build_prompt(config: dict[str, Any], category: str) -> str:
    prompt = category_prompt(config, category)
    suffix = str(config.get("generation", {}).get("prompt_suffix", "")).strip()
    if suffix:
        return f"{prompt}\n\n{suffix}"
    return prompt


def call_doubao(
    *,
    model: str,
    base_url: str,
    api_key: str,
    prompt: str,
    image_payload: str,
    size: str,
) -> Any:
    try:
        from volcenginesdkarkruntime import Ark
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "volcenginesdkarkruntime is required; install with "
            "pip install 'volcengine-python-sdk[ark]'"
        ) from exc

    client = Ark(api_key=api_key, base_url=base_url)
    return client.images.generate(
        model=model,
        prompt=prompt,
        image=image_payload,
        size=size,
        watermark=False,
    )


def existing_success_keys(rows: list[dict[str, Any]]) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    for row in rows:
        if row.get("status") == "success":
            try:
                keys.add((str(row["asset_id"]), int(row["candidate_index"])))
            except (KeyError, TypeError, ValueError):
                continue
    return keys


def main() -> None:
    args = parse_args()
    config = load_json(args.config)
    dataset_dir = args.dataset_dir or Path(config.get("dataset_dir", DEFAULT_DATASET_DIR))
    paths = dataset_paths(dataset_dir)

    raw_assets = read_jsonl(paths["raw_manifest"])
    if args.asset_id:
        raw_assets = [item for item in raw_assets if item.get("asset_id") == args.asset_id]
    if not raw_assets:
        raise SystemExit(f"no raw assets found in {paths['raw_manifest']}")

    generation = config.get("generation", {})
    model = os.environ.get("DOUBAO_IMAGE_MODEL", str(generation.get("model", "doubao-seededit-3-0-i2i-250628")))
    base_url = os.environ.get("ARK_BASE_URL", str(generation.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")))
    api_key = os.environ.get("ARK_API_KEY", "")
    candidates_per_asset = int(generation.get("candidates_per_asset", 2))
    size = str(generation.get("size", "adaptive"))
    price_usd = float(generation.get("price_usd_per_image", 0.03))

    if not args.mock and not api_key:
        raise SystemExit("ARK_API_KEY is required unless --mock is used")

    existing = read_jsonl(paths["candidates_manifest"])
    done_keys = existing_success_keys(existing)
    generated = 0

    for asset in raw_assets:
        asset_id = str(asset["asset_id"])
        category = str(asset["category"])
        input_path = dataset_dir / str(asset["image_path"])
        if not input_path.exists():
            print(f"skip missing source image: {input_path}")
            continue
        prompt = build_prompt(config, category)

        for candidate_index in range(1, candidates_per_asset + 1):
            if (asset_id, candidate_index) in done_keys:
                continue
            if args.limit is not None and generated >= args.limit:
                print(f"reached limit: {generated}")
                return

            candidate_id = f"{asset_id}_cand{candidate_index}"
            output = paths["candidate_images"] / category / f"{candidate_id}.png"
            record: dict[str, Any] = {
                "candidate_id": candidate_id,
                "asset_id": asset_id,
                "candidate_index": candidate_index,
                "category": category,
                "provider": "doubao",
                "model": model,
                "prompt": prompt,
                "source_image_path": asset["image_path"],
                "image_path": relative_to_dataset(output, dataset_dir),
                "status": "pending",
                "error": None,
                "cost_estimate": {"currency": "USD", "amount": price_usd},
                "created_at": utc_now(),
            }

            try:
                if args.mock:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(input_path, output)
                    provider_output_url = None
                else:
                    image_payload = (
                        str(asset.get("source_url"))
                        if args.image_input == "source-url"
                        else file_to_data_url(input_path)
                    )
                    last_exc: Exception | None = None
                    response = None
                    for attempt in range(args.retries + 1):
                        try:
                            response = call_doubao(
                                model=model,
                                base_url=base_url,
                                api_key=api_key,
                                prompt=prompt,
                                image_payload=image_payload,
                                size=size,
                            )
                            break
                        except Exception as exc:  # noqa: BLE001
                            last_exc = exc
                            if attempt >= args.retries:
                                raise
                            time.sleep(args.sleep_seconds * (attempt + 1))
                    if response is None:
                        raise RuntimeError(f"provider call failed: {last_exc}")
                    provider_output_url = download_output(first_response_data(response), output, timeout=args.timeout)

                width, height, mode = image_info(output)
                record.update(
                    {
                        "status": "success",
                        "provider_output_url": provider_output_url,
                        "sha256": sha256_file(output),
                        "width": width,
                        "height": height,
                        "mode": mode,
                    }
                )
                done_keys.add((asset_id, candidate_index))
                generated += 1
                print(f"generated {candidate_id}: {width}x{height}")
            except Exception as exc:  # noqa: BLE001
                record.update({"status": "failed", "error": repr(exc)})
                print(f"failed {candidate_id}: {exc}")
            append_jsonl(paths["candidates_manifest"], record)
            time.sleep(args.sleep_seconds)

    print(f"done, generated {generated} new candidates")


if __name__ == "__main__":
    main()
