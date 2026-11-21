"""Synthetic on-disk audits/cache and injected reads, never displayed as live data."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


class DataTests(unittest.TestCase):
    def test_empty_api_wallet_does_not_authorize_all_paid_models_via_free_tier(self):
        from providerdeck import data as d,collectors as c
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder,patch.dict(os.environ,{'PROVIDERDECK_DATA_DIR':folder,'PROVIDERDECK_AUDIT_DIR':str(Path(folder)/'no-audit')}):
            route=c.Route('explabs','Labs','https://api.experientiallabs.ai/v1','private-fixture-credential-123')
            def request(method,url,headers,body=None):
                if url.endswith('/models'):return 200,{'data':[{'id':'synthetic-paid','pricing':{'input_nano_usd_per_million_tokens':1000000000,'output_nano_usd_per_million_tokens':2000000000}},{'id':'synthetic-free:free'}]}
                if url.endswith('/key'):return 200,{'data':{'is_free_tier':True}}
                return 200,{'data':{'total_credits':1,'total_usage':1}}
            with patch.object(c,'discover_routes',return_value=[route]),patch.object(c,'read_json',side_effect=request):state=d.refresh_snapshot()
            models={m['id']:m for m in state['models']}
            self.assertEqual(models['synthetic-paid']['availability'],'blocked')
            self.assertEqual(models['synthetic-free:free']['availability'],'eligible')

    def test_unprobed_nvidia_catalog_model_cannot_win_as_verified_usable(self):
        from providerdeck import data as d,collectors as c
        from providerdeck.pricing import normalize_model
        p=c.provider_shell(c.Route('nvidia','NIM',kind='free'))
        p['access']={'state':'ready','reason':'synthetic historical reply','evidence':'audit'}
        known=normalize_model('nvidia',{'id':'synthetic-known'},p)
        known['verification']={'state':'ok','at':'2026-01-01T00:00:00Z','reason':'synthetic exact-model receipt'}
        seed={'schema_version':1,'generated_at':'2026-01-01T00:00:00Z','providers':[p],'models':[known],'notes':[],'errors':[]}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder,patch.dict(os.environ,{'PROVIDERDECK_DATA_DIR':folder}):
            (Path(folder)/'snapshot.json').write_text(json.dumps(seed),encoding='utf-8')
            route=c.Route('nvidia','NIM','https://integrate.api.nvidia.com/v1','private-fixture-credential-123','free')
            with patch.object(c,'discover_routes',return_value=[route]),patch.object(c,'read_json',return_value=(200,{'data':[{'id':'synthetic-known'},{'id':'synthetic-unprobed'}]})):
                state=d.refresh_snapshot()
            models={m['id']:m for m in state['models']}
            self.assertEqual(models['synthetic-known']['availability'],'eligible')
            self.assertEqual(models['synthetic-unprobed']['availability'],'unknown')

    def test_old_cache_is_stale_and_failed_model_cannot_reenter_usable_ranking(self):
        from providerdeck import data as d, collectors as c
        from providerdeck.pricing import normalize_model
        p = c.provider_shell(c.Route('nvidia', 'NVIDIA'))
        p.update({'stale': False, 'fetched_at': '2000-01-01T00:00:00Z', 'access': {'state': 'ready', 'reason': 'audit', 'evidence': 'audit'}})
        m = normalize_model('nvidia', {'id': 'retired-model'}, p)
        m.update({'availability': 'blocked', 'verification': {'state': 'failed', 'at': '2000-01-01T00:00:00Z', 'reason': 'Tarihli EOL hatası'}})
        seed = {'schema_version': 1, 'generated_at': p['fetched_at'], 'providers': [p], 'models': [m], 'notes': [], 'errors': []}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': tmp}):
            (Path(tmp)/'snapshot.json').write_text(json.dumps(seed))
            self.assertTrue(d.load_cached_snapshot()['providers'][0]['stale'])
            route = c.Route('nvidia', 'NVIDIA', 'https://integrate.api.nvidia.com/v1', 'synthetic-key')
            with patch.object(c, 'discover_routes', return_value=[route]), patch.object(c, 'read_json', return_value=(200, {'data': [{'id': 'retired-model'}]})):
                fresh = d.refresh_snapshot()
            self.assertEqual(fresh['models'][0]['availability'], 'blocked')
            self.assertEqual(fresh['models'][0]['verification']['state'], 'failed')

    def test_verified_free_suffix_survives_plain_catalog_without_inventing_other_routes(self):
        from providerdeck import data as d, collectors as c
        from providerdeck.pricing import normalize_model
        p = c.provider_shell(c.Route('explabs', 'Labs'))
        p['access'] = {'state': 'ready', 'reason': 'Tarihli doğrulama', 'evidence': 'audit'}
        m = normalize_model('explabs', {'id': 'synthetic-model:free'}, {**p, 'fetched_at': '2026-11-13T10:00:00Z'})
        m['verification'] = {'state': 'ok', 'at': '2026-11-13T10:00:00Z', 'reason': 'Tarihli tam rota doğrulaması'}
        seed = {'schema_version': 1, 'generated_at': '2026-11-13T10:00:00Z', 'providers': [p], 'models': [m], 'notes': [], 'errors': []}
        route = c.Route('explabs', 'Labs', 'https://api.experientiallabs.ai/v1', 'synthetic-key')
        def request(method, url, headers, body=None):
            return (200, {'data': [{'id': 'synthetic-model'}, {'id': 'other-model'}]}) if url.endswith('/models') else (200, {'data': {'total_credits': 0, 'total_usage': 0}})
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': tmp}):
            (Path(tmp)/'snapshot.json').write_text(json.dumps(seed))
            with patch.object(c, 'discover_routes', return_value=[route]), patch.object(c, 'read_json', side_effect=request):
                state = d.refresh_snapshot()
            ids = {m['id'] for m in state['models']}
            self.assertEqual(ids, {'synthetic-model', 'synthetic-model:free', 'other-model'})
            historic = next(m for m in state['models'] if m['id'].endswith(':free'))
            self.assertEqual(historic['source'], 'audit')
            self.assertIn('tarihli', historic['warning'])

    def test_audit_access_is_precise_and_catalogs_keep_actual_source_urls(self):
        from providerdeck.data import load_cached_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root/'audits'/'2026-11-13'
            day.mkdir(parents=True)
            (day/'provider-denetimi.json').write_text(json.dumps({'checked_at': '2026-11-13T10:00:00Z', 'providers': [{'provider': 'uberllm', 'group': 'positive_balance', 'status': 'Hesap bakiyesi doğrulandı; çıkarım denenmedi.'}, {'provider': 'voidai', 'group': 'blocked', 'status': 'Discord uygunluk koşulu engelliyor.'}, {'provider': 'nanogpt', 'group': 'blocked', 'status': 'NANOGPT_API_KEY yok; hesap bakiyesi bilinmiyor.'}]}))
            receipt = {'provider': 'uberllm', 'purpose': 'catalog', 'url': 'https://api.uberllm.dev/v1/models', 'http': 200}
            with zipfile.ZipFile(day/'provider-denetimi-kanit-v2.zip', 'w') as z:
                z.writestr('receipts.jsonl', json.dumps(receipt)+'\n')
                z.writestr('catalogs/catalog-uberllm.json', json.dumps({'provider': 'uberllm', 'models': [{'id': 'synthetic-model'}]}))
            with patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': str(root/'cache'), 'PROVIDERDECK_AUDIT_DIR': str(root/'audits')}):
                state = load_cached_snapshot()
            by_id = {p['id']: p for p in state['providers']}
            self.assertEqual(by_id['uberllm']['access']['state'], 'ready')
            self.assertEqual(by_id['nanogpt']['access']['state'], 'missing_key')
            self.assertIn('Discord', by_id['voidai']['access']['reason'])
            self.assertEqual(state['models'][0]['source'], 'https://api.uberllm.dev/v1/models')

    def test_audit_correction_separates_muse_and_preserves_exact_historical_free_routes(self):
        from providerdeck.data import load_cached_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root/'audits'/'2026-11-13'
            day.mkdir(parents=True)
            (day/'provider-denetimi.json').write_text(json.dumps({'checked_at': '2026-11-13T10:00:00Z', 'providers': [{'provider': 'meta-ai', 'group': 'subscription_cli_verified'}, {'provider': 'explabs', 'group': 'included_working'}]}))
            correction = {'checked_at': '2026-11-13T11:00:00Z', 'muse_code': {'model': 'muse-spark-1.3', 'observed_real_reply': 'MUSE_SUBSCRIPTION_OK', 'process_exit_code': 0}}
            receipt = {'provider': 'explabs', 'purpose': 'bounded_free_or_included_retry', 'model': 'deepseek-test:free', 'at': '2026-11-13T10:30:00Z', 'http': 200, 'inference_verified': True}
            with zipfile.ZipFile(day/'provider-denetimi-kanit-v2.zip', 'w') as z:
                z.writestr('abonelik-duzeltmesi.json', json.dumps(correction))
                z.writestr('receipts.jsonl', json.dumps(receipt)+'\n')
                z.writestr('catalogs/catalog-meta-ai.json', json.dumps({'provider': 'meta-ai', 'models': [{'id': 'muse-spark-1.3'}]}))
            with patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': str(root/'cache'), 'PROVIDERDECK_AUDIT_DIR': str(root/'audits')}):
                state = load_cached_snapshot()
            providers = {p['id']: p for p in state['providers']}
            self.assertIn('muse-code', providers)
            self.assertEqual(providers['muse-code']['kind'], 'cli')
            self.assertEqual(providers['muse-code']['last_inference']['status'], 'ok')
            by_key = {(m['provider_id'], m['id']): m for m in state['models']}
            self.assertEqual(by_key['muse-code', 'muse-spark-1.3']['billing'], 'subscription')
            self.assertEqual(by_key['meta-ai', 'muse-spark-1.3']['billing'], 'api')
            self.assertEqual(by_key['meta-ai', 'muse-spark-1.3']['verification']['state'], 'untested')
            free = by_key['explabs', 'deepseek-test:free']
            self.assertEqual(free['verification']['state'], 'ok')
            self.assertEqual(free['billing'], 'free')
            self.assertEqual(free['source'], 'audit')
            self.assertNotIn(('explabs', 'deepseek-test'), by_key)

    def test_refresh_isolates_failures_preserves_last_good_and_writes_safe_atomic_cache(self):
        from providerdeck import collectors as c, data as d
        from providerdeck.pricing import normalize_model
        old = c.provider_shell(c.Route('uberllm', 'UberLLM'))
        old.update({'balance_usd': 7, 'fetched_at': '2026-11-12T00:00:00Z', 'access': {'state': 'ready', 'reason': 'Önceki hesap okuması', 'evidence': 'account'}})
        previous_model = normalize_model('uberllm', {'id': 'old-model', 'pricing': {'input_usd': 2, 'output_usd': 4}}, old)
        seed = {'schema_version': 1, 'generated_at': old['fetched_at'], 'providers': [old], 'models': [previous_model], 'errors': [], 'notes': []}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': tmp}):
            (Path(tmp)/'snapshot.json').write_text(json.dumps(seed))
            routes = [c.Route('uberllm', 'UberLLM', 'https://api.uberllm.dev/v1', 'synthetic-secret'), c.Route('refuseless', 'Refuseless', 'https://api.refuseless.com/v1', 'synthetic-key')]
            def request(method, url, headers, body=None):
                if 'uberllm' in url:
                    raise TimeoutError('do not export synthetic-secret')
                if url.endswith('/models'):
                    return 200, {'data': [{'id': 'new-model', 'name': 'synthetic-secret private@example.com', 'pricing': {'currency': 'USD', 'input_per_m': 1, 'output_per_m': 2}}]}
                return 200, {'available': 3}
            with patch.object(c, 'discover_routes', return_value=routes), patch.object(c, 'read_json', side_effect=request):
                result = d.refresh_snapshot()
            by_id = {p['id']: p for p in result['providers']}
            self.assertEqual(by_id['uberllm']['balance_usd'], 7)
            self.assertTrue(by_id['uberllm']['stale'])
            self.assertEqual(by_id['uberllm']['fetched_at'], '2026-11-12T00:00:00+00:00')
            self.assertEqual(by_id['refuseless']['balance_usd'], 3)
            self.assertFalse(by_id['refuseless']['stale'])
            self.assertEqual({m['id'] for m in result['models']}, {'old-model', 'new-model'})
            self.assertEqual(result['errors'][0]['last_good_at'], '2026-11-12T00:00:00+00:00')
            self.assertNotIn('synthetic-secret', json.dumps(result))
            self.assertEqual(d.load_cached_snapshot(), result)
            self.assertEqual(json.loads((Path(tmp)/'snapshot.json').read_text(encoding='utf-8')), result)
            self.assertFalse(list(Path(tmp).glob('*.tmp')))
            # Broken newest file must still permit the timestamped last-good snapshot.
            (Path(tmp)/'snapshot.json').write_text('{broken', encoding='utf-8')
            self.assertEqual(d.load_cached_snapshot(), result)

    def test_muse_no_usage_retains_unexpired_weekly_and_antigravity_reuses_fresh_catalog(self):
        from datetime import datetime, timedelta, timezone
        from providerdeck import collectors as c, data as d
        future_reset = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        fresh_at = datetime.now(timezone.utc).isoformat()
        seed = {
            'schema_version': 1, 'generated_at': fresh_at,
            'providers': [
                {'id': 'muse-code', 'name': 'Muse Code CLI', 'kind': 'cli', 'fetched_at': fresh_at, 'stale': False,
                 'access': {'state': 'ready', 'reason': 'ok', 'evidence': 'account'},
                 'windows': [{'label': 'Haftalık', 'remaining_percent': 64.0, 'reset_at': future_reset, 'scope': 'account'}]},
                {'id': 'antigravity', 'name': 'Google Antigravity', 'kind': 'subscription', 'fetched_at': fresh_at, 'stale': False,
                 'access': {'state': 'ready', 'reason': 'ok', 'evidence': 'account'}, 'windows': []},
            ],
            'models': [
                {'id': 'gemini-4-neon', 'provider_id': 'antigravity', 'name': 'Gemini 4 Neon', 'canonical_key': 'gemini-4-neon',
                 'billing': 'subscription', 'pricing': {'unit': 'USD'}, 'availability': 'eligible', 'updated_at': fresh_at, 'source': 'audit'},
            ],
            'notes': [], 'errors': [],
        }
        seen_skip = []
        def fake_collect(route, *, request=None, skip_catalog=False):
            seen_skip.append((route.id, skip_catalog))
            p = c.provider_shell(route)
            p['fetched_at'] = fresh_at
            p['stale'] = False
            if route.id == 'muse-code':
                p['access'] = {'state': 'unknown', 'reason': 'ok', 'evidence': 'account'}
                p['note'] = 'server_sent_no_usage'
                return {'provider': p, 'models': [], 'errors': [], 'account_ok': True, 'catalog_ok': False, 'catalog_reused': False}
            p['access'] = {'state': 'ready', 'reason': 'ok', 'evidence': 'account'}
            p['windows'] = [{'label': 'Gemini Haftalık', 'remaining_percent': 95.0, 'reset_at': future_reset, 'scope': 'model'}]
            return {'provider': p, 'models': [], 'errors': [], 'account_ok': True, 'catalog_ok': False, 'catalog_reused': skip_catalog}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': tmp}):
            (Path(tmp) / 'snapshot.json').write_text(json.dumps(seed), encoding='utf-8')
            routes = [c.Route('muse-code', 'Muse Code CLI', kind='cli'), c.Route('antigravity', 'Google Antigravity', kind='subscription')]
            with patch.object(c, 'discover_routes', return_value=routes), patch.object(c, 'collect_route', side_effect=fake_collect):
                res = d.refresh_snapshot()
        by_id = {p['id']: p for p in res['providers']}
        self.assertEqual(by_id['muse-code']['windows'][0]['label'], 'Haftalık (son bilinen)')
        self.assertEqual(by_id['muse-code']['windows'][0]['remaining_percent'], 64.0)
        self.assertIn(('antigravity', True), seen_skip)
        self.assertEqual(by_id['antigravity']['models_count'], 1)
        self.assertIsNone(res['models'][0]['warning'])

    def test_catalog_only_refresh_clears_cached_missing_key_after_key_added(self):
        from providerdeck import collectors as c, data as d
        route = c.Route('magma', 'MagmaRouter', 'https://example.com/v1')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': tmp, 'PROVIDERDECK_AUDIT_DIR': tmp}), patch.object(c, 'discover_routes', return_value=[route]):
            with patch.object(c, 'read_json', side_effect=AssertionError('no requests without credentials')) as request:
                previous = d.refresh_snapshot()['providers'][0]
                request.assert_not_called()
            self.assertEqual(previous['access']['state'], 'missing_key')
            route.credential = 'synthetic-key'
            with patch.object(c, 'read_json', return_value=(200, {'data': [{'id': 'test-model'}]})):
                state = d.refresh_snapshot()
                again = d.refresh_snapshot()
            provider = state['providers'][0]
            self.assertEqual(provider['access']['state'], 'unknown')
            self.assertEqual(provider['access']['evidence'], 'catalog')
            self.assertIsNone(provider['balance_usd'])
            self.assertEqual(provider['models_count'], 1)
            self.assertIsNotNone(provider['fetched_at'])
            self.assertFalse(provider['stale'])
            self.assertEqual(state['models'][0]['availability'], 'unknown')
            self.assertEqual(again['providers'][0]['access']['state'], 'unknown')
            self.assertFalse(again['providers'][0]['stale'])
            self.assertEqual(d.load_cached_snapshot(), again)

    def test_public_projection_scrubs_cache_and_typed_error_metadata(self):
        from providerdeck.data import public_snapshot
        payload = {'schema_version': 1, 'generated_at': '2026-11-13T10:00:00Z', 'access_token': 'never-export', 'providers': [{'id': 'test', 'name': 'private@example.com never-export', 'balance_usd': None, 'email': 'private@example.com', 'windows': [{'label': 'Quota', 'remaining_percent': 0, 'access_token': 'never-export'}], 'access': {'state': 'ready', 'reason': 'Bearer never-export', 'evidence': 'account', 'raw': 'never-export'}}], 'models': [], 'errors': [{'provider_id': 'test', 'message': 'private@example.com never-export', 'kind': 'quota', 'http_status': 429, 'source_url': 'https://example.com/v1/models?token=never-export', 'docs_url': 'https://evil.example/docs', 'last_good_at': '2026-11-13T10:00:00Z', 'headers': {'Authorization': 'never-export'}}]}
        safe = public_snapshot(payload, secrets=['never-export'])
        text = json.dumps(safe)
        self.assertNotIn('never-export', text)
        self.assertNotIn('private@example.com', text)
        self.assertNotIn('Authorization', text)
        self.assertEqual(safe['errors'][0]['kind'], 'quota')
        self.assertEqual(safe['errors'][0]['http_status'], 429)
        self.assertIsNone(safe['errors'][0].get('source_url'))
        self.assertIsNone(safe['errors'][0].get('docs_url'))
        self.assertIsNotNone(safe['errors'][0]['last_good_at'])
        self.assertIsNone(safe['providers'][0]['balance_usd'])
        self.assertEqual(safe['providers'][0]['windows'][0]['remaining_percent'], 0)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': tmp}):
            (Path(tmp)/'snapshot.json').write_text(json.dumps(payload))
            from providerdeck.data import load_cached_snapshot
            self.assertNotIn('access_token', json.dumps(load_cached_snapshot()))

    def test_audit_seed_is_timestamped_safe_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / 'audit' / '2026-11-13'
            audit.mkdir(parents=True)
            (audit / 'provider-denetimi.json').write_text(json.dumps({'checked_at': '2026-11-13T10:00:00Z', 'providers': [{'provider': 'uberllm', 'name': 'UberLLM', 'group': 'positive_balance'}, {'provider': 'voidai', 'name': 'VoidAI', 'group': 'blocked'}]}))
            with zipfile.ZipFile(audit / 'provider-denetimi-kanit-v2.zip', 'w') as z:
                z.writestr('catalogs/catalog-uberllm.json', json.dumps({'provider': 'uberllm', 'at': '2026-11-13T10:00:00Z', 'models': [{'id': 'test-model', 'pricing': {'input_usd': 2, 'output_usd': 4}, 'access_token': 'never-export'}]}))
                z.writestr('receipts.jsonl', json.dumps({'provider': 'uberllm', 'purpose': 'balance', 'at': '2026-11-13T10:00:00Z', 'url': 'https://api.uberllm.dev/v1/balance', 'http': 200, 'payload': {'available_usd': 3, 'email': 'private@example.com', 'account_id': 'never-export'}}) + '\n')
            with patch.dict(os.environ, {'PROVIDERDECK_DATA_DIR': str(root/'cache'), 'PROVIDERDECK_AUDIT_DIR': str(root/'audit')}):
                d = importlib.import_module('providerdeck.data')
                with patch('providerdeck.collectors.read_json', side_effect=AssertionError('network forbidden')):
                    state = d.load_cached_snapshot()
                self.assertEqual(state['schema_version'], 1)
                self.assertEqual(state['providers'][0]['balance_usd'], 3)
                self.assertTrue(state['providers'][0]['stale'])
                self.assertEqual(state['models'][0]['pricing']['input_per_million_usd'], 2)
                self.assertEqual(state['models'][0]['updated_at'], '2026-11-13T10:00:00+00:00')
                self.assertNotIn('never-export', json.dumps(state))
                self.assertNotIn('private@example.com', json.dumps(state))
                self.assertEqual(state['providers'][1]['access']['state'], 'blocked')
                self.assertIsNone(state['providers'][1]['balance_usd'])
