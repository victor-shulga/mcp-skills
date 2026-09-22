---
name: api-to-mcp
description: >-
  Turn any REST/HTTP API into a working local MCP server that Claude Code can call: recon of the API
  surface, job-shaped tool design, a single-file Python server (uv + PEP 723 inline deps), a --check
  smoke test, safe secret handling, user-scope registration and a README. Use WHENEVER the user wants
  to wrap an API, service or product as MCP: "зроби MCP з цього API", "оберни API в MCP", "хочу свій
  MCP-сервер для X", "build an MCP server for <service>", "wrap this OpenAPI spec as MCP", "make a
  connector for X", "додай тули для X у Claude", "напиши mcp server", "expose our internal API to
  Claude", or when they paste API docs / an OpenAPI spec / a curl example and ask for tools. Also for
  extending, debugging or re-registering an existing local MCP server (server does not load, tools
  missing, token 401). Do NOT use for n8n workflows (n8n-workflow-builder) or a remote multi-tenant
  OAuth server (Cloudflare Workers, see agents-sdk).
---

# API → MCP

Wrap an API as a local MCP server that is actually pleasant to use: few tools, each answering a whole question, honest about what it does not know.

The canon here is extracted from three servers already running in this workspace. Read the relevant one when a decision is unclear — they are the ground truth, this file is the summary:

- **`fitcrm-mcp/server.py`** — a fitness CRM whose API is barely documented. The hardest-won file: real endpoint paths that the docs slug did not match, broken pagination that made a revenue number wrong by 3×, one field that lies and one that turned out to be trustworthy. Its `get_class_schedule` docstring is the model of how to record that (see "Write the traps into the docstring" below).
- **`tidycal-mcp/server.py`** — a documented API with an OpenAPI spec. Cleanest structure: single `_request` layer, read/write annotations, env-gated writes, `--check`, and a slimmed composite (`tidycal_agenda`).
- **`victor-mcp/server.py`** — composite tools over a stack (Twenty + Notion) where raw CRUD already exists elsewhere. Source of `get_guide`, two-phase `confirm_token`, honest empties, graceful degradation.

## Decide first: is a server the right answer?

Before writing anything, check in this order:

1. **Does an official MCP already exist?** Many products ship one. Search their docs. A hosted server that covers the job wins — less to maintain.
2. **If it exists but is gated or crippled** (read-only, Pro-only, missing the endpoints needed), a local server is justified — say so in the README, as tidycal-mcp does.
3. **One user, one machine → local stdio server.** Multi-tenant, clients logging in with their own accounts → that is OAuth 2.1 + DCR + PKCE + three well-known endpoints, a different and much larger job; use the `agents-sdk` skill instead.
4. **Would a plain script do?** If the user needs one number once a month, a script beats a server. A server earns its place when Claude must reach the data mid-conversation, repeatedly, unprompted.

## Step 1 — Recon the API

Never design tools from memory of the API. Get the real surface first. Details and probe commands: `references/recon.md`. What must be nailed down before any code:

- **Auth**: header shape, token type, where the user gets one, whether the plan gates API access.
- **Base URL** and whether it is account-specific (`https://<tenant>.example.com/rest/`).
- **Pagination**: page numbers, cursors, or none — and the page size cap.
- **Enum values**: many APIs reject the whole query on one unknown enum (Twenty does, with a bare 400). Read them from the metadata endpoint, don't guess.
- **Units and shapes**: money in cents/micro-units, timestamps' timezone, IDs numeric vs UUID.
- **Response weight**: if a list row inlines 4 KB of nested objects, tool output must be slimmed (see Step 2).
- **Rate limits** and what a 429 looks like.
- **Write side-effects**: which calls send email, charge money, or are irreversible.

If the vendor publishes an OpenAPI spec, save it into the repo next to `server.py` (`<service>_openapi.json`) — it is the reference for later edits. If they publish only a Redoc page, extract the spec from it and note in the README that it was extracted, not fetched.

**When the API is barely documented** — the common case for niche SaaS — recon is most of the work, and guessing paths is the expensive way to do it:

- The **base URL is often not in the public docs at all**, only inside the product (the fitness CRM: Settings → Integrations → API). Ask the user to look there before probing anything.
- **403 and 404 mean different things.** 404 = no such endpoint; 403 = it exists but this credential is the wrong kind (typically an admin/browser-session route that an API key will never open). Blind path-probing burned ~45 requests on the fitness CRM before that distinction stopped the search — and the endpoint that was actually needed only appeared after support enabled it.
- **The docs slug is not the path.** the fitness CRM's page was `classes/group-class-schedule-grid`; the real route was `/classes/schedule`. Take paths from request examples and the sidebar, not from page titles.
- **Verify pagination empirically instead of trusting the parameter names.** the fitness CRM's `page_start` returned overlapping random subsets: 4 pages × 100 rows on a `total=222` month yielded 400 rows with only 103 distinct ids, and an early revenue figure came out 3× wrong. Fetch two pages, intersect the ids, and compare the union against `total` before believing any of it. On `/clients` the working combination turned out to be `limit` + `start`-as-offset, while `page`/`page_start`/`offset` were silently ignored and kept returning page 1 — silent ignoring is the norm, not an error.
- **Check every number against one known truth** before a tool built on it ships. `capacity - free_places` matched the studio's own monthly count (330 vs 330), so it became the attendance source; `price` was a flat 500 on every row — the list rate, not what was paid — so revenue never came from there. A field that returns plausible values is not the same as a field that is true.
- **Rate limits can answer in HTML.** A fast loop of ~40 calls came back as a non-JSON 429 page, which crashes a naive `.json()`. Space the requests and treat a non-JSON body as a real, reportable outcome.
- **Future/empty records skew aggregates**: counting not-yet-happened classes (every seat free) dragged a fill rate from 21.7% to 18.7%. Whatever the tool computes, decide explicitly which rows are in scope.

## Step 2 — Design the tool surface (the part that matters)

A mechanical 1:1 mapping of endpoints to tools is the default failure. It produces 40 tools, each returning noise, and the model spends four round trips answering one question. Design for **jobs**, not endpoints.

**Rules that carry their weight:**

- **Name tools `<service>_<verb>_<thing>`** when the server is a thin wrapper (`tidycal_list_bookings`), or plain job names (`queue_today`, `account_card`) when it is a composite over a stack the model already has raw CRUD for. Two servers exposing `list_contacts` is a confusion the prefix prevents.
- **Add at least one composite tool** that answers a real question in one call. `account_card(name)` returns company facts + contacts + open deals + live signals + matching docs — otherwise five calls. This is usually the reason the server exists at all.
- **Slim the payload.** `tidycal_agenda` is `list_bookings` minus the inlined booking type, email templates and share URLs, plus local time and flattened answers — same data, ~30× smaller. Token budget is the user's money.
- **Split read from write with annotations** (`ToolAnnotations(read_only_hint=True, ...)`), and gate writes behind an env flag so the default surface cannot change anything.
- **Two-phase confirm for anything that writes to a live system**: first call returns `{preview, confirm_token}` and changes nothing; second call with identical args plus the token applies it. The guard lives on the server, not in a prompt — see `references/patterns.md`.
- **`get_guide(task)`** when a job takes several tools in a specific order. The playbook lives in the server, so the model does not have to guess the sequence and the user does not have to re-explain it every session.
- **Free `estimate_*` before anything that spends credits or money.**
- **Empty is a result, not silence.** A bucket with no rows comes back with `count: 0` and `empty_because` — "no rows" and "the field feeding this is never filled" are different facts, and the model must be able to tell the user which one it is.
- **Degrade, don't die.** If a second integration is unconfigured, its tools return `{"<x>_wired": false, "reason": ...}` and the rest of the server keeps working.
- **Write the traps into the docstring.** The tool docstring is the only documentation the model reads at the moment it calls the tool, so every hard-won fact about that endpoint belongs there, dated: which parameter is broken and what to do instead, which returned field cannot be trusted, which field was validated against real numbers, what a difference between two reads actually means. `fitcrm_get_class_schedule` carries exactly this, and it is why nobody has to rediscover the pagination bug. A trap recorded in a commit message or a memory file will not be in front of the model when it matters.

**The instructions block** (the `instructions=` argument to `MCPServer`) is loaded into every session that has the server. Make it: a glossary of the domain nouns, the two or three core workflows, and what honesty looks like here. Keep vendor cheerleading out of it — an instructions block that nudges the model to spend the user's credits is the fastest way to lose trust in the whole server.

Before coding, show the user the planned tool list — name, one-line purpose, read/write — and get a yes. Tools are cheap to write and expensive to unwind once the model has learned them.

## Step 3 — Scaffold

```bash
python3 ~/.claude/skills/api-to-mcp/scripts/scaffold.py \
  --slug acme-mcp --server-name acme --title "Acme" \
  --base-url https://api.acme.com/v1 --env-prefix ACME \
  --dir "$HOME/projects"
```

Writes `<dir>/<slug>/` with `server.py` (executable, uv shebang, PEP 723 inline deps — no venv, no requirements.txt), `README.md` skeleton, and `.gitignore`. The template already contains the request layer, error mapping, token loading, write gate, `get_guide`, the confirm-token helper and `--check`; you fill in the tools.

Then replace the two sample tools with the real ones from Step 2. Keep it a single file — one `server.py` is easy to read, copy and debug. Switch to a folder with `pyproject.toml` run as `uv --directory <dir> run server.py` (the `fitcrm-mcp` shape) once the server grows sibling modules that share its dependencies — report scripts, caches, exports.

## Step 4 — Wire the secret

Two moving parts, and both traps are known:

- **Claude Code launches from the GUI and never sources `~/.zshrc`**, so an exported env var is invisible to the server. Always support a `0600` token file as the fallback (`~/.config/<slug>/token`) — the template does.
- **The user pastes a bare key**, not `KEY="..."`. The template's loader strips whitespace and accepts either; do not add parsing that requires the `KEY=` form, or the token silently comes out empty and the API answers 403, which looks like a bad key.

Hand the secret over in **two steps**, never a one-liner with a placeholder in the middle (it puts the key in shell history and makes him edit inside a command):

```bash
mkdir -p ~/.config/acme && touch ~/.config/acme/token && chmod 600 ~/.config/acme/token && open -a TextEdit ~/.config/acme/token
```

Then, as plain text: paste the token into the window that opened, save, close. Nothing else in the file.

## Step 5 — Verify before registering

```bash
uv run --script "<path>/server.py" --check
```

`--check` calls the cheapest authenticated endpoint and prints the account identity plus whether writes are enabled. It must never print the token. A server that fails `--check` will fail silently inside Claude Code — the tools simply will not appear, and the next half hour goes into the wrong hypothesis.

## Step 6 — Register

Always `--scope user`. Project scope writes to a shared/committed `.mcp.json` (secret leak) and is bound to the **exact** cwd — a subfolder is a different project and the server is invisible there. Nested `claude mcp` from inside a session fails with EPERM, so give the user the command to run in their own terminal:

```bash
claude mcp add acme --scope user -- ~/.local/bin/uv run --script "$HOME/projects/acme-mcp/server.py"
```

Inspect the current state without the CLI:

```bash
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.claude.json')));print(sorted(d.get('mcpServers') or {}))"
```

Servers load only at session start — tell the user to restart Claude Code, otherwise the tools are absent and it looks broken.

## Step 7 — README and memory

The README carries a `## Requirements & integrations` table (Integration | Used for | Required? | Auth/setup) — this is the repo definition of done in this workspace. Add: env var table with defaults, the setup steps, the `--check` line, the tool list split read/write, and a Notes section for API quirks found during recon (missing endpoints, extraction sources, why this exists if an official server also does).

Then write one memory file for the server: what it wraps, where the token lives, the traps found, what is not tested yet. The next session starts from that file rather than re-deriving the API.

## Pre-ship checklist

- `--check` passes and prints no secret ✓
- read tools annotated read-only, writes gated by env flag ✓
- at least one composite tool that saves round trips ✓
- big list responses slimmed ✓
- empty results carry `empty_because` ✓
- optional integrations degrade with a reason instead of throwing ✓
- token loads from env **and** a 0600 file; bare key accepted ✓
- registered `--scope user`, session restarted, tools visible ✓
- README has the Requirements & integrations table ✓
- no token, no client name, no secret in anything that could be pushed public ✓

## Reference files

- `references/recon.md` — finding the real base URL and paths, probing auth, testing pagination for real, discovering enums, validating numbers against a known truth, and what to write down. Read at Step 1.
- `references/patterns.md` — the server patterns in full, with the reasoning: request layer, job-shaped tools, slimming, docstrings as trap logs, write gates, two-phase confirm, guides, honest empties, degradation, token loading, runtime choice, registration, instructions blocks. Read before designing tools.
- `scripts/probe.py` — read-only API prober: path scan with 403-vs-404 verdicts, and a pagination test that reports page overlap and distinct ids. Use it at Step 1.
- `scripts/scaffold.py` — writes the repo. `assets/server_template.py` and `assets/README_template.md` are what it renders; read them only if editing the template itself.
