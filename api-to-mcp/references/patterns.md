# Server patterns

The reasoning behind each pattern in the template. Ordered by how much difference it makes.

## 1. One request layer

Every tool goes through a single `_request()` that owns auth, timeout, param cleaning and error shaping. Tools then read as one line each, and a change to auth or a new status code is one edit rather than thirty.

Map status codes to sentences that name the fix, because the model relays this text to the user:

```python
if r.status_code == 401:
    raise AcmeError("401 Unauthorized — token invalid, expired, or the plan lacks API access.")
if r.status_code == 403:
    raise AcmeError("403 Forbidden — the token has no permission for this resource.")
```

`401` and `403` send someone to different places: one to the token, one to the plan or the credential type. A bare `403 Forbidden` costs half an hour of looking in the wrong file.

Strip `None` params before sending. Optional arguments are then just optional, with no `?page=None` reaching the API.

Non-JSON bodies are a real outcome, not an exception — rate limiters and WAFs answer in HTML. Return `{"status": ..., "body": text[:2000]}` instead of letting `.json()` throw something unrelated to what happened.

## 2. Job-shaped tools, not endpoint mirrors

A 1:1 endpoint mapping is the default failure: forty tools, each returning noise, four round trips per question. Two shapes actually work, and which one depends on whether the model already has raw CRUD for this data.

**Thin wrapper** (nothing else exposes this API): mirror the useful endpoints with a `<service>_` prefix, and add one or two composites on top. `tidycal-mcp` has twelve reads and four gated writes, plus `tidycal_agenda`.

**Composite over a stack** (raw CRUD already exists via other servers): expose only whole answers. `victor-mcp` has five tools over Twenty + Notion — `account_card(name)` returns company facts, contacts, open deals, live signals and matching docs in one call, replacing five. That composite is the entire reason the server exists.

## 3. Slim the payload

`tidycal_agenda` is `list_bookings` minus the inlined booking type, email templates and share URLs, plus local start time and the booking-form answers flattened into a map. Same information, roughly 30× smaller.

Do this whenever a list row inlines nested objects. Write an explicit `_slim_x(row)` returning the fields a human would ask about; the ~4 KB of vendor scaffolding per row is the user's token budget.

## 4. Write the traps into the docstring

The docstring is the only documentation the model sees at the moment it calls the tool. Anything learned the hard way about that endpoint belongs there, dated — not in a commit message, not in a memory file, both of which will be absent when it matters.

`fitcrm_get_class_schedule` is the model of this. Its docstring carries: pagination is broken and what to do instead (with the numbers that proved it), which field is trustworthy and against what it was validated, which field lies and why, and what a difference between two weekly reads actually means (a cancellation or a substitute trainer, not a schedule change). Anyone calling it — model or human — gets the accumulated knowledge for free.

Worth recording:

- a parameter that is broken or silently ignored, plus the workaround
- a field that cannot be trusted, and what to use instead
- a field that *was* validated, against which external number, on what date
- caps that shape usage ("keep every request under the 100-row cap")
- what an unexpected difference between two reads means

## 5. Read/write split and the write gate

Annotate reads, and gate writes behind an env flag so the default surface cannot change anything:

```python
READ  = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)
```

`_writes_guard()` raises unless `<PREFIX>_ALLOW_WRITES=1`. The point is that a mistake takes two independent things going wrong — the model deciding to write *and* the server being started in write mode — and turning writes off is one unset variable plus a restart, with no code edit.

## 6. Two-phase confirm

For anything that writes to a live system, the first call returns a preview and a token and changes nothing; the second call with identical arguments plus that token applies it.

```python
_CONFIRM_SECRET = secrets.token_hex(16)   # per process — a token dies with a restart

def _confirm_token(action: str, payload: dict) -> str:
    canonical = json.dumps([action, payload], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((_CONFIRM_SECRET + canonical).encode()).hexdigest()[:16]
```

The token is bound to the exact arguments, so an edited preview is rejected — the user approves what will actually happen, not something close to it. A prompt asking the model to confirm first is a request; this is a mechanism.

## 7. `get_guide(task)`

When a job needs several tools in a set order, keep the playbook in the server as a `GUIDES` dict exposed through one read tool. The sequence then lives next to the tools it describes and stays right when they change, instead of being re-explained every session or half-remembered.

Guides are also where the honesty rules go: *"a bucket that is empty stays visible with its reason — do not summarise the queue as 'nothing to do' when a bucket is empty only because a field is unfilled."*

## 8. Honest empties

```python
def _bucket(rows, why_empty):
    if rows:
        return {"count": len(rows), "rows": rows}
    return {"count": 0, "rows": [], "empty_because": why_empty}
```

"No rows" and "the field feeding this is never filled" are different facts about the business, and only the server knows which one happened. `queue_today` returning empty buckets with reasons is what surfaced that 674 of 790 companies were stuck on one band and the signal detector was not being maintained — a stateless `[]` would have read as "nothing to do today".

## 9. Degrade with a reason

When a second integration is optional, unconfigured means its tools return `{"notion_wired": false, "reason": ...}` while the rest of the server keeps working. An import-time crash takes down every tool, including the ones that need nothing.

Distinguish *not shared* from *not found*: a valid Notion token sees nothing until a page is explicitly shared with the connection, and reporting "not found" sends the user hunting for a page that exists. Same shape for a scope-limited API key.

## 10. Search endpoints that never return empty

Notion's `/search` answers a no-match query with recent pages rather than nothing. Passed through unfiltered, a company card would show a dozen unrelated documents as "its docs". Filter on your side — match the whole phrase, or require most of the query's words — and say plainly when nothing matched.

Assume any fuzzy search endpoint does this until proven otherwise.

## 11. `--check`

`--check` calls the cheapest authenticated endpoint, prints the account identity and whether writes are enabled, and never prints the token. Without it, a broken server is invisible: Claude Code starts it, stdio fails, the tools simply are not there, and the next half hour goes into the wrong hypothesis.

## 12. Token loading

Env var first, then a `0600` file:

```python
def _load_token() -> str:
    token = os.environ.get("ACME_TOKEN", "").strip()
    if token:
        return token
    return TOKEN_FILE.read_text().strip()   # guarded; accepts a bare key
```

Both halves are needed. Claude Code launches from the GUI and never sources `~/.zshrc`, so an exported variable is invisible to it. And the user pastes a **bare** token, not `KEY="..."` — a parser that demands the `KEY=` form yields an empty token and a 403 that looks exactly like a bad key.

Where the token can be reused from an already-configured server (`victor-mcp` reads the `twenty` entry in `~/.claude.json`), do that — one secret in one place beats two copies drifting apart.

## 13. Runtime: PEP 723 or `uv --directory`

Default to a single `server.py` with the uv shebang and inline deps:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.0,<3", "httpx>=0.27"]
# ///
```

No venv, no lockfile, no requirements.txt, and `uv` supplies its own interpreter — which matters because the system Python here is 3.9 and the MCP SDK needs ≥3.10.

The alternative, used by `fitcrm-mcp`, is a folder with `pyproject.toml` run as `uv --directory <dir> run server.py`. Prefer it once the server grows sibling modules (report scripts, caches, exports) that need the same dependencies.

`MCPServer` (mcp 2.x) and `FastMCP` (mcp 1.x) both work; new servers use `MCPServer` for the `ToolAnnotations` support.

## 14. Registration and scope

Always `--scope user`. Project scope writes to a shared, committable `.mcp.json` — and if auth is passed as a header or `--env`, the key goes in as plaintext. Project scope is also bound to the *exact* cwd: a subfolder is a different project where the server does not exist, and `claude mcp remove --scope local` from there silently finds nothing.

```bash
claude mcp add acme --scope user -- ~/.local/bin/uv run --script "/abs/path/acme-mcp/server.py"
```

Nested `claude mcp` from inside a session fails with EPERM, so hand the user the command for their own terminal. Inspect state without the CLI:

```bash
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.claude.json')));print(sorted(d.get('mcpServers') or {}));[print(p,sorted(v.get('mcpServers') or {})) for p,v in d.get('projects',{}).items() if v.get('mcpServers')]"
```

Servers load only at session start — a new server needs a restart before its tools appear.

## 15. The instructions block

`instructions=` is loaded into every session that has the server, so it is the highest-leverage text in the file and the easiest to abuse. Three parts earn their space: a glossary of the domain nouns and which field carries each one's state; the two or three core workflows ("what needs me?" → this tool → then that one); and what honesty looks like here ("never invent a field that came back null", "say plainly what a call returned, including zero counts").

Keep vendor incentives out of it. Telegrin's block nudges the model to optimise for leads and not to economise on credits — the strongest lever over an agent's behaviour, and the fastest way to lose the user's trust in the whole server. Never reproduce that in a server built for someone else.
