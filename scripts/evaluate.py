#!/usr/bin/env python3
import json,sys,time
from pathlib import Path
ROOT=Path(__file__).parents[1]; sys.path.insert(0,str(ROOT))
from engine.pipeline import TroubleshootingEngine
e=TroubleshootingEngine(ROOT/'data'); rows=[]
for s in e.scenarios:
 t=time.perf_counter(); r=e.troubleshoot(s['query']); ms=(time.perf_counter()-t)*1000
 c=r['response']['contexts']; got_domain=r['meta']['intent']['domain']; got_link=r['meta']['proof'][0]['catalog_id'] if r['meta']['proof'] else None
 rows.append({'query':s['query'],'domain_ok':got_domain==s['expected_domain'],'deeplink_ok':got_link==s['expected_catalog_id'],'latency_ms':round(ms,2),'fallback':r['meta']['fallback']})
print(json.dumps({'asset_mode':e.asset_status()['mode'],'cases':rows,'domain_accuracy':sum(x['domain_ok'] for x in rows)/len(rows),'deeplink_accuracy':sum(x['deeplink_ok'] for x in rows)/len(rows),'metrics':e.metrics()},indent=2))
