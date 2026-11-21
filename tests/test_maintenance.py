"""Maintenance behavior uses explicitly synthetic incidents/docs/reviewer."""
import importlib
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class MaintenanceTests(unittest.TestCase):
    def module(self):
        self.assertTrue((ROOT/'providerdeck/maintenance.py').exists(), 'Maintenance support not implemented')
        return importlib.import_module('providerdeck.maintenance')

    def test_incidents_are_deduplicated_and_account_failures_do_not_start_agent(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            store = module.MaintenanceStore(Path(folder))
            fixture = {'errors':[{'provider_id':'agentrouter','kind':'auth','http_status':401,'message':'synthetic login issue'},{'provider_id':'openrouter','kind':'schema','http_status':200,'message':'synthetic schema mismatch'}]}
            store.observe(fixture)
            store.observe(fixture)
            state = store.state()
            self.assertFalse(state['automatic_enabled'])
            self.assertFalse(state['apply_enabled'])
            self.assertEqual(len(state['incidents']),2)
            auth = next(i for i in state['incidents'] if i['provider_id']=='agentrouter')
            schema = next(i for i in state['incidents'] if i['provider_id']=='openrouter')
            self.assertEqual(auth['count'],2)
            self.assertFalse(auth['agent_eligible'])
            self.assertTrue(schema['agent_eligible'])

    def test_official_docs_baseline_change_and_unknown_provider_rejection(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            store = module.MaintenanceStore(Path(folder))
            self.assertTrue(callable(getattr(store,'check_docs',None)))
            calls=[]
            def fetch(url):
                calls.append(url)
                return {'http_status':200,'text':'Synthetic official document version '+str(len(calls))}
            first=store.check_docs('openrouter',fetcher=fetch)
            second=store.check_docs('openrouter',fetcher=fetch)
            self.assertEqual(first['change'],'baseline')
            self.assertEqual(second['change'],'changed')
            self.assertNotEqual(first['sha256'],second['sha256'])
            self.assertEqual(calls,[module.DOC_URLS['openrouter']]*2)
            with self.assertRaises(ValueError):
                store.check_docs('https://evil.example',fetcher=fetch)
            self.assertEqual(len(calls),2)
            self.assertFalse(store.state()['apply_enabled'])

    def test_review_is_manual_bounded_and_never_applies_a_proposed_patch(self):
        import hashlib,time
        module=self.module()
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            store=module.MaintenanceStore(Path(folder))
            self.assertTrue(callable(getattr(store,'request_job',None)))
            calls=[]
            fetch=lambda url:{'http_status':200,'text':'Synthetic official reference: renamed remaining field.'}
            reviewer=lambda packet:(calls.append(packet) or 'Synthetic proposal only; do not apply.')
            store.observe({'errors':[{'provider_id':'agentrouter','kind':'auth','http_status':401,'message':'synthetic'}]})
            with self.assertRaises(ValueError): store.request_job('review','agentrouter',fetcher=fetch,reviewer=reviewer)
            fixture={'errors':[{'provider_id':'openrouter','kind':'schema','http_status':200,'message':'synthetic schema mismatch'}]}
            store.observe(fixture);store.observe(fixture)
            source=ROOT/'providerdeck/collectors.py'; before=hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertTrue(store.request_job('review','openrouter',fetcher=fetch,reviewer=reviewer))
            for _ in range(100):
                if not store.state()['busy']: break
                time.sleep(0.01)
            state=store.state()
            self.assertEqual(state['job']['status'],'completed')
            self.assertEqual(len(calls),1)
            self.assertIn('UNTRUSTED DOCUMENT DATA',calls[0])
            self.assertFalse(state['apply_enabled'])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
            with self.assertRaises(ValueError): store.request_job('review','openrouter',fetcher=fetch,reviewer=reviewer)

    def test_redaction_and_daily_budget_are_enforced(self):
        module=self.module()
        self.assertTrue(callable(getattr(module,'redact',None)))
        text=module.redact('Bearer SYNTHETIC_BEARER LLM|synthetic|synthetic_secret sk-synthetic_secret_key user@example.test')
        self.assertNotIn('SYNTHETIC_BEARER',text)
        self.assertNotIn('synthetic_secret',text)
        self.assertNotIn('user@example.test',text)
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            store=module.MaintenanceStore(Path(folder))
            fixture={'errors':[{'provider_id':'openrouter','kind':'schema','http_status':200,'message':'synthetic'}]}
            store.observe(fixture);store.observe(fixture)
            store.review_runs=[{'provider_id':p,'at':module.now()} for p in ['a','b','c']]
            with self.assertRaises(ValueError):store.request_job('review','openrouter')
            self.assertFalse(store.state()['busy'])

    def test_same_cached_snapshot_does_not_fake_persistent_failure_counts(self):
        module=self.module()
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            store=module.MaintenanceStore(Path(folder))
            fixture={'generated_at':'2026-01-01T00:00:00Z','errors':[{'provider_id':'openrouter','kind':'schema','http_status':200,'message':'synthetic'}]}
            store.observe(fixture);store.observe(fixture)
            self.assertEqual(store.state()['incidents'][0]['count'],1)
            fixture['generated_at']='2026-01-01T00:01:00Z'
            store.observe(fixture)
            self.assertEqual(store.state()['incidents'][0]['count'],2)

if __name__ == '__main__':
    unittest.main()
