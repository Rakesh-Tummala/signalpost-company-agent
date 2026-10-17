#!/usr/bin/env python3
"""Build a single, self-contained, offline HTML page for browsing the submitted claims/evidence artifact.

It is built around the rubric question "can a user find, compare and verify company information on
desktop and mobile?":

* find    -- search by name, organisation number, town or industry; filter by what was found; sort;
* compare -- pick up to three companies and read their key facts side by side;
* verify  -- every fact shows its source link, retrieval date, content hash and the exact excerpt it was
             read from; the summary's [n] markers jump to the numbered sources it rests on;
* mobile  -- one column with a back button, large touch targets, tables that scroll inside their own box.

No external dependencies (no CDN, no build step): the page embeds a compact, flattened version of every
company's claims and evidence as JSON and renders it with plain HTML/CSS/JS, so it opens straight from disk
(file://) with no server.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _text(value: Any, limit: int = 300) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    value = "" if value is None else str(value)
    return value if len(value) <= limit else value[:limit].rstrip() + "..."


def flatten_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    evidence_by_id = {item["id"]: item for item in envelope.get("evidence", [])}
    claims = []
    by_field: dict[str, dict[str, Any]] = {}
    complete = available = 0
    for claim in envelope.get("claims", []):
        first = evidence_by_id.get((claim.get("evidence_ids") or [None])[0]) or {}
        row = {
            "field": claim.get("field"),
            "value": _text(claim.get("value")),
            "availability": claim.get("availability"),
            "confidence": claim.get("confidence"),
            "source_url": first.get("source_url"),
            "retrieved_at": first.get("retrieved_at"),
            "excerpt": _text(first.get("claim_span"), 320) if first.get("claim_span") else None,
            "method": first.get("extraction_method"),
            "sha": (first.get("content_sha256") or "")[:16],
            "snapshot": first.get("snapshot"),
        }
        claims.append(row)
        by_field[str(claim.get("field"))] = {**row, "raw": claim.get("value")}
        if claim.get("availability") == "available":
            available += 1
            if first.get("source_url") and first.get("retrieved_at") and first.get("content_sha256") and first.get("claim_span"):
                complete += 1

    def raw(field: str) -> Any:
        item = by_field.get(field)
        return item["raw"] if item and item["availability"] == "available" else None

    def families(prefix: str) -> list[tuple[str, dict[str, Any]]]:
        return sorted(
            [(name, item) for name, item in by_field.items() if name.startswith(prefix) and item["availability"] == "available"],
            key=lambda pair: pair[0],
        )

    address = raw("business_address") if isinstance(raw("business_address"), dict) else {}
    industry = raw("industry") if isinstance(raw("industry"), dict) else {}
    accounts = []
    for name, item in families("annual_accounts."):
        value = item["raw"] if isinstance(item["raw"], dict) else {}
        accounts.append({
            "year": name.split(".", 1)[1], "revenue": value.get("revenue"), "result": value.get("annual_result"),
            "operating_result": value.get("operating_result"), "source_url": item["source_url"], "sha": item["sha"],
        })
    accounts.sort(key=lambda a: a["year"], reverse=True)
    people = []
    for name, item in families("role."):
        value = item["raw"] if isinstance(item["raw"], dict) else {}
        if value.get("name"):
            people.append({"name": value.get("name"), "role": value.get("role") or value.get("role_code"), "since": value.get("last_changed"), "source_url": item["source_url"]})
    places = []
    for name, item in families("location."):
        value = item["raw"] if isinstance(item["raw"], dict) else {}
        place = value.get("address") if isinstance(value.get("address"), dict) else {}
        places.append({"name": value.get("name"), "town": (place.get("poststed") or place.get("kommune") or "").title(), "employees": value.get("employees"), "source_url": item["source_url"]})
    news = []
    for name, item in families("site_news."):
        value = item["raw"] if isinstance(item["raw"], dict) else {}
        news.append({"title": value.get("title") or value.get("headline"), "url": value.get("url"), "date": str(value.get("published_at") or "")[:10], "source_url": item["source_url"]})
    news.sort(key=lambda n: n["date"], reverse=True)
    jobs = []
    for name, item in families("job_posting."):
        value = item["raw"] if isinstance(item["raw"], dict) else {}
        jobs.append({"title": value.get("title"), "url": value.get("url"), "date": str(value.get("date_posted") or "")[:10], "source_url": item["source_url"]})
    socials = []
    for name, item in families("social_profile."):
        socials.append({"platform": name.split(".", 1)[1], "url": item["raw"] if isinstance(item["raw"], str) else item["source_url"]})
    workforce = None
    for name, item in reversed(families("workforce_value.")):
        workforce = {"value": item["raw"], "year": name.split(".", 1)[1], "source_url": item["source_url"]}
        break
    website = raw("official_website") if isinstance(raw("official_website"), str) else None
    leader = next((p for p in people if str(p["role"]).lower().startswith("daglig")), None)
    areas = {
        "overview": bool(raw("legal_name")),
        "financials": bool(accounts),
        "leadership": bool(people),
        "locations": bool(places) or (by_field.get("registered_workplaces", {}).get("availability") == "available"),
        "web": bool(website or news or jobs or socials),
    }
    summary = envelope.get("summary") or {}
    sections: list[dict[str, Any]] = []
    for sentence in summary.get("sentences", []):
        title = sentence.get("section") or "Summary"
        if not sections or sections[-1]["title"] != title:
            sections.append({"title": title, "sentences": []})
        sections[-1]["sentences"].append({"text": sentence.get("text"), "cites": sentence.get("citations", [])})
    revenue = accounts[0]["revenue"] if accounts else None
    registry_latest = str(raw("latest_submitted_accounts") or "")
    later_filed = registry_latest if (accounts and registry_latest.isdigit() and accounts[0]["year"].isdigit() and int(registry_latest) > int(accounts[0]["year"])) else None
    return {
        "org": envelope.get("organisation_number"),
        "name": raw("legal_name"),
        "form": raw("legal_form"),
        "town": (address.get("poststed") or address.get("kommune") or "").title(),
        "industry": industry.get("beskrivelse"),
        "areas": areas,
        "area_count": sum(areas.values()),
        "facts": {
            "revenue": revenue, "result": accounts[0]["result"] if accounts else None, "year": accounts[0]["year"] if accounts else None,
            "employees": raw("employees"), "workforce": workforce, "leader": leader["name"] if leader else None, "people": len(people),
            "workplaces": len(places), "later_filed": later_filed, "website": website, "founded": raw("founded_date") or raw("inception_date"),
            "news": len(news), "jobs": len(jobs), "socials": [s["platform"] for s in socials],
            "activity": raw("business_activity") or raw("registered_purpose"),
        },
        "summary_sections": sections,
        "summary_sources": summary.get("sources", []),
        "summary_text": "" if sections else summary.get("text", ""),
        "unknowns": summary.get("unknown_fields", []),
        "changes": [{"field": item.get("field"), "old": _text(item.get("old_value"), 120), "new": _text(item.get("new_value"), 120), "old_retrieved_at": item.get("old_retrieved_at")} for item in envelope.get("changes", [])],
        "accounts": accounts, "people": people, "places": places, "news": news, "jobs": jobs, "socials": socials,
        "evidence_complete": [complete, available],
        "claim_count": len(claims),
        "claims": claims,
    }


def embed_safe(data_json: str) -> str:
    """Make JSON safe to inline in a <script> block.

    Excerpts are literal slices of source HTML, so they can contain "</script>" or "<!--", which would end
    the data block early. "<" is legal as \\u003c inside a JSON string, and U+2028/2029 are escaped for
    older engines.
    """
    return data_json.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Signalpost company profiles</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f6f6f3; --panel: #ffffff; --border: #dcdcd6; --text: #1d211e; --muted: #5d625d;
    --accent: #1f6a47; --accent-bg: #e6f2ea; --warn: #8a5a00; --warn-bg: #fdf3dc; --danger: #9a3333;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #141618; --panel: #1e2123; --border: #343839; --text: #ebede9; --muted: #a2a8a2;
      --accent: #6fcf9f; --accent-bg: #1a3025; --warn: #e6bd5b; --warn-bg: #33290f; --danger: #e58a8a;
    }
  }
  :root[data-theme="dark"] {
    --bg: #141618; --panel: #1e2123; --border: #343839; --text: #ebede9; --muted: #a2a8a2;
    --accent: #6fcf9f; --accent-bg: #1a3025; --warn: #e6bd5b; --warn-bg: #33290f; --danger: #e58a8a;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
  a { color: var(--accent); }
  .skip { position: absolute; left: -999px; top: 0; background: var(--panel); padding: 8px 12px; z-index: 20; }
  .skip:focus { left: 8px; top: 8px; }
  header { position: sticky; top: 0; z-index: 10; background: var(--panel); border-bottom: 1px solid var(--border); padding: 10px 16px; padding-top: calc(10px + env(safe-area-inset-top, 0px)); }
  .bar { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; max-width: 1280px; margin: 0 auto; }
  h1 { font-size: 17px; margin: 0; flex: 1 1 auto; }
  .stats { color: var(--muted); font-size: 13px; }
  .tools { max-width: 1280px; margin: 8px auto 0; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  input[type=search], select { min-height: 44px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); color: var(--text); font-size: 15px; }
  input[type=search] { flex: 1 1 240px; min-width: 0; }
  button, .btn { min-height: 44px; padding: 8px 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); color: var(--text); font: inherit; font-size: 14px; cursor: pointer; }
  button:hover, .btn:hover { border-color: var(--accent); }
  button[aria-pressed="true"] { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); font-weight: 600; }
  button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  button:disabled { opacity: .5; cursor: not-allowed; }
  :focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
  .chips { max-width: 1280px; margin: 8px auto 0; display: flex; flex-wrap: wrap; gap: 6px; }
  .chips button { min-height: 38px; padding: 4px 12px; border-radius: 999px; font-size: 13px; }
  main { max-width: 1280px; margin: 0 auto; padding: 12px 16px 64px; }
  .layout { display: grid; grid-template-columns: minmax(280px, 380px) minmax(0, 1fr); gap: 16px; align-items: start; }
  .layout > * { min-width: 0; }
  #list { max-height: calc(100vh - 210px); overflow-y: auto; padding-right: 2px; }
  .count { color: var(--muted); font-size: 13px; margin: 0 0 8px; }
  .row { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel); margin-bottom: 8px; }
  .row:hover, .row.active { border-color: var(--accent); background: var(--accent-bg); }
  .row .main { flex: 1; min-width: 0; cursor: pointer; background: none; border: 0; text-align: left; padding: 0; min-height: 44px; color: inherit; }
  .name { font-weight: 600; font-size: 15px; overflow-wrap: anywhere; }
  .sub { color: var(--muted); font-size: 12.5px; overflow-wrap: anywhere; }
  .dots { display: inline-flex; gap: 3px; vertical-align: middle; margin-left: 6px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border); }
  .dot.on { background: var(--accent); }
  .cmp { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); min-height: 44px; padding: 0 4px; cursor: pointer; }
  .cmp input { width: 20px; height: 20px; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 14px; }
  .panel h2 { margin: 0 0 4px; font-size: 20px; overflow-wrap: anywhere; }
  .panel h3 { margin: 18px 0 8px; font-size: 14px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  .back { display: none; margin-bottom: 10px; }
  nav.tabs { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0 4px; }
  nav.tabs a { padding: 6px 12px; border: 1px solid var(--border); border-radius: 999px; font-size: 13px; text-decoration: none; color: var(--text); min-height: 36px; display: inline-flex; align-items: center; }
  nav.tabs a:hover { border-color: var(--accent); }
  .facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin: 12px 0; }
  .fact { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--bg); }
  .fact .k { color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: .03em; }
  .fact .v { font-size: 17px; font-weight: 600; overflow-wrap: anywhere; }
  .fact .v.small { font-size: 14px; }
  .sec { margin: 0 0 12px; }
  .sec b { display: block; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--accent); margin-bottom: 2px; }
  .sec p { margin: 0; }
  sup.cite a { text-decoration: none; font-weight: 600; padding: 0 1px; }
  .unknown { background: var(--warn-bg); color: var(--warn); border-radius: 8px; padding: 8px 12px; font-size: 14px; }
  .tablewrap { overflow-x: auto; -webkit-overflow-scrolling: touch; border: 1px solid var(--border); border-radius: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; overflow-wrap: anywhere; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; white-space: nowrap; background: var(--bg); }
  td.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .ok { color: var(--accent); } .no { color: var(--danger); } .amb { color: var(--warn); }
  details summary { cursor: pointer; color: var(--accent); font-size: 12.5px; min-height: 28px; }
  .excerpt { display: block; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; margin-top: 4px; font-size: 12px; }
  .meta { color: var(--muted); font-size: 11.5px; margin-top: 2px; overflow-wrap: anywhere; }
  ol.sources { padding-left: 22px; margin: 0; font-size: 13px; }
  ol.sources li { margin-bottom: 4px; overflow-wrap: anywhere; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; background: var(--accent-bg); color: var(--accent); font-size: 12px; font-weight: 600; }
  .empty { color: var(--muted); padding: 24px; text-align: center; }
  .cmptable th:first-child, .cmptable td:first-child { position: sticky; left: 0; background: var(--panel); min-width: 130px; }
  [hidden] { display: none !important; }
  @media (max-width: 860px) {
    .layout { grid-template-columns: minmax(0, 1fr); }
    header { position: static; }
    body[data-view="detail"] .tools, body[data-view="detail"] .chips, body[data-view="compare"] .tools, body[data-view="compare"] .chips { display: none; }
    #list { max-height: none; overflow: visible; }
    .panel { padding: 14px 12px; }
    .panel h2 { font-size: 18px; }
    body[data-view="detail"] #list, body[data-view="compare"] #list { display: none; }
    body[data-view="list"] #detail { display: none; }
    .back { display: inline-block; }
    h1 { font-size: 16px; }
    header { padding-left: 12px; padding-right: 12px; }
    main { padding-left: 12px; padding-right: 12px; }
    .facts { grid-template-columns: repeat(2, 1fr); }
  }
  @media print { header, .back, .tools, .chips, nav.tabs { display: none; } body { background: #fff; color: #000; } }
</style>
</head>
<body data-view="list">
<a class="skip" href="#detail">Skip to the selected profile</a>
<header>
  <div class="bar">
    <h1>Signalpost company profiles</h1>
    <span class="stats" id="stats" aria-live="polite"></span>
  </div>
  <div class="tools" role="search">
    <label for="search" class="sub" style="position:absolute;left:-9999px">Search companies</label>
    <input type="search" id="search" placeholder="Search name, organisation number, town or industry" autocomplete="off">
    <label for="sort" class="sub" style="position:absolute;left:-9999px">Sort</label>
    <select id="sort" aria-label="Sort companies">
      <option value="data">Most data found</option>
      <option value="name">Name A-Z</option>
      <option value="revenue">Revenue, high to low</option>
      <option value="employees">Employees, high to low</option>
    </select>
    <button id="compare-btn" class="primary" disabled>Compare (0)</button>
  </div>
  <div class="chips" id="chips" role="group" aria-label="Filter by what was found"></div>
</header>
<main>
  <div class="layout">
    <section id="list" aria-label="Companies"></section>
    <section id="detail" aria-live="polite" tabindex="-1"><div class="panel empty">Select a company to see its profile, or tick two or three to compare them.</div></section>
  </div>
</main>
<script>
const DATA = __DATA_JSON__;
const $ = (id) => document.getElementById(id);
const FILTERS = [
  ['website', 'Verified website', c => !!c.facts.website],
  ['social', 'Social profiles', c => c.facts.socials.length > 0],
  ['news', 'Dated news', c => c.facts.news > 0],
  ['jobs', 'Open roles', c => c.facts.jobs > 0],
  ['fin', 'Financials', c => c.areas.financials],
  ['work', 'Workforce size', c => !!c.facts.workforce],
  ['lead', 'Leadership', c => c.areas.leadership],
];
const state = { q: '', sort: 'data', filters: new Set(), shown: 60, compare: [] };
const byOrg = new Map(DATA.map(c => [c.org, c]));

function esc(v) { const d = document.createElement('div'); d.textContent = v == null ? '' : String(v); return d.innerHTML; }
function safeUrl(u) { return /^https?:\/\//i.test(String(u || '')) ? String(u) : null; }
function link(u, label) { const s = safeUrl(u); return s ? `<a href="${esc(s)}" target="_blank" rel="noopener noreferrer">${esc(label || s)}</a>` : esc(label || u || ''); }
function nok(n) { return n == null || n === '' ? '-' : Number(n).toLocaleString('nb-NO') + ' NOK'; }
function num(n) { return n == null || n === '' ? '-' : Number(n).toLocaleString('nb-NO'); }
function dots(c) { return `<span class="dots" title="${c.area_count} of 5 data areas found" aria-label="${c.area_count} of 5 data areas found">${Object.values(c.areas).map(on => `<span class="dot${on ? ' on' : ''}"></span>`).join('')}</span>`; }

$('stats').textContent = DATA.length.toLocaleString() + ' companies';
$('chips').innerHTML = FILTERS.map(([key, label]) => `<button type="button" data-f="${key}" aria-pressed="false">${esc(label)}</button>`).join('');

function visible() {
  const q = state.q.trim().toLowerCase();
  let rows = DATA.filter(c => {
    if (q && !((c.org || '').includes(q) || (c.name || '').toLowerCase().includes(q) || (c.town || '').toLowerCase().includes(q) || (c.industry || '').toLowerCase().includes(q))) return false;
    for (const [key, , test] of FILTERS) if (state.filters.has(key) && !test(c)) return false;
    return true;
  });
  const by = {
    data: (a, b) => b.area_count - a.area_count || (b.claim_count - a.claim_count) || (a.name || '').localeCompare(b.name || ''),
    name: (a, b) => (a.name || '').localeCompare(b.name || ''),
    revenue: (a, b) => (b.facts.revenue ?? -Infinity) - (a.facts.revenue ?? -Infinity),
    employees: (a, b) => (b.facts.employees ?? -1) - (a.facts.employees ?? -1),
  }[state.sort];
  return rows.sort(by);
}

function renderList() {
  const rows = visible();
  const slice = rows.slice(0, state.shown);
  const active = location.hash.startsWith('#/c/') ? decodeURIComponent(location.hash.slice(4)) : null;
  $('list').innerHTML = `<p class="count">${rows.length.toLocaleString()} of ${DATA.length.toLocaleString()} companies</p>` + (slice.map(c => `
    <div class="row${c.org === active ? ' active' : ''}">
      <button type="button" class="main" data-org="${esc(c.org)}" aria-label="Open ${esc(c.name || c.org)}">
        <div class="name">${esc(c.name || c.org)}${dots(c)}</div>
        <div class="sub">${esc(c.org)}${c.town ? ' &middot; ' + esc(c.town) : ''}${c.industry ? ' &middot; ' + esc(c.industry) : ''}</div>
      </button>
      <label class="cmp"><input type="checkbox" data-cmp="${esc(c.org)}" ${state.compare.includes(c.org) ? 'checked' : ''} aria-label="Compare ${esc(c.name || c.org)}">Compare</label>
    </div>`).join('') || '<div class="empty">No companies match. Clear a filter or change the search.</div>')
    + (rows.length > slice.length ? `<button type="button" id="more" style="width:100%">Show ${Math.min(60, rows.length - slice.length)} more of ${(rows.length - slice.length).toLocaleString()} remaining</button>` : '');
  $('compare-btn').textContent = `Compare (${state.compare.length})`;
  $('compare-btn').disabled = state.compare.length < 2;
}

function fact(k, v, small) { return `<div class="fact"><div class="k">${esc(k)}</div><div class="v${small ? ' small' : ''}">${v}</div></div>`; }

function summaryHtml(c) {
  const cite = (list) => list.length ? `<sup class="cite">${list.map(n => `<a href="#src-${n}" title="Source ${n}">[${n}]</a>`).join('')}</sup>` : '';
  return c.summary_sections.map(s => `<div class="sec"><b>${esc(s.title)}</b><p>${s.sentences.map(x => esc(x.text) + cite(x.cites)).join(' ')}</p></div>`).join('')
    || `<p>${esc(c.summary_text)}</p>`;
}

function renderDetail(org) {
  const c = byOrg.get(org);
  if (!c) { $('detail').innerHTML = '<div class="panel empty">Company not found.</div>'; return; }
  const f = c.facts;
  const finRows = c.accounts.map(a => `<tr><td>${esc(a.year)}</td><td class="num">${nok(a.revenue)}</td><td class="num">${nok(a.operating_result)}</td><td class="num">${nok(a.result)}</td><td>${link(a.source_url, 'source')}</td></tr>`).join('');
  const people = c.people.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.role)}</td><td>${esc(p.since || '')}</td><td>${link(p.source_url, 'source')}</td></tr>`).join('');
  const places = c.places.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.town)}</td><td class="num">${num(p.employees)}</td><td>${link(p.source_url, 'source')}</td></tr>`).join('');
  const news = c.news.map(n => `<tr><td class="num">${esc(n.date)}</td><td>${link(n.url, n.title || n.url)}</td><td>${link(n.source_url, 'saved page')}</td></tr>`).join('');
  const jobs = c.jobs.map(j => `<tr><td>${link(j.url, j.title || j.url)}</td><td class="num">${esc(j.date)}</td><td>${link(j.source_url, 'saved page')}</td></tr>`).join('');
  const claims = c.claims.map(k => `<tr>
      <td>${esc(k.field)}</td><td>${esc(k.value)}</td>
      <td class="${k.availability === 'available' ? 'ok' : k.availability === 'ambiguous' ? 'amb' : 'no'}">${esc(k.availability)}</td>
      <td>${k.source_url ? link(k.source_url, 'open source') : ''}${k.retrieved_at ? `<div class="meta">retrieved ${esc(String(k.retrieved_at).slice(0, 10))}${k.sha ? ' &middot; sha256 ' + esc(k.sha) + '...' : ''}</div>` : ''}
        ${k.excerpt ? `<details><summary>exact excerpt</summary><code class="excerpt">${esc(k.excerpt)}</code>${k.method ? `<div class="meta">${esc(k.method)}</div>` : ''}${k.snapshot ? `<div class="meta">saved response: ${esc(k.snapshot)}</div>` : ''}</details>` : ''}</td>
    </tr>`).join('');
  const [done, total] = c.evidence_complete;
  $('detail').innerHTML = `
    <button type="button" class="back" id="back">&larr; All companies</button>
    <article class="panel">
      <h2>${esc(c.name || c.org)}</h2>
      <div class="sub">Organisation ${esc(c.org)}${c.form ? ' &middot; ' + esc(c.form) : ''}${c.town ? ' &middot; ' + esc(c.town) : ''}${c.industry ? ' &middot; ' + esc(c.industry) : ''}</div>
      <p style="margin:8px 0 0"><span class="badge">${c.area_count} of 5 data areas found</span> <span class="badge">${done} of ${total} facts fully sourced</span></p>
      <nav class="tabs" aria-label="Sections">
        <a href="#s-overview">Overview</a><a href="#s-fin">Financials</a><a href="#s-people">People &amp; places</a><a href="#s-web">Web &amp; activity</a><a href="#s-evidence">Evidence</a>
      </nav>
      <h3 id="s-overview">Overview</h3>
      ${summaryHtml(c)}
      <div class="facts">
        ${fact((f.later_filed ? 'Latest captured revenue' : 'Latest revenue') + (f.year ? ' (' + f.year + ')' : ''), nok(f.revenue), true)}
        ${fact('Result' + (f.year ? ' (' + f.year + ')' : ''), nok(f.result), true)}
        ${fact('Employees (registry)', num(f.employees))}
        ${fact('Workforce (annual report)', f.workforce ? num(f.workforce.value) + ' (' + esc(f.workforce.year) + ')' : '-', true)}
        ${fact('Registered workplaces', c.places.length ? String(c.places.length) : '-')}
        ${fact('Dated news items', String(f.news))}
        ${fact('Open roles', String(f.jobs))}
        ${fact('Verified website', f.website ? link(f.website, f.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')) : '-', true)}
      </div>
      ${f.later_filed ? `<p class="sub">The registry shows a later accounts filing (${esc(f.later_filed)}) whose figures were not captured, so ${esc(f.year)} is the newest year shown here.</p>` : ''}
      ${c.unknowns.length ? `<div class="unknown"><b>Not yet determined:</b> ${c.unknowns.map(esc).join('; ')}.</div>` : ''}
      ${c.changes.length ? `<h3>Changed since the previous run</h3><div class="tablewrap"><table><thead><tr><th>Field</th><th>Was</th><th>Now</th></tr></thead><tbody>${c.changes.map(x => `<tr><td>${esc(x.field)}</td><td>${esc(x.old)}</td><td>${esc(x.new)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      <h3 id="s-fin">Financials</h3>
      ${finRows ? `<div class="tablewrap"><table><thead><tr><th>Year</th><th>Revenue</th><th>Operating result</th><th>Result</th><th>Evidence</th></tr></thead><tbody>${finRows}</tbody></table></div>` : '<p class="sub">No filed annual accounts were found for this company.</p>'}
      <h3 id="s-people">People &amp; places</h3>
      ${people ? `<div class="tablewrap"><table><thead><tr><th>Name</th><th>Role</th><th>Changed</th><th>Evidence</th></tr></thead><tbody>${people}</tbody></table></div>` : '<p class="sub">No role holders were found.</p>'}
      ${places ? `<h3>Registered workplaces</h3><div class="tablewrap"><table><thead><tr><th>Name</th><th>Town</th><th>Employees</th><th>Evidence</th></tr></thead><tbody>${places}</tbody></table></div>` : ''}
      <h3 id="s-web">Web &amp; activity</h3>
      ${f.website ? `<p>Verified website: ${link(f.website)}</p>` : '<p class="sub">No website could be verified as belonging to this exact company.</p>'}
      ${c.socials.length ? `<p>Verified profiles: ${c.socials.map(s => link(s.url, s.platform)).join(' &middot; ')}</p>` : ''}
      ${news ? `<h3>Dated news from its own site</h3><div class="tablewrap"><table><thead><tr><th>Date</th><th>Title</th><th>Evidence</th></tr></thead><tbody>${news}</tbody></table></div>` : ''}
      ${jobs ? `<h3>Open roles on its own site</h3><div class="tablewrap"><table><thead><tr><th>Role</th><th>Posted</th><th>Evidence</th></tr></thead><tbody>${jobs}</tbody></table></div>` : ''}
      <h3>Sources behind the summary</h3>
      ${c.summary_sources.length ? `<ol class="sources">${c.summary_sources.map(s => `<li id="src-${s.n}" value="${s.n}">${link(s.source_url)} <span class="meta">retrieved ${esc(String(s.retrieved_at || '').slice(0, 10))}</span></li>`).join('')}</ol>` : '<p class="sub">No sources.</p>'}
      <h3 id="s-evidence">Evidence for every fact (${c.claim_count})</h3>
      <div class="tablewrap"><table><thead><tr><th>Field</th><th>Value</th><th>State</th><th>Verify</th></tr></thead><tbody>${claims}</tbody></table></div>
    </article>`;
  const back = $('back'); if (back) back.addEventListener('click', () => { location.hash = '#/'; });
}

function compareCell(c, key) {
  const f = c.facts;
  switch (key) {
    case 'name': return `<a href="#/c/${esc(c.org)}">${esc(c.name || c.org)}</a><div class="meta">${esc(c.org)}</div>`;
    case 'form': return esc(c.form || '-');
    case 'town': return esc(c.town || '-');
    case 'industry': return esc(c.industry || '-');
    case 'revenue': return f.revenue == null ? '-' : nok(f.revenue) + ' (' + esc(f.year) + ')';
    case 'result': return nok(f.result);
    case 'employees': return num(f.employees);
    case 'workforce': return f.workforce ? num(f.workforce.value) + ' (' + esc(f.workforce.year) + ')' : '-';
    case 'leader': return esc(f.leader || '-');
    case 'workplaces': return c.places.length ? String(c.places.length) : '-';
    case 'website': return f.website ? link(f.website, f.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')) : '-';
    case 'social': return f.socials.length ? esc(f.socials.join(', ')) : '-';
    case 'news': return f.news ? String(f.news) + (c.news[0] ? ' (latest ' + esc(c.news[0].date) + ')' : '') : '0';
    case 'jobs': return String(f.jobs);
    case 'areas': return c.area_count + ' of 5';
    case 'sourced': return c.evidence_complete[0] + ' of ' + c.evidence_complete[1];
  }
  return '';
}

function renderCompare() {
  const cs = state.compare.map(o => byOrg.get(o)).filter(Boolean);
  const rows = [['name', 'Company'], ['form', 'Legal form'], ['town', 'Town'], ['industry', 'Industry'], ['revenue', 'Latest revenue'], ['result', 'Latest result'], ['employees', 'Employees (registry)'], ['workforce', 'Workforce (annual report)'], ['leader', 'Managing director'], ['workplaces', 'Registered workplaces'], ['website', 'Verified website'], ['social', 'Social profiles'], ['news', 'Dated news items'], ['jobs', 'Open roles'], ['areas', 'Data areas found'], ['sourced', 'Facts fully sourced']];
  $('detail').innerHTML = `
    <button type="button" class="back" id="back">&larr; All companies</button>
    <article class="panel">
      <h2>Compare companies</h2>
      <p class="sub">Open a company name for its full profile with sources. Tick or untick companies in the list to change this comparison (up to three).</p>
      <div class="tablewrap"><table class="cmptable"><tbody>${rows.map(([key, label]) => `<tr><th scope="row">${esc(label)}</th>${cs.map(c => `<td>${compareCell(c, key)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
    </article>`;
  const back = $('back'); if (back) back.addEventListener('click', () => { location.hash = '#/'; });
}

function route() {
  const h = location.hash;
  if (h.startsWith('#/c/')) {
    document.body.dataset.view = 'detail';
    renderDetail(decodeURIComponent(h.slice(4)));
    renderList();
    if (window.matchMedia('(max-width: 860px)').matches) window.scrollTo(0, 0);
  } else if (h === '#/compare' && state.compare.length >= 2) {
    document.body.dataset.view = 'compare';
    renderCompare();
    renderList();
  } else {
    document.body.dataset.view = 'list';
    renderList();
  }
}

$('search').addEventListener('input', (e) => { state.q = e.target.value; state.shown = 60; renderList(); });
$('sort').addEventListener('change', (e) => { state.sort = e.target.value; state.shown = 60; renderList(); });
$('chips').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-f]'); if (!b) return;
  const key = b.dataset.f;
  if (state.filters.has(key)) state.filters.delete(key); else state.filters.add(key);
  b.setAttribute('aria-pressed', state.filters.has(key) ? 'true' : 'false');
  state.shown = 60; renderList();
});
$('compare-btn').addEventListener('click', () => { location.hash = '#/compare'; });
$('list').addEventListener('click', (e) => {
  const open = e.target.closest('button[data-org]');
  if (open) { location.hash = '#/c/' + encodeURIComponent(open.dataset.org); return; }
  if (e.target.id === 'more') { state.shown += 60; renderList(); }
});
$('list').addEventListener('change', (e) => {
  const box = e.target.closest('input[data-cmp]'); if (!box) return;
  const org = box.dataset.cmp;
  if (box.checked) {
    if (state.compare.length >= 3) { box.checked = false; return; }
    state.compare.push(org);
  } else state.compare = state.compare.filter(o => o !== org);
  $('compare-btn').textContent = `Compare (${state.compare.length})`;
  $('compare-btn').disabled = state.compare.length < 2;
  if (document.body.dataset.view === 'compare') { if (state.compare.length >= 2) renderCompare(); else location.hash = '#/'; }
});
window.addEventListener('hashchange', route);
route();
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a self-contained offline HTML viewer for the claims/evidence submission artifact.")
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--profiles", help="Optional profiles.jsonl to pull display names from (kept for compatibility; names now come from the claims).")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    companies = [flatten_envelope(envelope) for envelope in read_jsonl(Path(args.envelopes))]
    companies.sort(key=lambda c: c["org"])
    data_json = embed_safe(json.dumps(companies, ensure_ascii=False, separators=(",", ":")))
    page = PAGE_TEMPLATE.replace("__DATA_JSON__", data_json)
    Path(args.output).write_text(page, encoding="utf-8")
    print(json.dumps({"companies": len(companies), "output": args.output, "size_bytes": len(page.encode("utf-8"))}, indent=2))


if __name__ == "__main__":
    main()
