#!/usr/bin/env python3
"""Scaffold a local MCP server repo from the api-to-mcp templates.

Writes <dir>/<slug>/ with server.py (executable), README.md and .gitignore, all
placeholders substituted. The result runs as-is: `uv run --script server.py --check`
hits __ME_PATH__ and fails with a clear message until you point it somewhere real.

Example:
    python3 scaffold.py --slug acme-mcp --server-name acme --title Acme \
        --base-url https://api.acme.com/v1 --env-prefix ACME \
        --dir "$HOME/projects"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"

GITIGNORE = """__pycache__/
.venv/
.env
*.token
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="repo folder name, e.g. acme-mcp")
    ap.add_argument("--server-name", required=True, help="MCP server name + tool prefix, e.g. acme")
    ap.add_argument("--title", required=True, help="human name of the service, e.g. Acme")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--env-prefix", required=True, help="env var prefix, e.g. ACME")
    ap.add_argument("--dir", required=True, help="parent directory for the new repo")
    ap.add_argument("--token-url", default="see the vendor's API docs")
    ap.add_argument("--me-path", default="/me", help="cheapest authenticated endpoint")
    ap.add_argument("--list-path", default="/items")
    ap.add_argument("--write-path", default="/items")
    ap.add_argument("--token-file", default=None, help="default ~/.config/<server-name>/token")
    ap.add_argument("--force", action="store_true", help="overwrite an existing folder")
    a = ap.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9_]*", a.server_name):
        print("--server-name must be a lowercase python identifier (it prefixes tool names)", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", a.env_prefix):
        print("--env-prefix must be UPPER_SNAKE", file=sys.stderr)
        return 1

    out = Path(a.dir).expanduser() / a.slug
    if out.exists() and not a.force:
        print(f"{out} already exists — pass --force to overwrite", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)

    server_path = out / "server.py"
    token_file = a.token_file or f"~/.config/{a.server_name}/token"
    subs = {
        "__SLUG__": a.slug,
        "__SERVER_NAME__": a.server_name,
        "__TITLE__": a.title,
        "__BASE_URL__": a.base_url.rstrip("/"),
        "__ENV_PREFIX__": a.env_prefix,
        "__TOKEN_FILE__": token_file,
        "__TOKEN_URL__": a.token_url,
        "__ME_PATH__": a.me_path,
        "__LIST_PATH__": a.list_path,
        "__WRITE_PATH__": a.write_path,
        "__SERVER_PATH__": str(server_path),
        "__ERRCLASS__": "".join(p.capitalize() for p in re.split(r"[^a-zA-Z0-9]", a.title) if p) + "Error",
        "__GUIDE_KEY__": "first_task",
        "__GUIDE_TITLE__": "Rename this to the job it describes",
    }

    def render(name: str) -> str:
        text = (ASSETS / name).read_text()
        for k, v in subs.items():
            text = text.replace(k, v)
        return text

    server_path.write_text(render("server_template.py"))
    server_path.chmod(0o755)
    (out / "README.md").write_text(render("README_template.md"))
    (out / ".gitignore").write_text(GITIGNORE)

    leftovers = sorted(set(re.findall(r"__[A-Z_]+__", server_path.read_text())))
    print(f"created {out}")
    print(f"  server.py   {server_path}")
    print(f"  README.md   {out / 'README.md'}")
    print(f"  token file  {token_file} (create it, chmod 600, paste the bare token)")
    if leftovers:
        print(f"  unsubstituted placeholders left in server.py: {', '.join(leftovers)}")
    print(f"\nnext: replace the sample tools, then\n  uv run --script \"{server_path}\" --check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
