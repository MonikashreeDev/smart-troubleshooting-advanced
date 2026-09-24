"""O1 guided multi-turn diagnosis + O2 session memory. Offline: a fake AI client
stands in for Workers AI, so no network or credentials are needed."""
import os, sys, unittest
os.environ.setdefault('SGT_DB', ':memory:')
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from engine.pipeline import TroubleshootingEngine
from engine.store import Store
from engine.ai import WorkersAI, NoAI
from engine.dialogue import Dialogue, QUESTIONS

DATA = Path(__file__).parents[1] / 'data'

class FakeAI(WorkersAI):
    def __init__(self, reply=None, pick=None):
        super().__init__(account_id='t', api_token='t'); self.reply = reply; self.pick = pick; self.calls = 0
    def understand(self, q):
        self.calls += 1
        return self.validate_understanding(self.reply) if self.reply else None
    def choose_question(self, q, cands):
        self.calls += 1
        return self.pick if self.pick in cands else None
    def embed(self, texts): return None

def make(ai=None):
    e = TroubleshootingEngine(DATA, ai=ai or NoAI(), store=Store(':memory:'))
    return e, Dialogue(e)

def cat(r): return r['plan']['meta']['proof'][0]['catalog_id']

class MultiTurnDiagnosisTests(unittest.TestCase):
    def test_vague_complaint_gets_one_question(self):
        e, d = make(); r = d.start('My phone has a problem')
        self.assertEqual(r['type'], 'question'); self.assertIn(r['question']['id'], QUESTIONS)
        self.assertEqual(r['question']['options'], QUESTIONS[r['question']['id']]['options'])

    def test_answer_sharpens_to_validated_plan(self):
        e, d = make(); r = d.start('My phone has a problem')
        p = d.turn(r['session_id'], {'type': 'answer', 'option': 'drain'})
        self.assertEqual(p['type'], 'plan'); self.assertEqual(cat(p), 'dl-battery')
        self.assertTrue(p['plan']['meta']['proof'][0]['validators']['passed'])

    def test_display_ambiguity_asks_display_question(self):
        e, d = make(); r = d.start('The screen is acting up')
        self.assertEqual(r['question']['id'], 'q_display')
        p = d.turn(r['session_id'], {'type': 'message', 'text': 'it keeps flickering'})
        self.assertEqual(cat(p), 'dl-motion')

    def test_llm_picks_question_from_fixed_set(self):
        ai = FakeAI(reply={'supported': False, 'vague': True, 'symptom': 'none', 'confidence': 0.8}, pick='q_display')
        e, d = make(ai); r = d.start('mobile sariya work aagala')
        self.assertEqual(r['question']['id'], 'q_display'); self.assertEqual(r['question']['chosen_by'], 'llm')

    def test_llm_invented_question_rejected(self):
        ai = FakeAI(reply={'supported': False, 'vague': True, 'symptom': 'none', 'confidence': 0.8}, pick='q_factory_reset')
        e, d = make(ai); r = d.start('mobile sariya work aagala')
        self.assertIn(r['question']['id'], QUESTIONS); self.assertEqual(r['question']['chosen_by'], 'rules')

    def test_option_outside_question_rejected(self):
        e, d = make(); r = d.start('The screen is acting up')
        with self.assertRaises(ValueError): d.turn(r['session_id'], {'type': 'answer', 'option': 'drain'})

    def test_only_one_question_then_fail_closed(self):
        e, d = make(); r = d.start('My phone has a problem')
        p = d.turn(r['session_id'], {'type': 'message', 'text': 'I do not know, it is just weird'})
        self.assertEqual(p['type'], 'no_match'); self.assertEqual(p['plan']['response'], {'contexts': []})

    def test_clear_complaint_skips_question(self):
        e, d = make(); r = d.start('My phone became slow after an update')
        self.assertEqual(r['type'], 'plan'); self.assertEqual(cat(r), 'dl-device-care')

    def test_confident_out_of_scope_no_question(self):
        ai = FakeAI(reply={'supported': False, 'vague': False, 'symptom': 'none', 'confidence': 0.95})
        e, d = make(ai); r = d.start('My phone case is the wrong colour')
        self.assertNotEqual(r['type'], 'question')

    def test_non_phone_vague_text_not_asked(self):
        e, d = make(); self.assertEqual(d.start('My toaster has a problem')['type'], 'no_match')

    def test_on_device_mode_makes_no_ai_calls(self):
        ai = FakeAI(reply={'supported': True, 'symptom': 'slow', 'confidence': 0.9}, pick='q_area')
        e, d = make(ai); r = d.start('My phone has a problem', offline=True)
        self.assertEqual(ai.calls, 0); self.assertEqual(r['type'], 'question'); self.assertEqual(r['question']['chosen_by'], 'rules')


RISK = {'auto': 0, 'manual': 1, 'critical': 2}

class SessionMemoryTests(unittest.TestCase):
    def _plan(self, d, msg='The battery dies quickly'):
        r = d.start(msg); self.assertEqual(r['type'], 'plan'); return r

    def test_still_stuck_never_repeats_a_fix(self):
        e, d = make(); r = self._plan(d); seen = [r['plan']['meta']['proof'][0]['source_id']]
        while True:
            n = d.turn(r['session_id'], {'type': 'still_stuck'})
            if n['type'] != 'plan': break
            sid = n['plan']['meta']['proof'][0]['source_id']
            self.assertNotIn(sid, seen); seen.append(sid)
        self.assertEqual(len(seen), len(e.ladder('drain')))

    def test_escalation_is_safe_before_destructive(self):
        e, d = make(); r = self._plan(d); risks = [RISK[r['plan']['response']['contexts'][0]['actions'][0]['category']]]
        while True:
            n = d.turn(r['session_id'], {'type': 'still_stuck'})
            if n['type'] != 'plan': break
            risks.append(RISK[n['plan']['response']['contexts'][0]['actions'][0]['category']])
        self.assertEqual(risks, sorted(risks)); self.assertEqual(risks[-1], 2)

    def test_every_escalation_plan_passes_validators(self):
        e, d = make(); r = self._plan(d, 'Camera photos are blurry')
        while True:
            n = d.turn(r['session_id'], {'type': 'still_stuck'})
            if n['type'] != 'plan': break
            self.assertTrue(n['plan']['meta']['proof'][0]['validators']['passed'])
            for dl in [g['actions'][0]['stepGroups'][0]['actionableDeeplink'] for g in n['plan']['response']['contexts']]:
                if dl: self.assertIn(dl['deeplink'], {x['deeplink'] for x in e.catalog})

    def test_ladder_exhausted_escalates_to_service_report(self):
        e, d = make(); r = self._plan(d)
        for _ in range(len(e.ladder('drain')) - 1): d.turn(r['session_id'], {'type': 'still_stuck'})
        x = d.turn(r['session_id'], {'type': 'still_stuck'})
        self.assertEqual(x['type'], 'escalate'); self.assertEqual(len(x['report']['fixes_tried']), len(e.ladder('drain')))

    def test_fixed_closes_loop_and_records_verified_fix(self):
        e, d = make(); r = self._plan(d)
        x = d.turn(r['session_id'], {'type': 'fixed'})
        self.assertEqual(x['type'], 'closed'); self.assertEqual(e.store.feedback_counts()['demo-battery-1']['fixed'], 1)
        with self.assertRaises(ValueError): d.turn(r['session_id'], {'type': 'still_stuck'})

    def test_free_text_still_stuck_and_fixed(self):
        e, d = make(); r = self._plan(d)
        n = d.turn(r['session_id'], {'type': 'message', 'text': 'still not working'})
        self.assertEqual(n['step'], 2)
        self.assertEqual(d.turn(r['session_id'], {'type': 'message', 'text': 'fixed now, thanks'})['type'], 'closed')

    def test_session_persists_in_sqlite(self):
        e, d = make(); r = self._plan(d); d.turn(r['session_id'], {'type': 'still_stuck'})
        d2 = Dialogue(e)  # fresh dialogue object, same database
        h = d2.history(r['session_id'])
        self.assertEqual(len(h['state']['tried']), 2); self.assertTrue(any(ev['kind'] == 'plan' for ev in h['events']))

    def test_still_stuck_without_plan_rejected(self):
        e, d = make(); r = d.start('My phone has a problem')
        with self.assertRaises(ValueError): d.turn(r['session_id'], {'type': 'still_stuck'})

    def test_unknown_session_rejected(self):
        e, d = make()
        with self.assertRaises(ValueError): d.turn('nope', {'type': 'fixed'})

    def test_on_device_mode_persists_across_turns(self):
        ai = FakeAI(reply={'supported': True, 'symptom': 'drain', 'confidence': 0.9})
        e, d = make(ai); r = d.start('The battery dies quickly', offline=True)
        d.turn(r['session_id'], {'type': 'still_stuck'})
        self.assertEqual(ai.calls, 0); self.assertEqual(r['plan']['meta']['mode'], 'on_device')

    def test_bad_catalog_binding_fails_closed(self):
        e, d = make(); bad = dict(e.followups[0], catalog_id='dl-does-not-exist')
        r = e.compile_evidence('x', bad, e.intent('x'))
        self.assertEqual(r['response'], {'contexts': []}); self.assertEqual(r['meta']['fallback'], 'catalog_binding_missing')

    def test_unsupported_step_fails_closed(self):
        e, d = make(); bad = dict(e.followups[0], steps=['Download the booster from our partner store.'])
        r = e.compile_evidence('x', bad, e.intent('x'))
        self.assertEqual(r['response'], {'contexts': []}); self.assertIn('contexts[0].actions[0].unsupported_step', r['meta']['validator_errors'])

if __name__ == '__main__': unittest.main()
