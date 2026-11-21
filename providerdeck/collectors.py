"""Read-only discovery and bounded account/catalog collectors.

Secrets exist only in private Route objects; never serialize them. Intentionally
avoid Hermes bootstrap/env loaders that sanitize files or seed credential pools.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import re

from .pricing import number

BASES = {
    'openrouter': 'https://openrouter.ai/api/v1',
    'nvidia': 'https://integrate.api.nvidia.com/v1',
    'meta-ai': 'https://api.meta.ai/v1',
    'ai-gateway': 'https://ai-gateway.vercel.sh/v1',
    'opencode-zen': 'https://opencode.ai/zen/v1',
    'opencode-go': 'https://opencode.ai/zen/go/v1',
    'anthropic': 'https://api.anthropic.com/v1',
    'openai-codex': 'https://chatgpt.com/backend-api/codex',
    'outrun': 'https://api.outrun.to/v1',
    'copilot': 'https://api.individual.githubcopilot.com',
    'uberllm': 'https://api.uberllm.dev/v1',
    'refuseless': 'https://api.refuseless.com/v1',
    'orcarouter': 'https://api.orcarouter.ai/v1',
    'venice': 'https://api.venice.ai/api/v1',
    'voidai': 'https://api.voidai.app/v1',
    'explabs': 'https://api.experientiallabs.ai/api/v1',
    'nanogpt': 'https://nano-gpt.com/api/v1',
}
ENV_KEYS = {
    'openrouter': ['OPENROUTER_API_KEY'], 'nvidia': ['NVIDIA_API_KEY'],
    'meta-ai': ['MODEL_API_KEY', 'META_API_KEY', 'META_MODEL_API_KEY'],
    'ai-gateway': ['AI_GATEWAY_API_KEY'], 'opencode-zen': ['OPENCODE_ZEN_API_KEY'],
    'opencode-go': ['OPENCODE_GO_API_KEY'], 'copilot': ['COPILOT_GITHUB_TOKEN'],
    'outrun': ['OUTRUN_API_KEY'], 'anthropic': ['ANTHROPIC_API_KEY'],
    'uberllm': ['UBERLLM_API_KEY'], 'refuseless': ['REFUSELESS_API_KEY'],
    'orcarouter': ['ORCAROUTER_API_KEY'], 'venice': ['VENICE_API_KEY'],
    'voidai': ['VOIDAI_API_KEY'], 'explabs': ['EXPLABS_API_KEY'],
    'nanogpt': ['NANOGPT_API_KEY'],
}
ALIASES = {'meta': 'meta-ai', 'model-api': 'meta-ai', 'msl': 'meta-ai', 'muse': 'meta-ai', 'muse-spark': 'meta-ai', 'agy': 'antigravity', 'antigravity-cli': 'antigravity'}
NAMES = {'openai-codex': 'ChatGPT / Codex', 'anthropic': 'Anthropic / Claude', 'meta-ai': 'Meta Model API', 'muse-code': 'Muse Code CLI', 'ai-gateway': 'Vercel AI Gateway', 'opencode-zen': 'OpenCode Zen', 'opencode-go': 'OpenCode Go', 'copilot': 'GitHub Copilot', 'nvidia': 'NVIDIA NIM', 'openrouter': 'OpenRouter', 'antigravity': 'Google Antigravity', 'uberllm': 'UberLLM', 'refuseless': 'Refuseless', 'orcarouter': 'OrcaRouter', 'venice': 'Venice', 'voidai': 'VoidAI', 'explabs': 'Experiential Labs', 'nanogpt': 'NanoGPT'}


def hermes_home():
    if os.environ.get('PROVIDERDECK_HOME'):
        return Path(os.environ['PROVIDERDECK_HOME'])
    if os.environ.get('HERMES_HOME'):
        return Path(os.environ['HERMES_HOME'])
    hermes_default = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'hermes'
    if hermes_default.is_dir():
        return hermes_default
    return Path.home() / '.config/providerdeck'


@dataclass
class Route:
    id: str
    name: str
    base_url: str = ''
    credential: str = field(default='', repr=False)
    kind: str = 'api'
    oauth: bool = False
    principal: str = field(default='', repr=False)


def borrow_claude_token():
    """Read-only Claude Code OAuth token lookup (standalone ~/.claude or optional Hermes)."""
    import sys
    for candidate in (hermes_home() / 'hermes-agent', Path(sys.executable).resolve().parents[2]):
        if (candidate / 'agent/anthropic_credentials.py').is_file() and str(candidate) not in sys.path:
            sys.path.append(str(candidate))
    try:
        from agent.anthropic_credentials import read_claude_code_credentials
        token = (read_claude_code_credentials() or {}).get('accessToken') or ''
        if token:
            return token
    except (ImportError, OSError, ValueError):
        pass
    try:
        creds = json.loads((Path.home() / '.claude/.credentials.json').read_text(encoding='utf-8-sig'))
        oauth = creds.get('claudeAiOauth') if isinstance(creds, dict) else None
        if isinstance(oauth, dict) and isinstance(oauth.get('accessToken'), str):
            return oauth['accessToken']
    except (OSError, ValueError, TypeError):
        pass
    return ''


def borrow_codex_auth():
    """Read-only OpenAI Codex CLI token lookup from ~/.codex/auth.json."""
    try:
        data = json.loads((Path.home() / '.codex/auth.json').read_text(encoding='utf-8-sig'))
        tokens = data.get('tokens') if isinstance(data, dict) else None
        if isinstance(tokens, dict):
            return str(tokens.get('access_token') or ''), str(tokens.get('account_id') or '')
    except (OSError, ValueError, TypeError):
        pass
    return '', ''


def _antigravity_bin():
    import shutil
    explicit = os.environ.get('AGY_BIN')
    if explicit:
        return explicit
    found = shutil.which('agy')
    if found:
        return found
    if os.name == 'nt' or os.environ.get('LOCALAPPDATA'):
        return str(Path(os.environ.get('LOCALAPPDATA', '')) / 'agy/bin/agy.exe')
    return 'agy'


def _muse_usage_command():
    import shutil
    if os.name == 'nt':
        distro = os.environ.get('MUSE_WSL_DISTRO', 'Ubuntu').strip()
        prefix = ['wsl.exe', '-d', distro] if distro else ['wsl.exe']
        return prefix + ['-e', 'bash', '-lc', 'export PATH="$HOME/.local/bin:$PATH"; muse usage --json']
    found = shutil.which('muse') or str(Path.home() / '.local/bin/muse')
    return [found, 'usage', '--json']


def discover_routes(*, config=None, auth=None, secrets=None, home=None):
    """Re-read only the active profile. No refresh, pool selection or mutation."""
    home = Path(home) if home else hermes_home()
    injected = config is not None
    if config is None:
        import yaml
        try:
            config = yaml.safe_load((home / 'config.yaml').read_text(encoding='utf-8-sig')) or {}
        except FileNotFoundError:
            config = {}
    if auth is None:
        try:
            auth = json.loads((home / 'auth.json').read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            auth = {}
    if secrets is None:
        from dotenv import dotenv_values
        # dotenv_values parses without rewriting .env, exporting or invoking plugins.
        secrets = dict(os.environ)
        for env_path in (Path(__file__).resolve().parents[1] / '.env', home / '.env'):
            if env_path.is_file():
                secrets.update({k: v for k, v in dotenv_values(env_path, encoding='utf-8-sig', interpolate=False).items() if v is not None})
    custom = config.get('providers') or {}
    pool = auth.get('credential_pool') or {}
    providers = auth.get('providers') or {}
    candidates = set(custom) | set(pool) | set(providers)
    candidates.update(pid for pid, names in ENV_KEYS.items() if any(secrets.get(k) for k in names))
    if not injected and home == hermes_home():
        if (Path.home() / '.claude/.credentials.json').is_file():
            candidates.add('anthropic')
        if (Path.home() / '.codex/auth.json').is_file():
            candidates.add('openai-codex')
        if Path(_antigravity_bin()).is_file():
            candidates.add('antigravity')
    nous_static_key = secrets.get('NOUS_API_KEY')
    if nous_static_key:
        candidates.add('nous')
    model_config = config.get('model') or {}
    if model_config.get('provider'):
        candidates.add(model_config['provider'])
    alias_conf = {}
    for value in (model_config.get('aliases') or {}).values():
        if isinstance(value, str) and '/' in value:
            candidates.add(value.split('/', 1)[0])
        elif isinstance(value, dict):
            pid = ALIASES.get(value.get('provider'), value.get('provider'))
            if value.get('base_url'):
                existing = next((k for k, v in custom.items() if v.get('base_url', '').rstrip('/') == value['base_url'].rstrip('/')), None)
                existing = existing or next((k for k, base in BASES.items() if base == value['base_url'].rstrip('/')), None)
                if existing:
                    pid = existing
                elif not pid or pid in {'custom', 'openai'}:
                    from urllib.parse import urlsplit
                    pid = re.sub('[^a-z0-9-]', '-', urlsplit(value['base_url']).hostname or 'custom')
                alias_conf[pid] = value
            if pid:
                candidates.add(pid)
    routes, seen = [], set()
    for raw_pid in sorted(candidates):
        pid = ALIASES.get(raw_pid, raw_pid)
        if pid in seen or pid == 'moa' or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', pid):
            continue
        seen.add(pid)
        conf = custom.get(pid) or alias_conf.get(pid) or {}
        if pid == 'nous' and nous_static_key and not conf:
            # Only the dedicated static credential may use this production default.
            routes.append(Route('nous', 'Nous Portal', 'https://inference-api.nousresearch.com/v1', str(nous_static_key)))
            continue
        entries = pool.get(pid) or pool.get(raw_pid) or []
        entry = next((e for e in entries if e.get('access_token') or e.get('api_key')), {})
        official_base = BASES.get(pid) or ''
        effective_base = str(conf.get('base_url') or official_base or entry.get('base_url') or '').rstrip('/')
        from urllib.parse import urlsplit
        official_host = (urlsplit(official_base).hostname or '').lower() if official_base else ''
        effective_host = (urlsplit(effective_base).hostname or '').lower() if effective_base else ''
        custom_host = bool(official_host and effective_host and effective_host != official_host)
        key = str(secrets.get(conf.get('key_env', ''), '') or conf.get('api_key') or '')
        principal = ''
        if not custom_host:
            if not key:
                key = next((str(secrets[k]) for k in ENV_KEYS.get(pid, []) if secrets.get(k)), '')
            key = key or entry.get('access_token') or entry.get('api_key') or ''
            tokens = (providers.get(pid) or {}).get('tokens') or {}
            key = key or tokens.get('access_token') or (providers.get(pid) or {}).get('access_token') or ''
            principal = str(tokens.get('account_id') or '')
            if pid == 'anthropic' and not key and not injected:
                key = borrow_claude_token()
            if pid == 'openai-codex' and not key and not injected:
                codex_key, codex_acct = borrow_codex_auth()
                key = key or codex_key
                principal = principal or codex_acct
        kind = 'api' if custom_host else ('subscription' if pid in {'anthropic', 'openai-codex', 'opencode-go', 'copilot', 'antigravity'} else 'api')
        if pid == 'nvidia' and not custom_host and (not conf.get('base_url') or effective_base == BASES['nvidia']):
            kind = 'free'
        if pid == 'anthropic' and key.startswith('sk-ant-api'):
            kind = 'api'
        is_oauth = (not custom_host) and pid == 'anthropic' and bool(key) and not key.startswith('sk-ant-api')
        routes.append(Route(pid, str(conf.get('name') or NAMES.get(pid) or pid), effective_base, key, kind, is_oauth, principal))
    routes.append(Route('muse-code', NAMES['muse-code'], kind='cli'))
    return routes


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def iso(value):
    from datetime import datetime, timezone
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def account_patch(pid, payload):
    """Whitelisted account fields only. Empty/missing data never becomes zero."""
    p = payload if isinstance(payload, dict) else {}
    d = p.get('data', p)
    d = d if isinstance(d, dict) else {}
    patch = {'balance_usd': None, 'credits': None, 'windows': []}
    def window(label, used, reset=None, scope='account', remaining=None):
        n = number(used)
        patch['windows'].append({'label': label, 'remaining_percent': remaining if remaining is not None else (max(0, min(100, 100-n)) if n is not None else None), 'reset_at': iso(reset), 'scope': scope})
    if pid in {'openrouter', 'explabs'}:
        quota = d.get('free_model_daily_requests') or {}
        if isinstance(quota, dict):
            limit, remaining = number(quota.get('limit')), number(quota.get('remaining'))
            if limit and remaining is not None:
                window('Günlük ücretsiz istek hakkı', None, remaining=min(100, 100*remaining/limit))
        total, used = number(d.get('total_credits'), negative=True), number(d.get('total_usage'), negative=True)
        if total is not None and used is not None:
            from decimal import Decimal
            patch['balance_usd'] = float(Decimal(str(total))-Decimal(str(used)))
    elif pid == 'uberllm':
        patch['balance_usd'] = number(d.get('available_usd'), negative=True)
    elif pid == 'ai-gateway':
        patch['balance_usd'] = number(d.get('balance'), negative=True)
    elif pid == 'refuseless':
        patch['balance_usd'] = number(d.get('available'), negative=True)
    elif pid == 'orcarouter':
        patch['balance_usd'] = number(d.get('spendable_usd'), negative=True)
    elif pid == 'outrun':
        patch['balance_usd'] = number(d.get('credit'), negative=True)
    elif pid == 'nanogpt':
        patch['balance_usd'] = number(d.get('usd_balance'), negative=True)
    elif pid == 'voidai':
        value = number(d.get('current_credits'))
        if value is not None:
            patch['credits'] = {'value': value, 'unit': 'kredi'}
        limit, used = number(d.get('daily_limit')), number(d.get('credits_used'))
        if limit and used is not None:
            window('Ücretsiz kredi penceresi (yaklaşık)', 100*used/limit, d.get('reset_time'))
            patch['note'] = 'Kredi birimi USD değildir; sıfırlama zamanı yaklaşık, uygunluk koşulu ayrıca geçerlidir.'
    elif pid == 'venice':
        patch['balance_usd'] = number((d.get('balances') or {}).get('USD'), negative=True)
        if d.get('accessPermitted') is False:
            patch['access'] = {'state': 'blocked', 'reason': 'Hesap API erişimine izin vermiyor (accessPermitted=false).', 'evidence': 'account'}
    elif pid in {'opencode-go', 'opencode-zen'}:
        for key, w in (d.get('usage') or {}).items():
            if isinstance(w, dict):
                window({'rolling': 'Kayan pencere', 'weekly': 'Haftalık', 'monthly': 'Aylık'}.get(key, 'Abonelik penceresi'), w.get('percent'), w.get('resetsAt'))
        if patch['windows']:
            patch['note'] = 'Go rota hakkı; Zen ve Go pencereleri toplanmaz, aynı aboneliğe bağlı olabilir. Plain Zen modellerini kapsamaz.'
    elif pid == 'anthropic':
        for key, label in [('five_hour', 'Oturum (5 saat)'), ('seven_day', 'Haftalık'), ('seven_day_opus', 'Haftalık Opus'), ('seven_day_sonnet', 'Haftalık Sonnet')]:
            if isinstance(d.get(key), dict):
                w = d[key]
                window(label, w.get('utilization'), w.get('resets_at'), 'model' if key not in {'five_hour', 'seven_day'} else 'account')
        if (d.get('extra_usage') or {}).get('is_enabled') is False:
            patch['note'] = 'Abonelik kotası; ek ücretli kullanım kapalı.'
    elif pid == 'openai-codex':
        limits = d.get('rate_limit') or {}
        for key in ['primary_window', 'secondary_window']:
            w = limits.get(key)
            if isinstance(w, dict):
                duration = w.get('limit_window_seconds')
                window({604800: 'Haftalık', 18000: 'Oturum (5 saat)'}.get(duration, 'Kota penceresi'), w.get('used_percent'), w.get('reset_at'))
        if limits.get('allowed') is False or limits.get('limit_reached') is True:
            patch['access'] = {'state': 'blocked', 'reason': 'Abonelik kullanım sınırına ulaşıldı.', 'evidence': 'account'}
    elif pid == 'copilot':
        for key, w in (d.get('quota_snapshots') or {}).items():
            if not isinstance(w, dict):
                continue
            if w.get('unlimited') is True:
                window('Standart sohbet — sınırsız plan hakkı', None, d.get('quota_reset_date_utc'))
            else:
                remaining, limit = number(w.get('remaining')), number(w.get('entitlement'))
                percentage = number(w.get('percent_remaining'))
                if percentage is None and remaining is not None and limit:
                    percentage = 100*remaining/limit
                window('Premium etkileşim' if key == 'premium_interactions' else 'Plan hakkı', None, d.get('quota_reset_date_utc'), remaining=percentage)
    return patch


ACCOUNT_URLS = {
    'openrouter': 'https://openrouter.ai/api/v1/credits',
    'ai-gateway': 'https://ai-gateway.vercel.sh/v1/credits',
    'uberllm': 'https://api.uberllm.dev/v1/balance',
    'refuseless': 'https://api.refuseless.com/v1/credits',
    'orcarouter': 'https://api.orcarouter.ai/v1/tools/balance',
    'venice': 'https://api.venice.ai/api/v1/api_keys/rate_limits',
    'voidai': 'https://api.voidai.app/v1/credits',
    'outrun': 'https://api.outrun.to/v1/account',
    'explabs': 'https://api.experientiallabs.ai/api/v1/credits',
    'nanogpt': 'https://api.nano-gpt.com/api/check-balance',
    'opencode-go': 'https://opencode.ai/zen/go/v1/usage',
    'opencode-zen': 'https://opencode.ai/zen/go/v1/usage',
    'anthropic': 'https://api.anthropic.com/api/oauth/usage',
    'openai-codex': 'https://chatgpt.com/backend-api/wham/usage',
    'copilot': 'https://api.github.com/copilot_internal/user',
}
KEY_URLS = {'openrouter': 'https://openrouter.ai/api/v1/key', 'explabs': 'https://api.experientiallabs.ai/api/v1/key'}
DOCS = {'openrouter': 'https://openrouter.ai/docs', 'anthropic': 'https://platform.claude.com/docs', 'openai-codex': 'https://developers.openai.com/codex', 'meta-ai': 'https://dev.meta.ai/docs', 'muse-code': 'https://dev.meta.ai/docs/muse-code/subscriptions', 'antigravity': 'https://antigravity.google/docs/plans', 'explabs': 'https://platform.experientiallabs.ai/llms.txt', 'uberllm': 'https://uberllm.dev', 'ai-gateway': 'https://vercel.com/docs/ai-gateway', 'opencode-zen': 'https://opencode.ai/docs/zen', 'opencode-go': 'https://opencode.ai/v2/docs/console/go/', 'venice': 'https://docs.venice.ai', 'voidai': 'https://docs.voidai.app', 'refuseless': 'https://refuseless.com', 'orcarouter': 'https://www.orcarouter.ai', 'copilot': 'https://docs.github.com/en/copilot', 'nanogpt': 'https://nano-gpt.com', 'nvidia': 'https://docs.api.nvidia.com/nim', 'outrun': 'https://outrun.to'}


def provider_shell(route):
    return {'id': route.id, 'name': route.name, 'kind': route.kind,
            'balance_usd': None, 'credits': None, 'windows': [],
            'access': {'state': 'unknown', 'reason': 'Hesap erişimi doğrulanmadı.', 'evidence': 'local'},
            'fetched_at': None, 'stale': True, 'models_count': 0,
            'dashboard_url': DOCS.get(route.id), 'note': 'Hesap/katalog okuması yanıt üretimi testi değildir.', 'last_inference': None}


def read_json(method, url, headers, body=None):
    """Only explicit read endpoints; no redirects/retries, limited bytes and time."""
    from urllib.parse import urlsplit
    import ipaddress
    import httpx
    parsed = urlsplit(url)
    from urllib.parse import parse_qsl
    if any(k not in {'limit', 'after_id', 'client_version'} for k, _ in parse_qsl(parsed.query)):
        raise ValueError('query credentials are forbidden')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('unsafe endpoint')
    if parsed.hostname in {'localhost', 'localhost.localdomain'} or parsed.hostname.endswith('.local'):
        raise ValueError('local endpoint')
    allowed = (method == 'GET' and (parsed.path.endswith('/models') or url in ACCOUNT_URLS.values() or url in KEY_URLS.values())) or (method == 'POST' and url == ACCOUNT_URLS['nanogpt'] and body == {})
    if not allowed:
        raise ValueError('not a read operation')
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global:
            raise ValueError('private endpoint')
    except ValueError as exc:
        if str(exc) == 'private endpoint':
            raise
        import socket
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        if not infos or any(not ipaddress.ip_address(info[4][0]).is_global for info in infos):
            raise ValueError('private endpoint')
    with httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False, transport=httpx.HTTPTransport(local_address='0.0.0.0', retries=0), trust_env=False) as client:
        with client.stream(method, url, headers=headers, json=body if method == 'POST' else None) as response:
            if response.status_code != 200:
                return response.status_code, {}
            content = bytearray()
            import time
            started = time.monotonic()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 12_000_000 or time.monotonic() - started > 12:
                    raise ValueError('response limit')
            return response.status_code, json.loads(content)


def failure(pid, status=None, *, schema=False, url=None):
    kind = ('schema' if schema else 'auth' if status == 401 else 'eligibility' if status == 403 else 'quota' if status == 429 else 'endpoint' if status in {404, 405, 410} else 'transient' if status is None or status >= 500 else 'unknown')
    messages = {'schema': 'Yanıt şeması beklenen alanları içermiyor; son iyi veri korunuyor.', 'auth': 'Kimlik doğrulama reddedildi; oturum yenilenmedi.', 'eligibility': 'Hesap veya uygunluk koşulu erişimi engelliyor.', 'quota': 'Kullanım kotası/hız sınırı; API şema değişikliği sayılmadı.', 'endpoint': 'Okuma uç noktası bulunamadı veya kaldırılmış olabilir.', 'transient': 'Ağ/sunucu okuması tamamlanamadı; son iyi veri korunuyor.', 'unknown': 'Okuma isteği doğrulanamadı.'}
    error = {'provider_id': pid, 'message': messages[kind], 'kind': kind}
    if isinstance(status, int):
        error['http_status'] = status
    if url:
        error['source_url'] = url
    if DOCS.get(pid):
        error['docs_url'] = DOCS[pid]
    return error


def _iso_ms(value):
    n = number(value)
    if n is None:
        return iso(value)
    return iso(n / 1000 if n > 1e11 else n)


def _parse_muse_usage(stdout):
    """Parse `muse usage --json` (or fallback plain no-usage output) into safe fields."""
    text = (stdout or '').strip()
    windows = []
    try:
        payload = json.loads(text)
    except ValueError:
        lower = text.lower()
        if any(tag in lower for tag in ('server_sent_no_usage', 'no_allowance', 'no usage data')):
            return {'windows': [], 'reason': 'server_sent_no_usage', 'schema_error': False}
        return {'windows': [], 'reason': None, 'schema_error': True}
    if not isinstance(payload, dict):
        return {'windows': [], 'reason': None, 'schema_error': True}
    reason = payload.get('reason') if isinstance(payload.get('reason'), str) else None
    allowance = payload.get('allowance')
    if not isinstance(allowance, dict):
        return {'windows': [], 'reason': reason, 'schema_error': False}

    def add_window(label, obj):
        if not isinstance(obj, dict):
            return
        raw_used = obj.get('usedPercent') if obj.get('usedPercent') is not None else obj.get('used_percent')
        used = number(raw_used)
        if used is None:
            return
        if obj.get('resetsAtMs') is not None:
            reset_at = _iso_ms(obj.get('resetsAtMs'))
        else:
            reset_at = iso(obj.get('resets_at'))
        windows.append({
            'label': label,
            'remaining_percent': max(0, min(100, 100 - used)),
            'reset_at': reset_at,
            'scope': 'account',
        })

    win = allowance.get('window') if isinstance(allowance.get('window'), dict) else allowance
    raw_mins = win.get('windowDurationMins') if win.get('windowDurationMins') is not None else win.get('window_duration_mins')
    mins = number(raw_mins)
    if mins and mins >= 60 and mins % 60 == 0:
        win_label = f'Oturum ({int(mins // 60)} saat)'
    elif mins and mins > 0:
        win_label = f'Oturum ({int(mins)} dk)'
    else:
        win_label = 'Oturum (5 saat)'
    add_window(win_label, win)
    if isinstance(allowance.get('weekly'), dict):
        add_window('Haftalık', allowance['weekly'])
    return {'windows': windows, 'reason': reason, 'schema_error': not bool(windows)}


def _parse_antigravity_usage(stdout):
    """Parse `agy -p /usage --output-format json` into whitelisted quota windows."""
    try:
        payload = json.loads((stdout or '').strip())
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get('status') != 'SUCCESS':
        return None
    cmd = payload.get('command') if isinstance(payload.get('command'), dict) else {}
    data = cmd.get('data') if isinstance(cmd.get('data'), dict) else {}
    groups = data.get('groups')
    if not isinstance(groups, list):
        return None
    group_names = {'gemini models': 'Gemini', 'claude and gpt models': 'Claude & GPT'}
    win_names = {'weekly': 'Haftalık', '5h': '(5 saat)'}
    windows = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        raw_gname = str(group.get('name') or '').strip()
        gname = group_names.get(raw_gname.lower(), raw_gname)
        for bucket in (group.get('buckets') or []):
            if not isinstance(bucket, dict):
                continue
            wkey = str(bucket.get('window') or '').strip().lower()
            wname = win_names.get(wkey) or str(bucket.get('name') or wkey or 'Kota').strip()
            label = f'{gname} {wname}'.strip()
            frac = number(bucket.get('remaining_fraction'))
            remaining = round(max(0.0, min(100.0, frac * 100.0)), 1) if frac is not None else None
            windows.append({
                'label': label,
                'remaining_percent': remaining,
                'reset_at': iso(bucket.get('reset_time')),
                'scope': 'model',
            })
    return windows


def _parse_antigravity_credits(stdout):
    """Parse `agy -p /credits --output-format json` remaining_credits safely."""
    try:
        payload = json.loads((stdout or '').strip())
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get('status') != 'SUCCESS':
        return None
    cmd = payload.get('command') if isinstance(payload.get('command'), dict) else {}
    data = cmd.get('data') if isinstance(cmd.get('data'), dict) else {}
    return number(data.get('remaining_credits'))


def collect_cli(route, *, skip_catalog=False):
    import subprocess
    from .pricing import normalize_model
    provider = provider_shell(route)
    result = {'provider': provider, 'models': [], 'errors': [], 'catalog_ok': False, 'account_ok': False, 'catalog_reused': False}

    def run_cmd(args):
        return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=18, shell=False)

    if route.id == 'muse-code':
        args = _muse_usage_command()
        try:
            completed = run_cmd(args)
            if completed.returncode:
                result['errors'].append(failure(route.id))
                provider['note'] = 'CLI okuması tamamlanamadı; ham çıktı güvenlik nedeniyle gösterilmedi.'
                return result
            parsed = _parse_muse_usage(completed.stdout)
            if parsed['schema_error']:
                result['errors'].append(failure(route.id, schema=True))
                provider['note'] = 'Muse kullanım çıktısı beklenen şemada değil; ham çıktı güvenlik nedeniyle gösterilmedi.'
                return result
            provider['fetched_at'] = now()
            provider['stale'] = False
            result['account_ok'] = True
            if parsed['windows']:
                provider['windows'] = parsed['windows']
                provider['access'] = {'state': 'ready', 'reason': 'Muse Code abonelik kotası CLI üzerinden okundu; yeni model yanıtı testi yapılmadı.', 'evidence': 'account'}
                provider['note'] = 'Muse Code CLI aboneliği kotası okundu. Native Meta API ayrı rotadır ve ayrı ücretlendirilir.'
            else:
                provider['access'] = {'state': 'unknown', 'reason': 'Muse kullanım komutu okundu; yeni model yanıtı testi yapılmadı.', 'evidence': 'account'}
                if parsed['reason'] == 'server_sent_no_usage':
                    provider['note'] = 'Muse Code CLI aboneliği; kalan sayaç bilinmiyor (aktif pencerede ilk kullanımdan önce sunucu veri göndermiyor). server_sent_no_usage sıfır kota veya tükenme değildir. Native Meta API ayrı rotadır.'
                else:
                    provider['note'] = 'Muse Code CLI aboneliği; kalan sayaç bilinmiyor. Native Meta API ayrı rotadır.'
        except Exception:
            result['errors'].append(failure(route.id))
        provider['models_count'] = len(result['models'])
        return result

    agy_bin = _antigravity_bin()
    if skip_catalog:
        result['catalog_reused'] = True
    else:
        try:
            completed = run_cmd([agy_bin, 'models'])
            if completed.returncode:
                result['errors'].append(failure(route.id))
            else:
                for line in completed.stdout.splitlines():
                    if '\t' not in line:
                        continue
                    mid, name = line.split('\t', 1)
                    if re.fullmatch(r'[A-Za-z0-9._:/-]+', mid):
                        result['models'].append(normalize_model(route.id, {'id': mid, 'name': name}, provider))
                result['catalog_ok'] = bool(result['models'])
                if not result['catalog_ok']:
                    result['errors'].append(failure(route.id, schema=True))
        except Exception:
            result['errors'].append(failure(route.id))

    try:
        usage_cmd = run_cmd([agy_bin, '-p', '/usage', '--output-format', 'json'])
        if usage_cmd.returncode == 0:
            windows = _parse_antigravity_usage(usage_cmd.stdout)
            if windows:
                provider['windows'] = windows
                result['account_ok'] = True
    except Exception:
        pass

    credits_val = None
    try:
        credits_cmd = run_cmd([agy_bin, '-p', '/credits', '--output-format', 'json'])
        if credits_cmd.returncode == 0:
            credits_val = _parse_antigravity_credits(credits_cmd.stdout)
            if credits_val is not None and credits_val > 0:
                provider['credits'] = {'value': credits_val, 'unit': 'AI kredisi'}
    except Exception:
        pass

    if result['catalog_ok'] or result['account_ok'] or result['catalog_reused']:
        provider['fetched_at'] = now()
        provider['stale'] = bool(result['errors'])
    if result['account_ok']:
        provider['access'] = {'state': 'ready', 'reason': 'Google Antigravity abonelik kotası CLI üzerinden okundu; model yanıtı testi yapılmadı.', 'evidence': 'account'}
        credit_note = f' Ek AI kredisi: {int(credits_val) if float(credits_val).is_integer() else credits_val}.' if credits_val is not None else ''
        provider['note'] = 'agy /usage ile model grubu kotaları okundu (model isteği harcanmadı).' + credit_note
    elif result['catalog_ok'] or result['catalog_reused']:
        provider['access'] = {'state': 'unknown', 'reason': 'CLI kataloğu okundu; abonelik kotası sayısal olarak bilinmiyor.', 'evidence': 'catalog'}
        provider['note'] = 'agy models okundu; model yanıtı veya sayısal kalan kota doğrulaması değildir.'
    else:
        provider['note'] = 'CLI okuması tamamlanamadı; ham çıktı güvenlik nedeniyle gösterilmedi.'
    provider['models_count'] = len(result['models'])
    return result


def collect_route(route, *, request=None, skip_catalog=False):
    """Failure-isolated vertical read. Private success flags aid last-good merging."""
    from .pricing import normalize_model
    if route.id in {'muse-code', 'antigravity'}:
        return collect_cli(route, skip_catalog=skip_catalog)
    request = request or read_json
    provider, models, errors = provider_shell(route), [], []
    result = {'provider': provider, 'models': models, 'errors': errors, 'catalog_ok': False, 'account_ok': False}
    if not route.credential:
        provider['access'] = {'state': 'missing_key', 'reason': 'Bu profilde kullanılabilir kimlik bilgisi yok.', 'evidence': 'local'}
        return result
    headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + route.credential, 'User-Agent': 'Hermes-ProviderDeck/0.1 (read-only)'}
    if route.id == 'anthropic':
        headers.update({'anthropic-version': '2023-06-01', 'anthropic-beta': 'claude-code-20250219,oauth-2025-04-20'})
        if not route.oauth:
            headers.pop('Authorization')
            headers['x-api-key'] = route.credential
    if route.id == 'copilot':
        headers.update({'Copilot-Integration-Id': 'vscode-chat', 'Editor-Version': 'vscode/1.99.0'})
    if route.id == 'openai-codex':
        # Pure header/URL helpers, explicit token; never invoke resolver/refresh.
        try:
            from agent.account_usage import _codex_headers
            headers.update(_codex_headers(route.credential, route.principal or None))
        except ImportError:
            pass
    catalog_url = route.base_url + '/models' if route.base_url else None
    if route.id == 'openai-codex' and catalog_url:
        catalog_url += '?client_version=1.0.0'
    if route.id == 'anthropic' and catalog_url:
        catalog_url += '?limit=1000'
    def fetch(url, method='GET', body=None, extra_headers=None):
        try:
            status, payload = request(method, url, {**headers, **(extra_headers or {})}, body=body)
        except (json.JSONDecodeError, UnicodeError):
            errors.append(failure(route.id, 200, schema=True, url=url))
            return None
        except Exception:
            errors.append(failure(route.id, url=url))
            return None
        if status != 200:
            errors.append(failure(route.id, status, url=url))
            if status in {401, 403, 429}:
                provider['access'] = {'state': 'blocked', 'reason': errors[-1]['message'], 'evidence': 'account' if url in ACCOUNT_URLS.values() else 'catalog'}
            return None
        if not isinstance(payload, (dict, list)):
            errors.append(failure(route.id, status, schema=True, url=url))
            return None
        return payload
    raw_models = []
    if catalog_url:
        page_url, cursors = catalog_url, set()
        for page_number in range(3):
            payload = fetch(page_url)
            if payload is None:
                break
            rows = payload.get('data', payload.get('models')) if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                errors.append(failure(route.id, 200, schema=True, url=catalog_url))
                break
            raw_models.extend(r if isinstance(r, dict) else {'id': r} for r in rows if isinstance(r, (dict, str)))
            if isinstance(payload, dict) and payload.get('has_more'):
                cursor = payload.get('last_id')
                if not isinstance(cursor, str) or not cursor or cursor in cursors or page_number == 2:
                    errors.append(failure(route.id, 200, schema=True, url=catalog_url))
                    break
                cursors.add(cursor)
                from urllib.parse import quote
                page_url = catalog_url + ('&' if '?' in catalog_url else '?') + 'after_id=' + quote(cursor, safe='')
                continue
            result['catalog_ok'] = True
            provider['access'] = {'state': 'unknown', 'reason': 'Katalog okundu; yanıt üretimi test edilmedi.', 'evidence': 'catalog'}
            break
    account_url = ACCOUNT_URLS.get(route.id)
    # Never send a native account credential to a different custom gateway.
    from urllib.parse import urlsplit
    if account_url and (route.id in {'anthropic', 'openai-codex', 'copilot'} or urlsplit(account_url).hostname == urlsplit(route.base_url).hostname or route.id == 'nanogpt'):
        if route.id == 'anthropic' and not route.oauth:
            account_url = None
        if account_url:
            extra = {'x-api-key': route.credential} if route.id == 'nanogpt' else ({'Authorization': 'token ' + route.credential} if route.id == 'copilot' else None)
            payload = fetch(account_url, 'POST' if route.id == 'nanogpt' else 'GET', {} if route.id == 'nanogpt' else None, extra)
            if isinstance(payload, dict):
                patch = account_patch(route.id, payload)
                # At least one understood account signal; empty JSON is schema drift.
                understood = patch['balance_usd'] is not None or patch['credits'] is not None or bool(patch['windows']) or 'access' in patch
                if understood:
                    provider.update(patch)
                    if 'access' not in patch:
                        provider['access'] = {'state': 'ready', 'reason': 'Hesap okundu; model yanıtı denenmedi.', 'evidence': 'account'}
                    result['account_ok'] = True
                else:
                    errors.append(failure(route.id, 200, schema=True, url=account_url))
    if route.id == 'opencode-zen' and result['account_ok']:
        provider['access'] = {'state': 'unknown', 'reason': 'Bağlı Go aboneliği okundu; plain Zen modellerinin kullanım izni bundan çıkarılamaz.', 'evidence': 'account'}
        provider['note'] = 'Zen anahtarının Go abonelik hakkı var; bu sayaç OpenCode Go ile ortak olabilir ve toplanmaz. Dahil modeller için OpenCode Go rotasını kullanın; plain Zen cüzdanı ve ücretsiz kanal izni ayrı.'
    key_url = KEY_URLS.get(route.id)
    if key_url and provider['access']['state'] != 'blocked' and urlsplit(key_url).hostname == urlsplit(route.base_url).hostname:
        payload = fetch(key_url)
        data = payload.get('data', payload) if isinstance(payload, dict) else {}
        quota = data.get('free_model_daily_requests') or {} if isinstance(data, dict) else {}
        if isinstance(quota, dict):
            limit, remaining = number(quota.get('limit')), number(quota.get('remaining'))
            if limit and remaining is not None:
                provider['windows'].append({'label': 'Günlük ücretsiz istek hakkı', 'remaining_percent': min(100, max(0, 100*remaining/limit)), 'reset_at': None, 'scope': 'account'})
                result['account_ok'] = True
        if route.id == 'explabs' and isinstance(data, dict) and data.get('is_free_tier') is True:
            provider['note'] = 'Ücretsiz katman var; yalnızca :free tam rotası ücretli taşmayı engeller. Plain model kredi harcayabilir.'
    provider['fetched_at'] = now() if result['account_ok'] or result['catalog_ok'] else None
    provider['stale'] = bool(errors)
    for raw in raw_models:
        if not (raw.get('id') or raw.get('slug')) or raw.get('visibility') == 'hide':
            continue
        model = normalize_model(route.id, raw, {**provider, 'catalog_url': catalog_url})
        models.append(model)
    provider['models_count'] = len(models)
    if route.id == 'meta-ai':
        provider['note'] = 'Native Meta API ayrı ücretlendirilir; Muse Code aboneliği bu modelleri kapsadığı anlamına gelmez.'
    return result
