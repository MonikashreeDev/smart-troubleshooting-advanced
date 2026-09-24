#!/usr/bin/env python3
"""Before/after benchmark for the AI layer.

Runs the messy-complaint set (data/eval_messy.json) through three configurations:
  rules_only       - original deterministic engine (hashed n-grams, no LLM)
  plus_embeddings  - real bge-small embeddings for retrieval, no LLM understanding
  full_ai          - Llama 3.3 70B complaint understanding + bge-small embeddings
Every case runs cold (caches cleared) so the numbers measure understanding and
retrieval, not caching. Needs CF_ACCOUNT_ID and CF_API_TOKEN for the AI configs.
Writes evaluation_ai.json, which the metrics dashboard shows.
"""
import json, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).parents[1]; sys.path.insert(0, str(ROOT))
from engine.pipeline import TroubleshootingEngine
from engine.ai import WorkersAI, NoAI

class EmbeddingsOnly(WorkersAI):
    def understand(self, query): return None

def run(name, ai, cases):
    e = TroubleshootingEngine(ROOT / 'data', ai=ai)
    if name != 'rules_only' and not e._ensure_embeddings():
        raise SystemExit(f'{name}: embeddings unavailable - set CF_ACCOUNT_ID and CF_API_TOKEN')
    rows, by_cat = [], defaultdict(lambda: [0, 0])
    for c in cases:
        e.exact.clear(); e.semantic.clear()
        t = time.perf_counter(); r = e.troubleshoot(c['query']); ms = (time.perf_counter() - t) * 1000
        got = r['meta']['proof'][0]['catalog_id'] if r['meta']['proof'] else None
        ok = got == c['expected_catalog_id']
        wrong_plan = got is not None and not ok
        by_cat[c['category']][0] += ok; by_cat[c['category']][1] += 1
        rows.append({'category': c['category'], 'query': c['query'], 'expected': c['expected_catalog_id'], 'got': got,
                     'correct': ok, 'wrong_plan': wrong_plan, 'fallback': r['meta']['fallback'],
                     'ai_reason': r['meta'].get('understanding', {}).get('reason'), 'latency_ms': round(ms, 1)})
    n = len(rows); lat = sorted(x['latency_ms'] for x in rows)
    return {'accuracy': round(sum(x['correct'] for x in rows) / n, 3),
            'correct': sum(x['correct'] for x in rows), 'total': n,
            'wrong_plans': sum(x['wrong_plan'] for x in rows),
            'by_category': {k: {'correct': v[0], 'total': v[1], 'accuracy': round(v[0] / v[1], 3)} for k, v in by_cat.items()},
            'cold_latency_ms_p50': lat[n // 2], 'rows': rows}

if __name__ == '__main__':
    data = json.load(open(ROOT / 'data' / 'eval_messy.json', encoding='utf8'))
    cases = data['cases']
    live = WorkersAI()
    configs = {'rules_only': run('rules_only', NoAI(), cases)}
    if live.enabled:
        configs['plus_embeddings'] = run('plus_embeddings', EmbeddingsOnly(), cases)
        configs['full_ai'] = run('full_ai', live, cases)
    out = {'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'dataset': {'file': 'data/eval_messy.json', 'cases': len(cases), 'note': data['note']},
           'cases': len(cases), 'configs': configs}
    (ROOT / 'evaluation_ai.json').write_text(json.dumps(out, indent=2, ensure_ascii=False))
    for k, v in configs.items():
        print(f"{k:16s} accuracy {v['accuracy']*100:5.1f}%  ({v['correct']}/{v['total']})  wrong plans {v['wrong_plans']}  p50 {v['cold_latency_ms_p50']} ms")
        print('   ', {c: f"{x['correct']}/{x['total']}" for c, x in v['by_category'].items()})
