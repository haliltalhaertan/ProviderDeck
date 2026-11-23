"""One explicitly triggered, free-only Nous check. Not part of refresh."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import monotonic

from .collectors import env_file_paths, read_json

BASE = 'https://inference-api.nousresearch.com/v1'
MODEL = 'stepfun/step-3.7-flash:free'
RESULTS = {
    'ok': 'Bu ücretsiz modelin yanıt verdiği doğrulandı.',
    'missing_key': 'Aktif Hermes profilinde NOUS_API_KEY yok. Model çağrısı yapılmadı.',
    'catalog_error': 'Nous kataloğu doğrulanamadı. Model çağrısı yapılmadı.',
    'not_free': 'Bu modelin ücretsiz olduğu doğrulanamadı. Model çağrısı yapılmadı.',
    'auth': 'Nous API anahtarını kabul etmedi (HTTP 401).',
    'denied': 'Nous bu ücretsiz isteğe izin vermedi (HTTP 403).',
    'quota': 'Nous ücretsiz isteği kota veya hız sınırı nedeniyle reddetti (HTTP 429).',
    'payment': 'Nous ödeme gerektiğini bildirdi (HTTP 402). Başka model denenmedi.',
    'no_text': 'Nous isteği aldı fakat görünür metin dönmedi. Model yanıtı doğrulanamadı.',
    'cost_warning': 'Nous yanıtta sıfır olmayan veya belirsiz bir maliyet bildirdi. Portal kullanımını kontrol et.',
    'upstream_error': 'Nous testi tamamlanamadı. İstek tekrarlanmadı.',
}


def _zero(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)).is_zero()
    except InvalidOperation:
        return False


def _free_text_price(data):
    rows = data.get('data') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return False
    matching = [m for m in rows if isinstance(m, dict) and m.get('id') == MODEL]
    if len(matching) != 1:
        return False
    pricing = matching[0].get('pricing')
    return (isinstance(pricing, dict) and _zero(pricing.get('prompt')) and _zero(pricing.get('completion'))
            and all(_zero(pricing[k]) for k in ('request', 'input_cache_read', 'input_cache_write', 'internal_reasoning') if k in pricing))


def _completion(key):
    import httpx

    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Reply with only OK.'}],
            'max_tokens': 64, 'stream': False, 'tags': ['user=providerdeck']}
    headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + key,
               'User-Agent': 'ProviderDeck/0.1 (manual free-model check)'}
    transport = httpx.HTTPTransport(local_address='0.0.0.0', retries=0)
    with httpx.Client(timeout=httpx.Timeout(12, connect=4), transport=transport,
                      follow_redirects=False, trust_env=False) as client:
        deadline = monotonic() + 18
        with client.stream('POST', BASE + '/chat/completions', headers=headers, json=body) as response:
            if response.status_code != 200:
                return response.status_code, {}
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=8192):
                if monotonic() > deadline or len(content) + len(chunk) > 65536:
                    raise ValueError('Probe response limit')
                content.extend(chunk)
            return 200, json.loads(content)


def run_nous_free_test(*, home=None, read_catalog=None, send=None):
    """Read the static key locally; return only a known result category."""
    import os
    from dotenv import dotenv_values

    if home is not None:
        key = dotenv_values(Path(home) / '.env', encoding='utf-8-sig', interpolate=False).get('NOUS_API_KEY')
    else:
        key = os.environ.get('NOUS_API_KEY')
        for env_path in env_file_paths():
            if env_path.is_file():
                val = dotenv_values(env_path, encoding='utf-8-sig', interpolate=False).get('NOUS_API_KEY')
                if val:
                    key = val
    if not key or not key.strip():
        return 'missing_key'
    read_catalog = read_catalog or read_json
    try:
        status, catalog = read_catalog('GET', BASE + '/models', {'Accept': 'application/json'})
    except Exception:
        return 'catalog_error'
    if status != 200:
        return 'catalog_error'
    if not _free_text_price(catalog):
        return 'not_free'
    try:
        status, response = (send or _completion)(key)
    except Exception:
        return 'upstream_error'
    if status != 200:
        return {401: 'auth', 403: 'denied', 429: 'quota', 402: 'payment'}.get(status, 'upstream_error')
    if not isinstance(response, dict):
        return 'upstream_error'
    usage = response.get('usage')
    if isinstance(usage, dict) and 'cost' in usage and usage['cost'] is not None and not _zero(usage['cost']):
        return 'cost_warning'
    choices = response.get('choices')
    if isinstance(choices, list):
        for choice in choices:
            message = choice.get('message') if isinstance(choice, dict) else None
            text = message.get('content') if isinstance(message, dict) else None
            if isinstance(text, str) and text.strip():
                return 'ok'
    return 'no_text'
