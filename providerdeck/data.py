"""Safe account/model snapshots; audit loading is entirely offline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import zipfile

from . import collectors as c
from .pricing import normalize_model

NOTES = ['Katalog fiyatları tahmindir; hesap/katalog HTTP 200, model yanıtı üretimini kanıtlamaz.',
         'Pozitif USD toplamına yalnızca pozitif dolar bakiyeleri dahildir; kredi birimleri ve abonelik kotaları toplanmaz.',
         'Bu uygulama yalnızca hesap/katalog okur; çıkarım, kimlik yenileme ve yapılandırma yazımı yapmaz.']


def data_dir():
    return Path(os.environ.get('PROVIDERDECK_DATA_DIR') or Path(__file__).resolve().parents[1] / 'data')


def empty_snapshot():
    return {'schema_version': 1, 'generated_at': None, 'providers': [], 'models': [], 'notes': list(NOTES), 'errors': []}


def _audit_seed():
    root = Path(os.environ.get('PROVIDERDECK_AUDIT_DIR') or c.hermes_home() / 'provider-audits')
    if not root.is_dir():
        return empty_snapshot()
    paths = sorted(root.glob('*/provider-denetimi.json'), reverse=True)
    if not paths:
        return empty_snapshot()
    try:
        report_path = paths[0]
        report = json.loads(report_path.read_text(encoding='utf-8-sig'))
        timestamp = c.iso(report.get('checked_at'))
        snapshot = empty_snapshot()
        snapshot['generated_at'] = timestamp
        snapshot['notes'].append('Başlangıç verisi tarihli denetimden yüklendi; canlı yenileme yapılana kadar eskidir.')
        providers = {}
        for row in report.get('providers', []):
            pid = row.get('provider')
            if not isinstance(pid, str) or pid == 'moa':
                continue
            kind = 'subscription' if pid in {'anthropic', 'openai-codex', 'opencode-go', 'copilot', 'antigravity'} else 'api'
            provider = c.provider_shell(c.Route(pid, c.NAMES.get(pid) or row.get('name') or pid, kind=kind))
            provider['fetched_at'] = timestamp
            group = row.get('group')
            state = 'blocked' if group == 'blocked' else ('ready' if group in {'positive_balance', 'included_working'} else 'unknown')
            status = str(row.get('status') or '')
            if '_API_KEY yok' in status or 'access token ve' in status:
                state = 'missing_key'
            provider['access'] = {'state': state, 'reason': status or ('Önceki denetimde erişim engeli görüldü.' if state == 'blocked' else 'Tarihli denetim verisi; güncel erişim kontrol edilmedi.'), 'evidence': 'audit'}
            provider['note'] = 'Tarihli denetim: ' + (status or 'Yeni model yanıtı testi yapılmadı.')
            if pid == 'meta-ai':
                provider['note'] = 'Native Meta API faturası ayrıdır; Muse Code abonelik doğrulaması yalnızca CLI rotasını kapsar.'
            providers[pid] = provider
        receipts = []
        archive = report_path.parent / 'provider-denetimi-kanit-v2.zip'
        if archive.exists():
            with zipfile.ZipFile(archive) as z:
                if 'receipts.jsonl' in z.namelist():
                    for line in z.read('receipts.jsonl').decode('utf-8-sig').splitlines():
                        if not line.strip():
                            continue
                        r = json.loads(line)
                        receipts.append(r)
                        pid = r.get('provider')
                        if pid not in providers or r.get('http') != 200 or not isinstance(r.get('payload'), dict):
                            continue
                        if r.get('url') == c.ACCOUNT_URLS.get(pid):
                            patch = c.account_patch(pid, r['payload'])
                            for key, value in patch.items():
                                if value is not None and value != []:
                                    providers[pid][key] = value
                            providers[pid]['fetched_at'] = c.iso(r.get('at')) or timestamp
                for name in z.namelist():
                    if not name.startswith('catalogs/catalog-') or not name.endswith('.json'):
                        continue
                    catalog = json.loads(z.read(name))
                    pid = catalog.get('provider')
                    if pid not in providers:
                        continue
                    catalog_source = next((r.get('url') for r in reversed(receipts) if r.get('provider') == pid and r.get('purpose') == 'catalog' and r.get('http') == 200 and r.get('url')), 'audit')
                    for raw in catalog.get('models', []):
                        model = normalize_model(pid, raw, {**providers[pid], 'fetched_at': c.iso(catalog.get('at')), 'catalog_url': catalog_source})
                        if model['id']:
                            snapshot['models'].append(model)
                if 'abonelik-duzeltmesi.json' in z.namelist():
                    correction = json.loads(z.read('abonelik-duzeltmesi.json'))
                    muse = correction.get('muse_code') or {}
                    if muse.get('process_exit_code') == 0 and muse.get('observed_real_reply') and muse.get('model'):
                        at = c.iso(correction.get('checked_at')) or timestamp
                        provider = c.provider_shell(c.Route('muse-code', c.NAMES['muse-code'], kind='cli'))
                        provider.update({'fetched_at': at, 'access': {'state': 'ready', 'reason': 'Tarihli denetimde CLI abonelik yanıtı doğrulandı; güncel sayaç bilinmiyor.', 'evidence': 'audit'}, 'last_inference': {'at': at, 'model': muse['model'], 'status': 'ok', 'source': 'audit'}, 'note': 'Muse Code CLI aboneliği; server_sent_no_usage bilinmeyen sayaçtır, sıfır değildir. Native Meta API ayrı ücretlendirilir.'})
                        providers['muse-code'] = provider
                        model = normalize_model('muse-code', {'id': muse['model']}, provider)
                        model['verification'] = {'state': 'ok', 'at': at, 'reason': 'Tarihli denetimde yalnızca bu CLI modeli yanıt verdi.'}
                        snapshot['models'].append(model)
        # Attach evidence only to the exact route/model. No all-model inference claims.
        for r in receipts:
            pid, mid = r.get('provider'), r.get('model')
            if pid not in providers:
                continue
            if pid == 'antigravity' and r.get('command') == 'agy models' and r.get('exit_code') == 0:
                for line in str(r.get('stdout') or '').splitlines():
                    if '\t' in line:
                        model_id, name = line.split('\t', 1)
                        snapshot['models'].append(normalize_model(pid, {'id': model_id, 'name': name}, {**providers[pid], 'fetched_at': c.iso(r.get('at'))}))
            if not mid or not any(word in str(r.get('purpose', '')) for word in ('inference', 'retry')):
                continue
            ok = r.get('inference_verified') is True
            if not ok and not (isinstance(r.get('http'), int) and r['http'] >= 400):
                continue
            at = c.iso(r.get('at')) or timestamp
            # A Zen credential using /go/ is evidence for a linked Go route, not plain Zen.
            if pid == 'opencode-zen' and '/go/' in str(r.get('url')):
                providers[pid]['note'] = 'Zen anahtarı Go hakkına bağlı; Go ve Zen kotaları toplanmaz. Plain Zen modellerine genellenemez.'
                continue
            existing = next((m for m in snapshot['models'] if m['provider_id'] == pid and m['id'] == mid), None)
            if existing is None and ok and (mid.endswith(':free') or providers[pid]['kind'] == 'subscription'):
                existing = normalize_model(pid, {'id': mid}, {**providers[pid], 'fetched_at': at})
                snapshot['models'].append(existing)
            if existing:
                existing['verification'] = {'state': 'ok' if ok else 'failed', 'at': at, 'reason': 'Bu tam model kimliği tarihli denetimde yanıt verdi.' if ok else 'Bu tam model kimliği tarihli denetimde yanıt veremedi.'}
                if not ok:
                    existing['availability'] = 'blocked'
            providers[pid]['last_inference'] = {'at': at, 'model': mid, 'status': 'ok' if ok else 'failed', 'source': 'audit'}
        for pid, p in providers.items():
            p['models_count'] = sum(m['provider_id'] == pid for m in snapshot['models'])
            p['stale'] = True
        snapshot['providers'] = list(providers.values())
        return snapshot
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        result = empty_snapshot()
        result['errors'].append({'provider_id': 'local', 'message': 'Denetim dosyası okunamadı; veri uydurulmadı.', 'kind': 'schema'})
        return result


def public_snapshot(payload, *, secrets=()):
    """Construct the entire browser/disk interface from an explicit whitelist."""
    import re
    from urllib.parse import urlsplit, parse_qsl
    from .pricing import number
    def text(value, default=''):
        if value is None:
            return default
        value = str(value)[:4000]
        for secret in secrets:
            if isinstance(secret, str) and len(secret) > 4:
                value = value.replace(secret, '[gizlendi]')
        value = re.sub(r'(?i)\bBearer\s+\S+|\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)', '[gizlendi]', value)
        value = re.sub(r'[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b', '[gizlendi]', value)
        return ''.join(ch for ch in value if ch >= ' ' or ch == '\n')
    def url(value):
        if not isinstance(value, str) or value != text(value):
            return None
        try:
            u = urlsplit(value)
            if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment:
                return None
            if any(k not in {'limit', 'after_id', 'client_version'} for k, _ in parse_qsl(u.query)):
                return None
            return value
        except ValueError:
            return None
    def enum(value, allowed, default):
        return value if value in allowed else default
    def obj(value):
        return value if isinstance(value, dict) else {}
    def rows(value):
        return value if isinstance(value, list) else []
    out = empty_snapshot()
    payload = obj(payload)
    out['generated_at'] = c.iso(payload.get('generated_at'))
    for raw in rows(payload.get('providers')):
        p = obj(raw)
        access = obj(p.get('access'))
        pid = text(p.get('id'))
        if not pid or pid == 'moa':
            continue
        windows = []
        for w in rows(p.get('windows')):
            w = obj(w)
            pct = number(w.get('remaining_percent'))
            windows.append({'label': text(w.get('label')), 'remaining_percent': min(100, pct) if pct is not None else None, 'reset_at': c.iso(w.get('reset_at')), 'scope': enum(w.get('scope'), {'account', 'model'}, 'account')})
        credit = obj(p.get('credits'))
        cv = number(credit.get('value'))
        inference = obj(p.get('last_inference'))
        last = {'at': c.iso(inference.get('at')), 'model': text(inference.get('model')), 'status': enum(inference.get('status'), {'ok', 'failed', 'unknown'}, 'unknown'), 'source': enum(inference.get('source'), {'audit', 'cli'}, 'audit')} if inference else None
        out['providers'].append({'id': pid, 'name': text(p.get('name') or pid), 'kind': enum(p.get('kind'), {'api', 'subscription', 'free', 'cli'}, 'api'), 'balance_usd': number(p.get('balance_usd'), negative=True), 'credits': {'value': cv, 'unit': text(credit.get('unit') or 'kredi')} if cv is not None else None,
                                 'windows': windows, 'access': {'state': enum(access.get('state'), {'ready', 'blocked', 'missing_key', 'unknown'}, 'unknown'), 'reason': text(access.get('reason')), 'evidence': enum(access.get('evidence'), {'account', 'inference', 'catalog', 'local', 'audit'}, 'local')},
                                 'fetched_at': c.iso(p.get('fetched_at')), 'stale': bool(p.get('stale', True)), 'models_count': 0, 'dashboard_url': p.get('dashboard_url') if p.get('dashboard_url') in c.DOCS.values() else None, 'note': text(p.get('note')), 'last_inference': last})
    allowed_providers = {p['id'] for p in out['providers']}
    seen = set()
    for raw in rows(payload.get('models')):
        m = obj(raw)
        pid, mid = text(m.get('provider_id')), text(m.get('id'))
        if not mid or pid not in allowed_providers or (pid, mid) in seen:
            continue
        seen.add((pid, mid))
        price = obj(m.get('pricing'))
        v = obj(m.get('verification'))
        out['models'].append({'id': mid, 'provider_id': pid, 'name': text(m.get('name') or mid), 'canonical_key': text(m.get('canonical_key') or mid), 'context_length': number(m.get('context_length')), 'tools': m.get('tools') if isinstance(m.get('tools'), bool) else None, 'vision': m.get('vision') if isinstance(m.get('vision'), bool) else None,
                              'billing': enum(m.get('billing'), {'api', 'free', 'subscription', 'credits', 'unknown'}, 'unknown'), 'pricing': {**{k: number(price.get(k)) for k in ('input_per_million_usd', 'output_per_million_usd', 'cache_read_per_million_usd', 'request_usd')}, 'unit': enum(price.get('unit'), {'USD', 'credits', 'unknown'}, 'unknown'), 'variable': bool(price.get('variable'))},
                              'availability': enum(m.get('availability'), {'eligible', 'blocked', 'unknown'}, 'unknown'), 'verification': {'state': enum(v.get('state'), {'ok', 'failed', 'untested'}, 'untested'), 'at': c.iso(v.get('at')), 'reason': text(v.get('reason'))}, 'updated_at': c.iso(m.get('updated_at')), 'source': url(m.get('source')) or 'audit', 'warning': text(m.get('warning'), None)})
    for p in out['providers']:
        p['models_count'] = sum(m['provider_id'] == p['id'] for m in out['models'])
    out['notes'] = [text(n) for n in rows(payload.get('notes'))] or list(NOTES)
    for raw in rows(payload.get('errors')):
        e = obj(raw)
        error = {'provider_id': text(e.get('provider_id') or 'local'), 'message': text(e.get('message') or 'Okuma tamamlanamadı.')}
        if e.get('kind') in {'auth', 'quota', 'eligibility', 'transient', 'schema', 'endpoint', 'unknown'}:
            error['kind'] = e['kind']
        if isinstance(e.get('http_status'), int) and 100 <= e['http_status'] <= 599:
            error['http_status'] = e['http_status']
        if url(e.get('source_url')):
            error['source_url'] = url(e['source_url'])
        if e.get('docs_url') in c.DOCS.values():
            error['docs_url'] = e['docs_url']
        if c.iso(e.get('last_good_at')):
            error['last_good_at'] = c.iso(e['last_good_at'])
        out['errors'].append(error)
    return out


def load_cached_snapshot():
    """Immediate disk/audit read. This path never opens an account connection."""
    for name in ('snapshot.json', 'last-good.json'):
        try:
            payload = json.loads((data_dir() / name).read_text(encoding='utf-8'))
            if payload.get('schema_version') == 1:
                safe = public_snapshot(payload)
                from datetime import datetime, timezone
                for provider in safe['providers']:
                    try:
                        timestamp = datetime.fromisoformat(provider['fetched_at'])
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                        if (datetime.now(timezone.utc) - timestamp).total_seconds() > 300:
                            provider['stale'] = True
                    except (TypeError, ValueError):
                        provider['stale'] = True
                return safe
        except (OSError, ValueError, AttributeError):
            pass
    return public_snapshot(_audit_seed())


def _atomic_cache(snapshot):
    import tempfile
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    content = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for name in ('last-good.json', 'snapshot.json'):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, folder / name)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()


def refresh_snapshot():
    """Bounded read-only refresh; one provider's failure cannot erase the others."""
    from concurrent.futures import ThreadPoolExecutor
    import copy
    previous = load_cached_snapshot()
    prior = {p['id']: p for p in previous['providers']}
    prior_models = {(m['provider_id'], m['id']): m for m in previous['models']}
    try:
        routes = c.discover_routes()
    except Exception:
        previous['errors'] = [c.failure('local', schema=True)]
        for p in previous['providers']:
            p['stale'] = True
        return public_snapshot(previous)
    from datetime import datetime, timezone
    now_dt = datetime.now(timezone.utc)

    def _is_recent(iso_str, max_age_seconds):
        try:
            ts = datetime.fromisoformat(str(iso_str).replace('Z', '+00:00'))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return 0 <= (now_dt - ts).total_seconds() <= max_age_seconds
        except (TypeError, ValueError):
            return False

    def _is_future(iso_str):
        try:
            ts = datetime.fromisoformat(str(iso_str).replace('Z', '+00:00'))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts > now_dt
        except (TypeError, ValueError):
            return False

    ag_models = [m for (pid, _), m in prior_models.items() if pid == 'antigravity']
    reuse_ag_catalog = bool(ag_models) and all(_is_recent(m.get('updated_at'), 900) for m in ag_models)

    snapshot = empty_snapshot()
    snapshot['generated_at'] = c.now()
    def isolated(route):
        try:
            if route.id == 'antigravity' and reuse_ag_catalog:
                return c.collect_route(route, skip_catalog=True)
            return c.collect_route(route)
        except Exception:
            return {'provider': c.provider_shell(route), 'models': [], 'errors': [c.failure(route.id)], 'account_ok': False, 'catalog_ok': False}
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix='providerdeck-read') as pool:
        # map keeps deterministic provider/error order, independent of response timing.
        results = list(pool.map(isolated, routes))
    for route, result in zip(routes, results):
        fresh = result['provider']
        old = prior.get(route.id)
        provider = copy.deepcopy(fresh)
        if old:
            provider['last_inference'] = old.get('last_inference')
            if not result['account_ok']:
                key_added = old['access']['state'] == 'missing_key' and bool(route.credential)
                for key in ('balance_usd', 'credits', 'windows'):
                    provider[key] = copy.deepcopy(old.get(key))
                cached_account = old.get('balance_usd') is not None or old.get('credits') is not None or bool(old.get('windows'))
                if cached_account or not result['catalog_ok']:
                    if old.get('fetched_at'):
                        provider['fetched_at'] = old['fetched_at']
                    provider['stale'] = True
                # Local key presence supersedes the cached absence, not an access denial.
                if not key_added and fresh['access']['state'] not in {'blocked', 'missing_key'}:
                    provider['access'] = copy.deepcopy(old['access'])
            elif route.id == 'muse-code' and not provider.get('windows'):
                prior_weekly = next(
                    (w for w in (old.get('windows') or [])
                     if isinstance(w, dict) and str(w.get('label') or '').startswith('Haftalık')
                     and w.get('remaining_percent') is not None and _is_future(w.get('reset_at'))),
                    None,
                )
                if prior_weekly is not None:
                    retained = copy.deepcopy(prior_weekly)
                    retained['label'] = 'Haftalık (son bilinen)'
                    provider['windows'] = [retained]
                    provider['note'] = (provider.get('note') or '') + ' Süresi dolmamış son haftalık kota korunuyor.'
            # A catalog or positive non-USD credits do not lift an audited eligibility gate.
            if old['access']['state'] == 'blocked' and fresh['access']['state'] not in {'blocked', 'missing_key'} and not (provider.get('balance_usd') is not None and provider['balance_usd'] > 0):
                provider['access'] = copy.deepcopy(old['access'])
        if result['catalog_ok']:
            models = result['models']
            for model in models:
                historical = prior_models.get((route.id, model['id']))
                if historical and historical.get('verification', {}).get('state') != 'untested':
                    model['verification'] = copy.deepcopy(historical['verification'])
                    if historical.get('availability') == 'blocked' and historical['verification']['state'] == 'failed':
                        model['availability'] = 'blocked'
                        model['warning'] = 'Tarihli model hatası korunuyor; katalogda görünmesi yeniden yanıt verebildiğini kanıtlamaz.'
            if route.id == 'explabs':
                present = {m['id'] for m in models}
                for (pid, mid), historical in prior_models.items():
                    if pid == route.id and mid.endswith(':free') and mid not in present and historical.get('verification', {}).get('state') == 'ok':
                        retained = copy.deepcopy(historical)
                        retained['source'] = 'audit'
                        retained['warning'] = 'Bu tam ücretsiz rota tarihli denetimde doğrulandı; güncel katalog yalnızca ana kimliği listeliyor.'
                        models.append(retained)
        elif result.get('catalog_reused'):
            models = [copy.deepcopy(m) for (pid, _), m in prior_models.items() if pid == route.id]
        else:
            models = [copy.deepcopy(m) for (pid, _), m in prior_models.items() if pid == route.id]
            for model in models:
                model['warning'] = 'Katalog yenilenemedi; tarihli son iyi kayıt gösteriliyor.'
        for model in models:
            if provider['access']['state'] in {'blocked', 'missing_key'}:
                model['availability'] = 'blocked'
            elif provider['access']['state'] == 'ready' and model['availability'] != 'blocked':
                model['availability'] = 'eligible'
            if model.get('billing') == 'api' and provider.get('balance_usd') is not None and provider['balance_usd'] <= 0:
                model['availability'] = 'blocked'
                model['warning'] = 'Ücretli cüzdan tükenmiş; ücretsiz veya aboneliğe dahil rota ayrı değerlendirilir.'
            if route.id == 'nvidia' and model['availability'] != 'blocked' and model.get('verification', {}).get('state') != 'ok':
                model['availability'] = 'unknown'
                model['warning'] = 'NVIDIA kataloğunda emekli modeller de kalabilir; bu tam model için başarılı yanıt kanıtı yok.'
        provider['models_count'] = len(models)
        snapshot['providers'].append(provider)
        snapshot['models'].extend(models)
        for error in result['errors']:
            if old and old.get('fetched_at'):
                error['last_good_at'] = old['fetched_at']
            snapshot['errors'].append(error)
    safe = public_snapshot(snapshot, secrets=[r.credential for r in routes if r.credential])
    try:
        _atomic_cache(safe)
    except OSError:
        safe['errors'].append({'provider_id': 'local', 'message': 'Yerel önbellek yazılamadı; sonuç bellekte döndürüldü.', 'kind': 'transient'})
    return safe
