"""All account payloads in these unit tests are synthetic fixtures."""
import unittest
from unittest.mock import patch
import importlib
from pathlib import Path


class CollectorTests(unittest.TestCase):
    def test_only_native_nvidia_build_is_classified_free(self):
        from providerdeck.collectors import discover_routes
        routes=discover_routes(config={'providers':{}},auth={},secrets={'NVIDIA_API_KEY':'synthetic'})
        self.assertEqual(next(r for r in routes if r.id=='nvidia').kind,'free')
        routes=discover_routes(config={'providers':{'nvidia':{'base_url':'https://custom.example/v1'}}},auth={},secrets={'NVIDIA_API_KEY':'synthetic'})
        self.assertEqual(next(r for r in routes if r.id=='nvidia').kind,'api')

    def test_nous_static_api_key_discovers_official_read_only_catalog(self):
        from providerdeck.collectors import discover_routes, collect_route
        from tempfile import TemporaryDirectory
        from unittest.mock import Mock
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home/'.env').write_text('NOUS_API_KEY=synthetic-nous-key' + chr(10), encoding='utf-8')
            routes = discover_routes(home=home, config={}, auth={})
        route = next((r for r in routes if r.id == 'nous'), None)
        self.assertIsNotNone(route)
        self.assertEqual(route.kind, 'api')
        self.assertFalse(route.oauth)
        request = Mock(return_value=(200, {'data': [{'id': 'synthetic-model'}]}))
        result = collect_route(route, request=request)
        request.assert_called_once()
        method, url, headers = request.call_args.args
        self.assertEqual((method, url), ('GET', 'https://inference-api.nousresearch.com/v1/models'))
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-nous-key')
        self.assertTrue(result['catalog_ok'])
        self.assertIsNone(result['provider']['balance_usd'])
        self.assertEqual(result['provider']['access']['state'], 'unknown')

    def test_copilot_catalog_and_quota_use_their_documented_auth_shapes(self):
        from providerdeck.collectors import Route, collect_route
        seen = {}
        def request(method,url,headers,body=None):
            seen[url] = dict(headers)
            if url.endswith('/models'):
                return 200, {'data':[{'id':'gpt-4.1'}]}
            return 200, {'quota_snapshots':{'chat':{'unlimited':True}}}
        collect_route(Route('copilot','Copilot','https://api.individual.githubcopilot.com','synthetic-key','subscription'), request=request)
        catalog = seen['https://api.individual.githubcopilot.com/models']
        self.assertEqual(catalog['Authorization'], 'Bearer synthetic-key')
        self.assertEqual(catalog.get('Copilot-Integration-Id'), 'vscode-chat')
        self.assertEqual(seen['https://api.github.com/copilot_internal/user']['Authorization'], 'token synthetic-key')

    def test_zen_go_quota_does_not_authorize_plain_zen_model_routes(self):
        from providerdeck.collectors import Route, collect_route
        def request(method, url, headers, body=None):
            if url.endswith('/models'):
                return 200, {'data':[{'id':'synthetic-free-model-free'}]}
            return 200, {'usage':{'monthly':{'percent':1}}}
        result = collect_route(Route('opencode-zen','Zen','https://opencode.ai/zen/v1','synthetic-key'), request=request)
        self.assertTrue(result['account_ok'])
        self.assertEqual(result['provider']['access']['state'], 'unknown')
        self.assertEqual(result['models'][0]['availability'], 'unknown')
        self.assertIn('Go', result['provider']['note'])

    def test_credit_quota_and_key_quota_preserve_units_and_reset_metadata(self):
        from providerdeck.collectors import account_patch
        void = account_patch('voidai', {'current_credits': 120, 'daily_limit': 150, 'credits_used': 30, 'reset_time': '2026-11-14T00:00:00Z'})
        self.assertEqual(void['windows'][0]['remaining_percent'], 80)
        self.assertEqual(void['windows'][0]['reset_at'], '2026-11-14T00:00:00+00:00')
        self.assertIsNone(void['balance_usd'])
        self.assertIn('yaklaşık', void['note'])
        quota = account_patch('openrouter', {'data': {'free_model_daily_requests': {'used': 20, 'limit': 100, 'remaining': 80}}})
        self.assertEqual(quota['windows'][0]['remaining_percent'], 80)
        self.assertIsNone(quota['balance_usd'])

    def test_native_anthropic_api_key_is_not_subscription_and_invalid_json_is_schema(self):
        from providerdeck.collectors import discover_routes, collect_route, Route
        import json
        routes = discover_routes(config={'model': {'provider': 'anthropic'}}, auth={}, secrets={'ANTHROPIC_API_KEY': 'sk-ant-api-synthetic-only'})
        self.assertEqual(next(r for r in routes if r.id == 'anthropic').kind, 'api')
        def invalid(*args, **kwargs):
            raise json.JSONDecodeError('synthetic invalid JSON', '<html>', 0)
        bad = collect_route(Route('uberllm', 'Uber', 'https://api.uberllm.dev/v1', 'synthetic-key'), request=invalid)
        self.assertTrue(all(e['kind'] == 'schema' for e in bad['errors']))

    def test_key_quota_reads_only_safe_daily_remaining_fields(self):
        from providerdeck.collectors import Route, collect_route
        calls = []
        def request(method, url, headers, body=None):
            calls.append(url)
            if url.endswith('/models'):
                return 200, {'data': []}
            if url.endswith('/key'):
                return 200, {'data': {'label': 'synthetic-private-label', 'workspace_id': 'synthetic-private-id', 'free_model_daily_requests': {'used': 25, 'limit': 100, 'remaining': 75}}}
            return 200, {'data': {'total_credits': 5, 'total_usage': 1}}
        result = collect_route(Route('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', 'synthetic-key'), request=request)
        self.assertIn('https://openrouter.ai/api/v1/key', calls)
        self.assertEqual(result['provider']['balance_usd'], 4)
        self.assertEqual(result['provider']['windows'][0]['remaining_percent'], 75)
        self.assertNotIn('synthetic-private', str(result))

    def test_transport_rejects_query_secrets_local_targets_and_inference(self):
        from providerdeck.collectors import read_json
        cases = [('GET', 'https://example.com/v1/models?api_key=synthetic-secret'), ('GET', 'https://127.0.0.1/v1/models'), ('POST', 'https://api.uberllm.dev/v1/chat/completions'), ('GET', 'https://user:synthetic-secret@example.com/v1/models')]
        with patch('httpx.Client', side_effect=AssertionError('transport must not open')):
            for method, url in cases:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    read_json(method, url, {})
            with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
                with self.assertRaises(ValueError):
                    read_json('GET', 'https://rebind.example/v1/models', {})

    def test_catalog_pagination_is_complete_or_explicitly_incomplete(self):
        from providerdeck.collectors import Route, collect_route
        calls = []
        def request(method, url, headers, body=None):
            calls.append(url)
            if '/models' in url:
                if 'after_id=' not in url:
                    return 200, {'data': [{'id': 'first-model'}], 'has_more': True, 'last_id': 'first-model'}
                return 200, {'data': [{'id': 'second-model'}], 'has_more': False}
            return 200, {'five_hour': {'utilization': 10}}
        r = collect_route(Route('anthropic', 'Anthropic', 'https://api.anthropic.com/v1', 'synthetic-oauth', 'subscription', True), request=request)
        self.assertEqual({m['id'] for m in r['models']}, {'first-model', 'second-model'})
        self.assertTrue(r['catalog_ok'])
        self.assertEqual(len(calls), 3)
        repeated = collect_route(Route('anthropic', 'Anthropic', 'https://api.anthropic.com/v1', 'synthetic-oauth', 'subscription', True), request=lambda *a, **k: (200, {'data': [{'id': 'first-model'}], 'has_more': True, 'last_id': 'first-model'}))
        self.assertFalse(repeated['catalog_ok'])
        self.assertTrue(repeated['errors'])

    def test_borrowed_claude_reader_supplies_oauth_without_pool_refresh(self):
        from providerdeck import collectors as c
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home/'config.yaml').write_text('model:\n  provider: anthropic\n')
            with patch.object(c, 'borrow_claude_token', return_value='synthetic-oauth') as reader:
                routes = c.discover_routes(home=home, auth={}, secrets={})
            route = next(r for r in routes if r.id == 'anthropic')
            self.assertEqual(route.credential, 'synthetic-oauth')
            self.assertTrue(route.oauth)
            reader.assert_called_once()

    def test_alias_base_matches_builtin_route_and_profile_files_are_reloaded(self):
        from providerdeck.collectors import discover_routes
        import tempfile, json, os
        routes = discover_routes(config={'model': {'aliases': {'route': {'provider': 'custom', 'base_url': 'https://api.outrun.to/v1', 'key_env': 'OUTRUN_API_KEY', 'model': 'synthetic-model'}}}}, auth={}, secrets={'OUTRUN_API_KEY': 'synthetic-outrun'})
        self.assertEqual([r.id for r in routes if r.base_url == 'https://api.outrun.to/v1'], ['outrun'])
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home/'config.yaml').write_text('providers: {}\n')
            (home/'auth.json').write_text('{}')
            (home/'.env').write_text('UNRELATED_SYNTHETIC_KEY=one\n')
            one = discover_routes(home=home, secrets={})
            (home/'config.yaml').write_text('providers:\n  later:\n    name: Later\n    base_url: https://example.com/v1\n    key_env: LATER_KEY\n')
            two = discover_routes(home=home, secrets={'LATER_KEY': 'synthetic-later'})
            self.assertNotIn('later', [r.id for r in one])
            self.assertIn('later', [r.id for r in two])
            self.assertEqual((home/'.env').read_text(), 'UNRELATED_SYNTHETIC_KEY=one\n')

    def test_cli_usage_is_bounded_read_only_and_counter_absence_is_unknown(self):
        from providerdeck.collectors import Route, collect_route
        import subprocess
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, 'No usage data (server_sent_no_usage)', '')
        with __import__('unittest').mock.patch('subprocess.run', side_effect=runner):
            result = collect_route(Route('muse-code', 'Muse Code', kind='cli'))
        self.assertTrue(result['account_ok'])
        self.assertEqual(result['provider']['windows'], [])
        self.assertIsNone(result['provider']['balance_usd'])
        self.assertNotEqual(result['provider']['access']['state'], 'blocked')
        self.assertIn('bilinmiyor', result['provider']['note'])
        self.assertIn('usage', ' '.join(calls[0][0]))
        self.assertNotIn('exec', ' '.join(calls[0][0]))
        self.assertLessEqual(calls[0][1]['timeout'], 20)
        self.assertFalse(calls[0][1].get('shell', False))

    def test_muse_cli_usage_json_parses_allowance_windows_and_no_usage_truthfully(self):
        from providerdeck.collectors import Route, collect_route
        import json, subprocess
        calls = []
        nested_payload = {
            'schemaVersion': 1,
            'allowance': {
                'window': {'usedPercent': 35, 'windowDurationMins': 300, 'resetsAtMs': 1795200000000},
                'weekly': {'usedPercent': 12, 'resetsAtMs': 1795700000000},
                'asOfMs': 1795180000000,
            },
        }
        def runner_nested(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, json.dumps(nested_payload), '')
        with patch('subprocess.run', side_effect=runner_nested):
            res = collect_route(Route('muse-code', 'Muse Code CLI', kind='cli'))
        self.assertTrue(res['account_ok'])
        self.assertEqual(res['provider']['access']['state'], 'ready')
        self.assertEqual(len(res['provider']['windows']), 2)
        self.assertEqual(res['provider']['windows'][0]['label'], 'Oturum (5 saat)')
        self.assertEqual(res['provider']['windows'][0]['remaining_percent'], 65)
        self.assertTrue(res['provider']['windows'][0]['reset_at'].startswith('2026-11-2'))
        self.assertEqual(res['provider']['windows'][1]['label'], 'Haftalık')
        self.assertEqual(res['provider']['windows'][1]['remaining_percent'], 88)
        self.assertIn('--json', ' '.join(calls[0][0]))
        self.assertEqual(calls[0][1].get('stdin'), subprocess.DEVNULL)

        flat_payload = {
            'schemaVersion': 1,
            'allowance': {
                'usedPercent': 108,
                'resetsAtMs': 1795200000000,
                'weekly': {'usedPercent': 40, 'resetsAtMs': 1795700000000},
                'asOfMs': 1795180000000,
            },
        }
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(flat_payload), '')):
            res_flat = collect_route(Route('muse-code', 'Muse Code CLI', kind='cli'))
        self.assertEqual(res_flat['provider']['windows'][0]['remaining_percent'], 0)
        self.assertEqual(res_flat['provider']['windows'][1]['remaining_percent'], 60)

        no_usage = {
            'schemaVersion': 1,
            'allowance': None,
            'reason': 'server_sent_no_usage',
            'hint': 'the server sent no usage data for this subscription',
        }
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(no_usage), '')):
            res_none = collect_route(Route('muse-code', 'Muse Code CLI', kind='cli'))
        self.assertTrue(res_none['account_ok'])
        self.assertEqual(res_none['provider']['windows'], [])
        self.assertEqual(res_none['provider']['access']['state'], 'unknown')
        self.assertIn('bilinmiyor', res_none['provider']['note'])

    def test_antigravity_cli_reads_models_usage_windows_and_credits(self):
        from providerdeck.collectors import Route, collect_route
        import json, subprocess
        calls = []
        usage_payload = {
            'status': 'SUCCESS',
            'num_turns': 0,
            'usage': {'total_tokens': 0},
            'command': {
                'name': 'usage',
                'data': {
                    'groups': [
                        {
                            'name': 'Gemini Models',
                            'buckets': [
                                {'id': 'gemini-weekly', 'name': 'Weekly Limit Remaining', 'window': 'weekly', 'remaining_fraction': 0.9815, 'reset_time': '2026-11-28T08:53:32Z'},
                                {'id': 'gemini-5h', 'name': 'Five Hour Limit Remaining', 'window': '5h', 'remaining_fraction': 0.9428, 'reset_time': '2026-11-21T13:53:32Z'},
                            ],
                        },
                        {
                            'name': 'Claude and GPT models',
                            'buckets': [
                                {'id': '3p-weekly', 'name': 'Weekly Limit Remaining', 'window': 'weekly', 'remaining_fraction': 1, 'reset_time': '2026-11-28T13:04:55Z'},
                                {'id': '3p-5h', 'name': 'Five Hour Limit Remaining', 'window': '5h', 'remaining_fraction': 1, 'reset_time': '2026-11-21T18:04:55Z'},
                            ],
                        },
                    ],
                },
            },
        }
        credits_zero = {'status': 'SUCCESS', 'command': {'name': 'credits', 'data': {'remaining_credits': 0}}}
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if args[1:] == ['models']:
                return subprocess.CompletedProcess(args, 0, 'gemini-4-neon\tGemini 4 Neon\n', '')
            if '/usage' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps(usage_payload), '')
            if '/credits' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps(credits_zero), '')
            return subprocess.CompletedProcess(args, 1, '', '')
        with patch('subprocess.run', side_effect=runner):
            res = collect_route(Route('antigravity', 'Google Antigravity', kind='subscription'))
        self.assertTrue(res['catalog_ok'])
        self.assertTrue(res['account_ok'])
        self.assertEqual(len(res['models']), 1)
        self.assertEqual(res['provider']['access']['state'], 'ready')
        self.assertEqual(len(res['provider']['windows']), 4)
        self.assertEqual(res['provider']['windows'][0]['label'], 'Gemini Haftalık')
        self.assertAlmostEqual(res['provider']['windows'][0]['remaining_percent'], 98.2, places=1)
        self.assertEqual(res['provider']['windows'][0]['reset_at'], '2026-11-28T08:53:32+00:00')
        self.assertEqual(res['provider']['windows'][1]['label'], 'Gemini (5 saat)')
        self.assertAlmostEqual(res['provider']['windows'][1]['remaining_percent'], 94.3, places=1)
        self.assertEqual(res['provider']['windows'][2]['label'], 'Claude & GPT Haftalık')
        self.assertEqual(res['provider']['windows'][2]['remaining_percent'], 100.0)
        self.assertIsNone(res['provider']['credits'])
        self.assertTrue(all(k.get('stdin') == subprocess.DEVNULL for _, k in calls))
        self.assertTrue(all(not k.get('shell', False) for _, k in calls))

        credits_pos = {'status': 'SUCCESS', 'command': {'name': 'credits', 'data': {'remaining_credits': 42}}}
        def runner_pos(args, **kwargs):
            if args[1:] == ['models']:
                return subprocess.CompletedProcess(args, 0, 'gemini-4-neon\tGemini 4 Neon\n', '')
            if '/usage' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps(usage_payload), '')
            return subprocess.CompletedProcess(args, 0, json.dumps(credits_pos), '')
        with patch('subprocess.run', side_effect=runner_pos):
            res_pos = collect_route(Route('antigravity', 'Google Antigravity', kind='subscription'))
        self.assertEqual(res_pos['provider']['credits'], {'value': 42, 'unit': 'AI kredisi'})

    def test_collection_reads_catalog_and_account_with_failure_metadata(self):
        from providerdeck.collectors import Route, collect_route
        calls = []
        def request(method, url, headers, body=None):
            calls.append((method, url))
            if url.endswith('/models'):
                return 200, {'data': [{'id': 'synthetic-model', 'pricing': {'input_usd': 2, 'output_usd': 4}}]}
            return 200, {'available_usd': 3, 'email': 'private@example.com', 'access_token': 'synthetic-secret'}
        result = collect_route(Route('uberllm', 'UberLLM', 'https://api.uberllm.dev/v1', 'synthetic-secret'), request=request)
        self.assertEqual(result['provider']['balance_usd'], 3)
        self.assertEqual(result['provider']['access']['evidence'], 'account')
        self.assertEqual(result['models'][0]['verification']['state'], 'untested')
        self.assertNotIn('synthetic-secret', str(result))
        self.assertNotIn('private@example.com', str(result))
        self.assertEqual(calls, [('GET', 'https://api.uberllm.dev/v1/models'), ('GET', 'https://api.uberllm.dev/v1/balance')])
        for status, kind in [(401, 'auth'), (403, 'eligibility'), (429, 'quota'), (503, 'transient'), (404, 'endpoint')]:
            failed = collect_route(Route('uberllm', 'UberLLM', 'https://api.uberllm.dev/v1', 'synthetic-secret'), request=lambda *a, **k: (status, {'message': 'synthetic-secret'}))
            self.assertEqual(failed['errors'][0]['kind'], kind)
            self.assertEqual(failed['errors'][0]['http_status'], status)
            self.assertNotIn('synthetic-secret', str(failed))
        malformed = collect_route(Route('uberllm', 'UberLLM', 'https://api.uberllm.dev/v1', 'synthetic-secret'), request=lambda *a, **k: (200, {'unexpected': []}))
        self.assertEqual(malformed['errors'][0]['kind'], 'schema')
        missing_calls = []
        collect_route(Route('uberllm', 'UberLLM', 'https://api.uberllm.dev/v1'), request=lambda *a, **k: missing_calls.append(a))
        self.assertEqual(missing_calls, [])

    def test_account_normalization_uses_spendable_units_and_quota_windows(self):
        from providerdeck.collectors import account_patch
        self.assertAlmostEqual(account_patch('openrouter', {'data': {'total_credits': 5, 'total_usage': 2}})['balance_usd'], 3)
        self.assertEqual(account_patch('uberllm', {'available_usd': 2, 'balance_usd': 9})['balance_usd'], 2)
        self.assertEqual(account_patch('explabs', {'data': {'total_credits': 0, 'total_usage': .01}})['balance_usd'], -.01)
        self.assertEqual(account_patch('voidai', {'current_credits': 125000})['credits'], {'value': 125000, 'unit': 'kredi'})
        self.assertIsNone(account_patch('voidai', {'current_credits': 125000})['balance_usd'])
        self.assertEqual(account_patch('venice', {'data': {'balances': {'USD': 0}, 'accessPermitted': False}})['access']['state'], 'blocked')
        codex = account_patch('openai-codex', {'rate_limit': {'allowed': True, 'primary_window': {'used_percent': 10, 'limit_window_seconds': 604800, 'reset_at': 1795072864}}})
        self.assertEqual(codex['windows'][0]['label'], 'Haftalık')
        self.assertEqual(codex['windows'][0]['remaining_percent'], 90)
        self.assertEqual(account_patch('anthropic', {'five_hour': {'utilization': 24, 'resets_at': '2026-11-14T00:00:00Z'}})['windows'][0]['remaining_percent'], 76)
        self.assertEqual(account_patch('opencode-go', {'usage': {'monthly': {'percent': 1, 'resetsAt': '2026-12-01T00:00:00Z'}}})['windows'][0]['remaining_percent'], 99)
        unlimited = account_patch('copilot', {'quota_snapshots': {'chat': {'unlimited': True, 'remaining': 0, 'entitlement': 0}, 'premium_interactions': {'remaining': 100, 'entitlement': 200}}})
        self.assertIsNone(unlimited['windows'][0]['remaining_percent'])
        self.assertIn('sınırsız', unlimited['windows'][0]['label'])
        self.assertEqual(unlimited['windows'][1]['remaining_percent'], 50)
        self.assertIsNone(account_patch('uberllm', {})['balance_usd'])

    def test_dynamic_discovery_dedupes_aliases_and_never_exposes_credentials(self):
        c = importlib.import_module('providerdeck.collectors')
        routes = c.discover_routes(config={'providers': {'custom': {'name': 'Custom', 'base_url': 'https://example.com/v1', 'key_env': 'CUSTOM_KEY'}}, 'model': {'aliases': {'a': 'meta/muse-spark', 'b': 'meta-ai/muse-spark', 'virtual': 'moa/x', 'x': {'provider': 'custom', 'base_url': 'https://example.com/v1', 'key_env': 'CUSTOM_KEY', 'model': 'model'}}}}, auth={'credential_pool': {'openai-codex': [{'access_token': 'synthetic-token'}, {'access_token': 'synthetic-token'}]}}, secrets={'CUSTOM_KEY': 'synthetic-key', 'MODEL_API_KEY': 'synthetic-meta'})
        ids = [r.id for r in routes]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn('moa', ids)
        self.assertNotIn('meta', ids)
        self.assertIn('meta-ai', ids)
        self.assertIn('muse-code', ids)
        custom = next(r for r in routes if r.id == 'custom')
        self.assertEqual(custom.credential, 'synthetic-key')
        self.assertNotIn('synthetic-key', repr(custom))
        self.assertEqual(len([r for r in routes if r.id == 'openai-codex']), 1)

    def test_standalone_local_cli_credentials_and_commands_work_without_hermes(self):
        import json, os, tempfile
        from providerdeck import collectors as c
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            (fake_home / '.claude').mkdir()
            (fake_home / '.claude/.credentials.json').write_text(
                json.dumps({'claudeAiOauth': {'accessToken': 'synthetic-claude-oauth'}}), encoding='utf-8'
            )
            (fake_home / '.codex').mkdir()
            (fake_home / '.codex/auth.json').write_text(
                json.dumps({'tokens': {'access_token': 'synthetic-codex-token', 'account_id': 'acct-synthetic'}}), encoding='utf-8'
            )
            with patch('pathlib.Path.home', return_value=fake_home), patch.dict(os.environ, {'HERMES_HOME': str(fake_home / 'absent')}):
                self.assertEqual(c.borrow_claude_token(), 'synthetic-claude-oauth')
                self.assertEqual(c.borrow_codex_auth(), ('synthetic-codex-token', 'acct-synthetic'))
            with patch.dict(os.environ, {'AGY_BIN': '/opt/agy/agy'}):
                self.assertEqual(c._antigravity_bin(), '/opt/agy/agy')
            cmd = c._muse_usage_command()
            self.assertIn('usage', ' '.join(cmd))
            self.assertIn('--json', ' '.join(cmd))

    def test_custom_base_url_for_builtin_provider_never_reuses_native_credentials(self):
        from providerdeck import collectors as c
        with patch.object(c, 'borrow_claude_token', return_value='synthetic-claude-oauth') as claude_reader, \
             patch.object(c, 'borrow_codex_auth', return_value=('synthetic-codex-oauth', 'acct-1')) as codex_reader:
            routes = c.discover_routes(
                config={'providers': {
                    'anthropic': {'base_url': 'https://custom-proxy.example/v1'},
                    'openai-codex': {'base_url': 'https://custom-codex.example/v1'},
                    'openrouter': {'base_url': 'https://custom-or.example/v1', 'key_env': 'CUSTOM_OR_KEY'},
                }},
                auth={'providers': {'openai-codex': {'tokens': {'access_token': 'synthetic-native-codex', 'account_id': 'acct-native'}}}},
                secrets={'ANTHROPIC_API_KEY': 'sk-ant-api-native', 'OPENROUTER_API_KEY': 'sk-or-native', 'CUSTOM_OR_KEY': 'sk-custom-explicit'},
            )
        by_id = {r.id: r for r in routes}
        self.assertEqual(by_id['anthropic'].credential, '')
        self.assertFalse(by_id['anthropic'].oauth)
        self.assertEqual(by_id['anthropic'].kind, 'api')
        self.assertEqual(by_id['openai-codex'].credential, '')
        self.assertEqual(by_id['openai-codex'].principal, '')
        self.assertEqual(by_id['openrouter'].credential, 'sk-custom-explicit')
        claude_reader.assert_not_called()
        codex_reader.assert_not_called()
