# AGENTS.md — AI Agent Setup & Safety Guide for ProviderDeck

When a user asks you to install, configure, test, or run **ProviderDeck**, follow these exact steps and safety boundaries.

## Safety Boundaries (Non-Negotiable)
- **Read-only credentials:** NEVER modify, refresh, or rewrite the user's `~/.claude/.credentials.json`, `~/.codex/auth.json`, `~/.config/muse/auth.json`, or Hermes config/auth files.
- **Never expose secrets:** NEVER print API keys, OAuth tokens, JWTs, or private emails to stdout/chat/logs, and NEVER commit `.env`, `auth.json`, or `data/`.
- **No paid inference:** NEVER run model completion probes to verify quotas. ProviderDeck reads quotas strictly through official read-only endpoints and zero-turn CLI commands (`agy -p /usage --output-format json`, `muse usage --json`).

## Standard Installation Steps
1. Ensure Python 3.10+ is available.
2. Create an isolated virtual environment inside the repo:
   - Windows: `python -m venv .venv && .venv\Scripts\pip.exe install -r requirements.txt`
   - Linux/macOS: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. Verify the installation with unit tests:
   - Python: `.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v` (or `.venv\Scripts\python.exe` on Windows)
   - Frontend (if Node.js 18+ is available): `node tests/ui.test.cjs`
4. Optional API keys:
   - Only if the user provides keys or asks to connect API gateways, copy `.env.example` to `.env` (already gitignored) and populate only the requested keys.
   - Local CLI subscriptions (Claude Code, Codex, Antigravity `agy`, Muse Code `muse`) are auto-discovered read-only with no extra config needed.
   - On Windows with Muse Code installed in a non-Ubuntu WSL distro, set `MUSE_WSL_DISTRO=<distro>` in `.env`.
   - If `agy` is installed in a custom path, set `AGY_BIN=<path>` in `.env`.
5. Launch ProviderDeck:
   - Interactive (opens browser + Windows tray): `python launch.py`
   - Background without browser: `python launch.py --no-browser`
   - Stop cleanly: `python launch.py --stop`
6. Confirm health via `GET http://127.0.0.1:8766/api/health` (must return `{"ok": true, "app": "providerdeck", ...}`).
