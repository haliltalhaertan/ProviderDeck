# ProviderDeck

[Türkçe README](README.tr.md)

A **local, read-only, zero-telemetry** dashboard and Windows system tray monitor for tracking **AI coding subscription quotas** (5-hour & weekly windows), **API wallet balances**, and **workload-adjusted cheapest model routes** in one place.

Built with vanilla HTML/CSS/JS (zero frontend build step, zero CDN dependencies) and a lightweight Python loopback server.

---

## Why ProviderDeck?

Most usage trackers monitor only coding subscriptions *or* only API spend. ProviderDeck brings them together with strict honesty and security guarantees:

- **Coding Subscriptions (Weekly + 5-Hour Windows):**
  - Anthropic / Claude Code (`~/.claude/.credentials.json` or OAuth)
  - OpenAI ChatGPT / Codex (`~/.codex/auth.json`)
  - Google Antigravity (`agy -p /usage --output-format json` + `/credits`, zero model turns)
  - Meta Muse Code CLI (`muse usage --json`, native Linux/macOS or Windows via WSL)
  - GitHub Copilot & OpenCode Go / Zen
- **15+ API Wallets & Model Catalogs:**
  - OpenRouter, Vercel AI Gateway, Experiential Labs, UberLLM, Venice, VoidAI, Refuseless, OrcaRouter, Outrun, NanoGPT, NVIDIA NIM, Meta Model API, Nous Portal, and custom OpenAI-compatible gateways via `config.yaml`.
- **Workload Cost Estimator & Same-Model Comparison:**
  - Enter your actual request profile (input tokens, output tokens, cache-hit %) to rank routes by real estimated cost and compare identical canonical models side-by-side without false model equivalences.
- **Native Windows System Tray Icon:**
  - Hover tooltip shows positive USD balance plus weekly & 5-hour remaining percentages for every enabled subscription (fitting Windows' 127-char limit automatically), with a right-click menu to toggle individual subscriptions.
- **Read-Only Maintenance Surface (`/maintenance`):**
  - Tracks persistent schema/endpoint integration errors, checks pinned official documentation URLs on demand, and never mutates configs or code automatically.

---

## Security & Honesty Principles

- **Strictly Local Loopback (`127.0.0.1`):** Enforces `SO_EXCLUSIVEADDRUSE` on Windows, `Host` + `Origin` + `Sec-Fetch-Site: same-origin` + `X-ProviderDeck-Request: 1`, strict CSP (`script-src 'self'`), and `Cache-Control: no-store`.
- **Zero Secret Leakage:** Browser and disk snapshots are constructed from an explicit whitelist projection; `Bearer`, `sk-`, `gh*_`, JWTs, emails, and UUIDs are scrubbed automatically.
- **No Paid Inference on Refresh:** Background refresh performs only bounded read-only catalog/account/CLI usage queries.
- **DNS-SSRF Protection:** Outbound HTTP reads reject non-HTTPS, redirects, private/loopback literals, and hostnames resolving to non-global IPs.
- **Truthful Metrics:** Unknown values stay `null` (never fabricated as `0`), non-USD credits are kept separate from USD wallet totals, and an HTTP 200 catalog check is never presented as proof of working inference.

---

## Quick Start

### 1. Install Dependencies

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Providers (Read-Only)

- **API keys:** Copy `.env.example` to `.env` and fill in only the keys you use.
- **Local CLI subscriptions (automatic, read-only):**
  - **Claude Code:** automatically detected from `~/.claude/.credentials.json`
  - **Codex CLI:** automatically detected from `~/.codex/auth.json`
  - **Google Antigravity:** automatically detected from `agy` on `PATH` (or `%LOCALAPPDATA%\agy\bin\agy.exe`, override with `AGY_BIN`)
  - **Muse Code:** automatically queries `muse usage --json` (native on Linux/macOS, or via WSL on Windows — override distro with `MUSE_WSL_DISTRO`)
  - **Optional Hermes Agent profile:** if `$HERMES_HOME` or `%LOCALAPPDATA%\hermes` exists, ProviderDeck also reads its active profile read-only without mutating files or refreshing token pools.

### 3. Run

```bash
python launch.py
```

- Opens the local dashboard at `http://127.0.0.1:8766` (and starts the system tray icon on Windows).
- Run without opening a browser: `python launch.py --no-browser`
- Run offline from cache only: `python launch.py --offline`
- Stop the background server (and tray): `python launch.py --stop`
- On Windows, you can also double-click `scripts/start.cmd` and `scripts/stop.cmd`.

---

## Running Tests

```bash
# Python backend, security & tray tests
python -m unittest discover -s tests -p "test_*.py" -v

# Frontend core logic tests (Node.js 18+)
node tests/ui.test.cjs
```

---

## License

[GNU GPL v3](LICENSE)
