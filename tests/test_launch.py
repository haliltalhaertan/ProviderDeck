"""Launcher boundary tests; no live network required."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

class LauncherTests(unittest.TestCase):
    def module(self):
        path = ROOT / 'launch.py'
        self.assertTrue(path.exists(), 'One-click launcher is not implemented')
        spec = importlib.util.spec_from_file_location('pd_launch', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_runtime_url_must_be_exact_loopback_http(self):
        module = self.module()
        for value in ['https://evil.example', 'http://127.0.0.1.evil.example:8766', 'http://user:pass@127.0.0.1:8766', 'http://127.0.0.1:8766/private', 'file:///secret']:
            self.assertFalse(module.safe_runtime_url(value), value)
        self.assertTrue(module.safe_runtime_url('http://127.0.0.1:8766'))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows tray only')
    def test_live_service_reuse_starts_tray_without_opening_browser(self):
        from unittest.mock import patch
        import tempfile
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / 'runtime.json'
            with patch.object(module, 'existing_url', return_value='http://127.0.0.1:8766'), patch('subprocess.Popen') as popen, patch('webbrowser.open') as browse:
                self.assertEqual(module.main(['--no-browser', '--runtime', str(runtime)]), 0)
                popen.assert_called_once()
                self.assertEqual(popen.call_args.args[0][1:], ['-m', 'providerdeck.tray', '--runtime', str(runtime)])
                browse.assert_not_called()
                popen.reset_mock()
                self.assertEqual(module.main(['--no-browser', '--no-tray', '--runtime', str(runtime)]), 0)
                popen.assert_not_called()

    def test_one_click_boots_real_offline_service_then_reuses_it(self):
        import os
        import subprocess
        import tempfile
        import urllib.request
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            runtime = Path(folder) / 'runtime.json'
            command = [sys.executable, str(ROOT/'launch.py'),'--no-browser','--no-tray','--offline','--runtime',str(runtime)]
            environment = {**os.environ,'PROVIDERDECK_PYTHON':sys.executable}
            first = subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,timeout=15)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertTrue(runtime.exists(),'Launcher did not boot the service')
            record = json.loads(runtime.read_text(encoding='utf-8'))
            try:
                second = subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,timeout=10)
                self.assertEqual(second.returncode,0,second.stderr)
                self.assertEqual(json.loads(runtime.read_text(encoding='utf-8'))['pid'],record['pid'])
                self.assertIn(record['url'],second.stdout)
            finally:
                request = urllib.request.Request(record['url']+'/api/shutdown',data=b'',headers={'X-ProviderDeck-Request':'1'},method='POST')
                urllib.request.urlopen(request,timeout=3).close()
                # The shutdown response precedes process exit; wait for Windows
                # to release the owned service's log handle before fixture cleanup.
                import time
                for _ in range(100):
                    try:
                        (Path(folder)/'server.log').unlink(missing_ok=True)
                        break
                    except PermissionError:
                        time.sleep(0.05)

if __name__ == '__main__':
    unittest.main()
