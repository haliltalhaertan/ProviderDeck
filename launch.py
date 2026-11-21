"""One-click local launcher; no credentials or model calls."""
from urllib.parse import urlsplit


def safe_runtime_url(value):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return url.scheme == 'http' and url.hostname == '127.0.0.1' and bool(url.port) and not url.username and not url.password and url.path in {'', '/'} and not url.query and not url.fragment
    except ValueError:
        return False


def existing_url(runtime):
    import json
    import urllib.request
    try:
        record = json.loads(runtime.read_text(encoding='utf-8'))
        url = record.get('url')
        if not safe_runtime_url(url):
            return None
        with urllib.request.urlopen(url + '/api/health', timeout=1.5) as response:
            health = json.load(response)
        return url.rstrip('/') if health.get('app') == 'providerdeck' and health.get('ok') is True else None
    except (OSError, ValueError, TypeError):
        return None


def _trim_log(path, max_bytes=2_000_000):
    try:
        if path.is_file() and path.stat().st_size > max_bytes:
            tail = path.read_bytes()[-(max_bytes // 2):]
            path.write_bytes(tail)
    except OSError:
        pass


def main(argv=None):
    import argparse
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    import urllib.request
    import webbrowser
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='ProviderDeck başlat / durdur')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-tray', action='store_true', help='Bildirim alanı simgesini başlatma')
    parser.add_argument('--offline', action='store_true', help='Önbellekten başlat, ağ isteği yapma')
    parser.add_argument('--runtime', type=Path, default=root/'data/runtime.json')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args(argv)
    url = existing_url(args.runtime)
    if args.stop:
        if url:
            request = urllib.request.Request(url+'/api/shutdown', data=b'', headers={'X-ProviderDeck-Request':'1'}, method='POST')
            urllib.request.urlopen(request, timeout=3).close()
        print('ProviderDeck durduruldu.' if url else 'ProviderDeck zaten kapalı.')
        return 0
    candidates = []
    if os.environ.get('PROVIDERDECK_PYTHON'):
        candidates.append(Path(os.environ['PROVIDERDECK_PYTHON']))
    venv_py = root / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    candidates.append(venv_py)
    home = Path(os.environ.get('HERMES_HOME') or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'hermes')
    candidates.append(home / ('hermes-agent/venv/Scripts/python.exe' if os.name == 'nt' else 'hermes-agent/venv/bin/python'))
    interpreter = next((str(p) for p in candidates if p.is_file()), sys.executable)
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    if not url:
        args.runtime.parent.mkdir(parents=True, exist_ok=True)
        command = [interpreter, '-m', 'providerdeck.server', '--port', '8766', '--runtime', str(args.runtime.resolve())]
        if args.offline:
            command.append('--no-refresh')
        _trim_log(args.runtime.parent/'server.log')
        with (args.runtime.parent/'server.log').open('ab') as log:
            process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags, close_fds=True)
        for _ in range(100):
            url = existing_url(args.runtime)
            if url:
                break
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if not url:
            print('Yerel sunucu açılamadı. data/server.log dosyasını kontrol edin.', file=sys.stderr)
            return 1
    if sys.platform == 'win32' and not args.no_tray:
        try:
            _trim_log(args.runtime.parent/'tray.log')
            with (args.runtime.parent/'tray.log').open('ab') as log:
                subprocess.Popen([interpreter, '-m', 'providerdeck.tray', '--runtime', str(args.runtime.resolve())],
                                 cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags, close_fds=True)
        except OSError:
            print('Bildirim alanı simgesi başlatılamadı; panel çalışıyor.', file=sys.stderr)
    if not args.no_browser:
        webbrowser.open(url)
    print(url)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
