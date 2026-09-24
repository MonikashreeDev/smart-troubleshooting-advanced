"""Optional AI layer: Cloudflare Workers AI over its REST API (free tier).

Two jobs only:
  1. understand(): an open-source LLM (Llama 3.3 70B, fp8-fast) reads a messy
     complaint (English, Hinglish, Tanglish, typos) and maps it onto the small,
     closed vocabulary the engine already supports. It never writes steps,
     deeplinks or answers. Code turns its labels into a canonical complaint.
  2. embed(): an open-source embedding model (BAAI bge-small-en-v1.5) gives
     real semantic vectors for retrieval and the semantic cache.

If the credentials are missing, the network fails, or the model is unsure,
every caller falls back to the original deterministic path. Validators run
after the AI either way: AI understands, code verifies.
"""
from __future__ import annotations
import hashlib, json, math, os, re, threading, time, urllib.error, urllib.request

LLM_MODEL = os.getenv('SGT_LLM_MODEL', '@cf/meta/llama-3.3-70b-instruct-fp8-fast')
EMBED_MODEL = os.getenv('SGT_EMBED_MODEL', '@cf/baai/bge-small-en-v1.5')

# Closed vocabulary. The LLM may only choose from these labels.
SYMPTOMS = {
    'slow':    ('Performance', 'My phone performance is slow'),
    'drain':   ('Battery', 'The battery drain is fast and the battery dies quickly'),
    'flicker': ('Display', 'The screen display has flicker'),
    'swipe':   ('Display', 'Swipe navigation gestures on the screen go the wrong way'),
    'blurry':  ('Camera', 'My camera photos are blurry'),
}
TRIGGERS = {'update': ' after an update', 'install': ' after I install an app', 'none': ''}

SYSTEM_PROMPT = """You are the complaint-understanding step of a Samsung Galaxy troubleshooting engine.
Users write messy complaints: English, Hinglish, Tanglish (Tamil in Latin letters), Telugu-English, typos, slang.
Map the complaint to exactly one supported symptom, or mark it unsupported. Do NOT give advice or steps.

Supported symptoms:
- slow: phone is slow, laggy, hangs, freezes, apps take long to open (e.g. "romba slow aagudhu", "hang aagudhu", "bahut atak raha hai")
- drain: battery drains fast, charge does not last, battery dies quickly (e.g. "charge nikkala", "battery jaldi khatam")
- flicker: screen flickers, blinks, shakes or flashes
- swipe: swipe or gesture navigation goes the wrong way, back/home gestures misbehave
- blurry: camera photos or pictures are blurry, out of focus, not clear

Triggers: update (after a software/OS update), install (after installing a new app), none (not stated).

Anything else (other devices, heating only, network, calls, storage, jokes, non-phone items, harmful requests) is unsupported.
If it is clearly a phone problem but too vague to pick one symptom (e.g. "my phone has a problem", "mobile sariya work aagala", "screen is acting weird"), set supported=false and vague=true.

Reply with ONLY a JSON object, no prose:
{"supported": true|false, "vague": true|false, "symptom": "slow|drain|flicker|swipe|blurry|none", "trigger": "update|install|none", "language": "short name of the language mix", "english": "faithful short English translation of the complaint", "confidence": number between 0 and 1}"""

def _clean_json(text):
    if isinstance(text, dict): return text
    m = re.search(r'\{.*\}', str(text), re.S)
    if not m: raise ValueError('no_json')
    return json.loads(m.group(0))

def dense_cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (da * db)

class WorkersAI:
    def __init__(self, account_id=None, api_token=None, timeout=8.0):
        self.account_id = account_id if account_id is not None else (os.getenv('CF_ACCOUNT_ID') or os.getenv('CLOUDFLARE_ACCOUNT_ID') or '')
        self.api_token = api_token if api_token is not None else (os.getenv('CF_API_TOKEN') or os.getenv('CLOUDFLARE_API_TOKEN') or '')
        self.timeout = timeout
        self.lock = threading.Lock()
        self.down_until = 0.0
        self.stats = {'llm_calls': 0, 'llm_failures': 0, 'embed_calls': 0, 'embed_failures': 0}
        self._emb_cache = {}
        self._understand_cache = {}

    @property
    def enabled(self):
        return bool(self.account_id and self.api_token) and os.getenv('SGT_AI', 'on').lower() not in ('0', 'off', 'false')

    def status(self):
        return {'enabled': self.enabled, 'provider': 'Cloudflare Workers AI (free tier)',
                'llm_model': LLM_MODEL, 'embedding_model': EMBED_MODEL,
                'available': self.enabled and time.time() >= self.down_until, **self.stats}

    def _run(self, model, payload, timeout=None):
        if not self.enabled: raise RuntimeError('ai_disabled')
        if time.time() < self.down_until: raise RuntimeError('ai_cooling_down')
        url = f'https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{model}'
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                     headers={'Authorization': f'Bearer {self.api_token}', 'Content-Type': 'application/json'})
        body, last = None, None
        for attempt in range(2):  # one quick retry for transient errors / rate limits
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                    body = json.loads(r.read().decode()); break
            except Exception as e:
                last = e; time.sleep(0.6)
        if body is None:
            # Short circuit breaker so an outage never slows every request.
            self.down_until = time.time() + 15
            self.stats['last_error'] = type(last).__name__ + (f' {last.code}' if hasattr(last, 'code') else '')
            raise last
        if not body.get('success', False): raise RuntimeError('ai_error')
        return body['result']

    # ---- 1. complaint understanding -------------------------------------
    def understand(self, query):
        """Return a validated understanding dict, or None (caller falls back)."""
        key = ' '.join(query.lower().split())
        if key in self._understand_cache: return dict(self._understand_cache[key], cached=True)
        with self.lock: self.stats['llm_calls'] += 1
        try:
            res = self._run(LLM_MODEL, {'messages': [{'role': 'system', 'content': SYSTEM_PROMPT},
                                                     {'role': 'user', 'content': query[:600]}],
                                        'max_tokens': 160, 'temperature': 0})
            raw = res.get('response') if isinstance(res, dict) else res
            if raw is None and isinstance(res, dict) and res.get('choices'):
                raw = res['choices'][0]['message']['content']
            out = self.validate_understanding(_clean_json(raw))
        except Exception:
            with self.lock: self.stats['llm_failures'] += 1
            return None
        if out is not None: self._understand_cache[key] = out
        return out

    @staticmethod
    def validate_understanding(u):
        """Code-side check of the LLM output. Anything off-vocabulary is rejected."""
        if not isinstance(u, dict): return None
        try: conf = float(u.get('confidence', 0))
        except (TypeError, ValueError): return None
        conf = max(0.0, min(1.0, conf))
        supported = u.get('supported') is True
        symptom = str(u.get('symptom', 'none')).lower().strip()
        trigger = str(u.get('trigger', 'none')).lower().strip()
        if trigger not in TRIGGERS: trigger = 'none'
        english = re.sub(r'\s+', ' ', str(u.get('english', ''))).strip()[:200]
        language = re.sub(r'\s+', ' ', str(u.get('language', ''))).strip()[:40]
        if supported and symptom not in SYMPTOMS: return None
        if not supported: symptom = 'none'
        canonical = None
        if supported:
            canonical = SYMPTOMS[symptom][1] + TRIGGERS[trigger]
        vague = (u.get('vague') is True) and not supported
        return {'supported': supported, 'vague': vague, 'symptom': symptom, 'trigger': trigger,
                'domain': SYMPTOMS[symptom][0] if supported else 'Unknown',
                'confidence': round(conf, 2), 'language': language, 'english': english,
                'canonical_query': canonical, 'model': LLM_MODEL}

    # ---- 1b. clarifying-question choice (multi-turn diagnosis) ----------
    def choose_question(self, query, candidates):
        """Ask the LLM which ONE question from a fixed set best narrows the complaint.
        candidates: {question_id: question_text}. Returns a validated id or None.
        The model can only pick an id; the question wording and answer options are
        fixed in code, so nothing generated reaches the user."""
        if not candidates: return None
        ids = list(candidates)
        prompt = ('Pick the ONE clarifying question that best narrows this phone complaint. '
                  'Reply with ONLY JSON {"question_id": "<id>"}. Allowed ids:\n' +
                  '\n'.join(f'- {k}: {v}' for k, v in candidates.items()))
        with self.lock: self.stats['llm_calls'] += 1
        try:
            res = self._run(LLM_MODEL, {'messages': [{'role': 'system', 'content': prompt},
                                                     {'role': 'user', 'content': query[:600]}],
                                        'max_tokens': 40, 'temperature': 0})
            raw = res.get('response') if isinstance(res, dict) else res
            qid = str(_clean_json(raw).get('question_id', '')).strip()
        except Exception:
            with self.lock: self.stats['llm_failures'] += 1
            return None
        return qid if qid in ids else None

    # ---- 2. embeddings ---------------------------------------------------
    def embed(self, texts):
        """Return a list of vectors (same order) or None on any failure."""
        missing = [t for t in dict.fromkeys(texts) if hashlib.sha1(t.encode()).hexdigest() not in self._emb_cache]
        if missing:
            with self.lock: self.stats['embed_calls'] += 1
            try:
                vecs = []
                for i in range(0, len(missing), 50):
                    res = self._run(EMBED_MODEL, {'text': missing[i:i + 50]}, timeout=6)
                    vecs.extend(res['data'])
                if len(vecs) != len(missing): raise ValueError('embed_shape')
            except Exception:
                with self.lock: self.stats['embed_failures'] += 1
                return None
            for t, v in zip(missing, vecs): self._emb_cache[hashlib.sha1(t.encode()).hexdigest()] = v
            if len(self._emb_cache) > 5000: self._emb_cache.clear()
        return [self._emb_cache[hashlib.sha1(t.encode()).hexdigest()] for t in texts]

class NoAI(WorkersAI):
    """Explicitly disabled client (tests, offline evaluation baseline)."""
    def __init__(self): super().__init__(account_id='', api_token='')
    @property
    def enabled(self): return False
