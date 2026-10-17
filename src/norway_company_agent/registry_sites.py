"""How many registered entities declare the same website, from the frozen Brreg bulk file.

A company that tells Brreg its homepage is the strongest public anchor for its website. But many registry
entries declare someone else's site: housing co-ops list their manager (usbl.no is declared by about 1,900
entities, obos.no by about 640), franchisees list the franchisor (7-eleven.no), group companies list the group.
A website declared by one or two entities is the company's own declaration about its own site; a website
declared by many is an umbrella site and is never published as one company's website.
"""
from __future__ import annotations

import csv
import gzip
from collections import Counter
from pathlib import Path
from typing import Any

from .identity import _host

MAX_DECLARING_ENTITIES = 2


def declared_host(profile: dict[str, Any]) -> str:
    """The website this entity declared in the registry (live record first, then the bulk row), as a bare host."""
    evidence = profile.get("evidence") or {}
    live = ((evidence.get("registry_live") or {}).get("value") or {}).get("website")
    bulk = ((evidence.get("registry") or {}).get("value") or {}).get("hjemmeside")
    return _host(live) or _host(bulk)


def count_declarations(bulk_path: str | Path, hosts: set[str]) -> Counter:
    """How many rows of the bulk registry declare each of `hosts` as their homepage."""
    counts: Counter = Counter()
    if not hosts:
        return counts
    with gzip.open(bulk_path, "rt", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        reader = csv.DictReader(handle, dialect=csv.Sniffer().sniff(sample, delimiters=";,\t"))
        for row in reader:
            host = _host(row.get("hjemmeside"))
            if host in hosts:
                counts[host] += 1
    return counts


def annotate_declared_sites(profiles: list[dict[str, Any]], bulk_path: str | Path) -> int:
    """Attach `registry_declared_site` = {host, declared_by} to every profile that declares a website."""
    hosts = {declared_host(profile) for profile in profiles} - {""}
    counts = count_declarations(bulk_path, hosts)
    annotated = 0
    for profile in profiles:
        host = declared_host(profile)
        if host:
            profile["registry_declared_site"] = {"host": host, "declared_by": max(1, counts.get(host, 0))}
            annotated += 1
    return annotated
