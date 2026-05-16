"""Scaffold the dedicated osint-goblin PKM vault + project-scoped settings.

Vault: ~/Documents/osint-goblin-vault
  - 01-Projects/osint-goblin/       project notes home
  - 03-Resources/Development/       reusable patterns
  - 05-Templates/                   note templates (scaffold later)
  - .obsidian/                      Obsidian config marker

Settings: <repo>/.claude/settings.json
  env.VAULT_PATH                    -> dedicated vault path
  permissions.allow                 -> includes obsidian-pkm MCP wildcard

Project-scoped so jobbot + other projects keep their own vault (or fall
back to the main Obsidian Vault). No global state mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

HOME = Path.home()
VAULT = HOME / "Documents" / "osint-goblin-vault"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_SETTINGS = PROJECT_ROOT / ".claude" / "settings.json"

para_dirs = [
    VAULT / "01-Projects" / "osint-goblin" / "development",
    VAULT / "01-Projects" / "osint-goblin" / "development" / "decisions",
    VAULT / "01-Projects" / "osint-goblin" / "development" / "debug",
    VAULT / "01-Projects" / "osint-goblin" / "research",
    VAULT / "01-Projects" / "osint-goblin" / "tasks",
    VAULT / "03-Resources" / "Development",
    VAULT / "05-Templates",
    VAULT / ".obsidian",
]
for d in para_dirs:
    d.mkdir(parents=True, exist_ok=True)

# Minimal .obsidian/app.json so the folder is a real vault
app_json = VAULT / ".obsidian" / "app.json"
if not app_json.exists():
    app_json.write_text(json.dumps({"alwaysUpdateLinks": True}, indent=2), encoding="utf-8")

# Welcome / index note so the vault isn't empty (MCP server requires >=1 .md)
index_md = VAULT / "_index.md"
if not index_md.exists():
    index_md.write_text(
        "---\ntags: [moc, project-index]\nproject: osint-goblin\n---\n\n"
        "# osint-goblin PKM vault\n\n"
        "Dedicated vault for the osint-goblin project (property-vetting OSINT tool).\n"
        "Separate from the main Obsidian Vault by design -- project-scoped so its\n"
        "notes don't bleed into the general-purpose vault.\n\n"
        "## Structure\n\n"
        "- `01-Projects/osint-goblin/development/` - devlog, ADRs, debug logs\n"
        "- `01-Projects/osint-goblin/research/` - research notes\n"
        "- `01-Projects/osint-goblin/tasks/` - task notes\n"
        "- `03-Resources/Development/` - reusable patterns + permanent notes\n"
        "- `05-Templates/` - note templates (scaffold via `npx obsidian-pkm init`)\n",
        encoding="utf-8",
    )

# Project-scoped settings.json
PROJECT_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
settings = {
    "env": {
        "VAULT_PATH": str(VAULT),
    },
    "permissions": {
        "allow": ["mcp__plugin_obsidian-pkm_obsidian-pkm__*"],
    },
}
PROJECT_SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")

# Report
print(f"VAULT: {VAULT}")
print(f"  exists: {VAULT.is_dir()}")
print(f"  .obsidian exists: {(VAULT/'.obsidian').is_dir()}")
print(f"  .md files: {sum(1 for _ in VAULT.rglob('*.md'))}")
print("  PARA dirs:")
for d in sorted(para_dirs):
    print(f"    {d.relative_to(VAULT)}")
print()
print(f"SETTINGS: {PROJECT_SETTINGS}")
print("  content:")
print(PROJECT_SETTINGS.read_text(encoding="utf-8"))
