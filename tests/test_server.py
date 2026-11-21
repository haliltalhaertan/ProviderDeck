"""Local service acceptance tests. Fixture snapshots are synthetic."""
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

@contextmanager
def running(loader=None, refresher=None, directory=None, maintenance=None, auto_refresh_seconds=None):
    module_path = ROOT / 'providerdeck/server.py'
    if not module_path.exists():
        raise AssertionError('ProviderDeck local server feature is not implemented')
    module = importlib.import_module('providerdeck.server')
    server = module.build_server(
        static_dir=directory or ROOT / 'providerdeck' / 'web',
        state_loader=loader or (lambda: {'schema_version': 1, 'providers': [], 'models': [], 'notes': [], 'errors': []}),
        refresher=refresher or (lambda: {'schema_version': 1, 'providers': [], 'models': [], 'notes': [], 'errors': []}),
        port=0,
        maintenance=maintenance,
        auto_refresh_seconds=auto_refresh_seconds,
    )
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        yield server, 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

class LocalReadTests(unittest.TestCase):
    def test_state_returns_injected_snapshot_without_network(self):
        fixture = {'schema_version': 1, 'providers': [{'id': 'fixture-provider', 'balance_usd': None}], 'models': [], 'notes': [], 'errors': []}
        with running(loader=lambda: fixture) as (_, url):
            with urllib.request.urlopen(url + '/api/state', timeout=3) as response:
                self.assertEqual(response.status, 200)
                state = json.load(response)
            self.assertEqual(state['providers'][0]['id'], 'fixture-provider')
            self.assertIsNone(state['providers'][0]['balance_usd'])
            self.assertFalse(state['refresh']['running'])

    def test_automatic_refresh_updates_without_http_post_and_skips_overlap(self):
        entered = threading.Event()
        release = threading.Event()
        called = []
        def refresh():
            called.append(time.monotonic())
            count = len(called)
            if count == 1:
                entered.set()
                release.wait(3)
            return {'providers': [{'id': 'fixture-provider', 'balance_usd': count}], 'models': []}
        with running(refresher=refresh, auto_refresh_seconds=.15) as (_, url):
            try:
                self.assertTrue(entered.wait(2))
                time.sleep(.34)
                with urllib.request.urlopen(url + '/api/state', timeout=2) as response:
                    state = json.load(response)
                self.assertTrue(state['refresh']['running'])
                self.assertEqual(state['refresh']['source'], 'automatic')
                self.assertEqual(state['refresh']['auto_interval_seconds'], .15)
                self.assertEqual(len(called), 1)
            finally:
                release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with urllib.request.urlopen(url + '/api/state', timeout=2) as response:
                    state = json.load(response)
                if state['providers'] and state['providers'][0]['balance_usd'] >= 2:
                    break
                time.sleep(.03)
            self.assertGreaterEqual(state['providers'][0]['balance_usd'], 2)
            self.assertGreaterEqual(called[1] - called[0], .15)

    def test_manual_refresh_is_background_single_flight(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def refresh():
            calls.append(True)
            entered.set()
            release.wait(4)
            return {'schema_version': 1, 'providers': [], 'models': [{'id': 'new-fixture'}], 'notes': [], 'errors': []}
        with running(refresher=refresh) as (_, url):
            try:
                request = urllib.request.Request(url + '/api/refresh', data=b'', headers={'X-ProviderDeck-Request': '1'}, method='POST')
                try:
                    first = urllib.request.urlopen(request, timeout=2)
                    first_status = first.status
                    first.close()
                except urllib.error.HTTPError as error:
                    first_status = error.code
                self.assertEqual(first_status, 202)
                self.assertTrue(entered.wait(2))
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 202)
                with urllib.request.urlopen(url + '/api/state', timeout=2) as response:
                    self.assertTrue(json.load(response)['refresh']['running'])
                self.assertEqual(len(calls), 1)
            finally:
                release.set()

    def test_nous_free_check_has_separate_guarded_http_action(self):
        from unittest.mock import patch
        from providerdeck import server as deck
        from providerdeck.nous_probe import MODEL
        started = threading.Event()
        release = threading.Event()
        calls = []
        def probe():
            calls.append(True)
            started.set()
            release.wait(3)
            return 'ok'
        def request(base, path, *, method='GET', body=None, headers=None):
            req = urllib.request.Request(base + path, data=body, headers=headers or {}, method=method)
            try:
                with urllib.request.urlopen(req, timeout=4) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)
        action = '/api/nous-free-test'
        headers = {'X-ProviderDeck-Request': '1'}
        with patch.object(deck, 'run_nous_free_test', probe, create=True), running() as (_, url):
            self.assertEqual(request(url, '/api/state')[0], 200)
            self.assertEqual(request(url, '/api/refresh', method='POST', body=b'', headers=headers)[0], 202)
            self.assertEqual(request(url, action)[0], 404)
            self.assertEqual(request(url, action, method='POST', body=b'')[0], 403)
            self.assertEqual(request(url, action, method='POST', body=b'', headers={**headers, 'Origin': 'https://outside.invalid'})[0], 403)
            self.assertEqual(request(url, action, method='POST', body=b'{"model":"paid"}', headers=headers)[0], 400)
            self.assertEqual(request(url, action + '?model=paid', method='POST', body=b'', headers=headers)[0], 400)
            self.assertFalse(calls)
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(request, url, action, method='POST', body=b'', headers=headers)
                try:
                    self.assertTrue(started.wait(2))
                    self.assertTrue(request(url, '/api/state')[1]['nous_free_test']['running'])
                    self.assertEqual(request(url, action, method='POST', body=b'', headers=headers)[0], 409)
                finally:
                    release.set()
                status, result = future.result(timeout=3)
            self.assertEqual(status, 200)
            self.assertEqual(set(result), {'code', 'message', 'at'})
            self.assertEqual(result['code'], 'ok')
            state = request(url, '/api/state')[1]
            self.assertEqual(state['nous_free_test']['model'], MODEL)
            self.assertEqual(state['nous_free_test']['result'], result)
            self.assertFalse(state['nous_free_test']['running'])
            self.assertEqual(state['providers'], [])
            self.assertEqual(request(url, action, method='POST', body=b'', headers=headers)[0], 429)
            self.assertEqual(len(calls), 1)

    def test_failed_refresh_preserves_last_snapshot_without_secret_message(self):
        def broken():
            raise RuntimeError('SYNTHETIC_SECRET_MUST_NOT_LEAK')
        with running(refresher=broken) as (service, url):
            service.store.request_refresh()
            for _ in range(50):
                state = service.store.read()
                if not state['refresh']['running']:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(state['refresh']['error'])
            self.assertNotIn('SYNTHETIC_SECRET_MUST_NOT_LEAK', json.dumps(state))
            self.assertEqual(state['providers'], [])

    def test_local_app_assets_and_health_are_served_with_safe_headers(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            directory = Path(folder)
            (directory / 'index.html').write_text('<!doctype html><title>fixture</title>', encoding='utf-8')
            (directory / 'style.css').write_text('body{color:green}', encoding='utf-8')
            with running(directory=directory) as (_, url):
                try:
                    response = urllib.request.urlopen(url + '/', timeout=2)
                    code = response.status
                except urllib.error.HTTPError as error:
                    response, code = error, error.code
                self.assertEqual(code, 200)
                self.assertIn(b'fixture', response.read())
                self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
                self.assertIn("script-src 'self'", response.headers.get('Content-Security-Policy', ''))
                with urllib.request.urlopen(url + '/style.css', timeout=2) as css:
                    self.assertIn('text/css', css.headers.get('Content-Type'))
                with urllib.request.urlopen(url + '/api/health', timeout=2) as health:
                    self.assertEqual(json.load(health)['app'], 'providerdeck')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url + '/data/snapshot.json', timeout=2)
                self.assertEqual(error.exception.code, 404)

    def test_loopback_boundary_rejects_host_csrf_and_cross_site_requests(self):
        def status(url, *, method='GET', headers=None, body=None):
            request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
            try:
                with urllib.request.urlopen(request, timeout=2) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        with running() as (_, url):
            self.assertEqual(status(url + '/api/state', headers={'Host': 'evil.example'}), 403)
            self.assertEqual(status(url + '/api/state', headers={'Sec-Fetch-Site': 'cross-site'}), 403)
            self.assertEqual(status(url + '/api/state', headers={'Origin': 'https://evil.example'}), 403)
            self.assertEqual(status(url + '/api/refresh', method='POST', body=b''), 403)
            self.assertEqual(status(url + '/api/refresh', method='POST', body=b'', headers={'X-ProviderDeck-Request':'1','Origin':'https://evil.example'}), 403)
            self.assertEqual(status(url + '/api/refresh', method='POST', body=b'x'*5000, headers={'X-ProviderDeck-Request':'1','Origin':url}), 413)
            self.assertEqual(status(url + '/api/health', headers={'Origin':url}), 200)
            self.assertEqual(status(url + '/%2e%2e/.env'), 404)

    def test_http_snapshot_is_whitelisted_even_if_loader_has_private_fields(self):
        fixture = {'schema_version':1, 'providers':[{'id':'synthetic','api_key':'PRIVATE_SYNTHETIC','balance_usd':None}], 'models':[], 'notes':[], 'errors':[], 'access_token':'PRIVATE_SYNTHETIC'}
        with running(loader=lambda:fixture) as (_, url):
            with urllib.request.urlopen(url + '/api/state', timeout=2) as response:
                body = response.read().decode('utf-8')
            self.assertNotIn('PRIVATE_SYNTHETIC', body)
            self.assertNotIn('api_key', body)
            self.assertNotIn('access_token', body)
            self.assertIsNone(json.loads(body)['providers'][0]['balance_usd'])

    def test_real_cli_service_starts_offline_and_shuts_down_cleanly(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            runtime = Path(folder) / 'runtime.json'
            process = subprocess.Popen([sys.executable, '-m', 'providerdeck.server', '--port', '0', '--no-refresh', '--runtime', str(runtime)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if runtime.exists() or process.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertTrue(runtime.exists(), 'CLI service did not publish a local runtime URL')
                url = json.loads(runtime.read_text(encoding='utf-8'))['url']
                self.assertTrue(url.startswith('http://127.0.0.1:'))
                with urllib.request.urlopen(url + '/api/health', timeout=3) as response:
                    self.assertEqual(json.load(response)['app'], 'providerdeck')
                request = urllib.request.Request(url + '/api/shutdown', data=b'', headers={'X-ProviderDeck-Request':'1'}, method='POST')
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    def test_a_second_service_cannot_bind_an_already_owned_port(self):
        with running() as (first, url):
            module = importlib.import_module('providerdeck.server')
            second = None
            try:
                with self.assertRaises(OSError):
                    second = module.build_server(static_dir=ROOT/'providerdeck'/'web',state_loader=lambda:{'providers':[],'models':[]},refresher=lambda:{'providers':[],'models':[]},port=first.server_port)
            finally:
                if second is not None:
                    second.server_close()

    def test_maintenance_api_observes_errors_without_automatic_llm_calls(self):
        import tempfile
        from providerdeck.maintenance import MaintenanceStore
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            maintenance=MaintenanceStore(Path(folder))
            fixture={'providers':[],'models':[],'errors':[{'provider_id':'agentrouter','kind':'auth','http_status':401,'message':'synthetic auth issue'}]}
            with running(loader=lambda:fixture,maintenance=maintenance) as (_,url):
                try:
                    response=urllib.request.urlopen(url+'/api/maintenance',timeout=2)
                    code=response.status
                except urllib.error.HTTPError as error:
                    response,code=error,error.code
                self.assertEqual(code,200)
                state=json.load(response)
                self.assertFalse(state['automatic_enabled'])
                self.assertEqual(len(state['incidents']),1)
                request=urllib.request.Request(url+'/api/maintenance/review',data=json.dumps({'provider_id':'agentrouter'}).encode(),headers={'X-ProviderDeck-Request':'1','Content-Type':'application/json'},method='POST')
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request,timeout=2)
                self.assertEqual(error.exception.code,400)
                self.assertFalse(maintenance.state()['busy'])

    def test_maintenance_page_is_an_explicit_local_asset(self):
        with running() as (_,url):
            try:
                response=urllib.request.urlopen(url+'/maintenance',timeout=2)
                code=response.status
            except urllib.error.HTTPError as error:
                response,code=error,error.code
            self.assertEqual(code,200)
            self.assertIn('Bakım',response.read().decode('utf-8'))
            with urllib.request.urlopen(url+'/maintenance.js',timeout=2) as script:
                self.assertIn('text/javascript',script.headers.get('Content-Type'))

    def test_offline_mode_blocks_manual_refresh_and_maintenance_network_jobs(self):
        import tempfile
        from providerdeck.maintenance import MaintenanceStore
        called = []
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            maintenance = MaintenanceStore(Path(folder))
            module = importlib.import_module('providerdeck.server')
            srv = module.build_server(
                static_dir=ROOT / 'providerdeck' / 'web',
                state_loader=lambda: {'providers': [], 'models': []},
                refresher=lambda: called.append('refresh') or {'providers': [], 'models': []},
                port=0,
                maintenance=maintenance,
                nous_test_enabled=False,
                refresh_enabled=False,
            )
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{srv.server_port}'
            try:
                with urllib.request.urlopen(url + '/api/state', timeout=2) as resp:
                    st = json.load(resp)
                self.assertTrue(st['refresh']['offline'])
                for path, body in [('/api/refresh', b''), ('/api/maintenance/docs', b'{"provider_id":"openrouter"}'), ('/api/nous-free-test', b'')]:
                    headers = {'X-ProviderDeck-Request': '1'}
                    if body:
                        headers['Content-Type'] = 'application/json'
                    req = urllib.request.Request(url + path, data=body, headers=headers, method='POST')
                    with self.assertRaises(urllib.error.HTTPError) as err:
                        urllib.request.urlopen(req, timeout=2)
                    self.assertEqual(err.exception.code, 503, path)
                self.assertEqual(called, [])
                self.assertFalse(maintenance.state()['busy'])
            finally:
                srv.shutdown()
                srv.server_close()
                thread.join(timeout=2)

if __name__ == '__main__':
    unittest.main()
