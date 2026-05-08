#!/usr/bin/env python3
"""Collect licensed source images from Openverse into the raw asset manifest."""

from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

from pipeline_common import (
    DEFAULT_DATASET_DIR,
    append_jsonl,
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
    parser.add_argument("--max-downloads", type=int, default=None)
    parser.add_argument("--mock-response", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=30)
    return parser.parse_args()


def request_openverse(
    endpoint: str,
    query: str,
    license_filter: str,
    page: int,
    page_size: int,
    user_agent: str,
    timeout: int,
) -> dict[str, Any]:
    import requests

    headers = {"User-Agent": user_agent}
    token = os.environ.get("OPENVERSE_ACCESS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(
        endpoint,
        params={
            "q": query,
            "license": license_filter,
            "page": page,
            "page_size": page_size,
            "mature": "false",
        },
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise SystemExit("Openverse response must be a JSON object")
    return data


def load_mock_results(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    results = data.get("results", data.get("items", []))
    if not isinstance(results, list):
        raise SystemExit("mock response must contain a results list")
    return [item for item in results if isinstance(item, dict)]


def download_image(url: str, output: Path, user_agent: str, timeout: int) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        source = Path(unquote(parsed.path))
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
        return
    if parsed.scheme == "" and Path(url).exists():
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(url), output)
        return

    import requests

    headers = {"User-Agent": user_agent}
    with requests.get(url, headers=headers, timeout=timeout, stream=True) as response:
        response.raise_for_status()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def result_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("foreign_landing_url") or item.get("url") or item.get("identifier"))


def main() -> None:
    args = parse_args()
    config = load_json(args.config)
    dataset_dir = args.dataset_dir or Path(config.get("dataset_dir", DEFAULT_DATASET_DIR))
    paths = dataset_paths(dataset_dir)
    raw_manifest = paths["raw_manifest"]
    raw_images_dir = paths["raw_images"]

    min_width = int(config.get("min_width", 1024))
    min_height = int(config.get("min_height", 1024))
    openverse = config.get("openverse", {})
    endpoint = str(openverse.get("endpoint", "https://api.openverse.org/v1/images/"))
    license_filter = str(openverse.get("license", "cc0,pdm,by"))
    page_size = int(openverse.get("page_size", 20))
    sleep_seconds = float(openverse.get("sleep_seconds", 1.0))
    user_agent = str(openverse.get("user_agent", "qwen-gongbi-lora-dataset/0.1"))

    existing = read_jsonl(raw_manifest)
    seen_sources = {str(item.get("source_url")) for item in existing}
    seen_sha = {str(item.get("sha256")) for item in existing}
    existing_by_category: dict[str, int] = {}
    for item in existing:
        category = str(item.get("category", ""))
        existing_by_category[category] = existing_by_category.get(category, 0) + 1

    downloaded_total = 0
    for category_cfg in config.get("categories", []):
        category = str(category_cfg["name"])
        target_count = int(category_cfg.get("target_count", 0))
        already = existing_by_category.get(category, 0)
        remaining = max(target_count - already, 0)
        if args.max_downloads is not None:
            remaining = min(remaining, args.max_downloads - downloaded_total)
        if remaining <= 0:
            continue

        queries = [str(query) for query in category_cfg.get("queries", []) if str(query).strip()]
        if not queries:
            print(f"skip {category}: no queries")
            continue

        print(f"collecting {category}: need {remaining}, already {already}")
        for query in queries:
            page = 1
            while remaining > 0:
                if args.mock_response:
                    results = load_mock_results(args.mock_response)
                else:
                    data = request_openverse(
                        endpoint=endpoint,
                        query=query,
                        license_filter=license_filter,
                        page=page,
                        page_size=page_size,
                        user_agent=user_agent,
                        timeout=args.timeout,
                    )
                    results = data.get("results", [])
                    if not isinstance(results, list):
                        raise SystemExit("Openverse response field results must be a list")
                if not results:
                    break

                for item in results:
                    if remaining <= 0:
                        break
                    if not isinstance(item, dict):
                        continue
                    image_url = item.get("url") or item.get("thumbnail")
                    if not image_url:
                        continue
                    image_url = str(image_url)
                    if image_url in seen_sources:
                        continue

                    declared_width = int(item.get("width") or 0)
                    declared_height = int(item.get("height") or 0)
                    if declared_width and declared_height and (
                        declared_width < min_width or declared_height < min_height
                    ):
                        continue

                    openverse_id = result_id(item)
                    asset_id = f"{category}_{slugify(openverse_id, 32)}"
                    suffix = safe_suffix(image_url)
                    output = raw_images_dir / category / f"{asset_id}{suffix}"
                    if output.exists():
                        asset_id = f"{asset_id}_{int(time.time())}"
                        output = raw_images_dir / category / f"{asset_id}{suffix}"

                    try:
                        download_image(image_url, output, user_agent=user_agent, timeout=args.timeout)
                        width, height, mode = image_info(output)
                    except Exception as exc:  # noqa: BLE001
                        print(f"skip failed download {image_url}: {exc}")
                        if output.exists():
                            output.unlink()
                        continue

                    if width < min_width or height < min_height:
                        output.unlink()
                        continue

                    digest = sha256_file(output)
                    if digest in seen_sha:
                        output.unlink()
                        continue

                    record = {
                        "asset_id": asset_id,
                        "category": category,
                        "source": "openverse",
                        "source_url": image_url,
                        "landing_url": item.get("foreign_landing_url"),
                        "license": item.get("license"),
                        "license_version": item.get("license_version"),
                        "creator": item.get("creator"),
                        "title": item.get("title"),
                        "image_path": relative_to_dataset(output, dataset_dir),
                        "sha256": digest,
                        "width": width,
                        "height": height,
                        "mode": mode,
                        "query": query,
                        "collected_at": utc_now(),
                    }
                    append_jsonl(raw_manifest, record)
                    seen_sources.add(image_url)
                    seen_sha.add(digest)
                    remaining -= 1
                    downloaded_total += 1
                    print(f"collected {asset_id}: {width}x{height}")

                    if args.max_downloads is not None and downloaded_total >= args.max_downloads:
                        print(f"reached max downloads: {downloaded_total}")
                        return

                if args.mock_response:
                    break
                page += 1
                time.sleep(sleep_seconds)

            if remaining <= 0:
                break

    print(f"done, downloaded {downloaded_total} new assets")


if __name__ == "__main__":
    main()
