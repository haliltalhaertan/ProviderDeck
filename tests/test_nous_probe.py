"""Synthetic transport fixtures; tests must not call Nous over the network."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

MODEL = 'stepfun/step-3.7-flash:free'


class NousProbeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        (self.home / '.env').write_text('NOUS_API_KEY=synthetic-nous-secret' + chr(10), encoding='utf-8')
        self.catalog = Mock(return_value=(200, {'data': [
            {'id': MODEL, 'pricing': {'prompt': '0', 'completion': '0', 'input_cache_read': '0'}},
            {'id': MODEL.removesuffix(':free'), 'pricing': {'prompt': '0.1', 'completion': '0.2'}},
        ]}))
        self.send = Mock(return_value=(200, {
            'choices': [{'message': {'content': 'synthetic-private-content'}}],
            'usage': {'cost': 0},
        }))

    def run_probe(self):
        from providerdeck.nous_probe import run_nous_free_test
        return run_nous_free_test(home=self.home, read_catalog=self.catalog, send=self.send)

    def test_fixed_free_model_is_probed_once_and_returns_only_safe_evidence(self):
        self.assertEqual(self.run_probe(), 'ok')
        self.catalog.assert_called_once_with('GET', 'https://inference-api.nousresearch.com/v1/models', {'Accept': 'application/json'})
        self.send.assert_called_once_with('synthetic-nous-secret')

    def test_no_completion_when_price_or_catalog_is_unverified(self):
        for price in ({'prompt': '0', 'completion': '0.0001'}, {'prompt': '0'}, {'prompt': '0', 'completion': '0', 'request': '0.01'}):
            with self.subTest(price=price):
                self.catalog.return_value = (200, {'data': [{'id': MODEL, 'pricing': price}]})
                self.assertEqual(self.run_probe(), 'not_free')
        self.catalog.return_value = (200, {'data': [{'id': MODEL.removesuffix(':free'), 'pricing': {'prompt': '0', 'completion': '0'}}]})
        self.assertEqual(self.run_probe(), 'not_free')
        self.catalog.return_value = (503, {})
        self.assertEqual(self.run_probe(), 'catalog_error')
        self.send.assert_not_called()

    def test_no_secret_or_completion_when_key_is_missing(self):
        (self.home / '.env').unlink()
        self.assertEqual(self.run_probe(), 'missing_key')
        self.catalog.assert_not_called()
        self.send.assert_not_called()

    def test_reports_unauthorized_quota_and_no_text_without_retry(self):
        for status, body, expected in [
            (401, {'error': 'synthetic-nous-secret'}, 'auth'),
            (403, {}, 'denied'), (429, {}, 'quota'), (402, {}, 'payment'), (302, {}, 'upstream_error'),
            (200, {'choices': [{'message': {'reasoning': 'private', 'content': ''}}]}, 'no_text'),
            (200, {'choices': [{'message': {'content': 'OK'}}], 'usage': {'cost': '0.001'}}, 'cost_warning'),
        ]:
            with self.subTest(status=status, expected=expected):
                self.send.reset_mock()
                self.send.return_value = (status, body)
                self.assertEqual(self.run_probe(), expected)
                self.send.assert_called_once()

    def test_completion_transport_pins_request_and_does_not_follow_redirects(self):
        import httpx
        import json
        from unittest.mock import patch
        from providerdeck.nous_probe import _completion
        captured = []
        def handler(request):
            captured.append(request)
            return httpx.Response(302, headers={'Location': 'https://elsewhere.invalid/x'})
        with patch('httpx.HTTPTransport', return_value=httpx.MockTransport(handler)) as transport:
            self.assertEqual(_completion('synthetic-nous-secret'), (302, {}))
        self.assertEqual(transport.call_args.kwargs['retries'], 0)
        self.assertEqual(len(captured), 1)
        request = captured[0]
        self.assertEqual((request.method, str(request.url)), ('POST', 'https://inference-api.nousresearch.com/v1/chat/completions'))
        self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-nous-secret')
        body = json.loads(request.content)
        self.assertEqual(body['model'], MODEL)
        self.assertFalse(body['stream'])
        self.assertLessEqual(body['max_tokens'], 64)
        self.assertEqual(body['messages'], [{'role': 'user', 'content': 'Reply with only OK.'}])
        self.assertNotIn('models', body)
        self.assertNotIn('provider', body)
