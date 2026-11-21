"""Conservative catalog normalization. No model equivalence guessing."""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

VENDORS = {'deepseek', 'anthropic', 'openai', 'google', 'meta-llama', 'meta', 'nvidia', 'qwen', 'qwen3', 'z-ai', 'x-ai', 'mistralai', 'minimax', 'moonshotai', 'alibaba', 'aion-labs'}


def number(value, *, negative=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(Decimal(str(value)))
        return n if math.isfinite(n) and (negative or n >= 0) else None
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        return None


def normalize_model(provider_id, raw_model, provider_record=None):
    raw = raw_model if isinstance(raw_model, dict) else {'id': str(raw_model)}
    record = provider_record or {}
    mid = str(raw.get('id') or raw.get('slug') or '')
    canonical = mid
    if '/' in canonical and canonical.split('/', 1)[0] in VENDORS:
        canonical = canonical.split('/', 1)[1]
    documented_dash_free = provider_id in {'opencode-zen', 'orcarouter'} and mid.endswith('-free')
    if canonical.endswith(':free'):
        canonical = canonical[:-5]
    elif documented_dash_free:
        canonical = canonical[:-5]
    p = raw.get('pricing') or {}
    price = {k: None for k in ('input_per_million_usd', 'output_per_million_usd', 'cache_read_per_million_usd', 'request_usd')}
    unit = 'unknown'
    fields, scale = (), 1
    if provider_id in {'openrouter', 'orcarouter'}:
        fields, scale, unit = ('prompt', 'completion', 'input_cache_read'), 1_000_000, 'USD'
    elif provider_id == 'ai-gateway':
        fields, scale, unit = ('input', 'output', 'input_cache_read'), 1_000_000, 'USD'
    elif provider_id == 'uberllm':
        fields, unit = ('input_usd', 'output_usd', 'cache_read_usd'), 'USD'
    elif provider_id == 'explabs':
        fields, scale, unit = ('input_nano_usd_per_million_tokens', 'output_nano_usd_per_million_tokens', 'cached_input_nano_usd_per_million_tokens'), 1e-9, 'USD'
    elif provider_id == 'refuseless' and p.get('currency', 'USD') == 'USD':
        fields, unit = ('input_per_m', 'output_per_m', 'cached_input_per_m'), 'USD'
    elif provider_id == 'venice':
        p = {k: v.get('usd') for k, v in (raw.get('model_spec', {}).get('pricing') or {}).items() if isinstance(v, dict)}
        fields, unit = ('input', 'output', 'cache_input'), 'USD'
    elif provider_id == 'voidai':
        unit = 'credits'
    for dest, src in zip(('input_per_million_usd', 'output_per_million_usd', 'cache_read_per_million_usd'), fields):
        n = number(p.get(src))
        price[dest] = float(Decimal(str(n)) * Decimal(str(scale))) if n is not None else None
    price['request_usd'] = number(p.get('request')) if unit == 'USD' else None
    if provider_id == 'uberllm':
        for dest, src in [('input_per_million_usd', 'prompt'), ('output_per_million_usd', 'completion'), ('cache_read_per_million_usd', 'input_cache_read')]:
            n = number(p.get(src))
            if price[dest] is None and n is not None:
                price[dest] = float(Decimal(str(n)) * Decimal(1_000_000))
    if provider_id == 'orcarouter':
        for dest, src in [('input_per_million_usd', 'prompt_per_million'), ('output_per_million_usd', 'completion_per_million')]:
            if number(p.get(src)) is not None:
                price[dest] = number(p[src])
    params = raw.get('supported_parameters')
    caps = raw.get('model_spec', {}).get('capabilities') or {}
    tools = raw.get('supports_tools', caps.get('supportsFunctionCalling'))
    if not isinstance(tools, bool):
        tools = ('tools' in params) if isinstance(params, list) else None
    vision = caps.get('supportsVision')
    if not isinstance(vision, bool):
        modes = raw.get('architecture', {}).get('input_modalities')
        vision = ('image' in modes) if isinstance(modes, list) else None
    access = record.get('access', {}).get('state')
    included = record.get('kind') in {'subscription', 'cli'} and provider_id != 'meta-ai'
    nim_trial = provider_id == 'nvidia' and record.get('kind') == 'free' and not p
    free = unit != 'credits' and (nim_trial or mid.endswith(':free') or documented_dash_free or raw.get('free') is True or raw.get('is_free') is True)
    billing = 'subscription' if included else ('credits' if unit == 'credits' else ('free' if free else 'api'))
    if free or included:
        price = {k: (0.0 if included or v is None else v) for k, v in price.items()}
        unit = 'USD'
    outputs = (raw.get('architecture') or {}).get('output_modalities') or (raw.get('modalities') or {}).get('output')
    non_text = (isinstance(outputs, list) and any(mode != 'text' for mode in outputs)) or raw.get('type') in {'embedding', 'embeddings', 'image', 'audio', 'video', 'speech', 'transcription', 'rerank'}
    if provider_id == 'nvidia' and any(hint in mid.lower() for hint in ('embed', 'rerank', 'reward', 'safety', 'guard', 'transcribe', 'speech', 'parakeet', 'kosmos', 'deplot', 'nvclip', 'cosmos', 'vila', 'neva', 'fuyu', 'sam-')):
        non_text = True
    warning = None
    if non_text:
        price = {k: None for k in price}
        unit, billing = 'unknown', 'unknown'
        warning = 'Metin token fiyat karşılaştırmasına dahil değil; farklı çıktı türü veya fiyat birimi.'
    return {'id': mid, 'provider_id': provider_id, 'name': str(raw.get('name') or raw.get('display_name') or mid), 'canonical_key': canonical,
            'context_length': number(raw.get('context_length', raw.get('context_window_tokens', raw.get('context_window', raw.get('max_context_tokens'))))), 'tools': tools, 'vision': vision,
            'billing': billing, 'pricing': {**price, 'unit': unit, 'variable': bool(p.get('varies_by_provider'))},
            'availability': 'blocked' if access in {'blocked', 'missing_key'} or raw.get('available') is False else ('eligible' if access == 'ready' else 'unknown'),
            'verification': {'state': 'untested', 'at': None, 'reason': 'Katalog erişimi, yanıt üretimini doğrulamaz.'},
            'updated_at': record.get('fetched_at'), 'source': record.get('catalog_url') or 'audit', 'warning': warning}


def estimate_cost(model, input_tokens, output_tokens, cache_percent=0):
    """Estimate text token cost; no extra inference, currency conversion or discounts."""
    inp, out, hit = number(input_tokens), number(output_tokens), number(cache_percent)
    def result(cost=None, warning=None):
        return {'cost_usd': cost, 'warning': warning}
    if inp is None or out is None or hit is None or hit > 100:
        return result(warning='Token sayıları ve önbellek oranı geçersiz.')
    if model.get('availability') == 'blocked':
        return result(warning='Bu rota şu anda kullanıma kapalı.')
    if model.get('billing') == 'subscription':
        return result(0.0, 'Aboneliğe dahil marjinal tahmin; kota ve rota sınırları geçerlidir, sınırsız API değildir.')
    p = model.get('pricing') or {}
    if p.get('unit') != 'USD':
        return result(warning='USD fiyatı bilinmiyor; kredi birimi dolara çevrilmez.')
    pi, po, pc = (number(p.get(k)) for k in ('input_per_million_usd', 'output_per_million_usd', 'cache_read_per_million_usd'))
    if (inp and pi is None) or (out and po is None):
        return result(warning='Token fiyatı eksik; sıfır varsayılmadı.')
    warnings = []
    if hit and pc is None:
        pc = pi
        warnings.append('Önbellek fiyatı bilinmiyor; muhafazakâr olarak normal girdi fiyatı kullanıldı.')
    cost = (inp * ((1-hit/100) * (pi or 0) + hit/100 * (pc or 0)) + out * (po or 0)) / 1_000_000
    fee = number(p.get('request_usd'))
    cost += fee or 0
    if fee is None:
        warnings.append('İstek ücreti belirtilmemiş; yalnızca bilinen token bedeli hesaplandı.')
    if p.get('variable'):
        warnings.append('Sağlayıcıya göre değişen katalog fiyatı; kesin fatura değildir.')
    if not math.isfinite(cost):
        return result(warning='İş yükü hesaplama sınırlarını aşıyor.')
    return result(cost, ' '.join(warnings) or None)
