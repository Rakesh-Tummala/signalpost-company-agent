"""Structured signals read from one first-party company page.

Everything returned here comes from markup the company itself published on a page
we fetched: JSON-LD (`JobPosting`, `Article`/`NewsArticle`/`BlogPosting`,
`sameAs`), `<time datetime>` elements, article meta tags, RSS/Atom links, job
listing anchors and apply actions. Each item carries a `span`, a literal slice of
the raw HTML, so it can be located again in the saved snapshot.

Deliberately conservative: a hiring fact needs a real role (a `JobPosting`, a
role card linking to an individual posting, or an explicit apply action) -- a
generic "Careers" link is not one -- and a news fact needs a parseable
publication date.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from bs4 import BeautifulSoup, Tag


MAX_ITEMS = 25
MAX_SPAN = 400

ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "ReportageNewsArticle", "TechArticle", "PressRelease"}
# Schema types that mean "a post"; a plain `Article` is not enough on its own because
# WordPress/Yoast marks ordinary pages (contact, about) as Article with a publish date.
POST_TYPES = {"NewsArticle", "BlogPosting", "ReportageNewsArticle", "PressRelease"}
NEWS_URL = re.compile(
    r"(?:/(?:19|20)\d{2}/\d{1,2}(?:/|$)|/(?:news|newsroom|nyheter|nyhet|nytt|aktuelt|aktuelle-saker|blog|blogg|bloggen|artikler?|articles?|"
    r"posts?|press|presse|pressemelding(?:er)?|media|siste-nytt|stories|insights|magasin|magazine)(?:/|$))", re.I,
)
COMMENT_FEED = re.compile(r"(?:^|/)comments?(?:/|$)|feed=comments|comments-feed", re.I)
NOT_A_POST = re.compile(r"^(?:protected|private|beskyttet|privat)\s*:|^(?:comment on|kommentar (?:til|fra))\b", re.I)
CAREERS_PAGE = re.compile(r"(?:^|[-/])(?:careers?|jobs?|jobb|karriere|ledige-stillinger|stillinger?|vacanc(?:y|ies)|rekruttering|open-positions?|work-with-us)(?:[-/]|$)", re.I)
JOB_DETAIL = re.compile(r"/(?:jobs?|jobb|stillinger?|stilling|karriere|careers?|vacanc(?:y|ies)|positions?|ledige-stillinger|open-positions?|o)/([^/?#]{3,})", re.I)
GENERIC_SLUGS = {
    "ledige-stillinger", "stillinger", "stilling", "jobs", "job", "jobb", "careers", "career", "karriere", "open-positions",
    "positions", "alle", "all", "om-oss", "about", "kontakt", "contact", "search", "sok", "søk", "index", "page", "feed",
    "rss", "apply", "soknad", "søknad", "students", "studenter", "internship", "praksis",
}
# A vacancy link often carries the vacancy words in its file name rather than under a
# /jobs/ folder ("/ledig-stilling-servicemarkedsleder-kongsvinger"). Two or more
# hyphenated words are required so a bare "/jobb" or "/karriere" never qualifies.
VACANCY_SLUG = re.compile(r"(?:^|-)(?:ledig|stilling|vacanc\w*|vakans\w*|open-position\w*|soknad|apply)(?:-|$)", re.I)
# An aggregate "Ledige stillinger hos ..." link is a listing, not an individual role.
LISTING_TITLE = re.compile(r"^(?:se |alle |vis )?(?:ledige stillinger|open positions|current openings|vacancies|all jobs|our jobs|jobs at)\b", re.I)
GENERIC_TITLE = re.compile(
    r"^(karriere|careers?|jobs?|jobb|stillinger?|ledige stillinger|open positions?|se alle.*|vis alle.*|see all.*|view all.*|"
    r"les mer|read more|mer|more|apply|søk|søk her|søknad|kontakt.*|about.*|om oss|join us|bli med.*|work with us.*|"
    r"tilbake.*|back.*|next|neste|previous|forrige)$",
    re.I,
)
APPLY_TEXT = re.compile(
    r"^(søk (her|nå|på (stillingen|jobben|denne stillingen))|søk stillingen|send (inn )?søknad|send søknad( her)?|"
    r"apply( now| here| for this (job|position|role))?|apply online)$",
    re.I,
)
ATS_HOSTS = (
    "teamtailor.com", "recruitee.com", "webcruiter.no", "webcruiter.com", "easycruit.com", "jobylon.com", "varbi.com",
    "reachmee.com", "jobbnorge.no", "lever.co", "greenhouse.io", "workable.com", "personio.de", "personio.com",
    "smartrecruiters.com", "hrmanager.no", "cvpartner.com", "flowcase.com", "polymer.co", "ashbyhq.com",
)
_JSONLD = re.compile(r'<script\b[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def parse_date(value: Any) -> str | None:
    """Return an ISO date/datetime for a plausible publication date, else None."""
    text = str(value or "").strip()
    if not text or len(text) > 64:
        return None
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed is None:
        return None
    now = datetime.now(timezone.utc)
    reference = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    if reference.year < 2000 or reference > now + timedelta(days=2):
        return None
    if not (re.search(r"[T ]\d{1,2}:\d{2}", text) or re.search(r"\d{1,2}:\d{2}:\d{2}", text)):
        return reference.date().isoformat()
    return reference.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _news_like(url: str) -> bool:
    return bool(NEWS_URL.search(urllib.parse.urlparse(url).path))


def _clip(text: str) -> str:
    return text[:MAX_SPAN]


def _raw_window(html: str, needle: str, *, before: int = 100, after: int = 260, start: int = 0) -> tuple[str, int] | None:
    index = html.find(needle, start)
    if index < 0:
        return None
    return _clip(html[max(0, index - before): index + len(needle) + after]), index + len(needle)


def _open_before(html: str, pattern: str, index: int, reach: int = 400) -> int:
    last = -1
    for match in re.finditer(pattern, html[max(0, index - reach): index + 1]):
        last = max(0, index - reach) + match.start()
    return last


def _element_span(html: str, index: int, open_pattern: str, close_marker: str, *, reach: int = 400) -> tuple[int, int] | None:
    start = _open_before(html, open_pattern, index, reach)
    end = html.find(close_marker, index)
    if start < 0 or end < 0 or end + len(close_marker) - start > MAX_SPAN:
        return None
    return start, end + len(close_marker)


def _element_bounds(html: str, index: int, open_pattern: str, close_marker: str, *, reach: int = 700, limit: int = 4000) -> tuple[int, int] | None:
    """Like _element_span but tolerant of long, heavily-styled markup; the excerpt is chosen separately."""
    start = _open_before(html, open_pattern, index, reach)
    end = html.find(close_marker, index)
    if start < 0 or end < 0 or end + len(close_marker) - start > limit:
        return None
    return start, end + len(close_marker)


def _card_span(html: str, start: int, end: int, title: str) -> str:
    """A literal excerpt for a link/card: the whole element when short, else the title text with context."""
    raw = html[start:end]
    if len(raw) <= MAX_SPAN:
        return _with_title(html, start, end, title)
    at = raw.find(title) if title else -1
    if at >= 0:
        return _clip(raw[max(0, at - 60): at + len(title) + 40])
    return _clip(raw[: raw.find(">") + 1])


def _with_title(html: str, start: int, end: int, title: str) -> str:
    """Widen an element span to the adjacent headline text when it fits, so date/role and title travel together."""
    after = html.find(title, end, end + 300) if title else -1
    if after >= 0 and after + len(title) - start <= MAX_SPAN:
        return html[start: after + len(title)]
    before = html.rfind(title, max(0, start - 300), start) if title else -1
    if before >= 0 and end - before <= MAX_SPAN:
        return html[before:end]
    return html[start:end]


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from _walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child)


def _types(node: dict[str, Any]) -> set[str]:
    kind = node.get("@type")
    return {str(item) for item in (kind if isinstance(kind, list) else [kind]) if item}


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id") or ""
    return " ".join(str(value or "").split())


def _pair_span(script: str, key: str, value: Any) -> tuple[int, int] | None:
    encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else None
    if encoded:
        match = re.search(r'"' + re.escape(key) + r'"\s*:\s*' + re.escape(encoded), script)
        if match:
            return match.start(), match.end()
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*', script)
    if not match:
        return None
    try:
        _, end = json.JSONDecoder().raw_decode(script, match.end())
    except ValueError:
        return None
    return match.start(), end


def _covering_span(script: str, pairs: list[tuple[str, Any]]) -> str | None:
    ranges = [r for key, value in pairs if (r := _pair_span(script, key, value))]
    if not ranges:
        return None
    start, end = min(r[0] for r in ranges), max(r[1] for r in ranges)
    if end - start <= MAX_SPAN:
        return script[start:end]
    first = ranges[0]
    return _clip(script[first[0]:first[1]])


def _jsonld_blocks(html: str):
    for match in _JSONLD.finditer(html):
        script = match.group(1)
        cleaned = re.sub(r"^\s*(?:<!--)?\s*(?://<!\[CDATA\[)?", "", script.strip())
        cleaned = re.sub(r"(?://\]\]>)?\s*(?:-->)?\s*$", "", cleaned)
        try:
            yield script, json.loads(cleaned)
        except ValueError:
            continue


def _absolute(base_url: str, href: str) -> str | None:
    url = urllib.parse.urljoin(base_url, href.strip())
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def _same_site(base_url: str, url: str) -> bool:
    def registered(value: str) -> str:
        host = (urllib.parse.urlparse(value).hostname or "").casefold().removeprefix("www.")
        return ".".join(host.split(".")[-2:])
    return registered(base_url) == registered(url)


def _ats_host(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").casefold()
    return any(host == domain or host.endswith("." + domain) for domain in ATS_HOSTS)


def _jsonld_items(html: str, final_url: str) -> tuple[list[dict[str, Any]], list[Any]]:
    items: list[dict[str, Any]] = []
    parsed_objects: list[Any] = []
    for script, data in _jsonld_blocks(html):
        parsed_objects.append(data)
        for node in _walk(data):
            kinds = _types(node)
            if "JobPosting" in kinds:
                title = _text(node.get("title") or node.get("name"))
                if not title:
                    continue
                span = _covering_span(script, [("title", node.get("title") or node.get("name")), ("datePosted", node.get("datePosted"))])
                posted = parse_date(node.get("datePosted"))
                valid_through = parse_date(node.get("validThrough")) if node.get("validThrough") else None
                url = _absolute(final_url, _text(node.get("url"))) if node.get("url") else final_url
                items.append({
                    "kind": "job_posting", "title": title[:200], "date": posted, "valid_through": valid_through,
                    "employment_type": _text(node.get("employmentType"))[:80] or None,
                    "url": url or final_url, "evidence_kind": "json_ld_jobposting", "span": span,
                })
            elif kinds & ARTICLE_TYPES:
                headline = _text(node.get("headline") or node.get("name"))
                published = parse_date(node.get("datePublished"))
                if not headline or not published:
                    continue
                target = node.get("url") or node.get("mainEntityOfPage")
                url = _absolute(final_url, _text(target)) if target else final_url
                if not (kinds & POST_TYPES) and not _news_like(url or final_url):
                    continue
                span = _covering_span(script, [("headline", node.get("headline") or node.get("name")), ("datePublished", node.get("datePublished"))])
                items.append({
                    "kind": "article", "title": headline[:300], "date": published, "url": url or final_url,
                    "evidence_kind": "json_ld_article", "schema_type": sorted(kinds & ARTICLE_TYPES)[0], "span": span,
                })
            if len(items) >= MAX_ITEMS:
                return items, parsed_objects
    return items, parsed_objects


def _container(tag: Tag) -> Tag:
    node: Tag = tag
    for _ in range(4):
        parent = node.parent
        if not isinstance(parent, Tag) or parent.name in {"body", "html", "main"}:
            break
        node = parent
        if node.find(["h1", "h2", "h3", "h4"]) or node.find("a", href=True):
            return node
    return tag.parent if isinstance(tag.parent, Tag) else tag


def _card_link(container: Tag, heading: Tag | None, final_url: str) -> Tag | None:
    """The link that carries a card's headline, ignoring the site logo / home links that
    often sit in the same container."""

    def deep(anchor: Tag) -> bool:
        target = _absolute(final_url, str(anchor.get("href") or ""))
        return bool(target) and urllib.parse.urlparse(target).path not in {"", "/"}

    if heading is not None:
        inside = heading.find("a", href=True)
        if inside is not None and deep(inside):
            return inside
    candidates = [anchor for anchor in container.find_all("a", href=True) if deep(anchor)]
    if not candidates:
        return None
    title_words = set((heading.get_text(" ", strip=True) if heading is not None else "").casefold().split())
    if title_words:
        for anchor in candidates:
            words = set(anchor.get_text(" ", strip=True).casefold().split())
            if words and len(words & title_words) / len(words | title_words) >= 0.6:
                return anchor
    return candidates[0]


def _dated_elements(html: str, soup: BeautifulSoup, final_url: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    cursor = 0
    for tag in soup.select("time[datetime]"):
        raw_value = str(tag.get("datetime") or "").strip()
        date = parse_date(raw_value)
        if not date:
            continue
        container = _container(tag)
        heading = container.find(["h1", "h2", "h3", "h4"])
        anchor = _card_link(container, heading, final_url)
        title = " ".join((heading or anchor).get_text(" ", strip=True).split()) if (heading or anchor) else ""
        if not (8 <= len(title) <= 300):
            continue
        target = _absolute(final_url, str(anchor.get("href"))) if anchor else None
        if target and not _same_site(final_url, target):
            continue
        index = html.find(raw_value, cursor)
        element = _element_span(html, index, r"<time\b", "</time>") if index >= 0 else None
        if element is None:
            continue
        cursor = element[1]
        span = _with_title(html, element[0], element[1], title)
        found.append({"kind": "article", "title": title, "date": date, "url": target or final_url, "evidence_kind": "time_element", "span": span})
        if len(found) >= MAX_ITEMS:
            break
    return found


def _meta_article(html: str, soup: BeautifulSoup, final_url: str) -> dict[str, Any] | None:
    published = soup.select_one('meta[property="article:published_time"], meta[property="og:published_time"], meta[itemprop="datePublished"]')
    if published is None:
        return None
    raw_value = str(published.get("content") or "").strip()
    date = parse_date(raw_value)
    if not date:
        return None
    headline_tag = soup.select_one('meta[property="og:title"]')
    headline = str(headline_tag.get("content") or "").strip() if headline_tag else ""
    if not headline:
        heading = soup.find("h1")
        headline = heading.get_text(" ", strip=True) if heading else (soup.title.get_text(" ", strip=True) if soup.title else "")
    headline = " ".join(headline.split())
    if not (8 <= len(headline) <= 300):
        return None
    index = html.find(raw_value)
    element = _element_span(html, index, r"<meta\b", ">", reach=300) if index >= 0 else None
    if element is None:
        return None
    canonical = soup.select_one('link[rel="canonical"]')
    url = _absolute(final_url, str(canonical.get("href"))) if canonical and canonical.get("href") else final_url
    if not _news_like(url or final_url):
        return None
    return {"kind": "article", "title": headline[:300], "date": date, "url": url or final_url, "evidence_kind": "meta_published_time", "span": html[element[0]:element[1]]}


def _feeds(soup: BeautifulSoup, final_url: str) -> list[str]:
    urls: list[str] = []
    for link in soup.select("link[rel~=alternate][type]"):
        kind = str(link.get("type") or "").casefold()
        if "rss" in kind or "atom" in kind:
            target = _absolute(final_url, str(link.get("href") or ""))
            if target and _same_site(final_url, target) and target not in urls and not COMMENT_FEED.search(urllib.parse.urlparse(target).path + "?" + urllib.parse.urlparse(target).query):
                urls.append(target)
    return urls[:3]


def _job_signals(html: str, soup: BeautifulSoup, final_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = urllib.parse.urlparse(final_url).path
    if not (CAREERS_PAGE.search(path) or JOB_DETAIL.search(path)):
        return [], []
    cards: dict[str, dict[str, Any]] = {}
    actions: dict[str, dict[str, Any]] = {}
    cursor = 0
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        target = _absolute(final_url, href)
        if not target:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        ats = _ats_host(target)
        if not (_same_site(final_url, target) or ats):
            continue
        if APPLY_TEXT.match(text):
            index = html.find(href)
            element = _element_bounds(html, index, r"<a\b", "</a>") if index >= 0 else None
            if element and target not in actions:
                actions[target] = {"kind": "job_posting", "title": None, "url": target, "evidence_kind": "apply_action", "text": text, "span": _card_span(html, element[0], element[1], text)}
            continue
        target_path = urllib.parse.urlparse(target).path
        match = JOB_DETAIL.search(target_path)
        last_segment = target_path.rstrip("/").split("/")[-1].casefold()
        vacancy_slug = bool(VACANCY_SLUG.search(last_segment)) and last_segment.count("-") >= 1 and target_path.rstrip("/") != path.rstrip("/")
        if not match and not vacancy_slug and not (ats and len([part for part in target_path.split("/") if part]) >= 2):
            continue
        slug = (match.group(1) if match else last_segment).casefold()
        if slug in GENERIC_SLUGS or re.fullmatch(r"\d{1,2}", slug):
            continue
        index = html.find(href, cursor)
        element = _element_bounds(html, index, r"<a\b", "</a>") if index >= 0 else None
        if element is None:
            continue
        # The link text as parsed, else the text between the raw <a ...> and </a> (a lenient
        # HTML parser moves block children out of an inline link), else the card heading.
        candidates = [
            text,
            " ".join(re.sub(r"<[^>]+>", " ", html[element[0]:element[1]]).split()),
        ]
        heading = _container(anchor).find(["h2", "h3", "h4"])
        if heading:
            candidates.append(" ".join(heading.get_text(" ", strip=True).split()))
        title = next((c for c in candidates if c and 3 <= len(c) <= 120 and not GENERIC_TITLE.match(c)), "")
        if not title or LISTING_TITLE.match(title) or len(title.split()) > 14 or not re.search(r"[A-Za-z\u00c6\u00d8\u00c5\u00e6\u00f8\u00e5]{3}", title):
            continue
        cursor = element[1]
        if target not in cards:
            cards[target] = {"kind": "job_posting", "title": title, "url": target, "evidence_kind": "role_card", "span": _card_span(html, element[0], element[1], title)}
        if len(cards) >= MAX_ITEMS:
            break
    return list(cards.values()), list(actions.values())[:5]


def _social_links(html: str, soup: BeautifulSoup, final_url: str, json_ld: list[Any]) -> list[dict[str, Any]]:
    from .website import normalize_social_url  # website imports this module; keep the edge lazy

    found: dict[tuple[str, str], dict[str, Any]] = {}

    def add(raw_value: str, source: str) -> None:
        candidate = raw_value.strip()
        url = urllib.parse.urljoin(final_url, candidate)
        parsed = urllib.parse.urlparse(url)
        if (parsed.hostname or "").casefold().removeprefix("www.") == "facebook.com" and parsed.path.startswith("/plugins/"):
            embedded = urllib.parse.parse_qs(parsed.query).get("href", [])
            if embedded:
                url = embedded[0]
        normalized = normalize_social_url(url)
        if not normalized:
            return
        key = (normalized["platform"], normalized["url"])
        if key in found:
            return
        located = _raw_window(html, candidate, before=60, after=80)
        if located is None:
            located = _raw_window(html, candidate.replace("/", "\\/"), before=60, after=80)
        if located is None:
            located = _raw_window(html, normalized["url"].split("://", 1)[1], before=60, after=80)
        found[key] = {**normalized, "source": source, "found_on": final_url, "span": located[0] if located else None}

    for node in soup.select("a[href]"):
        add(str(node.get("href") or ""), "anchor")
    for node in soup.select("[data-href]"):
        add(str(node.get("data-href") or ""), "anchor")
    for node in soup.select("iframe[src]"):
        add(str(node.get("src") or ""), "iframe")
    for node in soup.select('link[rel~=me][href], a[rel~=me][href]'):
        add(str(node.get("href") or ""), "rel_me")
    for data in json_ld:
        for node in _walk(data):
            same_as = node.get("sameAs")
            for raw_value in (same_as if isinstance(same_as, list) else [same_as]):
                if isinstance(raw_value, str):
                    add(raw_value, "json_ld_sameAs")
    for node in soup.select('meta[name="twitter:site"], meta[name="twitter:creator"], meta[property="twitter:site"]'):
        handle = str(node.get("content") or "").strip().lstrip("@")
        if re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
            add(f"https://x.com/{handle}", "twitter_meta")
            located = _raw_window(html, str(node.get("content") or ""), before=60, after=40)
            entry = found.get(("x", f"https://x.com/{handle}"))
            if entry is not None and located and entry.get("span") is None:
                entry["span"] = located[0]
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def extract_page_signals(html: str, final_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    json_ld_items, json_ld_objects = _jsonld_items(html, final_url)
    dated = _dated_elements(html, soup, final_url)
    meta_article = _meta_article(html, soup, final_url)
    role_cards, apply_actions = _job_signals(html, soup, final_url)
    articles = [item for item in json_ld_items if item["kind"] == "article"] + dated + ([meta_article] if meta_article else [])
    seen: set[tuple[str, str]] = set()
    unique_articles = []
    for item in articles:
        key = (item["url"], item["date"])
        if key in seen:
            continue
        seen.add(key)
        unique_articles.append(item)
    return {
        "articles": unique_articles[:MAX_ITEMS],
        "jobs": ([item for item in json_ld_items if item["kind"] == "job_posting"] + role_cards)[:MAX_ITEMS],
        "apply_actions": apply_actions,
        "feeds": _feeds(soup, final_url),
        "social_links": _social_links(html, soup, final_url, json_ld_objects),
    }


def parse_feed_items(feed_text: str, base_url: str) -> list[dict[str, Any]]:
    """Dated items from an RSS 2.0 or Atom feed, each with a literal slice of the feed as its span."""
    items: list[dict[str, Any]] = []
    for match in re.finditer(r"<(item|entry)\b[^>]*>.*?</\1>", feed_text, re.I | re.S):
        block = match.group(0)

        def field(*names: str) -> str:
            for name in names:
                found = re.search(r"<" + name + r"\b[^>]*>(.*?)</" + name + r">", block, re.I | re.S)
                if found:
                    value = re.sub(r"^<!\[CDATA\[(.*?)\]\]>$", r"\1", found.group(1).strip(), flags=re.S)
                    return " ".join(BeautifulSoup(value, "lxml").get_text(" ", strip=True).split()) if "<" in value else " ".join(html_lib.unescape(value).split())
            return ""

        link = field("link")
        if not link:
            href = re.search(r"<link\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", block, re.I)
            link = href.group(1) if href else ""
        title = field("title")
        date = parse_date(field("pubDate", "published", "updated", "dc:date"))
        url = _absolute(base_url, link) if link else None
        if title and date and url and _same_site(base_url, url) and not NOT_A_POST.match(title) and "#comment" not in url:
            items.append({"kind": "article", "title": title[:300], "date": date, "url": url, "evidence_kind": "feed_item", "span": _clip(block)})
        if len(items) >= MAX_ITEMS:
            break
    return items


def feed_record(url: str, raw: bytes, retrieved_at: str) -> dict[str, Any]:
    """A fetched RSS/Atom feed reduced to its dated items, with the raw body saved."""
    import hashlib

    from .snapshot_store import save_snapshot

    return {
        "url": url,
        "retrieved_at": retrieved_at,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot_path": save_snapshot(raw, "xml"),
        "items": parse_feed_items(raw.decode("utf-8", errors="replace"), url),
    }


def looks_like_feed(raw: bytes) -> bool:
    head = raw[:600].lstrip().lower()
    return head.startswith((b"<?xml", b"<rss", b"<feed", b"<rdf"))


def title_span(html: str) -> str | None:
    """The page's own `<title>` element, exactly as written (identity evidence for the site)."""
    match = re.search(r"<title\b[^>]*>.*?</title>", html, re.I | re.S)
    return _clip(match.group(0)) if match else None
