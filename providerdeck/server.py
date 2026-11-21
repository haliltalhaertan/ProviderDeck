"""Loopback HTTP surface for ProviderDeck: read-only refresh and a separate manual test."""
from __future__ import annotations

import copy
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from math import ceil
from pathlib import Path
import threading
from time import monotonic
from urllib.parse import urlsplit

from .data import public_snapshot
from .nous_probe import MODEL as NOUS_FREE_MODEL, RESULTS as NOUS_RESULTS, run_nous_free_test


AUTO_REFRESH_SECONDS = 60


def iso_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class SnapshotStore:
    def __init__(self, loader, refresher, observer=None, auto_refresh_seconds=None):
        self._lock = threading.Lock()
        self._state = public_snapshot(loader())
        self._refresher = refresher
        self._observer = observer
        self._last_started = None
        self._auto_refresh_seconds = auto_refresh_seconds
        self._refresh = {'running': False, 'started_at': None, 'finished_at': None, 'error': None,
                         'source': None, 'auto_interval_seconds': auto_refresh_seconds}
        if self._observer:
            self._observer(self.read())

    def read(self):
        with self._lock:
            state = copy.deepcopy(self._state)
            state['refresh'] = dict(self._refresh)
            return state

    def request_refresh(self, source='manual'):
        with self._lock:
            if self._refresh['running']:
                return False
            started = monotonic()
            if source == 'automatic':
                interval = self._auto_refresh_seconds
                if interval is None or (self._last_started is not None and started - self._last_started < interval):
                    return False
            self._last_started = started
            self._refresh.update(running=True, started_at=iso_now(), error=None, source=source)
        threading.Thread(target=self._run_refresh, name='providerdeck-refresh', daemon=True).start()
        return True

    def _run_refresh(self):
        try:
            state = self._refresher()
            with self._lock:
                self._state = public_snapshot(state)
            if self._observer:
                self._observer(state)
        except Exception as exc:
            with self._lock:
                self._refresh['error'] = 'Yenileme tamamlanamadı (' + type(exc).__name__ + '); son veri korundu.'
        finally:
            with self._lock:
                self._refresh.update(running=False, finished_at=iso_now())


class DeckServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def service_actions(self):
        self.store.request_refresh(source='automatic')

    def server_bind(self):
        import os
        import socket
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class NousTestGate:
    def __init__(self, enabled):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.running = False
        self.next_run = 0.0
        self.result = None

    def state(self):
        with self.lock:
            return {'enabled': self.enabled, 'model': NOUS_FREE_MODEL, 'running': self.running,
                    'wait_seconds': max(0, ceil(self.next_run - monotonic())),
                    'result': dict(self.result) if self.result else None}

    def run(self):
        with self.lock:
            if not self.enabled:
                return 503, {'message': 'Ücretsiz model testi çevrimdışı modda kapalı.'}
            if self.running:
                return 409, {'message': 'Ücretsiz model testi zaten çalışıyor.'}
            if monotonic() < self.next_run:
                return 429, {'message': 'Yeniden test etmek için kısa bir süre bekle.'}
            self.running = True
        code = 'upstream_error'
        try:
            code = run_nous_free_test()
        except Exception:
            code = 'upstream_error'
        finally:
            if not isinstance(code, str) or code not in NOUS_RESULTS:
                code = 'upstream_error'
            result = {'code': code, 'message': NOUS_RESULTS[code], 'at': iso_now()}
            with self.lock:
                self.result = result
                self.next_run = monotonic() + 30
                self.running = False
        return 200, result


def build_server(*, static_dir: Path, state_loader, refresher, port: int = 0, maintenance=None,
                 nous_test_enabled=True, auto_refresh_seconds=None):
    store = SnapshotStore(state_loader, refresher, observer=maintenance.observe if maintenance else None,
                          auto_refresh_seconds=auto_refresh_seconds)
    nous_test = NousTestGate(nous_test_enabled)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'ProviderDeck/0.1'
        sys_version = ''

        def send_bytes(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_bytes(status, body, 'application/json; charset=utf-8')

        def allowed_request(self, *, mutation=False):
            hosts = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
            origin = self.headers.get('Origin')
            if self.headers.get('Host', '').lower() not in hosts or (origin is not None and origin not in {'http://' + host for host in hosts}) or self.headers.get('Sec-Fetch-Site', '').lower() not in {'', 'none', 'same-origin'}:
                self.send_json(403, {'error': 'Yalnız bu bilgisayardaki aynı kaynak kabul edilir.'})
                return False
            if mutation and self.headers.get('X-ProviderDeck-Request') != '1':
                self.send_json(403, {'error': 'Aynı kaynak istek başlığı gerekli.'})
                return False
            if mutation:
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    length = -1
                if length < 0 or self.headers.get('Transfer-Encoding'):
                    self.send_json(400, {'error': 'Geçersiz istek boyutu.'})
                    return False
                if length > 4096:
                    self.send_json(413, {'error': 'İstek fazla büyük.'})
                    return False
            return True

        def do_GET(self):
            if not self.allowed_request():
                return
            path = urlsplit(self.path).path
            if path == '/api/state':
                state = store.read()
                state['nous_free_test'] = nous_test.state()
                self.send_json(200, state)
                return
            if path == '/api/maintenance':
                self.send_json(200, maintenance.state() if maintenance else {'automatic_enabled': False, 'apply_enabled': False, 'busy': False, 'incidents': [], 'documents': {}, 'reviews': {}, 'available_docs': []})
                return
            if path == '/api/health':
                self.send_json(200, {'ok': True, 'app': 'providerdeck', 'version': '0.1.0'})
                return
            assets = {'/maintenance': ('maintenance.html', 'text/html'), '/maintenance.html': ('maintenance.html', 'text/html'), '/maintenance.js': ('maintenance.js', 'text/javascript'), '/maintenance.css': ('maintenance.css', 'text/css'), '/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'), '/style.css': ('style.css', 'text/css'), '/app.js': ('app.js', 'text/javascript'), '/core.js': ('core.js', 'text/javascript')}
            if path not in assets:
                self.send_json(404, {'error': 'Bulunamadı'})
                return
            filename, content_type = assets[path]
            try:
                content = (Path(static_dir) / filename).read_bytes()
            except OSError:
                self.send_json(404, {'error': 'Arayüz dosyası bulunamadı'})
                return
            self.send_bytes(200, content, content_type + '; charset=utf-8')

        def do_POST(self):
            if not self.allowed_request(mutation=True):
                return
            target = urlsplit(self.path)
            path = target.path
            if path == '/api/nous-free-test':
                if target.query or int(self.headers.get('Content-Length', '0')) != 0:
                    self.send_json(400, {'message': 'Test isteği gövde veya parametre kabul etmez.'})
                    return
                status, result = nous_test.run()
                self.send_json(status, result)
                return
            if path in {'/api/maintenance/docs', '/api/maintenance/review'}:
                if not maintenance:
                    self.send_json(503, {'error': 'Bakım modülü bu servis örneğinde etkin değil.'})
                    return
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
                    pid = body.get('provider_id') if isinstance(body, dict) else None
                    if not isinstance(pid, str):
                        raise ValueError('Sağlayıcı seçilmeli.')
                    accepted = maintenance.request_job('docs' if path.endswith('/docs') else 'review', pid)
                except (ValueError, TypeError):
                    self.send_json(400, {'error': 'Uygun kalıcı entegrasyon sorunu veya güvenilir belge kaydı yok; hesap/kota sorunları otomatik düzeltilmez. Aynı ajan için bekleme/bütçe sınırı da uygulanır.'})
                    return
                self.send_json(202, {'accepted': accepted, 'running': True})
                return
            if path == '/api/shutdown':
                self.send_json(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if urlsplit(self.path).path != '/api/refresh':
                self.send_json(404, {'error': 'Bulunamadı'})
                return
            store.request_refresh()
            self.send_json(202, {'accepted': True, 'running': True})

        def log_message(self, *_args):
            pass

    server = DeckServer(('127.0.0.1', port), Handler)
    server.store = store
    return server


def main(argv=None):
    import argparse
    import os
    parser = argparse.ArgumentParser(description='ProviderDeck local read-only monitor')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--no-refresh', action='store_true', help='Disable startup and automatic account refresh')
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args(argv)
    from .data import data_dir, load_cached_snapshot, refresh_snapshot
    from .maintenance import MaintenanceStore
    maintenance = MaintenanceStore(data_dir() / 'maintenance')
    folder = Path(__file__).resolve().parent
    settings = dict(static_dir=folder / 'web', state_loader=load_cached_snapshot, refresher=refresh_snapshot,
                    maintenance=maintenance, nous_test_enabled=not args.no_refresh,
                    auto_refresh_seconds=None if args.no_refresh else AUTO_REFRESH_SECONDS)
    try:
        service = build_server(port=args.port, **settings)
    except OSError:
        if args.port == 0:
            raise
        service = build_server(port=0, **settings)
    url = 'http://127.0.0.1:' + str(service.server_port)
    runtime = args.runtime or data_dir() / 'runtime.json'
    runtime.parent.mkdir(parents=True, exist_ok=True)
    record = {'app': 'providerdeck', 'url': url, 'pid': os.getpid(), 'started_at': iso_now()}
    import tempfile
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=runtime.parent, prefix='runtime-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(record, ensure_ascii=False))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, runtime)
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)
    print('PROVIDERDECK_READY ' + url, flush=True)
    if not args.no_refresh:
        service.store.request_refresh(source='startup')
    try:
        service.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        service.server_close()
        try:
            if json.loads(runtime.read_text(encoding='utf-8')).get('pid') == os.getpid():
                runtime.unlink()
        except (OSError, ValueError):
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
