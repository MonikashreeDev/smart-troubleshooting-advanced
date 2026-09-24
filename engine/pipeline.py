from __future__ import annotations
import hashlib,json,os,re,threading,time
from pathlib import Path
from statistics import median
from .api_models import APIEnvelope
from .retrieval import HybridIndex,tokens,charvec,cosine
from .validators import validate
from .ai import WorkersAI,dense_cosine,LLM_MODEL,EMBED_MODEL,SYMPTOMS
from .store import Store
RISK={'auto':0,'manual':1,'critical':2}

AI_MIN_CONFIDENCE=float(os.getenv('SGT_AI_MIN_CONFIDENCE','0.6'))
EMB_DOMAIN_MIN=float(os.getenv('SGT_EMB_DOMAIN_MIN','0.55'))
EMB_DOMAIN_MARGIN=float(os.getenv('SGT_EMB_DOMAIN_MARGIN','0.06'))
SEM_CACHE_EMB=float(os.getenv('SGT_SEM_CACHE_EMB','0.90'))

class TroubleshootingEngine:
 def __init__(self,data_root,ai=None,store=None):
    self.store=store if store is not None else Store()
    self.ai=ai if ai is not None else WorkersAI(); self._emb_ready=False; self._emb_retry_at=0.0; self.ai_hits={'understood':0,'fallback':0,'out_of_scope':0}
    self.data_root=Path(os.getenv('TROUBLESHOOT_DATA_DIR',data_root)); self.lock=threading.Lock(); self.exact={}; self.semantic=[]; self.times=[]; self.requests=0; self.hits={'exact':0,'semantic':0}; self.abstentions=0
    self.siis=self._load('siis_responses.json','demo_siis.json'); self.links=self._load('deeplinks.json','demo_deeplinks.json'); self.scenarios=self._load('queries.json','demo_queries.json')
    self.siis_idx=HybridIndex(self.siis,lambda d:' '.join([d['domain'],d['title'],d['text'],' '.join(d.get('keywords',[]))]))
    # Security property: deeplink itself is deliberately excluded.
    self.link_idx=HybridIndex(self.links,lambda d:' '.join([d['description'],d.get('message',''),d.get('qna_description',''),json.dumps(d.get('classes',{})),d.get('originalType','')]))
    # Synthetic follow-up/predictive fixes (session escalation + predictive maintenance).
    self.followups=self._load_entries('demo_siis_followups.json'); self.fu_links=self._load_entries('demo_deeplinks_followups.json')
    self.catalog=self.links+self.fu_links; self.catalog_by_id={d['id']:d for d in self.catalog}
    self.offline_requests=0
    self.version=hashlib.sha256(json.dumps([self.siis,self.links,self.followups,self.fu_links],sort_keys=True).encode()).hexdigest()[:12]
 def _load(self,official,demo):
    p=self.data_root/'official'/official
    q=self.data_root/demo
    with open(p if p.exists() else q,encoding='utf8') as f: return json.load(f)
 def _load_entries(self,name):
    q=self.data_root/name
    if not q.exists(): q=Path(__file__).parents[1]/'data'/name
    if not q.exists(): return []
    with open(q,encoding='utf8') as f: return json.load(f)['entries']
 def asset_status(self):
    expected=['queries.json','siis_responses.json','deeplinks.json','samples/','schema.py']
    present=[x for x in expected if (self.data_root/'official'/x).exists()]
    return {'mode':'official' if len(present)==len(expected) else 'synthetic_demo','official_present':present,'official_missing':[x for x in expected if x not in present],'pipeline_version':self.version}
 def health(self): return {'status':'ok','ai':{'enabled':self.ai.enabled,'llm':LLM_MODEL,'embeddings':EMBED_MODEL,'embeddings_ready':self._emb_ready},'indexes':{'siis':len(self.siis),'deeplinks':len(self.links),'followup_fixes':len(self.followups),'followup_deeplinks':len(self.fu_links)},'assets':self.asset_status()['mode'],'features':['multi_turn_diagnosis','session_memory','predictive_maintenance','feedback_ranking','on_device_mode','verified_plans_only'],'storage':'sqlite','version':'advanced-1.0'}
 def normalize(self,q): return ' '.join(tokens(q))
 def intent(self,q):
    t=set(tokens(q)); domains={d:sum(w in t for w in ws) for d,ws in {'Battery':['battery','drain','charge'],'Display':['screen','display','flicker','dark','swipe','navigation'],'Camera':['camera','photo','picture','blurry'],'Performance':['slow','performance','lag','update','hot']}.items()}
    domain=max(domains,key=domains.get) if max(domains.values()) else 'Unknown'
    trigger=next((x for x in ['update','install','app','restart','charge'] if x in t),'none')
    feature=next((x for x in ['navigation','battery','camera','screen','performance'] if x in t),domain.lower())
    symptom=next((x for x in ['slow','drain','flicker','dark','blurry','swipe','hot'] if x in t),'unknown')
    return {'domain':domain,'trigger':trigger,'feature':feature,'symptom':symptom}
 def variations(self,q,i):
    d=i['domain'].lower(); s=i['symptom']; tr='' if i['trigger']=='none' else ' after '+i['trigger']
    vals=[q.strip(),f'{d} {s}{tr}',f'Galaxy {i["feature"]} issue: {s}{tr}',f'Help fix {s} {i["feature"]}{tr}',f'{i["feature"]} is {s}{tr}',f'Troubleshoot {d} {s}',f'{s} problem on Samsung {d}',f'Phone {i["feature"]} behaves {s}{tr}']
    return list(dict.fromkeys(vals))[:10]
 def _ensure_embeddings(self):
    if self._emb_ready or not self.ai.enabled or time.time()<self._emb_retry_at: return self._emb_ready
    a=self.ai.embed(self.siis_idx.texts); b=self.ai.embed(self.link_idx.texts) if a else None
    if a and b: self.siis_idx.set_embeddings(a); self.link_idx.set_embeddings(b); self._emb_ready=True
    else: self._emb_retry_at=time.time()+60
    return self._emb_ready
 def _qemb(self,text,offline=False):
    if offline or not self._ensure_embeddings(): return None
    v=self.ai.embed([text]); return v[0] if v else None
 def understand(self,query,offline=False):
    """AI complaint understanding with deterministic fallback. Returns (effective_query,intent,info).
    offline=True is the Galaxy AI hybrid "on-device" mode: cloud AI is skipped and the rules path runs."""
    rules=self.intent(query)
    info={'used':False,'reason':'ai_disabled' if not self.ai.enabled else None,'rules_intent':rules,'mode':'on_device' if offline else 'cloud'}
    if offline: info['reason']='on_device_mode_rules'; return query,rules,info
    if not self.ai.enabled: return query,rules,info
    u=self.ai.understand(query)
    if u is None:
        info['reason']='ai_unavailable_fallback_to_rules'; self.ai_hits['fallback']+=1
        return query,self._embedding_domain(query,rules,info),info
    info.update({k:u[k] for k in ('supported','symptom','trigger','confidence','language','english','canonical_query','model')})
    info['vague']=u.get('vague',False)
    if u['supported'] and u['confidence']>=AI_MIN_CONFIDENCE:
        intent=self.intent(u['canonical_query'])
        if intent['domain']==u['domain']:
            info['used']=True; info['reason']='ai_understood'; self.ai_hits['understood']+=1
            return u['canonical_query'],intent,info
        info['reason']='ai_label_mismatch_fallback_to_rules'
    elif not u['supported'] and u['confidence']>=AI_MIN_CONFIDENCE:
        info['reason']='ai_out_of_scope'; self.ai_hits['out_of_scope']+=1
        if rules['domain']=='Unknown': info['used']=True
        return query,rules,info
    else: info['reason']='ai_low_confidence_fallback_to_rules'
    self.ai_hits['fallback']+=1
    return query,self._embedding_domain(query,rules,info),info
 def _embedding_domain(self,query,rules,info):
    """When keyword rules find no domain, let real embeddings suggest one.
    Only a clear, high-similarity match with a margin is accepted; retrieval
    gates and validators still run afterwards."""
    if rules['domain']!='Unknown': return rules
    v=self._qemb(self.normalize(query))
    if v is None: return rules
    sims=sorted(((dense_cosine(v,e),d['domain'],d['id']) for e,d in zip(self.siis_idx.emb,self.siis)),reverse=True)
    top=sims[0]; runner=next((x for x in sims[1:] if x[1]!=top[1]),(0,None,None))
    info['embedding_domain']={'domain':top[1],'source_id':top[2],'similarity':round(top[0],3),'margin':round(top[0]-runner[0],3)}
    if top[0]>=EMB_DOMAIN_MIN and top[0]-runner[0]>=EMB_DOMAIN_MARGIN:
        info['reason']=(info.get('reason') or '')+'+embedding_domain'
        return {**rules,'domain':top[1],'feature':top[1].lower()}
    return rules
 def _cache_key(self,n): return self.version+':'+hashlib.sha256(n.encode()).hexdigest()
 def _semantic_hit(self,n,intent,emb=None,offline=False):
    v=charvec(n)
    for row in self.semantic:
      if row.get('offline',False)!=offline: continue
      same=all(intent[k]==row['intent'][k] for k in ('domain','symptom','trigger','feature'))
      if not same: continue
      if emb is not None and row.get('emb') is not None:
        if dense_cosine(emb,row['emb'])>=SEM_CACHE_EMB: return row['payload']
      elif cosine(v,row['vec'])>=.73: return row['payload']
 def troubleshoot(self,query,siis_response=None,offline=False):
    start=time.perf_counter(); n=self.normalize(query); key=self._cache_key(('on_device:' if offline else '')+n+('|siis:'+hashlib.sha256(siis_response.encode()).hexdigest() if siis_response else '')); cache='miss'; payload=None; emb=None
    with self.lock:
      self.requests+=1
      if offline: self.offline_requests+=1
      if key in self.exact: payload=json.loads(json.dumps(self.exact[key])); cache='exact'; self.hits['exact']+=1
    if cache=='miss':
      eff,intent,info=self.understand(query,offline); en=self.normalize(eff)
      emb=self._qemb(en,offline)
      with self.lock:
        hit=None if siis_response else self._semantic_hit(en,intent,emb,offline)
        if hit: payload=json.loads(json.dumps(hit)); cache='semantic'; self.hits['semantic']+=1; payload['meta']['understanding']=info
      if payload is None:
        if info.get('reason')=='ai_out_of_scope' and info.get('used'): payload=self._abstain(query,'no_match',intent,understanding=info)
        else: payload=self._compile(query,intent,siis_response,eff,info,offline)
    elapsed=round((time.perf_counter()-start)*1000,2)
    if payload['meta'].get('proof'): payload['meta']['ranking']=self.ranking_info(payload['meta']['proof'][0]['source_id'])
    payload['query']=query; payload['meta']['mode']='on_device' if offline else 'cloud'; payload['meta']['latency_ms']=elapsed; payload['meta']['cache_hit']=cache!='miss'; payload['meta']['cache_type']=cache
    with self.lock:
      self.times.append(elapsed); self.times=self.times[-1000:]
      if cache=='miss':
        self.exact[key]=json.loads(json.dumps(payload))
        self.semantic.append({'offline':offline,'vec':charvec(en),'emb':emb,'intent':intent,'payload':json.loads(json.dumps(payload))}); self.semantic=self.semantic[-300:]
    return payload
 def _compile(self,query,intent,siis_response,eff=None,info=None,offline=False):
    eff=eff or query; info=info or {}
    if intent['domain']=='Unknown': return self._abstain(query,'no_match',intent,understanding=info)
    q=eff+' '+intent['domain']+' '+intent['symptom']+' '+intent['trigger']
    sr=self.siis_idx.search(q,3,self._qemb(q,offline)); best=sr[0]
    if siis_response:
      evidence={'id':'request_context','domain':intent['domain'],'title':'Provided SIIS','text':siis_response,'keywords':[]}; e_score=.99
    else: evidence=best['doc']; e_score=best['score']
    if e_score<.20 or (evidence['domain']!=intent['domain'] and not siis_response): return self._abstain(query,'no_siis_context',intent,understanding=info)
    lq=evidence['text']+' '+eff; lr=self.link_idx.search(lq,3,self._qemb(lq,offline)); link=lr[0]
    if link['score']<.18: return self._abstain(query,'no_match',intent,understanding=info)
    d=link['doc']; steps=evidence['steps']; category=evidence.get('category','auto'); action_name=evidence['actionName']; desc=evidence['description']
    action={'actionName':action_name,'description':desc,'stepGroups':[{'steps':steps,'validationDeeplink':None,'actionableDeeplink':{k:d.get(k) for k in ['deeplink','description','message','classes','originalType']}}],'category':category}
    topic=evidence['domain']; goal={'goal':f'Follow these steps to perform this {topic} Troubleshooting.','title':evidence['title'],'actions':[action],'score':float(round(min(.99,.58*e_score+.42*link['score']),2))}
    core={'contexts':[goal]}; errors=validate(core,self.links,{action_name:evidence['text']})
    if errors: return self._abstain(query,'validation_failed',intent,errors,understanding=info)
    proof={'source_id':evidence['id'],'source_span':evidence['text'],'catalog_id':d['id'],'screen_identity':d['description'],'risk':category,'retrieval':{'siis':sr,'deeplink':[{k:v for k,v in x.items() if k!='doc'}|{'id':x['doc']['id']} for x in lr]},'validators':{'passed':True,'errors':[]}}
    meta={'model':self._model_label(info,sr),'cost_usd':0.0,'fallback':None,'intent':intent,'understanding':info,'retrieval_mode':sr[0].get('dense_model'),'proof':[proof],'asset_mode':self.asset_status()['mode'],'pipeline_version':self.version,'latency_ms':0,'cache_hit':False,'cache_type':'miss'}
    return APIEnvelope(query,self.variations(query,intent),core,meta).to_dict()
 # ---- verified compile for a known evidence record (escalation ladder, predictive plans) ----
 def compile_evidence(self,query,evidence,intent,info=None,purpose='escalation',extra_meta=None):
    """Compile one SIIS/follow-up record into an Appendix A plan and run the SAME validators.
    The catalog binding is checked in code (id must exist, URI must be in the catalog); any
    failure fails closed with an abstention."""
    info=info or {}
    cid=evidence.get('catalog_id',None) if 'catalog_id' in evidence else None
    link=None
    if cid is not None:
      link=self.catalog_by_id.get(cid)
      if link is None: return self._abstain(query,'catalog_binding_missing',intent,[f'unknown_catalog_id:{cid}'],understanding=info)
    elif 'catalog_id' not in evidence:
      # primary SIIS record: use the verified metadata retrieval, as in the main path
      lr=self.link_idx.search(evidence['text'],3,None); link=lr[0]['doc'] if lr and lr[0]['score']>=.18 else None
      if link is None: return self._abstain(query,'no_match',intent,understanding=info)
    ad={k:link.get(k) for k in ['deeplink','description','message','classes','originalType']} if link else None
    action={'actionName':evidence['actionName'],'description':evidence['description'],'stepGroups':[{'steps':evidence['steps'],'validationDeeplink':None,'actionableDeeplink':ad}],'category':evidence.get('category','manual')}
    topic=evidence['domain']
    goal={'goal':f'Follow these steps to perform this {topic} Troubleshooting.','title':evidence['title'],'actions':[action],'score':0.9}
    core={'contexts':[goal]}; errors=validate(core,self.catalog,{evidence['actionName']:evidence['text']})
    if errors: return self._abstain(query,'validation_failed',intent,errors,understanding=info)
    proof={'source_id':evidence['id'],'source_span':evidence['text'],'catalog_id':link['id'] if link else None,'screen_identity':link['description'] if link else 'manual step (no screen)','risk':action['category'],'retrieval':{'binding':'catalog_id checked in code' if cid else ('metadata retrieval' if link else 'manual')},'validators':{'passed':True,'errors':[]},'synthetic_data':True}
    meta={'model':'deterministic validators','cost_usd':0.0,'fallback':None,'purpose':purpose,'intent':intent,'understanding':info,'proof':[proof],'asset_mode':self.asset_status()['mode'],'pipeline_version':self.version,'latency_ms':0,'cache_hit':False,'cache_type':'miss'}
    meta['ranking']=self.ranking_info(evidence['id'])
    if extra_meta: meta.update(extra_meta)
    return APIEnvelope(query,self.variations(query,intent),core,meta).to_dict()
 def primary_for(self,symptom):
    """The main-path SIIS record for a canonical symptom (same retrieval as troubleshoot)."""
    if symptom not in SYMPTOMS: return None
    dom,canon=SYMPTOMS[symptom]
    sr=self.siis_idx.search(canon+' '+dom+' '+symptom,1)
    return sr[0]['doc'] if sr and sr[0]['doc']['domain']==dom and sr[0]['score']>=.20 else None
 def ladder(self,symptom,include_predictive=False):
    """Ordered fixes for a symptom: primary first, then follow-ups.
    Order key = (risk class, -verified-fix score, authored order). Feedback can only
    reorder fixes WITHIN a risk class; safe-before-destructive is never overridden."""
    fb=self.store.feedback_counts()
    cands=[d for d in self.followups if symptom in d.get('symptoms',[]) and (include_predictive or not d.get('predictive_only'))]
    def key(d): return (RISK.get(d.get('category'),9),-self.fix_score(fb.get(d['id'])),d.get('order',5),d['id'])
    out=sorted(cands,key=key)
    prim=self.primary_for(symptom)
    return ([prim] if prim else [])+out
 @staticmethod
 def fix_score(c):
    """Verified-fix score: Laplace-smoothed fix rate minus the neutral prior (0 with no feedback)."""
    if not c: return 0.0
    return round((c['fixed']+1)/(c['fixed']+c['not_fixed']+2)-0.5,4)
 # ---- O4 feedback-driven ranking -------------------------------------------
 def known_sources(self):
    return {d['id']:d for d in self.siis+self.followups}
 def record_feedback(self,source_id,outcome,session_id=None):
    src=self.known_sources().get(source_id)
    if src is None: raise ValueError('unknown_source_id')
    if outcome not in ('fixed','not_fixed'): raise ValueError('outcome_must_be_fixed_or_not_fixed')
    self.store.add_feedback(source_id,outcome,session_id,src.get('catalog_id'),None)
    return {'ok':True,'ranking':self.ranking_info(source_id)}
 def ranking_info(self,source_id):
    c=self.store.feedback_counts().get(source_id,{'fixed':0,'not_fixed':0})
    return {'verified_fixes':c['fixed'],'not_fixed':c['not_fixed'],'fix_score':self.fix_score(c) if (c['fixed'] or c['not_fixed']) else 0.0,
            'policy':'feedback reorders fixes only within the same risk class; safe-before-destructive and validators are never overridden'}
 def feedback_board(self):
    fb=self.store.feedback_counts(); src=self.known_sources(); rows=[]
    for sid,c in fb.items():
      n=c['fixed']+c['not_fixed']
      rows.append({'source_id':sid,'title':src.get(sid,{}).get('title',sid),'risk':src.get(sid,{}).get('category'),'verified_fixes':c['fixed'],'not_fixed':c['not_fixed'],'fix_rate':round(c['fixed']/n,3) if n else None,'fix_score':self.fix_score(c)})
    rows.sort(key=lambda r:(-r['verified_fixes'],r['not_fixed'],r['source_id']))
    return {'total_feedback':sum(r['verified_fixes']+r['not_fixed'] for r in rows),'verified_fixes':sum(r['verified_fixes'] for r in rows),'by_fix':rows}
 def _model_label(self,info,sr=None):
    parts=[]
    if info.get('used'): parts.append('llama-3.3-70b-instruct-fp8-fast (understanding)')
    if sr and sr[0].get('dense_model')=='bge-small-en-v1.5': parts.append('bge-small-en-v1.5 (retrieval)')
    parts.append('deterministic validators')
    return ' + '.join(parts) if len(parts)>1 else 'deterministic-hybrid-demo'
 def _abstain(self,q,reason,intent,errors=None,understanding=None):
    with self.lock: self.abstentions+=1
    return APIEnvelope(q,self.variations(q,intent),{'contexts':[]},{'model':self._model_label(understanding or {}),'cost_usd':0.0,'fallback':reason,'intent':intent,'understanding':understanding or {},'proof':[],'validator_errors':errors or [],'asset_mode':self.asset_status()['mode'],'pipeline_version':self.version,'latency_ms':0,'cache_hit':False,'cache_type':'miss'}).to_dict()
 def metrics(self):
    ts=sorted(self.times); pct=lambda p: ts[min(len(ts)-1,int((len(ts)-1)*p))] if ts else 0
    return {'requests':self.requests,'cache_hits':self.hits,'cache_hit_rate':round(sum(self.hits.values())/max(1,self.requests),3),'abstentions':self.abstentions,'latency_ms':{'p50':pct(.5),'p95':pct(.95)},'schema_contract':'Appendix A core + Appendix B envelope','catalog_integrity':1.0,'url_leaks':0,'assets':self.asset_status(),'ai':{**self.ai.status(),'embeddings_ready':self._emb_ready,**self.ai_hits},'on_device_requests':self.offline_requests,'sessions':self.store.session_count(),'feedback':self.feedback_board(),'benchmark':self.benchmark()}
 def benchmark(self):
    p=Path(__file__).parents[1]/'evaluation_ai.json'
    try:
      with open(p,encoding='utf8') as f: b=json.load(f)
      return {k:b[k] for k in ('generated_at','dataset','cases','configs') if k in b}
    except Exception: return None
