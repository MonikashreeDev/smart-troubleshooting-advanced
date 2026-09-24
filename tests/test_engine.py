import json,os,sys,unittest
os.environ.setdefault("SGT_DB", ":memory:")
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from engine.pipeline import TroubleshootingEngine
from engine.validators import validate
class EngineTests(unittest.TestCase):
 def setUp(self): self.e=TroubleshootingEngine(Path(__file__).parents[1]/'data')
 def test_health_and_missing_assets(self):
  self.assertEqual(self.e.health()['status'],'ok'); self.assertEqual(self.e.asset_status()['mode'],'synthetic_demo')
 def test_supported_contract(self):
  r=self.e.troubleshoot('My phone became slow after an update'); self.assertEqual(set(r['response']),{'contexts'}); self.assertTrue(r['response']['contexts']); self.assertIsNone(r['meta']['fallback'])
 def test_exact_cache(self):
  self.e.troubleshoot('The battery dies quickly'); r=self.e.troubleshoot('The battery dies quickly'); self.assertEqual(r['meta']['cache_type'],'exact')
 def test_semantic_cache_guard(self):
  self.e.troubleshoot('My phone is slow after update'); r=self.e.troubleshoot('Phone became slow after software update'); self.assertIn(r['meta']['cache_type'],('semantic','miss'))
  wrong=self.e.troubleshoot('Camera is blurry'); self.assertNotEqual(wrong['meta']['cache_type'],'semantic')
 def test_abstention(self):
  r=self.e.troubleshoot('My toaster is singing'); self.assertEqual(r['response'],{'contexts':[]}); self.assertEqual(r['meta']['fallback'],'no_match')
 def test_deeplink_exact_catalog(self):
  r=self.e.troubleshoot('Swipe gestures go wrong after app install'); uri=r['response']['contexts'][0]['actions'][0]['stepGroups'][0]['actionableDeeplink']['deeplink']; self.assertIn(uri,{x['deeplink'] for x in self.e.links})
 def test_determinism_core(self):
  a=self.e.troubleshoot('The screen keeps flickering')['response']; b=self.e.troubleshoot('The screen keeps flickering')['response']; self.assertEqual(a,b)
 def test_no_web_urls(self):
  r=json.dumps(self.e.troubleshoot('Camera photos are blurry')['response']); self.assertNotIn('http://',r); self.assertNotIn('https://',r); self.assertNotIn('www.',r)
if __name__=='__main__': unittest.main()
