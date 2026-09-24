"""O3 predictive maintenance on synthetic telemetry. Offline, no AI needed."""
import os, sys, unittest
os.environ.setdefault('SGT_DB', ':memory:')
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from engine.pipeline import TroubleshootingEngine
from engine.store import Store
from engine.ai import NoAI
from engine import predictive
from engine.predictive import Predictor, analyse, slope, warning_for

DATA = Path(__file__).parents[1] / 'data'

def make():
    e = TroubleshootingEngine(DATA, ai=NoAI(), store=Store(':memory:')); return e, Predictor(e)

class PredictiveTests(unittest.TestCase):
    def test_healthy_device_no_warnings(self):
        e, p = make(); self.assertEqual(p.predict('demo-galaxy-healthy')['warnings'], [])

    def test_battery_degradation_warns_with_validated_plan(self):
        e, p = make(); w = p.predict('demo-galaxy-battery')['warnings']
        self.assertEqual([x['metric'] for x in w], ['battery_drain_pct_per_hr'])
        self.assertEqual(w[0]['plan_status'], 'validated')
        self.assertEqual(w[0]['plan']['meta']['proof'][0]['catalog_id'], 'dl-battery')
        self.assertTrue(w[0]['plan']['meta']['proof'][0]['validators']['passed'])

    def test_storage_forecast_and_crash_spike(self):
        e, p = make(); r = p.predict('demo-galaxy-storage')
        self.assertEqual({x['metric'] for x in r['warnings']}, {'storage_used_pct', 'app_crashes_per_day'})
        self.assertLess(r['metrics']['storage_used_pct']['days_to_full'], 30)
        cats = {x['metric']: x['plan']['meta']['proof'][0]['catalog_id'] for x in r['warnings']}
        self.assertEqual(cats, {'storage_used_pct': 'dl-storage', 'app_crashes_per_day': 'dl-safe-mode'})

    def test_demo_data_is_labelled_synthetic(self):
        e, p = make(); r = p.predict('demo-galaxy-battery')
        self.assertTrue(r['synthetic_data']); self.assertIn('Synthetic', r['data_note'])
        self.assertTrue(all(d['synthetic'] for d in p.devices()))

    def test_slope_math(self):
        self.assertAlmostEqual(slope([(1, 2), (2, 4), (3, 6)]), 2.0)
        self.assertEqual(slope([(1, 5), (2, 5), (3, 5)]), 0.0)

    def test_z_score_detects_step_change(self):
        pts = [(d, 4.0 + (0.1 if d % 2 else -0.1)) for d in range(1, 24)] + [(d, 7.0) for d in range(24, 31)]
        a = analyse(pts, 'battery_drain_pct_per_hr')
        self.assertGreater(a['z_score'], 3); self.assertIsNotNone(warning_for(a)); self.assertEqual(a['anomaly_days'], list(range(24, 31)))

    def test_noise_alone_does_not_warn(self):
        pts = [(d, 4.0 + (0.2 if d % 3 == 0 else -0.1)) for d in range(1, 31)]
        self.assertIsNone(warning_for(analyse(pts, 'battery_drain_pct_per_hr')))

    def test_insufficient_history_no_warning(self):
        a = analyse([(d, 9.0) for d in range(1, 10)], 'battery_drain_pct_per_hr')
        self.assertEqual(a['status'], 'insufficient_data'); self.assertIsNone(warning_for(a))

    def test_ingest_validation(self):
        e, p = make()
        with self.assertRaises(ValueError): p.ingest('dev1', 1, 'cpu_temp', 40)
        with self.assertRaises(ValueError): p.ingest('dev1', 1, 'storage_used_pct', -3)
        p.ingest('dev1', 1, 'storage_used_pct', 50); self.assertIn('dev1', [d['id'] for d in p.devices()])

    def test_unknown_device_rejected(self):
        e, p = make()
        with self.assertRaises(ValueError): p.predict('nope')

    def test_plan_withheld_when_evidence_fails_validation(self):
        e, p = make()
        old = predictive.METRICS['battery_drain_pct_per_hr']['evidence']
        bad = dict(next(d for d in e.siis if d['id'] == old), id='bad-ev', steps=['Install the booster app from the web.'])
        e.followups.append(bad); predictive.METRICS['battery_drain_pct_per_hr']['evidence'] = 'bad-ev'
        try:
            w = p.predict('demo-galaxy-battery')['warnings'][0]
            self.assertIsNone(w['plan']); self.assertEqual(w['plan_status'], 'withheld_failed_validation')
        finally:
            predictive.METRICS['battery_drain_pct_per_hr']['evidence'] = old; e.followups.remove(bad)

if __name__ == '__main__': unittest.main()
