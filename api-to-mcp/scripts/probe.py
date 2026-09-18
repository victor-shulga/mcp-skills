#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["httpx>=0.27"]
# ///
"""Probe an unfamiliar HTTP API: which paths exist, and whether pagination is real.

Two modes.

Path scan — status, body shape, row count, top-level keys per path. 404 means the
endpoint is absent; 403 means it exists but rejects this credential kind (usually an
admin/browser route an API key will never open) — stop probing variants of it.

    probe.py --base https://my.example.com/api --header "Api-Key: $KEY" \
        --paths /me /clients /memberships

Pagination test — fetches several pages and reports rows, DISTINCT ids, page overlap
and the union against any reported total. Overlapping subsets or identical pages mean
the parameter is broken or ignored; aggregates built on it will be silently wrong.

    probe.py --base https://my.example.com/api --header "Api-Key: $KEY" \
        --paginate /classes/schedule --param page_start --limit-param page_limit \
        --limit 100 --pages 4 --extra start=2026-08-01 --extra finish=2026-08-31

Never prints the credential. Read-only: issues GETs only.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

ROW_KEYS = ("data", "items", "results", "rows", "records", "list")


def _headers(pairs: list[str]) -> dict[str, str]:
    out = {"Accept": "application/json", "User-Agent": "api-to-mcp-probe/1.0"}
    for p in pairs:
        if ":" not in p:
            sys.exit(f"--header must be 'Name: value', got {p!r}")
        k, v = p.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _rows(body: Any) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in ROW_KEYS:
            if isinstance(body.get(k), list):
                return body[k]
    return []


def _total(body: Any) -> Any:
    if isinstance(body, dict):
        for k in ("total", "total_count", "totalCount", "count", "meta"):
            v = body.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, dict):
                for kk in ("total", "total_count", "count"):
                    if isinstance(v.get(kk), int):
                        return v[kk]
    return None


def _ids(rows: list) -> list:
    out = []
    for r in rows:
        if isinstance(r, dict):
            for k in ("id", "uuid", "_id", "key"):
                if k in r:
                    out.append(r[k])
                    break
    return out


def _get(client: httpx.Client, path: str, params: dict) -> tuple[int, Any, str]:
    r = client.get(path, params=params or None)
    ctype = r.headers.get("content-type", "")
    if "json" not in ctype:
        # A rate limit or a WAF often answers in HTML; that is a finding, not a crash.
        return r.status_code, None, f"non-JSON ({ctype or 'no content-type'}) {r.text[:120]!r}"
    try:
        return r.status_code, r.json(), ""
    except ValueError:
        return r.status_code, None, f"unparseable body {r.text[:120]!r}"


VERDICT = {
    404: "absent — do not probe spellings of it",
    403: "EXISTS but wrong credential kind (admin/session route?) — stop here",
    401: "unauthorized — check the auth header shape",
    429: "rate limited — space requests and retry",
}


def scan(client: httpx.Client, paths: list[str], extra: dict, delay: float) -> None:
    print(f"{'status':>6}  {'rows':>5}  path / shape")
    print("-" * 72)
    for path in paths:
        try:
            code, body, note = _get(client, path, extra)
        except httpx.HTTPError as exc:
            print(f"{'ERR':>6}  {'-':>5}  {path}  {type(exc).__name__}: {exc}")
            continue
        rows = _rows(body)
        shape = note
        if not shape and body is not None:
            if isinstance(body, dict):
                shape = "keys: " + ", ".join(list(body)[:8])
            else:
                shape = f"{type(body).__name__}"
            tot = _total(body)
            if tot is not None:
                shape += f" · total={tot}"
        verdict = VERDICT.get(code, "")
        print(f"{code:>6}  {len(rows) or '-':>5}  {path}  {shape}")
        if verdict:
            print(f"{'':>6}  {'':>5}  -> {verdict}")
        time.sleep(delay)


def paginate(
    client: httpx.Client, path: str, param: str, limit_param: str | None,
    limit: int, pages: int, zero_based: bool, extra: dict, delay: float,
) -> None:
    print(f"paginating {path} via {param} (limit {limit_param}={limit}), {pages} pages\n")
    seen: list[list] = []
    total = None
    for i in range(pages):
        page = i if zero_based else i + 1
        params = dict(extra)
        params[param] = page * limit if param in {"start", "offset", "skip"} else page
        if limit_param:
            params[limit_param] = limit
        code, body, note = _get(client, path, params)
        if code != 200 or body is None:
            print(f"page {page}: HTTP {code} {note} — {VERDICT.get(code, '')}")
            return
        rows = _rows(body)
        ids = _ids(rows)
        total = _total(body) if total is None else total
        seen.append(ids)
        print(f"page {page}: {len(rows)} rows, {len(set(ids))} distinct ids, {params}")
        time.sleep(delay)

    flat = [i for page in seen for i in page]
    union = set(flat)
    print("\n--- verdict ---")
    print(f"rows fetched: {len(flat)} · distinct ids: {len(union)} · reported total: {total}")
    if not flat:
        print("no ids found in rows — check the row key or id field name")
        return
    if len(union) < len(flat) * 0.95:
        print("BROKEN: pages overlap. Do not paginate — query narrow windows under the")
        print("row cap and merge by id. Any aggregate built on these pages is wrong.")
    elif len(seen) > 1 and all(set(p) == set(seen[0]) for p in seen[1:]):
        print(f"IGNORED: every page identical — {param!r} is not the parameter this")
        print("endpoint reads. Try limit+start-as-offset, or a cursor.")
    elif isinstance(total, int) and len(union) < min(total, len(seen) * limit) * 0.95:
        print("SHORT: fewer distinct ids than expected against total — verify before trusting.")
    else:
        print("OK: pages look disjoint and consistent with total.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--header", action="append", default=[], help="'Name: value', repeatable")
    ap.add_argument("--paths", nargs="*", default=[], help="paths to scan")
    ap.add_argument("--paginate", help="single path to test pagination on")
    ap.add_argument("--param", default="page", help="page/offset param name")
    ap.add_argument("--limit-param", default="limit")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--zero-based", action="store_true")
    ap.add_argument("--extra", action="append", default=[], help="k=v query param, repeatable")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds between calls")
    ap.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args()

    if not a.paths and not a.paginate:
        ap.error("give --paths and/or --paginate")
    extra = {}
    for kv in a.extra:
        if "=" not in kv:
            ap.error(f"--extra must be k=v, got {kv!r}")
        k, v = kv.split("=", 1)
        extra[k] = v

    with httpx.Client(
        base_url=a.base.rstrip("/"), headers=_headers(a.header),
        timeout=a.timeout, follow_redirects=True,
    ) as client:
        if a.paths:
            scan(client, a.paths, extra, a.delay)
        if a.paginate:
            if a.paths:
                print()
            paginate(
                client, a.paginate, a.param, a.limit_param, a.limit,
                a.pages, a.zero_based, extra, a.delay,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
