#!/usr/bin/env python3
"""Run a local-only HTML review server for generated gongbi candidates."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from pipeline_common import (
    DEFAULT_DATASET_DIR,
    append_jsonl,
    dataset_paths,
    ensure_inside,
    read_jsonl,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    return parser.parse_args()


def latest_reviews(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for index, row in enumerate(rows):
        candidate_id = str(row.get("candidate_id", ""))
        if candidate_id:
            latest[candidate_id] = {**row, "_line": index}
    return latest


def build_items(dataset_dir: Path) -> list[dict]:
    paths = dataset_paths(dataset_dir)
    raw_assets = read_jsonl(paths["raw_manifest"])
    candidates = [
        item
        for item in read_jsonl(paths["candidates_manifest"])
        if item.get("status") == "success"
    ]
    reviews = latest_reviews(read_jsonl(paths["reviews_manifest"]))
    raw_by_id = {str(item.get("asset_id")): item for item in raw_assets}
    grouped: dict[str, list[dict]] = {}
    for candidate in candidates:
        grouped.setdefault(str(candidate.get("asset_id")), []).append(candidate)

    items = []
    for asset_id, asset in raw_by_id.items():
        asset_candidates = grouped.get(asset_id, [])
        if not asset_candidates:
            continue
        asset_candidates.sort(key=lambda item: int(item.get("candidate_index", 0)))
        reviewed = [
            reviews.get(str(candidate.get("candidate_id")), {}).get("decision")
            for candidate in asset_candidates
        ]
        if any(decision == "accept" for decision in reviewed):
            status = "accepted"
        elif reviewed and all(decision == "reject" for decision in reviewed):
            status = "rejected"
        elif any(decision == "maybe" for decision in reviewed):
            status = "maybe"
        elif any(reviewed):
            status = "reviewed"
        else:
            status = "pending"
        items.append(
            {
                "asset_id": asset_id,
                "category": asset.get("category"),
                "license": asset.get("license"),
                "source_url": asset.get("source_url"),
                "landing_url": asset.get("landing_url"),
                "title": asset.get("title"),
                "status": status,
                "source_image_url": f"/image?path={quote(str(asset.get('image_path', '')))}",
                "candidates": [
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "candidate_index": candidate.get("candidate_index"),
                        "image_url": f"/image?path={quote(str(candidate.get('image_path', '')))}",
                        "decision": reviews.get(str(candidate.get("candidate_id")), {}).get("decision"),
                        "reason": reviews.get(str(candidate.get("candidate_id")), {}).get("reason"),
                    }
                    for candidate in asset_candidates
                ],
            }
        )
    return items


def html_page() -> bytes:
    page = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gongbi Candidate Review</title>
  <style>
    :root {
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f4ef;
      color: #1f2528;
    }
    body { margin: 0; }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      padding: 12px 18px;
      background: #ffffff;
      border-bottom: 1px solid #d8d2c7;
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    button, select, input {
      font: inherit;
      border: 1px solid #aab2b0;
      background: #ffffff;
      border-radius: 6px;
      padding: 8px 10px;
    }
    button.primary { background: #245b4f; color: #fff; border-color: #245b4f; }
    button.warn { background: #8a3a2b; color: #fff; border-color: #8a3a2b; }
    main { padding: 18px; }
    .meta {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
      font-size: 14px;
    }
    .meta div {
      background: #fff;
      border: 1px solid #d8d2c7;
      border-radius: 8px;
      padding: 10px;
      overflow-wrap: anywhere;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      align-items: start;
    }
    figure {
      margin: 0;
      background: #fff;
      border: 1px solid #d8d2c7;
      border-radius: 8px;
      overflow: hidden;
    }
    figcaption {
      padding: 10px;
      border-bottom: 1px solid #e5dfd5;
      font-weight: 600;
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }
    img {
      display: block;
      width: 100%;
      height: min(64vh, 680px);
      object-fit: contain;
      background: #ece8df;
    }
    .actions {
      margin-top: 14px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .status { font-weight: 700; }
    @media (max-width: 900px) {
      .meta { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
      img { height: 52vh; }
    }
  </style>
</head>
<body>
  <header>
    <button id="prev">上一张</button>
    <button id="next">下一张</button>
    <select id="filter">
      <option value="pending">仅待审核</option>
      <option value="all">全部</option>
      <option value="accepted">已接受</option>
      <option value="rejected">已拒绝</option>
      <option value="maybe">待定</option>
    </select>
    <span id="progress"></span>
  </header>
  <main>
    <section class="meta" id="meta"></section>
    <section class="grid" id="grid"></section>
    <section class="actions" id="actions"></section>
  </main>
<script>
let items = [];
let filtered = [];
let index = 0;

async function loadItems() {
  const response = await fetch('/api/items');
  items = await response.json();
  applyFilter();
}

function applyFilter() {
  const value = document.getElementById('filter').value;
  filtered = value === 'all' ? items : items.filter(item => item.status === value);
  if (index >= filtered.length) index = Math.max(filtered.length - 1, 0);
  render();
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function render() {
  const progress = document.getElementById('progress');
  const meta = document.getElementById('meta');
  const grid = document.getElementById('grid');
  const actions = document.getElementById('actions');
  if (!filtered.length) {
    progress.textContent = `0 / ${items.length}`;
    meta.innerHTML = '';
    grid.innerHTML = '<p>没有符合筛选条件的候选。</p>';
    actions.innerHTML = '';
    return;
  }
  const item = filtered[index];
  progress.textContent = `${index + 1} / ${filtered.length}（总计 ${items.length}）`;
  meta.innerHTML = `
    <div><b>类别</b><br>${esc(item.category)}</div>
    <div><b>状态</b><br><span class="status">${esc(item.status)}</span></div>
    <div><b>授权</b><br>${esc(item.license)}</div>
    <div><b>来源</b><br><a target="_blank" href="${esc(item.landing_url || item.source_url)}">${esc(item.title || item.asset_id)}</a></div>
  `;
  const figures = [
    `<figure><figcaption>原图 <span>${esc(item.asset_id)}</span></figcaption><img src="${item.source_image_url}"></figure>`
  ];
  for (const candidate of item.candidates) {
    figures.push(`
      <figure>
        <figcaption>候选 ${candidate.candidate_index} <span>${esc(candidate.decision || 'pending')}</span></figcaption>
        <img src="${candidate.image_url}">
      </figure>
    `);
  }
  grid.innerHTML = figures.join('');
  const acceptButtons = item.candidates.map(candidate =>
    `<button class="primary" onclick="submitReview('${item.asset_id}', '${candidate.candidate_id}', 'accept')">Accept ${candidate.candidate_index}</button>`
  ).join('');
  actions.innerHTML = `
    ${acceptButtons}
    <button class="warn" onclick="submitReview('${item.asset_id}', null, 'reject')">Reject</button>
    <button onclick="submitReview('${item.asset_id}', null, 'maybe')">Maybe</button>
    <select id="reason">
      <option value="">无原因</option>
      <option value="identity_changed">身份/主体变化</option>
      <option value="composition_changed">构图偏移</option>
      <option value="weak_gongbi_style">工笔味不足</option>
      <option value="distorted_details">细节变形</option>
      <option value="ai_artifact">AI痕迹重</option>
      <option value="copyright_risk">授权风险</option>
    </select>
  `;
}

async function submitReview(assetId, candidateId, decision) {
  const reason = document.getElementById('reason')?.value || '';
  const response = await fetch('/api/review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({asset_id: assetId, candidate_id: candidateId, decision, reason})
  });
  if (!response.ok) {
    alert(await response.text());
    return;
  }
  await loadItems();
  if (filtered.length) index = Math.min(index, filtered.length - 1);
  render();
}

document.getElementById('prev').onclick = () => { index = Math.max(0, index - 1); render(); };
document.getElementById('next').onclick = () => { index = Math.min(filtered.length - 1, index + 1); render(); };
document.getElementById('filter').onchange = () => { index = 0; applyFilter(); };
loadItems();
</script>
</body>
</html>
"""
    return page.encode("utf-8")


class ReviewHandler(BaseHTTPRequestHandler):
    dataset_dir: Path

    def write_json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def write_text(self, text: str, status: int = 400) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            payload = html_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/api/items":
            self.write_json(build_items(self.dataset_dir))
            return
        if parsed.path == "/image":
            params = parse_qs(parsed.query)
            requested = params.get("path", [""])[0]
            try:
                path = ensure_inside(self.dataset_dir / requested, self.dataset_dir)
            except SystemExit:
                self.write_text("invalid image path", status=403)
                return
            if not path.exists() or not path.is_file():
                self.write_text("image not found", status=404)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            payload = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.write_text(f"not found: {html.escape(parsed.path)}", status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/review":
            self.write_text("not found", status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.write_text("invalid JSON")
            return
        decision = payload.get("decision")
        if decision not in {"accept", "reject", "maybe"}:
            self.write_text("decision must be accept, reject, or maybe")
            return

        asset_id = str(payload.get("asset_id", ""))
        candidate_id = payload.get("candidate_id")
        reason = str(payload.get("reason", ""))
        items = build_items(self.dataset_dir)
        item = next((entry for entry in items if entry.get("asset_id") == asset_id), None)
        if item is None:
            self.write_text("asset not found", status=404)
            return

        records = []
        for candidate in item["candidates"]:
            current_candidate_id = str(candidate["candidate_id"])
            if decision == "accept":
                current_decision = "accept" if current_candidate_id == candidate_id else "reject"
            else:
                current_decision = decision
            records.append(
                {
                    "asset_id": asset_id,
                    "candidate_id": current_candidate_id,
                    "decision": current_decision,
                    "reason": reason,
                    "reviewed_at": utc_now(),
                }
            )
        paths = dataset_paths(self.dataset_dir)
        for record in records:
            append_jsonl(paths["reviews_manifest"], record)
        self.write_json({"ok": True, "records": records})


def main() -> None:
    args = parse_args()
    ReviewHandler.dataset_dir = args.dataset_dir
    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    print(f"review server: http://{args.host}:{args.port}")
    print("for remote use: ssh -L 7860:127.0.0.1:7860 workspace.featurize.cn")
    server.serve_forever()


if __name__ == "__main__":
    main()
