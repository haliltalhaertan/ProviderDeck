"""Synthetic fixtures only; never used for displayed production data."""
import importlib
import unittest


class PricingTests(unittest.TestCase):
    def test_mixed_audio_output_is_not_a_free_text_token_quote(self):
        from providerdeck.pricing import normalize_model,estimate_cost
        model=normalize_model('openrouter',{'id':'synthetic-music','architecture':{'output_modalities':['text','audio']},'pricing':{'prompt':'0','completion':'0'}},{'access':{'state':'ready'}})
        self.assertIsNone(estimate_cost(model,1000,1000)['cost_usd'])
        self.assertEqual(model['billing'],'unknown')

    def test_nvidia_documented_free_text_route_excludes_non_chat_products(self):
        from providerdeck.pricing import normalize_model,estimate_cost
        record={'kind':'free','access':{'state':'ready'}}
        text=normalize_model('nvidia',{'id':'nvidia/synthetic-chat'},record)
        self.assertEqual(text['billing'],'free')
        self.assertEqual(estimate_cost(text,1000,100)['cost_usd'],0)
        embedding=normalize_model('nvidia',{'id':'nvidia/synthetic-embed'},record)
        self.assertIsNone(estimate_cost(embedding,1000,100)['cost_usd'])
        newprice=normalize_model('nvidia',{'id':'nvidia/synthetic-new-price','pricing':{'unknown_future_rate':1}},record)
        self.assertIsNone(estimate_cost(newprice,1000,100)['cost_usd'])

    def test_context_schema_and_nvidia_free_trial_are_explicit(self):
        from providerdeck.pricing import normalize_model,estimate_cost
        context=normalize_model('ai-gateway',{'id':'synthetic','context_window':123456})
        self.assertEqual(context['context_length'],123456)

    def test_non_text_products_are_not_zero_cost_text_llms(self):
        from providerdeck.pricing import normalize_model, estimate_cost
        for raw in [{'id':'synthetic-audio','architecture':{'output_modalities':['audio']},'pricing':{'prompt':'0','completion':'0'}}, {'id':'synthetic-embedding','type':'embedding','pricing':{'prompt':'0','completion':'0'}}]:
            model = normalize_model('openrouter',raw,{'access':{'state':'ready'}})
            self.assertIsNone(estimate_cost(model,1000,500)['cost_usd'])
            self.assertEqual(model['pricing']['unit'],'unknown')
            self.assertIsNotNone(model['warning'])

    def test_fallback_prices_currency_gates_and_exact_free_markers(self):
        from providerdeck.pricing import normalize_model, estimate_cost
        fallback = normalize_model('uberllm', {'id': 'x', 'pricing': {'prompt': '.0000002', 'completion': '.0000008'}})
        self.assertEqual(fallback['pricing']['input_per_million_usd'], .2)
        self.assertEqual(fallback['pricing']['output_per_million_usd'], .8)
        credit_free = normalize_model('voidai', {'id': 'test:free', 'free': True, 'input_multiplier': 2})
        self.assertEqual(credit_free['pricing']['unit'], 'credits')
        self.assertIsNone(estimate_cost(credit_free, 1, 1)['cost_usd'])
        unknown_currency = normalize_model('refuseless', {'id': 'x', 'pricing': {'currency': 'EUR', 'input_per_m': 1, 'output_per_m': 2}})
        self.assertIsNone(unknown_currency['pricing']['input_per_million_usd'])
        free = normalize_model('opencode-zen', {'id': 'synthetic-model-free'})
        self.assertEqual(free['canonical_key'], 'synthetic-model')
        self.assertEqual(free['billing'], 'free')
        arbitrary = normalize_model('unknown', {'id': 'synthetic-model-free'})
        self.assertEqual(arbitrary['canonical_key'], 'synthetic-model-free')
        self.assertNotEqual(arbitrary['billing'], 'free')

    def test_cost_cache_fallback_and_included_route_limits(self):
        from providerdeck.pricing import normalize_model, estimate_cost
        m = normalize_model('refuseless', {'id': 'test', 'pricing': {'input_per_m': 2, 'output_per_m': 4, 'cached_input_per_m': .5}})
        self.assertAlmostEqual(estimate_cost(m, 1000000, 250000, 50)['cost_usd'], 2.25)
        m['pricing']['cache_read_per_million_usd'] = None
        self.assertEqual(estimate_cost(m, 1000000, 250000, 50)['cost_usd'], 3)
        self.assertIsNotNone(estimate_cost(m, 1000000, 250000, 50)['warning'])
        for pid in ['voidai', 'unknown']:
            self.assertIsNone(estimate_cost(normalize_model(pid, {'id': 'x'}), 1, 1)['cost_usd'])
        included = normalize_model('anthropic', {'id': 'claude-test'}, {'kind': 'subscription', 'access': {'state': 'ready'}})
        self.assertEqual(included['billing'], 'subscription')
        self.assertEqual(estimate_cost(included, 100, 50)['cost_usd'], 0)
        self.assertIsNotNone(estimate_cost(included, 100, 50)['warning'])
        blocked = normalize_model('anthropic', {'id': 'claude-test'}, {'kind': 'subscription', 'access': {'state': 'blocked'}})
        self.assertIsNone(estimate_cost(blocked, 1, 1)['cost_usd'])
        self.assertIsNone(estimate_cost(m, -1, 1)['cost_usd'])
        self.assertIsNone(estimate_cost(m, 1, 1, 101)['cost_usd'])
        # A zero request fee is not zero token billing.
        request_only = normalize_model('orcarouter', {'id': 'x', 'pricing': {'request': '0'}})
        self.assertIsNone(estimate_cost(request_only, 100, 100)['cost_usd'])

    def test_provider_specific_price_units_and_capabilities(self):
        from providerdeck.pricing import normalize_model
        fixtures = [
            ('ai-gateway', {'pricing': {'input': '0.0000003', 'output': '0.0000012', 'varies_by_provider': True}}, .3, 1.2),
            ('uberllm', {'pricing': {'input_usd': .3, 'output_usd': 1.2, 'unit': '1M tokens', 'prompt': '99'}}, .3, 1.2),
            ('explabs', {'context_window_tokens': 8192, 'supports_tools': True, 'pricing': {'input_nano_usd_per_million_tokens': 300000000, 'output_nano_usd_per_million_tokens': 1200000000}}, .3, 1.2),
            ('orcarouter', {'pricing': {'prompt': '0.0000003', 'completion': '0.0000012'}}, .3, 1.2),
            ('refuseless', {'pricing': {'currency': 'USD', 'input_per_m': .3, 'output_per_m': 1.2}}, .3, 1.2),
            ('venice', {'model_spec': {'pricing': {'input': {'usd': .3}, 'output': {'usd': 1.2}}, 'capabilities': {'supportsFunctionCalling': True, 'supportsVision': True}}}, .3, 1.2),
        ]
        for pid, raw, inp, out in fixtures:
            with self.subTest(provider=pid):
                m = normalize_model(pid, {'id': 'test-model', **raw})
                self.assertEqual(m['pricing']['input_per_million_usd'], inp)
                self.assertEqual(m['pricing']['output_per_million_usd'], out)
        self.assertTrue(normalize_model('ai-gateway', fixtures[0][1])['pricing']['variable'])
        self.assertTrue(normalize_model('venice', fixtures[-1][1])['vision'])
        self.assertTrue(normalize_model('explabs', fixtures[2][1])['tools'])
        self.assertEqual(normalize_model('voidai', {'id': 'x', 'input_multiplier': 12})['pricing']['unit'], 'credits')
        self.assertIsNone(normalize_model('unknown', {'id': 'x', 'pricing': {'input': 2}})['pricing']['input_per_million_usd'])
        self.assertIsNone(normalize_model('orcarouter', {'id': 'x', 'pricing': {'request': '0'}})['pricing']['input_per_million_usd'])

    def test_openrouter_per_token_and_conservative_canonical(self):
        pricing = importlib.import_module('providerdeck.pricing')
        m = pricing.normalize_model('openrouter', {'id': 'deepseek/deepseek-v4.1-flash:free', 'pricing': {'prompt': '0.0000003', 'completion': '0.0000012', 'input_cache_read': '0.000000007'}, 'supported_parameters': ['tools'], 'context_length': 1048576})
        self.assertEqual(m['pricing']['input_per_million_usd'], 0.3)
        self.assertEqual(m['pricing']['output_per_million_usd'], 1.2)
        self.assertEqual(m['pricing']['cache_read_per_million_usd'], 0.007)
        self.assertEqual(m['canonical_key'], 'deepseek-v4.1-flash')
        self.assertEqual(m['id'], 'deepseek/deepseek-v4.1-flash:free')
        self.assertTrue(m['tools'])
        self.assertEqual(m['verification']['state'], 'untested')
        for mid in ['deepseek-v4.1-flash-abliterated', 'deepseek-v4-pro-0813', 'unknown/deepseek-v4.1-flash']:
            self.assertEqual(pricing.normalize_model('openrouter', {'id': mid})['canonical_key'], mid)
