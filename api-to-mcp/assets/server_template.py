#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.0,<3", "httpx>=0.27"]
# ///
"""Local MCP server for the __TITLE__ API.

Auth: token in the __ENV_PREFIX___TOKEN env var, falling back to a 0600 file at
__TOKEN_FILE__ (Claude Code launches from the GUI and never sources ~/.zshrc,
so the env var alone is not enough).

Writes refuse to run unless __ENV_PREFIX___ALLOW_WRITES=1, so the default
surface cannot change anything upstream.

Run `uv run --script server.py --check` to verify the token without starting stdio.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

TOKEN_FILE = Path(
    os.environ.get("__ENV_PREFIX___TOKEN_FILE", "__TOKEN_FILE__")
).expanduser()
BASE_URL = os.environ.get("__ENV_PREFIX___BASE_URL", "__BASE_URL__")
TIMEOUT = float(os.environ.get("__ENV_PREFIX___TIMEOUT", "30"))
ALLOW_WRITES = os.environ.get("__ENV_PREFIX___ALLOW_WRITES", "").strip() in {"1", "true", "yes"}

# Regenerated on every process start, so a confirm_token never survives a restart.
_CONFIRM_SECRET = secrets.token_hex(16)


def _load_token() -> str:
    """Env first, then the 0600 file. Accepts a bare key or KEY="value" — the
    user pastes the raw token, and demanding the KEY= form yields an empty
    token and a 403 that looks like a bad key."""
    token = os.environ.get("__ENV_PREFIX___TOKEN", "").strip()
    if token:
        return token
    try:
        raw = TOKEN_FILE.read_text().strip()
    except OSError:
        return ""
    if "=" in raw.split("\n", 1)[0] and raw.split("=", 1)[0].isidentifier():
        raw = raw.split("=", 1)[1]
    return raw.strip().strip('"').strip("'")


TOKEN = _load_token()

mcp = MCPServer(
    "__SERVER_NAME__",
    version="0.1.0",
    instructions=(
        "__TITLE__ data, exposed as whole answers instead of raw rows.\n\n"
        "GLOSSARY\n"
        "- <noun>: what it means in this product, and which field carries its state.\n\n"
        "CORE WORKFLOWS\n"
        '- "What needs me?" -> <tool>. Buckets with no rows still come back, with the '
        "reason they are empty. Do not hide them.\n"
        "- Before any write -> call it once without confirm_token, show the preview, "
        "then call again with the token.\n\n"
        "HONESTY RULES\n"
        "- Never invent a field that came back null. Report it as unknown.\n"
        "- Say plainly what a call actually returned, including zero counts."
    ),
)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
)


class __ERRCLASS__(RuntimeError):
    pass


def _client() -> httpx.Client:
    if not TOKEN:
        raise __ERRCLASS__(
            "No __TITLE__ token found. Set __ENV_PREFIX___TOKEN, or write the token to "
            f"{TOKEN_FILE} (chmod 600). Get one at: __TOKEN_URL__"
        )
    return httpx.Client(
        base_url=BASE_URL,
        timeout=TIMEOUT,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
            "User-Agent": "__SLUG__/0.1",
        },
    )


def _request(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> Any:
    """One place for auth, timeouts and error shaping — every tool goes through it.

    Status codes are mapped to sentences that name the fix, because the model
    surfaces this text to the user and "401" alone sends them looking in the
    wrong place.
    """
    params = {k: v for k, v in (params or {}).items() if v is not None}
    json = {k: v for k, v in (json or {}).items() if v is not None} if json is not None else None
    with _client() as client:
        r = client.request(method, path, params=params, json=json)
    if r.status_code == 401:
        raise __ERRCLASS__("401 Unauthorized — token invalid, expired, or the plan lacks API access.")
    if r.status_code == 403:
        raise __ERRCLASS__("403 Forbidden — the token has no permission for this resource.")
    if r.status_code == 429:
        raise __ERRCLASS__("429 Too Many Requests — rate limit hit, retry later.")
    if r.status_code >= 400:
        raise __ERRCLASS__(f"{r.status_code} {r.reason_phrase}: {r.text[:500]}")
    if not r.content:
        return {"status": r.status_code, "body": None}
    try:
        return r.json()
    except ValueError:
        return {"status": r.status_code, "body": r.text[:2000]}


def _writes_guard() -> None:
    if not ALLOW_WRITES:
        raise __ERRCLASS__(
            "Write tools are disabled. Set __ENV_PREFIX___ALLOW_WRITES=1 in the server env to enable them."
        )


def _bucket(rows: list, why_empty: str) -> dict:
    """A bucket never disappears when empty — it carries the reason instead."""
    if rows:
        return {"count": len(rows), "rows": rows}
    return {"count": 0, "rows": [], "empty_because": why_empty}


def _confirm_token(action: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps([action, payload], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((_CONFIRM_SECRET + canonical).encode()).hexdigest()[:16]


GUIDES: dict[str, dict[str, str]] = {
    "__GUIDE_KEY__": {
        "title": "__GUIDE_TITLE__",
        "body_md": (
            "1. <first tool> — what to read from it.\n"
            "2. <second tool> — what decision it feeds.\n"
            "Rule: state what is missing rather than filling a gap with a guess."
        ),
    },
}


@mcp.tool(annotations=READ)
def get_guide(task: str | None = None) -> Any:
    """Step-by-step playbook for a multi-step task on this stack.

    Call it before starting one. Omit `task` to list the available guides.
    """
    if task is None:
        return {"available_guides": sorted(GUIDES)}
    guide = GUIDES.get(task)
    if not guide:
        return {"error": f"No guide '{task}'.", "available_guides": sorted(GUIDES)}
    return {"task": task, **guide}


# --------------------------------------------------------------------------
# Read tools — replace with the real surface
# --------------------------------------------------------------------------


@mcp.tool(annotations=READ)
def __SERVER_NAME___me() -> Any:
    """Identify the authenticated account. Cheapest call — also used by --check."""
    return _request("GET", "__ME_PATH__")


@mcp.tool(annotations=READ)
def __SERVER_NAME___list_items(page: int | None = None) -> Any:
    """List <things>. Slim the rows here if the API inlines nested objects."""
    return _request("GET", "__LIST_PATH__", params={"page": page})


# --------------------------------------------------------------------------
# Write tools — two-phase, and only when __ENV_PREFIX___ALLOW_WRITES=1
# --------------------------------------------------------------------------


@mcp.tool(annotations=WRITE)
def __SERVER_NAME___write_example(target: str, value: str, confirm_token: str | None = None) -> Any:
    """WRITE — changes live data. Two-phase.

    Call once without `confirm_token` to get a preview and a token; nothing is
    written. Show the preview to the user, and only on a yes call again with the
    same arguments plus the token. The token is bound to this process and to
    those exact arguments — change one character and it is rejected, by design.
    """
    _writes_guard()
    payload = {"target": target, "value": value}
    expected = _confirm_token("__SERVER_NAME___write_example", payload)
    if confirm_token is None:
        return {
            "preview": payload,
            "confirm_token": expected,
            "next": "Show this to the user. On a yes, call again with the same arguments plus confirm_token.",
        }
    if confirm_token != expected:
        return {"error": "confirm_token does not match these arguments (or the server restarted)."}
    return _request("POST", "__WRITE_PATH__", json=payload)


def _check() -> int:
    """Verify the token against the cheapest endpoint. Never prints the token."""
    try:
        me = _request("GET", "__ME_PATH__")
    except __ERRCLASS__ as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {json.dumps(me)[:300]}")
    print(f"writes: {'ENABLED' if ALLOW_WRITES else 'disabled (read-only)'}")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    mcp.run()
