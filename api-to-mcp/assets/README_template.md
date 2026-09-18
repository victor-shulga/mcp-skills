# __SLUG__

Local MCP server over the __TITLE__ API. Single file, no build step, runs through `uv`.

Read tools are always on. Write tools refuse to run unless `__ENV_PREFIX___ALLOW_WRITES=1`
is set in the server environment, so the default surface cannot change anything upstream.

## Requirements & integrations

| Integration | Used for | Required? | Auth/setup |
|---|---|---|---|
| __TITLE__ API (`__BASE_URL__`) | all tools | yes | personal token in `__ENV_PREFIX___TOKEN`; sent as `Authorization: Bearer <token>` |
| `uv` | runs the script and resolves deps (PEP 723 header) | yes | installed at `~/.local/bin/uv` |
| Python ≥ 3.10 | `mcp` 2.x requires it; system Python is 3.9 | yes | `uv` provides its own interpreter |
| `mcp` ≥ 2.0, `httpx` ≥ 0.27 | MCP server + HTTP client | yes | installed automatically on first run |

## Setup

1. Create a token: __TOKEN_URL__
2. Store it. The server reads `__ENV_PREFIX___TOKEN` first, then falls back to
   `__TOKEN_FILE__`. The file matters: Claude Code launches from the GUI and never
   sources `~/.zshrc`, so the env var alone is not visible to it.

   ```bash
   mkdir -p $(dirname __TOKEN_FILE__) && touch __TOKEN_FILE__ && chmod 600 __TOKEN_FILE__ && open -a TextEdit __TOKEN_FILE__
   ```

   Paste the bare token into the window that opens, save, close. Override the location
   with `__ENV_PREFIX___TOKEN_FILE`.

3. Verify — prints the account, never the token:

   ```bash
   uv run --script "__SERVER_PATH__" --check
   ```

4. Register in **user** scope (project scope would put the path in a shared file and is
   bound to the exact folder):

   ```bash
   claude mcp add __SERVER_NAME__ --scope user -- ~/.local/bin/uv run --script "__SERVER_PATH__"
   ```

5. Restart Claude Code — MCP servers load only at session start.

## Env

| Var | Default | Meaning |
|---|---|---|
| `__ENV_PREFIX___TOKEN` | falls back to the token file | bearer token |
| `__ENV_PREFIX___TOKEN_FILE` | `__TOKEN_FILE__` | 0600 file holding that token |
| `__ENV_PREFIX___BASE_URL` | `__BASE_URL__` | API base |
| `__ENV_PREFIX___ALLOW_WRITES` | unset (read-only) | `1` enables write tools |
| `__ENV_PREFIX___TIMEOUT` | `30` | seconds |

## Tools

Read: `get_guide`, `__SERVER_NAME___me`, `__SERVER_NAME___list_items`.

Write (gated, two-phase): `__SERVER_NAME___write_example`.

## Notes

- API quirks found during recon: pagination shape, enum traps, units, missing endpoints.
- Why this server exists if the vendor also ships one.
