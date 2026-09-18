# Recon: learn the real API before designing tools

Goal of this stage: a short written record of how the API actually behaves, so tool design is a decision and not a guess. Everything here ends up in the README's Notes section and in the tool docstrings.

## 1. Get credentials and the true base URL

Ask the user for the API settings page, not for the docs. In niche SaaS the host is frequently absent from public documentation and visible only inside the product (the fitness CRM: *Settings → Integrations → API*, which is also where the key is generated). Note whether there is a sandbox host — it lets writes be tested without touching live data.

Auth header shapes seen in the wild, in rough order of frequency:

```
Authorization: Bearer <token>     # TidyCal, Twenty, Notion, most modern APIs
Api-Key: <key>                    # the fitness CRM
X-API-Key: <key>
Authorization: <key>              # Instantly — no Bearer prefix
```

Getting this wrong produces a 401/403 that reads exactly like a bad key. Confirm it from a request example in the docs, then verify with one curl.

## 2. Probe the surface

```bash
python3 ~/.claude/skills/api-to-mcp/scripts/probe.py \
  --base https://my.example.com/api --header "Api-Key: $KEY" \
  --paths /me /clients /memberships /leads
```

The script prints status, body shape, row count and the top-level keys for each path, and it distinguishes the two answers that matter:

- **404** — no such endpoint. Move on; do not try ten spellings of it.
- **403** — the endpoint exists, but this credential is the wrong kind. Usually an admin route that expects a browser session and will never open with an API key. This is a stop signal, not a retry signal. On the fitness CRM, `/schedules`, `/lessons`, `/trainers` all answered 403 while the public `/classes/schedule` answered 200 — recognising that saved another round of probing.

Two more rules that come from the same server:

- Take paths from **request examples and the docs sidebar**, never from page titles or slugs. The documented slug was `classes/group-class-schedule-grid`; the working route was `/classes/schedule`.
- If an endpoint you clearly need is missing, **ask support to enable it** before building an elaborate workaround. Some are off per account.

## 3. Test pagination empirically

Parameter names are the least reliable part of any API, and a wrong assumption here silently corrupts every aggregate built on it.

```bash
python3 ~/.claude/skills/api-to-mcp/scripts/probe.py \
  --base https://my.example.com/api --header "Api-Key: $KEY" \
  --paginate /classes/schedule --param page_start --limit-param page_limit --limit 100 \
  --extra start=2026-08-01 --extra finish=2026-08-31
```

It fetches several pages and reports rows returned, **distinct ids**, overlap between pages, and the union against the reported `total`. Three outcomes to expect:

- Clean: distinct ids ≈ union ≈ total. Pagination works.
- **Overlapping subsets**: the fitness CRM returned 400 rows across four pages of 100 with only 103 distinct ids on a `total=222` month. A revenue figure built on that was 3× wrong. The fix was to never paginate — query narrow windows that fit under the row cap (a week ≈ 50 rows) and merge by `id`.
- **Silently ignored**: every page identical, because the parameter is not the one this endpoint reads. On `/clients` the working pair was `limit` + `start`-as-offset while `page`, `page_start` and `offset` all returned page 1 without complaint.

Record the verdict, with the date, in the docstring of the tool that touches that endpoint.

## 4. Discover enums and required fields

Some APIs reject an entire query on one unfamiliar enum value, with a bare 400 and no hint at which field caused it — Twenty does this, and its live values only exist in `/rest/metadata/fields` (662 fields, paginated). Read enums from the metadata endpoint and pin them as constants at the top of the server, with a comment saying they are read values, not guesses.

Also record: which fields are required on create, what the id type is (numeric vs UUID vs slug), and whether a `PUT` replaces or merges.

## 5. Validate the numbers against one known truth

Before any tool that reports a metric ships, reconcile it against a number the user already trusts — their own monthly report, an export, an invoice.

Two results from the same endpoint make the point. `capacity - free_places` on past classes matched the studio's manual visit count (Jul 330 vs 330, Jun 98%, May 105%), so it became the attendance source and replaced an earlier conclusion that attendance was unavailable. `price`, on the same rows, was a flat 500 on every lesson — the drop-in list rate rather than what anyone actually paid — so revenue was never taken from it. Both fields look equally plausible in the JSON.

Also settle what belongs in the aggregate: counting future classes, where every seat is free by definition, pushed a fill rate from 21.7% down to 18.7%.

## 6. Note the failure modes

- **Rate limits may answer in HTML.** ~40 rapid calls came back as a non-JSON 429 page; a naive `.json()` throws something unrelated to what happened. Space requests (~0.6 s), retry once, and treat a non-JSON body as a reportable outcome rather than a crash.
- **Caps that shape the design.** A hard 100-row or 200-record cap is not an inconvenience to work around silently — it decides the tool's shape (weekly windows, snapshot-and-diff).
- **Lifetime-only counters** (Aimfox-style) can't answer "this week" — a weekly delta needs a stored snapshot, which means a file, not a stateless tool.

## What to write down

A recon note, ~15 lines, that goes into the README Notes and the memory file:

```
base URL + where it came from · auth header shape · what --check hits
pagination: verdict + date + the workaround
caps: rows per call, records total
enums: source endpoint
fields that lie / fields validated against what
endpoints probed and absent (so nobody probes them again)
403 routes: which, and what credential they'd need
rate limit: shape of the response, safe spacing
writes: which calls are irreversible or send mail/charge money
```
