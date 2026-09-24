from __future__ import annotations
import re
URL=re.compile(r'(https?://|www\.|\[[^\]]+\]\([^\)]+\))',re.I)
GOAL=re.compile(r'^Follow these steps to perform this .+ (Troubleshooting|Configuration)\.$')
TITLE=re.compile(r'^[A-Z][^.!?]*$')
def wc(s): return len(re.findall(r"\b[\w'-]+\b",s))
def validate(response,catalog,evidence_by_action):
    errors=[]; uri_set={d['deeplink'] for d in catalog}; ranks={'auto':0,'manual':1,'critical':2}
    if set(response)!={'contexts'}: errors.append('core_schema_top_level')
    for gi,g in enumerate(response.get('contexts',[])):
        p=f'contexts[{gi}]'
        if not GOAL.match(g.get('goal','')): errors.append(p+'.goal_syntax')
        if not 2<=wc(g.get('title',''))<=3 or not TITLE.match(g.get('title','')): errors.append(p+'.title')
        if not isinstance(g.get('score'),float) or not 0<=g['score']<=1: errors.append(p+'.score')
        cats=[ranks.get(a.get('category'),99) for a in g.get('actions',[])]
        if cats!=sorted(cats): errors.append(p+'.risk_order')
        for ai,a in enumerate(g.get('actions',[])):
            ap=f'{p}.actions[{ai}]'; desc=a.get('description','')
            if not (5<=wc(desc)<=7 and desc.startswith('It will ')): errors.append(ap+'.description')
            if not 2<=wc(a.get('actionName',''))<=6: errors.append(ap+'.actionName')
            ev=evidence_by_action.get(a.get('actionName',''),'').lower()
            for sg in a.get('stepGroups',[]):
                if not sg.get('steps'): errors.append(ap+'.empty_steps')
                for step in sg.get('steps',[]):
                    if URL.search(step): errors.append(ap+'.url_leak')
                    words=[x for x in re.findall(r'[a-z0-9]+',step.lower()) if len(x)>3]
                    if words and ev and not any(w in ev for w in words): errors.append(ap+'.unsupported_step')
                dl=sg.get('actionableDeeplink')
                if dl and dl.get('deeplink') not in uri_set: errors.append(ap+'.catalog_integrity')
    if URL.search(str(response).replace('bixby://','')): errors.append('recursive_url_leak')
    return sorted(set(errors))
