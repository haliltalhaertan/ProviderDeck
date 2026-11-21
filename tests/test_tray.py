"""Tray data policy tests; Windows only for the native rendering itself."""
import http.server
import json
from pathlib import Path
import tempfile
import threading
import unittest


class TrayTests(unittest.TestCase):
    def test_tooltip_keeps_unknown_separate_and_shows_weekly_and_five_hour_quotas(self):
        from providerdeck.tray import tooltip
        state = {'providers': [
            {'id': 'anthropic', 'kind': 'subscription', 'name': '<untrusted>', 'windows': [{'label': 'Oturum (5 saat)', 'remaining_percent': 44}, {'label': 'Haftalık', 'remaining_percent': 0}]},
            {'id': 'openai-codex', 'kind': 'subscription', 'windows': [{'label': 'Haftalık', 'remaining_percent': 22.5}], 'stale': True},
            {'id': 'test', 'balance_usd': 1.424825}, {'id': 'zero', 'balance_usd': 0},
            {'id': 'other', 'balance_usd': -9}, {'id': 'voidai', 'credits': {'value': 100000}},
        ]}
        summary = tooltip(state)
        self.assertIn('Pozitif USD: $1,42', summary)
        self.assertIn('Claude haftalık %0 · 5s %44', summary)
        self.assertIn('Codex haftalık %22,5 (eski)', summary)
        for forbidden in ('<untrusted>', '100000'):
            self.assertNotIn(forbidden, summary)
        self.assertIn('USD bakiye bilinmiyor', tooltip({'providers': []}))
        self.assertIn('yerel panele ulaşılamadı', tooltip(None))
        self.assertLessEqual(len(summary), 127)

    def test_tooltip_lists_every_live_subscription_including_five_hour_and_unknown(self):
        from datetime import datetime
        from providerdeck.tray import tooltip, subscription_lines
        snapshot_time = '2026-11-21T12:05:49+00:00'
        state = {'generated_at': snapshot_time, 'providers': [
            {'id': 'anthropic', 'kind': 'subscription', 'windows': [{'label': 'Oturum (5 saat)', 'remaining_percent': 100}, {'label': 'Haftalık', 'remaining_percent': 0}]},
            {'id': 'openai-codex', 'kind': 'subscription', 'windows': [{'label': 'Haftalık', 'remaining_percent': 19}]},
            {'id': 'opencode-go', 'kind': 'subscription', 'windows': [{'label': 'Kayan pencere', 'remaining_percent': 100}, {'label': 'Haftalık', 'remaining_percent': 0}]},
            {'id': 'copilot', 'kind': 'subscription', 'windows': [{'label': 'Standart sohbet', 'remaining_percent': None}, {'label': 'Premium etkileşim', 'remaining_percent': 100}]},
            {'id': 'antigravity', 'kind': 'subscription', 'windows': [{'label': 'Gemini Haftalık', 'remaining_percent': 97.9, 'scope': 'model'}, {'label': 'Gemini (5 saat)', 'remaining_percent': 93.4, 'scope': 'model'}]},
            {'id': 'muse-code', 'kind': 'cli', 'windows': [{'label': 'Oturum (5 saat)', 'remaining_percent': 100}, {'label': 'Haftalık', 'remaining_percent': 64}]},
            {'id': 'meta-ai', 'kind': 'api', 'name': 'Meta Model API', 'balance_usd': 37.529},
            {'id': 'opencode-zen', 'kind': 'api', 'name': 'OpenCode Zen'},
        ]}
        visible = [line for line in subscription_lines(state) if line.provider_id != 'copilot']
        summary = tooltip(state, visible)
        header, *rows = summary.splitlines()
        self.assertIn('$37,53', header)
        self.assertIn(datetime.fromisoformat(snapshot_time).astimezone().strftime('%H:%M'), header)
        self.assertEqual(rows, ['Claude: %0 · 5s %100', 'Codex: %19', 'Go: %0 · 5s %100', 'Google: %97,9 · 5s %93,4', 'Muse: %64 · 5s %100'])
        self.assertLessEqual(len(summary.encode('utf-16-le')) // 2, 127)
        all_summary = tooltip(state)
        self.assertLessEqual(len(all_summary.encode('utf-16-le')) // 2, 127)
        self.assertIn('Google: %97,9', all_summary)
        self.assertIn('Muse: %64', all_summary)
        full = [line.detail for line in subscription_lines(state)]
        self.assertEqual(len(full), 6)
        self.assertIn('Google Antigravity — gemini haftalık (model) %97,9 · 5s %93,4', full)
        self.assertIn('Muse Code — haftalık %64 · 5s %100', full)
        self.assertIn('OpenCode Go — haftalık %0 · 5s %100', full)
        self.assertIn('GitHub Copilot — premium etkileşim %100', full)

    def test_right_click_summary_keeps_additional_subscriptions_beyond_tooltip_limit(self):
        from providerdeck.tray import tooltip, subscription_lines
        state = {'providers': [{'id': f'provider{i}', 'kind': 'subscription', 'name': f'Abonelik {i} ({"uzun ad " * 5})'} for i in range(10)]}
        self.assertEqual(len(subscription_lines(state)), 10)
        summary = tooltip(state)
        self.assertIn('sağ tık', summary)
        self.assertLessEqual(len(summary), 127)

    def test_visibility_remembers_hidden_subscriptions_and_includes_new_ones(self):
        from providerdeck.tray import TrayPreferences, subscription_lines, tooltip
        state = {'providers': [
            {'id': 'antigravity', 'kind': 'subscription'},
            {'id': 'muse-code', 'kind': 'cli'},
            {'id': 'wallet', 'kind': 'api', 'balance_usd': 12.34},
        ]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'tray-preferences.json'
            prefs = TrayPreferences(path)
            self.assertEqual(len(prefs.visible(subscription_lines(state))), 2)
            prefs.toggle('antigravity')
            state['providers'].append({'id': 'another-subscription', 'kind': 'subscription', 'name': 'Yeni Abonelik'})
            prefs = TrayPreferences(path)
            visible = prefs.visible(subscription_lines(state))
            self.assertEqual([line.provider_id for line in visible], ['muse-code', 'another-subscription'])
            summary = tooltip(state, visible)
            self.assertNotIn('Google', summary)
            self.assertIn('Muse Code', summary)
            self.assertIn('$12,34', summary)
            prefs.toggle('antigravity')
            self.assertEqual(len(TrayPreferences(path).visible(subscription_lines(state))), 3)

    def test_tray_fetches_only_the_local_cached_state_and_refuses_redirects(self):
        from providerdeck.tray import read_state
        calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                if len(calls) == 1:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"providers": []}')
                else:
                    self.send_response(302)
                    self.send_header('Location', '/must-not-visit')
                    self.end_headers()
            def log_message(self, *args):
                pass
        with http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as temp:
                    record = Path(temp) / 'runtime.json'
                    good = 'http://127.0.0.1:' + str(server.server_port)
                    record.write_text(json.dumps({'app': 'providerdeck', 'url': good}))
                    self.assertEqual(read_state(record), (good, {'providers': []}))
                    self.assertEqual(read_state(record), (None, None))
                    record.write_text(json.dumps({'app': 'providerdeck', 'url': 'https://example.invalid'}))
                    self.assertEqual(read_state(record), (None, None))
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(calls, ['/api/state', '/api/state'])
