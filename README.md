# mcp-skills — your own MCP servers over other people's APIs

One Claude Code skill and one worked example for the infrastructure block of a GTM system: when a
connector you need does not exist, or the one that exists returns raw rows instead of answers, wrap
the API yourself.

Part of the GTM-system methodology by [Victor Shulga](https://victorshulga.com) (Fractional CRO).

## Install

```bash
npx skills add victor-shulga/mcp-skills
```

Then call it in Claude with `/api-to-mcp`.

## What is inside

| Path | What it is |
|---|---|
| `api-to-mcp/` | The skill. Seven steps: recon of the API surface → job-shaped tool design → single-file Python server (uv + PEP 723 inline deps) → safe secret handling → `--check` smoke test → user-scope registration → README. Ships `scripts/scaffold.py`, `scripts/probe.py`, a server template and a README template, plus two reference files: `references/recon.md` (how to find the real endpoints when the docs lie) and `references/patterns.md` (15 patterns with the reasoning behind each). |
| `examples/gsc-mcp/` | A finished server built with the skill: Google Search Console as a read-only local MCP with six tools (`sites`, `performance`, `compare`, `inspect_url`, `sitemaps`, `index_coverage`). Runs with `uv run --script server.py`, needs a Google service account added as a user in Search Console. Read its README and `server.py` to see what the skill's output looks like on a real API. |

## The idea

A generic connector gives you CRUD. Your work needs answers: this week's numbers per sender, the
account card in one call, the delta against the previous window. The skill builds *thin composites*
that compute what the API itself cannot, and puts every trap the API has (broken pagination, a
docs slug that is not the real path, 403 that means "wrong kind of key") straight into the tool
docstring — the only documentation the model reads at the moment it calls the tool.

Servers built this way in production: a fitness-club CRM, a scheduling tool, a LinkedIn outreach
platform (weekly numbers per SDR), a CRM + docs composite, and Search Console (the example here).

## Requirements & integrations

| Integration | Used for | Required? | Auth / setup |
|---|---|---|---|
| Claude Code | running the skill and registering servers | yes | claude.com/claude-code |
| `uv` | running generated servers with inline deps | yes | astral.sh/uv |
| Python 3.11+ | generated servers | yes | comes with uv |
| The target API's key | whatever you wrap | per server | stored in `~/.config/<server>/token`, never in git |
| Google service account | only for `examples/gsc-mcp` | optional | see the example's README |

## License

MIT © Victor Shulga
