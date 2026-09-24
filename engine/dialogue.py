"""Guided multi-turn diagnosis (O1) and session memory / escalation (O2).

Rules that keep this safe:
  * At most ONE clarifying question per complaint. Question text and answer
    options are fixed in code; the LLM may only pick a question id.
  * Every plan still comes from TroubleshootingEngine (troubleshoot() or
    compile_evidence()), so every plan passes the same validators.
  * "Still stuck" walks a ladder ordered safe -> destructive and never repeats a
    fix already tried in the session. When the ladder is exhausted the engine
    escalates to a service centre instead of inventing another step.
"""
from __future__ import annotations
import re
from .ai import SYMPTOMS, TRIGGERS
from .retrieval import tokens

QUESTIONS = {
    'q_area': {'text': 'Which part of the phone is giving trouble?',
               'options': [{'id': 'slow', 'label': 'Phone is slow or hanging'},
                           {'id': 'drain', 'label': 'Battery drains fast'},
                           {'id': 'flicker', 'label': 'Screen flickers or blinks'},
                           {'id': 'swipe', 'label': 'Swipe gestures go wrong'},
                           {'id': 'blurry', 'label': 'Camera photos are blurry'}]},
    'q_display': {'text': 'What is the screen doing?',
                  'options': [{'id': 'flicker', 'label': 'It flickers or blinks'},
                              {'id': 'swipe', 'label': 'Swipe gestures go the wrong way'}]},
}
PHONE_WORDS = {'phone', 'mobile', 'galaxy', 'device', 'samsung', 'problem', 'issue', 'issues', 'working', 'work',
               'weird', 'broken', 'help', 'wrong', 'strange', 'sariya', 'theek', 'properly', 'screen', 'display'}
DISPLAY_WORDS = {'screen', 'display'}
DEVICE_WORDS = {'phone', 'mobile', 'galaxy', 'device', 'samsung', 'screen', 'display', 'handset', 'cell', 'fone'}
CLOSE_WORDS = re.compile(r'\b(fixed|solved|works now|working now|resolved|sari aagiduchu|ho gaya)\b', re.I)
STUCK_WORDS = re.compile(r'\b(still|not fixed|didn.?t work|did not work|same problem|stuck|no change)\b', re.I)

class Dialogue:
    def __init__(self, engine):
        self.e = engine; self.store = engine.store
        self._primary = {}

    # ---- helpers -------------------------------------------------------------
    def symptom_of_source(self, source_id):
        if not self._primary:
            for s in SYMPTOMS:
                d = self.e.primary_for(s)
                if d: self._primary.setdefault(d['id'], s)
        return self._primary.get(source_id)

    def question_for(self, query, intent, info, offline):
        """Return (question_id, chooser) when the complaint is too vague, else (None, None)."""
        if info.get('used') and info.get('reason') == 'ai_understood': return None, None
        t = set(tokens(query))
        if intent['domain'] == 'Display' and intent['symptom'] not in ('flicker', 'swipe'):
            return 'q_display', 'rules'
        vague_ai = bool(info.get('vague'))
        confident_oos = info.get('reason') == 'ai_out_of_scope' and not vague_ai
        low_conf = info.get('reason') == 'ai_low_confidence_fallback_to_rules'
        if intent['domain'] == 'Unknown' and not confident_oos and (vague_ai or low_conf or ((t & DEVICE_WORDS) and (t & PHONE_WORDS))):
            cands = {k: v['text'] for k, v in QUESTIONS.items()}
            if not offline and self.e.ai.enabled:
                qid = self.e.ai.choose_question(query, cands)
                if qid: return qid, 'llm'
            return ('q_display' if t & DISPLAY_WORDS else 'q_area'), 'rules'
        return None, None

    def _public_question(self, qid, chooser):
        q = QUESTIONS[qid]
        return {'id': qid, 'text': q['text'], 'options': q['options'], 'chosen_by': chooser}

    def _plan_reply(self, sid, state, plan, symptom, step):
        proof = plan['meta']['proof'][0]
        state.update(stage='plan', symptom=symptom, current=proof['source_id'], current_catalog=proof['catalog_id'])
        if proof['source_id'] not in state['tried']: state['tried'].append(proof['source_id'])
        total = len(self.e.ladder(symptom)) if symptom else 1
        self.store.save_session(sid, state)
        self.store.log_event(sid, 'plan', {'source_id': proof['source_id'], 'catalog_id': proof['catalog_id'], 'step': step})
        return {'session_id': sid, 'type': 'plan', 'step': step, 'ladder_size': total, 'symptom': symptom,
                'tried': list(state['tried']), 'plan': plan, 'ask_feedback': 'Did this fix it?'}

    def _no_match(self, sid, state, plan, why):
        state['stage'] = 'closed_no_match'; self.store.save_session(sid, state)
        self.store.log_event(sid, 'no_match', {'reason': why})
        return {'session_id': sid, 'type': 'no_match', 'reason': why, 'plan': plan}

    def _plan_from_query(self, sid, state, query, step=1):
        plan = self.e.troubleshoot(query, offline=state['offline'])
        if not plan['response']['contexts']:
            return self._no_match(sid, state, plan, plan['meta']['fallback'])
        sym = self.symptom_of_source(plan['meta']['proof'][0]['source_id'])
        return self._plan_reply(sid, state, plan, sym, step)

    # ---- public API ------------------------------------------------------------
    def start(self, message, offline=False):
        state = {'stage': 'new', 'offline': bool(offline), 'tried': [], 'original': message, 'question': None,
                 'symptom': None, 'current': None, 'turns': 0}
        sid = self.store.new_session(state)
        return self.turn(sid, {'type': 'message', 'text': message})

    def turn(self, sid, t):
        state = self.store.get_session(sid)
        if state is None: raise ValueError('unknown_session')
        kind = t.get('type', 'message'); text = (t.get('text') or '').strip()
        if 'offline' in t: state['offline'] = bool(t['offline'])
        state['turns'] += 1
        self.store.log_event(sid, 'user', {'type': kind, 'text': text, 'option': t.get('option')})
        # free-text shortcuts once a plan is showing
        if kind == 'message' and state['stage'] == 'plan':
            if CLOSE_WORDS.search(text): kind = 'fixed'
            elif STUCK_WORDS.search(text): kind = 'still_stuck'
        if kind == 'message' and state['stage'] == 'awaiting_answer': kind = 'answer'

        if kind == 'message':
            if not text: raise ValueError('text_required')
            state['original'] = text; state['tried'] = []
            eff, intent, info = self.e.understand(text, state['offline'])
            qid, chooser = self.question_for(text, intent, info, state['offline'])
            if qid:
                state.update(stage='awaiting_answer', question=qid, trigger=intent.get('trigger', 'none'))
                self.store.save_session(sid, state)
                self.store.log_event(sid, 'question', {'id': qid, 'chosen_by': chooser})
                return {'session_id': sid, 'type': 'question', 'question': self._public_question(qid, chooser),
                        'understanding': info, 'why': 'The complaint is too vague to pick one verified fix.'}
            return self._plan_from_query(sid, state, text)

        if kind == 'answer':
            if state['stage'] != 'awaiting_answer': raise ValueError('no_pending_question')
            q = QUESTIONS[state['question']]; opt = t.get('option')
            state['stage'] = 'answered'
            if opt is not None:
                if opt not in {o['id'] for o in q['options']}: raise ValueError('option_not_in_question')
                trig = state.get('trigger', 'none'); trig = trig if trig in TRIGGERS else 'none'
                return self._plan_from_query(sid, state, SYMPTOMS[opt][1] + TRIGGERS[trig])
            if not text: raise ValueError('answer_required')
            # free-text answer: one more understanding pass on original + answer, no second question
            return self._plan_from_query(sid, state, state['original'] + '. ' + text)

        if kind == 'still_stuck':
            if state['stage'] != 'plan': raise ValueError('no_active_plan')
            self.store.add_feedback(state['current'], 'not_fixed', sid, state.get('current_catalog'), state.get('symptom'))
            ladder = self.e.ladder(state['symptom']) if state.get('symptom') else []
            nxt = next((d for d in ladder if d['id'] not in state['tried']), None)
            if nxt is None:
                state['stage'] = 'escalated'; self.store.save_session(sid, state)
                self.store.log_event(sid, 'escalate', {'tried': state['tried']})
                tried = [next((d['title'] for d in ladder if d['id'] == x), x) for x in state['tried']]
                return {'session_id': sid, 'type': 'escalate', 'tried': state['tried'],
                        'report': {'complaint': state['original'], 'symptom': state.get('symptom'),
                                   'fixes_tried': tried, 'mode': 'on_device' if state['offline'] else 'cloud',
                                   'recommendation': 'Every validated fix in the catalog for this symptom was tried. Visit a Samsung service centre or book a repair; show this report.'}}
            plan = self.e.compile_evidence(state['original'], nxt, self.e.intent(SYMPTOMS[state['symptom']][1]),
                                           purpose='escalation', extra_meta={'escalation_from': state['current']})
            if not plan['response']['contexts']:
                return self._no_match(sid, state, plan, plan['meta']['fallback'])
            return self._plan_reply(sid, state, plan, state['symptom'], len(state['tried']) + 1)

        if kind == 'fixed':
            if state['stage'] != 'plan': raise ValueError('no_active_plan')
            self.store.add_feedback(state['current'], 'fixed', sid, state.get('current_catalog'), state.get('symptom'))
            state['stage'] = 'closed_fixed'; self.store.save_session(sid, state)
            self.store.log_event(sid, 'closed', {'fixed_by': state['current']})
            return {'session_id': sid, 'type': 'closed', 'fixed_by': state['current'], 'tried': state['tried'],
                    'message': 'Glad it is fixed. This fix now counts as a verified fix.'}
        raise ValueError('bad_turn_type')

    def history(self, sid):
        state = self.store.get_session(sid)
        if state is None: raise ValueError('unknown_session')
        return {'session_id': sid, 'state': state, 'events': self.store.session_events(sid)}
