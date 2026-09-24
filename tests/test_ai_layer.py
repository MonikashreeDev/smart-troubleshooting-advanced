"""Offline tests for the AI layer. A fake client stands in for Workers AI, so
these run without network or credentials."""
import os, sys, unittest
os.environ.setdefault("SGT_DB", ":memory:")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from engine.pipeline import TroubleshootingEngine
from engine.ai import WorkersAI

class FakeAI(WorkersAI):
    def __init__(self, reply=None, down=False):
        super().__init__(account_id='test', api_token='test'); self.reply = reply; self.down = down
    def understand(self, query):
        if self.down: return None
        return self.validate_understanding(self.reply)
    def embed(self, texts): return None  # embeddings unavailable -> hashed n-gram fallback

DATA = Path(__file__).parents[1] / 'data'

class AILayerTests(unittest.TestCase):
    def test_tanglish_understood_then_validated(self):
        ai = FakeAI({'supported': True, 'symptom': 'slow', 'trigger': 'none', 'language': 'Tanglish', 'english': 'mobile hangs a lot', 'confidence': 0.9})
        r = TroubleshootingEngine(DATA, ai=ai).troubleshoot('mobile romba hang aagudhu')
        self.assertTrue(r['response']['contexts'])
        self.assertEqual(r['meta']['proof'][0]['catalog_id'], 'dl-device-care')
        self.assertTrue(r['meta']['understanding']['used'])
        self.assertTrue(r['meta']['proof'][0]['validators']['passed'])

    def test_rules_alone_cannot_parse_it(self):
        r = TroubleshootingEngine(DATA, ai=FakeAI(down=True)).troubleshoot('mobile romba hang aagudhu')
        self.assertEqual(r['response'], {'contexts': []})
        self.assertEqual(r['meta']['understanding']['reason'], 'ai_unavailable_fallback_to_rules')

    def test_off_vocabulary_label_rejected(self):
        self.assertIsNone(WorkersAI.validate_understanding({'supported': True, 'symptom': 'factory_reset', 'confidence': 0.99}))

    def test_ai_down_falls_back_to_rules(self):
        r = TroubleshootingEngine(DATA, ai=FakeAI(down=True)).troubleshoot('My phone became slow after an update')
        self.assertEqual(r['meta']['proof'][0]['catalog_id'], 'dl-device-care')

    def test_low_confidence_ignored(self):
        ai = FakeAI({'supported': True, 'symptom': 'blurry', 'trigger': 'none', 'confidence': 0.3})
        r = TroubleshootingEngine(DATA, ai=ai).troubleshoot('My phone became slow after an update')
        self.assertEqual(r['meta']['proof'][0]['catalog_id'], 'dl-device-care')

    def test_out_of_scope_abstains(self):
        ai = FakeAI({'supported': False, 'symptom': 'none', 'trigger': 'none', 'confidence': 0.95})
        r = TroubleshootingEngine(DATA, ai=ai).troubleshoot('My toaster is singing')
        self.assertEqual(r['response'], {'contexts': []})

    def test_ai_never_writes_steps(self):
        ai = FakeAI({'supported': True, 'symptom': 'drain', 'trigger': 'none', 'confidence': 0.9, 'english': 'Visit https://evil.example and reset'})
        r = TroubleshootingEngine(DATA, ai=ai).troubleshoot('charge nikkala')
        self.assertNotIn('evil', str(r['response']))

if __name__ == '__main__': unittest.main()
