"""O4 feedback-driven ranking. Offline."""
import os, sys, unittest
os.environ.setdefault('SGT_DB', ':memory:')
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from engine.pipeline import TroubleshootingEngine
from engine.store import Store
from engine.ai import NoAI
from engine.dialogue import Dialogue

DATA = Path(__file__).parents[1] / 'data'
RISK = {'auto': 0, 'manual': 1, 'critical': 2}

def make():
    e = TroubleshootingEngine(DATA, ai=NoAI(), store=Store(':memory:')); return e, Dialogue(e)
def ids(e, s): return [d['id'] for d in e.ladder(s)]

class FeedbackRankingTests(unittest.TestCase):
    def test_no_feedback_keeps_authored_order(self):
        e, d = make(); self.assertEqual(ids(e, 'slow'), ['demo-perf-1', 'fu-restart', 'fu-safe-mode', 'fu-reset-settings'])

    def test_verified_fixes_boost_within_risk_class(self):
        e, d = make()
        for _ in range(3): e.record_feedback('fu-safe-mode', 'fixed')
        self.assertEqual(ids(e, 'slow'), ['demo-perf-1', 'fu-safe-mode', 'fu-restart', 'fu-reset-settings'])

    def test_not_fixed_demotes_within_risk_class(self):
        e, d = make()
        for _ in range(3): e.record_feedback('fu-restart', 'not_fixed')
        self.assertEqual(ids(e, 'slow').index('fu-safe-mode') < ids(e, 'slow').index('fu-restart'), True)

    def test_feedback_never_lifts_destructive_fix(self):
        e, d = make()
        for _ in range(50): e.record_feedback('fu-reset-settings', 'fixed')
        L = e.ladder('drain'); risks = [RISK[x['category']] for x in L]
        self.assertEqual(risks, sorted(risks)); self.assertEqual(L[-1]['id'], 'fu-reset-settings')

    def test_primary_plan_stays_first(self):
        e, d = make()
        for _ in range(10): e.record_feedback('fu-power-saving', 'fixed')
        self.assertEqual(ids(e, 'drain')[0], 'demo-battery-1')

    def test_unknown_source_and_bad_outcome_rejected(self):
        e, d = make()
        with self.assertRaises(ValueError): e.record_feedback('made-up-fix', 'fixed')
        with self.assertRaises(ValueError): e.record_feedback('fu-restart', 'maybe')

    def test_plan_carries_verified_fix_count(self):
        e, d = make(); e.record_feedback('demo-perf-1', 'fixed'); e.record_feedback('demo-perf-1', 'fixed')
        r = e.troubleshoot('My phone became slow after an update')
        self.assertEqual(r['meta']['ranking']['verified_fixes'], 2)

    def test_session_escalation_follows_boosted_order(self):
        e, d = make()
        for _ in range(3): e.record_feedback('fu-safe-mode', 'fixed')
        r = d.start('My phone became slow after an update')
        n = d.turn(r['session_id'], {'type': 'still_stuck'})
        self.assertEqual(n['plan']['meta']['proof'][0]['source_id'], 'fu-safe-mode')

    def test_metrics_dashboard_shows_feedback(self):
        e, d = make(); r = d.start('The battery dies quickly'); d.turn(r['session_id'], {'type': 'fixed'})
        m = e.metrics()['feedback']
        self.assertEqual(m['verified_fixes'], 1); self.assertEqual(m['by_fix'][0]['source_id'], 'demo-battery-1')

    def test_feedback_does_not_change_plan_content(self):
        e, d = make(); a = e.troubleshoot('The screen keeps flickering')['response']
        for _ in range(5): e.record_feedback('demo-display-2', 'not_fixed')
        e.exact.clear(); e.semantic.clear()
        self.assertEqual(e.troubleshoot('The screen keeps flickering')['response'], a)

if __name__ == '__main__': unittest.main()
