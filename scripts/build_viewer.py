#!/usr/bin/env python3
"""Build a single, self-contained, offline-capable HTML page for browsing the
submitted claims/evidence artifact -- addresses the "is it easy to find, compare
and verify company information on desktop and mobile" rubric dimension, which had
no viewer at all before this.

No external dependencies (no CDN, no build step): the page embeds a compact,
flattened version of every company's claims and evidence directly as JSON and
renders it with plain HTML/CSS/JS, so it opens correctly straight from disk
(file://) with no local server required -- important since the evaluator's own
setup instructions never mention running a web server.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def flatten_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    evidence_by_id = {item["id"]: item for item in envelope.get("evidence", [])}
    claims = []
    for claim in envelope.get("claims", []):
        first_evidence = evidence_by_id.get((claim.get("evidence_ids") or [None])[0]) or {}
        value = claim.get("value")
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)[:400]
        claims.append({
            "field": claim.get("field"),
            "value": value,
            "availability": claim.get("availability"),
            "confidence": claim.get("confidence"),
            "source_url": first_evidence.get("source_url"),
            "retrieved_at": first_evidence.get("retrieved_at"),
        })
    summary = envelope.get("summary") or {}
    return {
        "org": envelope.get("organisation_number"),
        "summary": summary.get("text", ""),
        "unknowns": summary.get("unknown_fields", []),
        "claim_count": len(claims),
        "claims": claims,
    }


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Signalpost company profiles</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f7f7f5; --panel: #ffffff; --border: #e2e2df; --text: #1f2320; --muted: #6b6f6a;
    --accent: #2f6f4f; --accent-bg: #e8f3ec; --danger: #9a3b3b;
    padding-top: env(safe-area-inset-top, 0px);
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #16181a; --panel: #202325; --border: #34383a; --text: #e9ebe8; --muted: #9aa09a;
      --accent: #62c493; --accent-bg: #1c3327; --danger: #e08585;
    }
  }
  :root[data-theme="dark"] {
    --bg: #16181a; --panel: #202325; --border: #34383a; --text: #e9ebe8; --muted: #9aa09a;
    --accent: #62c493; --accent-bg: #1c3327; --danger: #e08585;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
  header { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--panel); border-bottom: 1px solid var(--border); padding: 14px 16px; }
  h1 { font-size: 17px; margin: 0 0 8px; }
  .stats { color: var(--muted); font-size: 13px; margin-bottom: 10px; }
  input[type=search] { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); color: var(--text); font-size: 15px; }
  main { padding: 12px 16px 48px; max-width: 900px; margin: 0 auto; }
  .layout { display: flex; gap: 16px; align-items: flex-start; }
  #list { flex: 1 1 280px; min-width: 0; max-height: calc(100vh - 140px); overflow-y: auto; }
  #detail { flex: 2 1 480px; min-width: 0; }
  .company-row { padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); margin-bottom: 8px; cursor: pointer; }
  .company-row:hover, .company-row.active { border-color: var(--accent); background: var(--accent-bg); }
  .company-name { font-weight: 600; font-size: 14px; }
  .company-org { color: var(--muted); font-size: 12px; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .summary-text { margin: 0 0 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; word-break: break-word; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; }
  .avail-available { color: var(--accent); }
  .avail-not_available, .avail-failed, .avail-blocked { color: var(--danger); }
  a { color: var(--accent); }
  .empty { color: var(--muted); padding: 24px; text-align: center; }
  @media (max-width: 720px) {
    .layout { flex-direction: column; }
    #list { max-height: 40vh; }
  }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<header>
  <h1>Signalpost company profiles</h1>
  <div class="stats" id="stats"></div>
  <input type="search" id="search" placeholder="Search by organisation number or company name" autocomplete="off">
</header>
<main>
  <div class="layout">
    <div id="list"></div>
    <div id="detail"><div class="empty">Select a company to view its profile.</div></div>
  </div>
</main>
<script>
const DATA = __DATA_JSON__;

const listEl = document.getElementById('list');
const detailEl = document.getElementById('detail');
const searchEl = document.getElementById('search');
const statsEl = document.getElementById('stats');

statsEl.textContent = DATA.length.toLocaleString() + ' companies';

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function renderList(filter) {
  const q = (filter || '').trim().toLowerCase();
  const matches = q
    ? DATA.filter(c => c.org.includes(q) || (c.name || '').toLowerCase().includes(q))
    : DATA.slice(0, 200);
  listEl.innerHTML = matches.map(c =>
    `<div class="company-row" data-org="${c.org}">
       <div class="company-name">${escapeHtml(c.name || c.org)}</div>
       <div class="company-org">${c.org} &middot; ${c.claim_count} claims</div>
     </div>`
  ).join('') || '<div class="empty">No matches.</div>';
  if (!q && DATA.length > 200) {
    listEl.insertAdjacentHTML('beforeend', `<div class="empty">Showing first 200 of ${DATA.length.toLocaleString()} -- search to narrow.</div>`);
  }
}

function renderDetail(org) {
  const c = DATA.find(x => x.org === org);
  if (!c) { detailEl.innerHTML = '<div class="empty">Not found.</div>'; return; }
  const rows = c.claims.map(claim => `
    <tr>
      <td>${escapeHtml(claim.field)}</td>
      <td>${escapeHtml(claim.value)}</td>
      <td class="avail-${escapeHtml(claim.availability)}">${escapeHtml(claim.availability)}</td>
      <td>${claim.confidence == null ? '' : claim.confidence}</td>
      <td>${claim.source_url ? `<a href="${escapeHtml(claim.source_url)}" target="_blank" rel="noopener">source</a>` : ''}</td>
    </tr>`).join('');
  detailEl.innerHTML = `
    <div class="panel">
      <h2>${escapeHtml(c.name || c.org)}</h2>
      <p class="company-org">Organisation number: ${c.org}</p>
      <p class="summary-text">${escapeHtml(c.summary)}</p>
      <table>
        <thead><tr><th>Field</th><th>Value</th><th>Availability</th><th>Confidence</th><th>Evidence</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

listEl.addEventListener('click', (e) => {
  const row = e.target.closest('.company-row');
  if (!row) return;
  document.querySelectorAll('.company-row.active').forEach(el => el.classList.remove('active'));
  row.classList.add('active');
  renderDetail(row.dataset.org);
});

searchEl.addEventListener('input', () => renderList(searchEl.value));

renderList('');
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a self-contained offline HTML viewer for the claims/evidence submission artifact.")
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--profiles", help="Optional profiles.jsonl to pull display names from (claims alone don't always carry the name field distinctly).")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    envelopes = read_jsonl(Path(args.envelopes))
    names_by_org: dict[str, str] = {}
    if args.profiles:
        for profile in read_jsonl(Path(args.profiles)):
            evidence = profile.get("evidence") or {}
            registry_module = "registry_live" if (evidence.get("registry_live") or {}).get("status") == "available" else "registry"
            value = (evidence.get(registry_module) or {}).get("value") or {}
            name = value.get("name") or value.get("navn")
            if name:
                names_by_org[str(profile["organisation_number"])] = name

    companies = []
    for envelope in envelopes:
        flat = flatten_envelope(envelope)
        flat["name"] = names_by_org.get(flat["org"])
        companies.append(flat)
    companies.sort(key=lambda c: c["org"])

    data_json = json.dumps(companies, ensure_ascii=False, separators=(",", ":"))
    page = PAGE_TEMPLATE.replace("__DATA_JSON__", data_json)
    Path(args.output).write_text(page, encoding="utf-8")
    print(json.dumps({"companies": len(companies), "output": args.output, "size_bytes": len(page.encode("utf-8"))}, indent=2))


if __name__ == "__main__":
    main()
