#!/usr/bin/env python3
"""Collect licensed source images from Openverse into the raw asset manifest."""

from __future__ import annotations

import argparse
import email.utils
import os
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

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

DEFAULT_ENDPOINT = "https://api.openverse.org/v1/images/"
DEFAULT_AUTH_ENDPOINT = "https://api.openverse.org/v1/auth_tokens/token/"
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
JSON_CONTENT_TYPES = ("application/json", "application/vnd.oai.openapi")
CHUNK_SIZE = 1024 * 1024


class FetchError(Exception):
    """Network or HTTP failure with a stable failure kind for manifests."""

    def __init__(
        self,
        failure_kind: str,
        error: str,
        *,
        status_code: int | None = None,
        attempt: int = 0,
    ) -> None:
        super().__init__(error)
        self.failure_kind = failure_kind
        self.error = error
        self.status_code = status_code
        self.attempt = attempt


@dataclass
class CollectorStats:
    downloaded: int = 0
    skipped: int = 0
    api_failures: int = 0
    download_failures: int = 0
    size_failures: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/pipeline_gongbi_v1.json"))
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--max-downloads", type=int, default=None)
    parser.add_argument("--mock-response", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=30)
    return parser.parse_args()


def response_snippet(response: Any, max_length: int = 500) -> str:
    try:
        text = response.text
    except Exception as exc:  # noqa: BLE001
        return f"<failed to read response text: {exc}>"
    return " ".join(text[:max_length].split())


def is_cloudflare_challenge(response: Any) -> bool:
    content_type = response.headers.get("Content-Type", "").lower()
    if response.status_code != 403 or "text/html" not in content_type:
        return False
    text = response_snippet(response, 2000).lower()
    return "just a moment" in text or "cloudflare" in text or "challenge-platform" in text


def retry_after_seconds(headers: Any) -> float | None:
    value = headers.get("Retry-After")
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        retry_at = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, retry_at.timestamp() - time.time())


def rate_limit_exhausted(headers: Any) -> bool:
    for key, value in headers.items():
        if key.lower().startswith("x-ratelimit-available-") and str(value).strip() == "0":
            return True
    return False


def exception_kind(exc: Exception) -> str:
    name = exc.__class__.__name__
    if name == "Timeout" or "Timeout" in name:
        return "timeout"
    if name == "SSLError" or "SSL" in name:
        return "ssl_error"
    if name == "ChunkedEncodingError":
        return "chunked_encoding_error"
    if name == "ConnectionError":
        return "connection_error"
    return "request_error"


class OpenverseClient:
    def __init__(
        self,
        *,
        endpoint: str,
        auth_endpoint: str,
        user_agent: str,
        timeout: int,
        request_retries: int,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
    ) -> None:
        import requests

        self.requests = requests
        self.endpoint = endpoint
        self.auth_endpoint = auth_endpoint
        self.timeout = timeout
        self.request_retries = max(0, request_retries)
        self.backoff_base_seconds = max(0.1, backoff_base_seconds)
        self.backoff_max_seconds = max(self.backoff_base_seconds, backoff_max_seconds)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    @property
    def max_attempts(self) -> int:
        return self.request_retries + 1

    def configure_auth(self) -> None:
        token = os.environ.get("OPENVERSE_ACCESS_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
            print("Openverse auth: using OPENVERSE_ACCESS_TOKEN")
            return

        client_id = os.environ.get("OPENVERSE_CLIENT_ID")
        client_secret = os.environ.get("OPENVERSE_CLIENT_SECRET")
        if not client_id or not client_secret:
            print("Openverse auth: anonymous mode")
            return

        try:
            response = self._request(
                "POST",
                self.auth_endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            data = self._json_response(response)
        except FetchError as exc:
            print(f"Openverse auth failed, falling back to anonymous: {exc.failure_kind}: {exc.error}")
            return

        access_token = data.get("access_token")
        if not access_token:
            print("Openverse auth failed, falling back to anonymous: missing access_token")
            return
        self.session.headers["Authorization"] = f"Bearer {access_token}"
        print("Openverse auth: using OPENVERSE_CLIENT_ID/SECRET")

    def search(
        self,
        *,
        query: str,
        license_filter: str,
        page: int,
        page_size: int,
        mature: bool,
        size: str | None,
        source: str | None,
        excluded_source: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": query,
            "license": license_filter,
            "page": page,
            "page_size": page_size,
            "mature": "true" if mature else "false",
        }
        if size:
            params["size"] = size
        if source:
            params["source"] = source
        if excluded_source:
            params["excluded_source"] = excluded_source

        response = self._request(
            "GET",
            self.endpoint,
            params=params,
            headers={"Accept": "application/json"},
        )
        data = self._json_response(response)
        if not isinstance(data, dict):
            raise FetchError("invalid_json", "Openverse response must be a JSON object")
        return data

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        last_error: FetchError | None = None
        for attempt in range(1, self.max_attempts + 1):
            response = None
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if is_cloudflare_challenge(response):
                    raise FetchError(
                        "cloudflare_challenge",
                        "Openverse returned a Cloudflare challenge page",
                        status_code=response.status_code,
                        attempt=attempt,
                    )
                if response.status_code in RETRYABLE_HTTP_STATUS:
                    failure_kind = "rate_limited" if response.status_code == 429 else "server_error"
                    raise FetchError(
                        failure_kind,
                        response_snippet(response),
                        status_code=response.status_code,
                        attempt=attempt,
                    )
                if response.status_code >= 400:
                    raise FetchError(
                        "http_status",
                        response_snippet(response),
                        status_code=response.status_code,
                        attempt=attempt,
                    )
                return response
            except FetchError as exc:
                last_error = exc
                retryable = exc.failure_kind in {"rate_limited", "server_error"}
                if not retryable or attempt >= self.max_attempts:
                    raise exc
            except self.requests.exceptions.RequestException as exc:
                last_error = FetchError(exception_kind(exc), str(exc), attempt=attempt)
                if attempt >= self.max_attempts:
                    raise last_error from exc

            self._sleep_before_retry(attempt=attempt, response=response, error=last_error)

        if last_error:
            raise last_error
        raise FetchError("request_error", f"request failed without response: {url}")

    def _json_response(self, response: Any) -> dict[str, Any]:
        content_type = response.headers.get("Content-Type", "").lower()
        if not any(content_type.startswith(item) for item in JSON_CONTENT_TYPES):
            raise FetchError(
                "invalid_content_type",
                f"expected JSON, got {content_type or '<missing>'}: {response_snippet(response)}",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise FetchError("invalid_json", str(exc), status_code=response.status_code) from exc
        if not isinstance(data, dict):
            raise FetchError("invalid_json", "response JSON must be an object", status_code=response.status_code)
        return data

    def _sleep_before_retry(self, *, attempt: int, response: Any | None, error: FetchError | None) -> None:
        delay = retry_after_seconds(response.headers) if response is not None else None
        if delay is None:
            jitter = random.uniform(0, min(1.0, self.backoff_base_seconds))
            delay = self.backoff_base_seconds * (2 ** (attempt - 1)) + jitter
        delay = min(delay, self.backoff_max_seconds)

        if response is not None and rate_limit_exhausted(response.headers):
            print("Openverse rate limit appears exhausted; backing off before retry")
        reason = error.failure_kind if error else "request_error"
        print(f"retry {attempt}/{self.max_attempts - 1} after {delay:.1f}s: {reason}")
        time.sleep(delay)


def load_mock_page(path: Path, page: int) -> tuple[list[dict[str, Any]], int]:
    data = load_json(path)
    pages = data.get("pages")
    if isinstance(pages, dict):
        page_data = pages.get(str(page), {})
        if not isinstance(page_data, dict):
            raise SystemExit("mock page entry must be a JSON object")
        results = page_data.get("results", page_data.get("items", []))
        page_count = int(data.get("page_count") or len(pages))
    elif page == 1:
        results = data.get("results", data.get("items", []))
        page_count = int(data.get("page_count") or 1)
    else:
        results = []
        page_count = int(data.get("page_count") or 1)

    if not isinstance(results, list):
        raise SystemExit("mock response must contain a results list")
    return [item for item in results if isinstance(item, dict)], page_count


def copy_local_image(url: str, output: Path) -> bool:
    parsed = urlparse(url)
    source: Path | None = None
    if parsed.scheme == "file":
        source = Path(unquote(parsed.path))
    elif parsed.scheme == "" and Path(url).exists():
        source = Path(url)
    if source is None:
        return False

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_name(f"{output.name}.part")
    try:
        shutil.copy2(source, tmp_output)
        tmp_output.replace(output)
    finally:
        if tmp_output.exists():
            tmp_output.unlink()
    return True


def download_image(
    *,
    session: Any,
    url: str,
    output: Path,
    timeout: int,
    download_retries: int,
    backoff_base_seconds: float,
    backoff_max_seconds: float,
) -> None:
    if copy_local_image(url, output):
        return

    import requests

    max_attempts = max(1, download_retries + 1)
    tmp_output = output.with_name(f"{output.name}.part")
    headers = {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    last_error: FetchError | None = None

    for attempt in range(1, max_attempts + 1):
        response = None
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with session.get(url, headers=headers, timeout=timeout, stream=True) as response:
                if is_cloudflare_challenge(response):
                    raise FetchError(
                        "cloudflare_challenge",
                        "image source returned a Cloudflare challenge page",
                        status_code=response.status_code,
                        attempt=attempt,
                    )
                if response.status_code in RETRYABLE_HTTP_STATUS:
                    failure_kind = "rate_limited" if response.status_code == 429 else "server_error"
                    raise FetchError(
                        failure_kind,
                        response_snippet(response),
                        status_code=response.status_code,
                        attempt=attempt,
                    )
                if response.status_code >= 400:
                    raise FetchError(
                        "http_status",
                        response_snippet(response),
                        status_code=response.status_code,
                        attempt=attempt,
                    )

                with tmp_output.open("wb") as f:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                if not tmp_output.exists() or tmp_output.stat().st_size == 0:
                    raise FetchError("empty_download", "downloaded file is empty", attempt=attempt)
                tmp_output.replace(output)
                return
        except FetchError as exc:
            last_error = exc
            retryable = exc.failure_kind in {"rate_limited", "server_error"}
            if not retryable or attempt >= max_attempts:
                raise exc
        except requests.exceptions.RequestException as exc:
            last_error = FetchError(exception_kind(exc), str(exc), attempt=attempt)
            if attempt >= max_attempts:
                raise last_error from exc
        finally:
            if tmp_output.exists():
                tmp_output.unlink()

        delay = retry_after_seconds(response.headers) if response is not None else None
        if delay is None:
            jitter = random.uniform(0, min(1.0, backoff_base_seconds))
            delay = backoff_base_seconds * (2 ** (attempt - 1)) + jitter
        delay = min(delay, backoff_max_seconds)
        reason = last_error.failure_kind if last_error else "download_error"
        print(f"download retry {attempt}/{max_attempts - 1} after {delay:.1f}s: {reason}")
        time.sleep(delay)

    if last_error:
        raise last_error
    raise FetchError("download_error", f"failed to download {url}")


def result_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("foreign_landing_url") or item.get("url") or item.get("identifier"))


def candidate_urls(item: dict[str, Any]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for download_kind, key in (("source", "url"), ("thumbnail", "thumbnail")):
        value = item.get(key)
        if not value:
            continue
        url = str(value).strip()
        if not url or url in seen:
            continue
        candidates.append((download_kind, url))
        seen.add(url)
    return candidates


def dimensions_pass(width: int, height: int, min_long_edge: int, min_short_edge: int) -> bool:
    return max(width, height) >= min_long_edge and min(width, height) >= min_short_edge


def dimension_config(config: dict[str, Any]) -> tuple[int, int]:
    if "min_long_edge" in config or "min_short_edge" in config:
        return int(config.get("min_long_edge", 1024)), int(config.get("min_short_edge", 768))

    min_width = int(config.get("min_width", 1024))
    min_height = int(config.get("min_height", 768))
    return max(min_width, min_height), min(min_width, min_height)


def write_failure(
    failures_manifest: Path,
    *,
    category: str,
    query: str,
    page: int,
    openverse_id: str | None,
    url: str | None,
    download_kind: str,
    failure_kind: str,
    status_code: int | None = None,
    error: str = "",
    attempt: int = 0,
) -> None:
    append_jsonl(
        failures_manifest,
        {
            "category": category,
            "query": query,
            "page": page,
            "openverse_id": openverse_id,
            "url": url,
            "download_kind": download_kind,
            "failure_kind": failure_kind,
            "status_code": status_code,
            "error": error,
            "attempt": attempt,
            "created_at": utc_now(),
        },
    )


def existing_indexes(existing: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str], dict[str, int]]:
    seen_urls: set[str] = set()
    seen_sha: set[str] = set()
    seen_openverse_ids: set[str] = set()
    existing_by_category: dict[str, int] = {}
    for item in existing:
        for key in ("source_url", "download_url"):
            value = item.get(key)
            if value:
                seen_urls.add(str(value))
        digest = item.get("sha256")
        if digest:
            seen_sha.add(str(digest))
        openverse_id = item.get("openverse_id")
        if openverse_id:
            seen_openverse_ids.add(str(openverse_id))
        category = str(item.get("category", ""))
        existing_by_category[category] = existing_by_category.get(category, 0) + 1
    return seen_urls, seen_sha, seen_openverse_ids, existing_by_category


def collect_item(
    *,
    item: dict[str, Any],
    category: str,
    query: str,
    page: int,
    dataset_dir: Path,
    raw_manifest: Path,
    raw_images_dir: Path,
    failures_manifest: Path,
    client: OpenverseClient,
    min_long_edge: int,
    min_short_edge: int,
    download_retries: int,
    backoff_base_seconds: float,
    backoff_max_seconds: float,
    seen_urls: set[str],
    seen_sha: set[str],
    seen_openverse_ids: set[str],
    stats: CollectorStats,
) -> bool:
    openverse_id = result_id(item)
    candidates = candidate_urls(item)
    if not candidates:
        stats.skipped += 1
        return False
    if openverse_id in seen_openverse_ids:
        stats.skipped += 1
        return False
    source_url = str(item.get("url") or "")
    if source_url and source_url in seen_urls:
        stats.skipped += 1
        return False

    declared_width = int(item.get("width") or 0)
    declared_height = int(item.get("height") or 0)
    if declared_width and declared_height and not dimensions_pass(
        declared_width,
        declared_height,
        min_long_edge,
        min_short_edge,
    ):
        stats.size_failures += 1
        stats.skipped += 1
        write_failure(
            failures_manifest,
            category=category,
            query=query,
            page=page,
            openverse_id=openverse_id,
            url=str(item.get("url") or item.get("thumbnail") or ""),
            download_kind="metadata",
            failure_kind="size_filter_declared",
            error=f"declared size {declared_width}x{declared_height}",
        )
        return False

    asset_id = f"{category}_{slugify(openverse_id, 32)}"
    if not asset_id.strip("_"):
        asset_id = f"{category}_{int(time.time())}"

    for download_kind, download_url in candidates:
        suffix = safe_suffix(download_url)
        output = raw_images_dir / category / f"{asset_id}{suffix}"
        if output.exists():
            output = raw_images_dir / category / f"{asset_id}_{int(time.time())}{suffix}"

        try:
            download_image(
                session=client.session,
                url=download_url,
                output=output,
                timeout=client.timeout,
                download_retries=download_retries,
                backoff_base_seconds=backoff_base_seconds,
                backoff_max_seconds=backoff_max_seconds,
            )
            width, height, mode = image_info(output)
        except Exception as exc:  # noqa: BLE001
            if output.exists():
                output.unlink()
            if isinstance(exc, FetchError):
                failure_kind = exc.failure_kind
                status_code = exc.status_code
                attempt = exc.attempt
                error = exc.error
            else:
                failure_kind = exception_kind(exc)
                status_code = None
                attempt = 0
                error = str(exc)
            stats.download_failures += 1
            write_failure(
                failures_manifest,
                category=category,
                query=query,
                page=page,
                openverse_id=openverse_id,
                url=download_url,
                download_kind=download_kind,
                failure_kind=failure_kind,
                status_code=status_code,
                error=error,
                attempt=attempt,
            )
            print(f"skip failed {download_kind} download {download_url}: {failure_kind}: {error}")
            continue

        if not dimensions_pass(width, height, min_long_edge, min_short_edge):
            output.unlink()
            stats.size_failures += 1
            stats.skipped += 1
            write_failure(
                failures_manifest,
                category=category,
                query=query,
                page=page,
                openverse_id=openverse_id,
                url=download_url,
                download_kind=download_kind,
                failure_kind="size_filter_actual",
                error=f"downloaded size {width}x{height}",
            )
            return False

        digest = sha256_file(output)
        if digest in seen_sha:
            output.unlink()
            stats.skipped += 1
            return False

        record = {
            "asset_id": asset_id,
            "category": category,
            "source": "openverse",
            "source_url": source_url or download_url,
            "download_url": download_url,
            "download_kind": download_kind,
            "thumbnail_url": item.get("thumbnail"),
            "landing_url": item.get("foreign_landing_url"),
            "openverse_id": openverse_id,
            "provider": item.get("provider"),
            "openverse_source": item.get("source"),
            "license": item.get("license"),
            "license_version": item.get("license_version"),
            "license_url": item.get("license_url"),
            "creator": item.get("creator"),
            "creator_url": item.get("creator_url"),
            "title": item.get("title"),
            "filetype": item.get("filetype"),
            "filesize": item.get("filesize"),
            "image_path": relative_to_dataset(output, dataset_dir),
            "sha256": digest,
            "width": width,
            "height": height,
            "mode": mode,
            "query": query,
            "collected_at": utc_now(),
            "openverse_item": item,
        }
        append_jsonl(raw_manifest, record)
        if source_url:
            seen_urls.add(source_url)
        seen_urls.add(download_url)
        seen_sha.add(digest)
        seen_openverse_ids.add(openverse_id)
        stats.downloaded += 1
        print(f"collected {asset_id}: {width}x{height} via {download_kind}")
        return True

    stats.skipped += 1
    return False


def main() -> None:
    args = parse_args()
    config = load_json(args.config)
    dataset_dir = args.dataset_dir or Path(config.get("dataset_dir", DEFAULT_DATASET_DIR))
    paths = dataset_paths(dataset_dir)
    raw_manifest = paths["raw_manifest"]
    failures_manifest = paths.get("openverse_failures") or paths["manifests"] / "openverse_failures.jsonl"
    raw_images_dir = paths["raw_images"]

    min_long_edge, min_short_edge = dimension_config(config)
    openverse = config.get("openverse", {})
    endpoint = str(openverse.get("endpoint", DEFAULT_ENDPOINT))
    auth_endpoint = str(openverse.get("auth_endpoint", DEFAULT_AUTH_ENDPOINT))
    license_filter = str(openverse.get("license", "cc0,pdm,by"))
    page_size = int(openverse.get("page_size", 20))
    sleep_seconds = float(openverse.get("sleep_seconds", 1.0))
    user_agent = str(openverse.get("user_agent", "qwen-gongbi-lora-dataset/0.1"))
    size = openverse.get("size", "large")
    size_filter = str(size) if size else None
    source = openverse.get("source")
    source_filter = str(source) if source else None
    excluded_source = openverse.get("excluded_source")
    excluded_source_filter = str(excluded_source) if excluded_source else None
    request_retries = int(openverse.get("request_retries", 4))
    download_retries = int(openverse.get("download_retries", 3))
    backoff_base_seconds = float(openverse.get("backoff_base_seconds", 1.0))
    backoff_max_seconds = float(openverse.get("backoff_max_seconds", 60.0))
    max_pages_per_query = int(openverse.get("max_pages_per_query", 50))

    existing = read_jsonl(raw_manifest)
    seen_urls, seen_sha, seen_openverse_ids, existing_by_category = existing_indexes(existing)
    stats = CollectorStats()

    client = OpenverseClient(
        endpoint=endpoint,
        auth_endpoint=auth_endpoint,
        user_agent=user_agent,
        timeout=args.timeout,
        request_retries=request_retries,
        backoff_base_seconds=backoff_base_seconds,
        backoff_max_seconds=backoff_max_seconds,
    )
    if not args.mock_response:
        client.configure_auth()

    for category_cfg in config.get("categories", []):
        category = str(category_cfg["name"])
        target_count = int(category_cfg.get("target_count", 0))
        already = existing_by_category.get(category, 0)
        remaining = max(target_count - already, 0)
        if args.max_downloads is not None:
            remaining = min(remaining, args.max_downloads - stats.downloaded)
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
                if args.max_downloads is not None and stats.downloaded >= args.max_downloads:
                    print(f"reached max downloads: {stats.downloaded}")
                    print_summary(stats)
                    return
                if max_pages_per_query > 0 and page > max_pages_per_query:
                    break

                if args.mock_response:
                    results, page_count = load_mock_page(args.mock_response, page)
                else:
                    try:
                        data = client.search(
                            query=query,
                            license_filter=license_filter,
                            page=page,
                            page_size=page_size,
                            mature=False,
                            size=size_filter,
                            source=source_filter,
                            excluded_source=excluded_source_filter,
                        )
                    except FetchError as exc:
                        stats.api_failures += 1
                        write_failure(
                            failures_manifest,
                            category=category,
                            query=query,
                            page=page,
                            openverse_id=None,
                            url=endpoint,
                            download_kind="api",
                            failure_kind=exc.failure_kind,
                            status_code=exc.status_code,
                            error=exc.error,
                            attempt=exc.attempt,
                        )
                        print(f"skip query page {query!r} #{page}: {exc.failure_kind}: {exc.error}")
                        break

                    results = data.get("results", [])
                    if not isinstance(results, list):
                        stats.api_failures += 1
                        write_failure(
                            failures_manifest,
                            category=category,
                            query=query,
                            page=page,
                            openverse_id=None,
                            url=endpoint,
                            download_kind="api",
                            failure_kind="invalid_json",
                            error="Openverse response field results must be a list",
                        )
                        break
                    page_count = int(data.get("page_count") or 0)

                if not results:
                    break

                for item in results:
                    if remaining <= 0:
                        break
                    if not isinstance(item, dict):
                        stats.skipped += 1
                        continue
                    collected = collect_item(
                        item=item,
                        category=category,
                        query=query,
                        page=page,
                        dataset_dir=dataset_dir,
                        raw_manifest=raw_manifest,
                        raw_images_dir=raw_images_dir,
                        failures_manifest=failures_manifest,
                        client=client,
                        min_long_edge=min_long_edge,
                        min_short_edge=min_short_edge,
                        download_retries=download_retries,
                        backoff_base_seconds=backoff_base_seconds,
                        backoff_max_seconds=backoff_max_seconds,
                        seen_urls=seen_urls,
                        seen_sha=seen_sha,
                        seen_openverse_ids=seen_openverse_ids,
                        stats=stats,
                    )
                    if collected:
                        remaining -= 1

                if args.mock_response or (page_count and page >= page_count):
                    break
                page += 1
                if remaining > 0:
                    time.sleep(sleep_seconds)

            if remaining <= 0:
                break

    print_summary(stats)


def print_summary(stats: CollectorStats) -> None:
    print(
        "done: "
        f"downloaded={stats.downloaded}, "
        f"skipped={stats.skipped}, "
        f"api_failures={stats.api_failures}, "
        f"download_failures={stats.download_failures}, "
        f"size_failures={stats.size_failures}"
    )


if __name__ == "__main__":
    main()
