#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.0,<3", "httpx>=0.27", "google-auth>=2.30", "requests>=2.31"]
# ///
"""Local MCP server: Google Search Console, read-only, via a service account.

Why this exists next to the OpenSEO connector: OpenSEO proxies GSC but narrows it — its
`type` enum has six values, `searchAppearance` comes back empty, and there is no URL
Inspection or Sitemaps at all. This server talks to Google directly, so the full Search
Analytics surface (16 months, every dimension, every filter) and per-URL index state are
available, for any property the service account has been added to.

Auth: a service-account JSON key at ~/.config/gsc-mcp/service-account.json (chmod 600),
overridable with GSC_CREDENTIALS. The account is granted data access inside Search Console
itself (Settings -> Users and permissions -> add the client_email), NOT through IAM roles —
a service account with no project roles is normal and correct here.

Read-only by design: scope is webmasters.readonly, and no tool submits, deletes or
re-indexes anything.

Run `uv run --script server.py --check` to verify the key and list visible properties.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

KEY_FILE = Path(os.environ.get("GSC_CREDENTIALS", "~/.config/gsc-mcp/service-account.json")).expanduser()
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
WM = "https://www.googleapis.com/webmasters/v3"
SC = "https://searchconsole.googleapis.com/v1"
DEFAULT_SITE = os.environ.get("GSC_DEFAULT_SITE", "sc-domain:example.com")
TIMEOUT = float(os.environ.get("GSC_TIMEOUT", "60"))

# Google finalises Search Analytics ~2-3 days back; asking for yesterday returns a
# half-filled day that reads as a traffic collapse.
LAG_DAYS = 3

mcp = MCPServer(
    "gsc",
    version="0.1.0",
    instructions=(
        "Google Search Console, read-only, straight from Google's API with a service account.\n\n"
        "GLOSSARY\n"
        "- property (siteUrl) = one verified site. Domain properties look like "
        "'sc-domain:example.com'; URL-prefix ones like 'https://example.com/'. The default is "
        f"{DEFAULT_SITE}; pass `site` to work on any other property the service account was added to.\n"
        "- impression/click/position = Search Analytics metrics. `ctr` here is returned as a "
        "percentage (0-100), not Google's 0-1 fraction, because every report quotes it that way.\n"
        "- window = a date range. Every tool defaults to the last 28 days ending "
        f"{LAG_DAYS} days back, because fresher days are still being filled in.\n\n"
        "CORE WORKFLOWS\n"
        "- What ranks / what people search -> performance(dimensions=['query']) or ['page'].\n"
        "- Trend or a report chart -> performance(dimensions=['date']).\n"
        "- Did it get better -> compare(): the window against the one before it, same length.\n"
        "- Is this page indexed, what does Google see -> inspect_url(url).\n"
        "- Which properties do I even have access to -> sites().\n\n"
        "HONESTY RULES\n"
        "- The AI-mode / generative-AI report is NOT here and cannot be: Google exposes it only "
        "in the Search Console interface, neither through this API nor through the BigQuery bulk "
        "export. It carries impressions only (no clicks) and starts 2026-05-18. Anyone asking for "
        "AI traffic needs the UI export, not this server.\n"
        "- The last 2-3 days are incomplete by design. Never read a fresh dip as a drop.\n"
        "- Search Analytics hides low-volume queries for privacy, so the sum of query rows is "
        "normally LESS than the property total. Quote totals from an unsegmented call.\n"
        "- A property the service account was not added to returns 403; that is a missing "
        "invitation in Search Console, not a broken key."
    ),
)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)


class GscError(RuntimeError):
    pass


_creds = None


def _token() -> str:
    """Service-account JWT exchanged for an access token, refreshed when stale."""
    global _creds
    if _creds is None:
        if not KEY_FILE.exists():
            raise GscError(
                f"No service-account key at {KEY_FILE}. Create one in Google Cloud "
                "(IAM & Admin -> Service Accounts -> Keys -> JSON), store it there with chmod 600, "
                "then add its client_email as a user of the property in Search Console."
            )
        try:
            _creds = service_account.Credentials.from_service_account_file(
                str(KEY_FILE), scopes=SCOPES)
        except Exception as e:                      # noqa: BLE001 — surfaced verbatim to the caller
            raise GscError(f"Key file at {KEY_FILE} is not a usable service-account JSON: {e}")
    if not _creds.valid:
        _creds.refresh(GoogleRequest())
    return _creds.token


def _call(method: str, url: str, *, json_body: dict | None = None) -> Any:
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json",
               "User-Agent": "gsc-mcp/0.1"}
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.request(method, url, headers=headers, json=json_body)
    if r.status_code == 401:
        raise GscError("401 — access token rejected. Key revoked or the Search Console API is "
                       "disabled on the Cloud project.")
    if r.status_code == 403:
        # The single most common failure, and its cause is never in Google's message.
        raise GscError(
            "403 — the service account has no access to this property (or the API is not enabled). "
            "Fix it in Search Console -> Settings -> Users and permissions: add "
            f"{_client_email()} with Full permission. IAM roles do not grant this."
        )
    if r.status_code == 404:
        raise GscError(f"404 — no such property or URL for this account: {url}")
    if r.status_code == 429:
        raise GscError("429 — Search Console quota hit (1200 queries/min, 2000/day per property).")
    if r.status_code >= 400:
        raise GscError(f"{r.status_code}: {r.text[:500]}")
    if not r.text.strip():
        return {}
    try:
        return r.json()
    except ValueError:
        raise GscError(f"{r.status_code} unparseable body: {r.text[:300]!r}")


def _client_email() -> str:
    try:
        return json.loads(KEY_FILE.read_text()).get("client_email", "the service account")
    except Exception:                                # noqa: BLE001
        return "the service account"


def _site_path(site: str | None) -> str:
    return urllib.parse.quote(site or DEFAULT_SITE, safe="")


def _window(start_date: str | None, end_date: str | None, days: int) -> tuple[str, str]:
    """Explicit dates win; otherwise `days` back from the last day Google has settled."""
    if start_date and end_date:
        return start_date, end_date
    end = date.today() - timedelta(days=LAG_DAYS)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def _query(site: str | None, body: dict) -> dict:
    return _call("POST", f"{WM}/sites/{_site_path(site)}/searchAnalytics/query", json_body=body)


def _rows(raw: list[dict], dimensions: list[str]) -> list[dict]:
    out = []
    for r in raw:
        row: dict[str, Any] = {}
        for i, dim in enumerate(dimensions):
            row[dim] = r.get("keys", [None] * len(dimensions))[i]
        row.update({
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        })
        out.append(row)
    return out


def _totals(site: str | None, start: str, end: str, search_type: str, data_state: str) -> dict:
    """Unsegmented totals — the only numbers safe to quote, since dimensioned rows drop
    low-volume queries for privacy and never add up to the property total."""
    raw = _query(site, {"startDate": start, "endDate": end, "type": search_type,
                        "dataState": data_state}).get("rows", [])
    if not raw:
        return {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None}
    r = raw[0]
    return {"clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2), "position": round(r.get("position", 0), 1)}


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool(annotations=READ)
def sites() -> Any:
    """Every Search Console property this service account has been added to, with its permission.

    An empty list means the invitation step was skipped: the key is fine, but nobody added
    the account inside Search Console yet.
    """
    body = _call("GET", f"{WM}/sites")
    entries = body.get("siteEntry", [])
    return {
        "serviceAccount": _client_email(),
        "default": DEFAULT_SITE,
        "count": len(entries),
        "sites": [{"site": e.get("siteUrl"), "permission": e.get("permissionLevel")} for e in entries],
        "note": ("No properties: add the service account in Search Console -> Settings -> "
                 "Users and permissions." if not entries else None),
    }


@mcp.tool(annotations=READ)
def performance(
    dimensions: list[str] | None = None,
    site: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 28,
    search_type: str = "web",
    filters: list[dict] | None = None,
    row_limit: int = 100,
    start_row: int = 0,
    data_state: str = "final",
) -> Any:
    """Search Analytics rows plus the unsegmented totals for the same window.

    dimensions: any of query, page, country, device, date, searchAppearance (default ['query']).
    search_type: web, image, video, news, discover, googleNews. AI-mode traffic is NOT one of
    them and is not reachable from any API — see this server's honesty rules.
    filters: [{"dimension":"page","operator":"contains","expression":"/blog/"}]; operators are
    equals, notEquals, contains, notContains, includingRegex, excludingRegex.
    data_state: 'final' (settled) or 'all' (includes the freshest, still-moving days).
    """
    dims = dimensions or ["query"]
    start, end = _window(start_date, end_date, days)
    body: dict[str, Any] = {
        "startDate": start, "endDate": end, "dimensions": dims, "type": search_type,
        "rowLimit": max(1, min(row_limit, 25000)), "startRow": start_row, "dataState": data_state,
    }
    if filters:
        body["dimensionFilterGroups"] = [{"filters": [
            {"dimension": f["dimension"], "operator": f.get("operator", "equals"),
             "expression": f["expression"]} for f in filters]}]
    raw = _query(site, body).get("rows", [])
    return {
        "site": site or DEFAULT_SITE,
        "window": {"start": start, "end": end, "days": days if not (start_date and end_date) else None},
        "type": search_type,
        "dimensions": dims,
        "totals": _totals(site, start, end, search_type, data_state),
        "rowCount": len(raw),
        "hasMore": len(raw) == body["rowLimit"],
        "rows": _rows(raw, dims),
        "note": ("Dimensioned rows omit low-volume queries Google hides for privacy — they sum to "
                 "less than `totals`. Quote totals, not the sum of rows."),
    }


@mcp.tool(annotations=READ)
def compare(
    site: str | None = None,
    days: int = 28,
    search_type: str = "web",
    dimensions: list[str] | None = None,
    row_limit: int = 25,
) -> Any:
    """The last `days` against the `days` immediately before it: totals, deltas, and movers.

    This is the shape a weekly or monthly report actually needs — one call instead of two plus
    arithmetic. Position deltas are inverted on purpose: a smaller number is an improvement,
    so `position_delta` is reported as (previous - current), positive = better.
    """
    end = date.today() - timedelta(days=LAG_DAYS)
    cur_start = end - timedelta(days=days - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    cur = _totals(site, cur_start.isoformat(), end.isoformat(), search_type, "final")
    prev = _totals(site, prev_start.isoformat(), prev_end.isoformat(), search_type, "final")

    def pct(now, was):
        if not was:
            return None
        return round((now - was) / was * 100, 1)

    out: dict[str, Any] = {
        "site": site or DEFAULT_SITE,
        "current": {"start": cur_start.isoformat(), "end": end.isoformat(), **cur},
        "previous": {"start": prev_start.isoformat(), "end": prev_end.isoformat(), **prev},
        "delta": {
            "clicks_pct": pct(cur["clicks"], prev["clicks"]),
            "impressions_pct": pct(cur["impressions"], prev["impressions"]),
            "ctr_pp": round(cur["ctr"] - prev["ctr"], 2),
            "position_delta": (round(prev["position"] - cur["position"], 1)
                               if cur["position"] and prev["position"] else None),
        },
    }
    if dimensions:
        def keyed(s, e):
            rows = _rows(_query(site, {"startDate": s, "endDate": e, "dimensions": dimensions,
                                       "type": search_type, "rowLimit": 25000,
                                       "dataState": "final"}).get("rows", []), dimensions)
            return {tuple(r[d] for d in dimensions): r for r in rows}

        now_rows, was_rows = keyed(cur_start.isoformat(), end.isoformat()), keyed(prev_start.isoformat(), prev_end.isoformat())
        movers = []
        for k in set(now_rows) | set(was_rows):
            n, w = now_rows.get(k), was_rows.get(k)
            movers.append({
                **{d: k[i] for i, d in enumerate(dimensions)},
                "clicks": (n or {}).get("clicks", 0),
                "clicks_prev": (w or {}).get("clicks", 0),
                "clicks_change": (n or {}).get("clicks", 0) - (w or {}).get("clicks", 0),
                "impressions": (n or {}).get("impressions", 0),
                "impressions_change": (n or {}).get("impressions", 0) - (w or {}).get("impressions", 0),
                "status": "new" if w is None else "lost" if n is None else "kept",
            })
        movers.sort(key=lambda m: abs(m["clicks_change"]) or abs(m["impressions_change"]) / 1000, reverse=True)
        out["movers"] = movers[:row_limit]
    return out


@mcp.tool(annotations=READ)
def inspect_url(url: str, site: str | None = None) -> Any:
    """What Google actually knows about one URL: index state, canonical, crawl, mobile, rich results.

    Answers the questions a rank drop raises before any content work: is it indexed at all, did
    Google pick a different canonical, when was it last crawled, is the sitemap seeing it.
    """
    body = _call("POST", f"{SC}/urlInspection/index:inspect",
                 json_body={"inspectionUrl": url, "siteUrl": site or DEFAULT_SITE})
    r = (body.get("inspectionResult") or {})
    idx = r.get("indexStatusResult") or {}
    return {
        "url": url,
        "site": site or DEFAULT_SITE,
        "verdict": idx.get("verdict"),
        "coverage": idx.get("coverageState"),
        "indexed": idx.get("verdict") == "PASS",
        "googleCanonical": idx.get("googleCanonical"),
        "userCanonical": idx.get("userCanonical"),
        "lastCrawled": idx.get("lastCrawlTime"),
        "crawledAs": idx.get("crawledAs"),
        "robotsState": idx.get("robotsTxtState"),
        "sitemaps": idx.get("sitemap"),
        "referringUrls": idx.get("referringUrls"),
        "mobileUsability": (r.get("mobileUsabilityResult") or {}).get("verdict"),
        "richResults": (r.get("richResultsResult") or {}).get("verdict"),
        "inspectionLink": r.get("inspectionResultLink"),
    }


@mcp.tool(annotations=READ)
def sitemaps(site: str | None = None) -> Any:
    """Submitted sitemaps for a property: last download, warnings, errors, URLs submitted/indexed."""
    body = _call("GET", f"{WM}/sites/{_site_path(site)}/sitemaps")
    out = []
    for s in body.get("sitemap", []):
        contents = s.get("contents") or [{}]
        out.append({
            "path": s.get("path"),
            "lastSubmitted": s.get("lastSubmitted"),
            "lastDownloaded": s.get("lastDownloaded"),
            "isPending": s.get("isPending"),
            "errors": int(s.get("errors", 0)),
            "warnings": int(s.get("warnings", 0)),
            "submitted": sum(int(c.get("submitted", 0)) for c in contents),
            "indexed": sum(int(c.get("indexed", 0)) for c in contents),
        })
    return {"site": site or DEFAULT_SITE, "count": len(out), "sitemaps": out}


@mcp.tool(annotations=READ)
def index_coverage(site: str | None = None, sitemap_url: str | None = None, limit: int = 100) -> Any:
    """Index state of every URL in the sitemap, grouped — the audit the UI makes you click through.

    Search Console's own Pages report shows counts but not which URL sits in which bucket, and
    the Sitemaps API's `indexed` field is deprecated (it returns 0 for everyone, so never read it
    as "nothing is indexed"). This walks the sitemap and inspects each URL one by one.

    Costs one URL Inspection call per URL; the quota is 2000/day per property, so `limit` guards
    against burning it on a large sitemap by accident.
    """
    target = site or DEFAULT_SITE
    if not sitemap_url:
        listed = _call("GET", f"{WM}/sites/{_site_path(site)}/sitemaps").get("sitemap", [])
        if not listed:
            raise GscError(f"No sitemap submitted for {target}; pass sitemap_url explicitly.")
        sitemap_url = listed[0]["path"]

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        xml = client.get(sitemap_url, headers={"User-Agent": "gsc-mcp/0.1"}).text
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    if not urls:
        raise GscError(f"No <loc> entries in {sitemap_url} — index sitemap or wrong URL?")

    scanned, buckets = [], {}
    for u in urls[:limit]:
        res = inspect_url(u, site)
        state = res["coverage"] or res["verdict"] or "unknown"
        buckets.setdefault(state, []).append(u)
        scanned.append({"url": u, "coverage": state, "indexed": res["indexed"],
                        "googleCanonical": res["googleCanonical"], "lastCrawled": res["lastCrawled"]})

    return {
        "site": target,
        "sitemap": sitemap_url,
        "urlsInSitemap": len(urls),
        "scanned": len(scanned),
        "truncated": len(urls) > limit,
        "indexed": sum(1 for r in scanned if r["indexed"]),
        "byState": {k: len(v) for k, v in sorted(buckets.items(), key=lambda kv: -len(kv[1]))},
        "problems": [r for r in scanned if not r["indexed"]],
        "note": ("Sitemaps API reports indexed=0 for every property — the field is deprecated. "
                 "These per-URL verdicts are the real answer."),
    }


def _check() -> int:
    try:
        info = sites()
    except GscError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"OK — service account {info['serviceAccount']}")
    for s in info["sites"]:
        print(f"  {s['permission']:<12} {s['site']}")
    if not info["sites"]:
        print("  (no properties yet — add the account in Search Console)", file=sys.stderr)
        return 1
    return 0


def _cli() -> int:
    """`--call <tool> '<json args>'` — the same tools from a shell script.

    inject_seo.py and friends need these numbers without speaking JSON-RPC over stdio;
    without this they would re-implement auth and paging a second time.
    """
    tools = {"sites": sites, "performance": performance, "compare": compare,
             "inspect_url": inspect_url, "sitemaps": sitemaps,
             "index_coverage": index_coverage}
    i = sys.argv.index("--call")
    name = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
    if name not in tools:
        print(f"unknown tool {name!r}; available: {', '.join(sorted(tools))}", file=sys.stderr)
        return 2
    args = json.loads(sys.argv[i + 2]) if len(sys.argv) > i + 2 else {}
    try:
        print(json.dumps(tools[name](**args), ensure_ascii=False, indent=2))
    except GscError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    if "--call" in sys.argv:
        raise SystemExit(_cli())
    mcp.run()
