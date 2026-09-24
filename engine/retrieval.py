from __future__ import annotations
import math,re
from collections import Counter
from .ai import dense_cosine
EMB_FLOOR=0.30
LEX_W_EMB=0.62
TOKEN=re.compile(r"[a-z0-9]+")
SYN={'laggy':'slow','lags':'slow','sluggish':'slow','software':'update','upgrade':'update','draining':'drain','dies':'drain','photos':'camera','picture':'camera','dim':'dark'}
def tokens(text): return [SYN.get(x,x) for x in TOKEN.findall(text.lower())]
def charvec(text,n=3):
    s=' '.join(tokens(text)); c=Counter(s[i:i+n] for i in range(max(0,len(s)-n+1))); z=math.sqrt(sum(v*v for v in c.values())) or 1
    return {k:v/z for k,v in c.items()}
def cosine(a,b):
    if len(a)>len(b): a,b=b,a
    return sum(v*b.get(k,0) for k,v in a.items())
class HybridIndex:
    def __init__(self,docs,text_fn):
        self.docs=docs; self.texts=[text_fn(d) for d in docs]; self.toks=[tokens(x) for x in self.texts]; self.vecs=[charvec(x) for x in self.texts]
        self.df=Counter(); [self.df.update(set(t)) for t in self.toks]; self.avg=sum(map(len,self.toks))/max(1,len(self.toks)); self.N=len(docs)
        self.emb=None  # real embedding vectors (bge-small) when the AI layer is available
    def set_embeddings(self,vecs):
        self.emb=vecs if vecs and len(vecs)==len(self.docs) else None
    def search(self,q,k=3,qemb=None):
        qt=tokens(q); qv=charvec(q); out=[]
        use_emb=self.emb is not None and qemb is not None
        for i,(d,dt,dv) in enumerate(zip(self.docs,self.toks,self.vecs)):
            tf=Counter(dt); bm=0
            for term in qt:
                if not tf[term]: continue
                idf=math.log(1+(self.N-self.df[term]+.5)/(self.df[term]+.5)); den=tf[term]+1.5*(.25+.75*len(dt)/(self.avg or 1)); bm+=idf*tf[term]*2.5/den
            lexical=1-math.exp(-bm/max(1,len(set(qt))))
            if use_emb:
                # bge cosine sits around 0.4-0.5 for unrelated text; rescale so 0 means unrelated.
                dense=max(0.0,min(1.0,(dense_cosine(qemb,self.emb[i])-EMB_FLOOR)/(1-EMB_FLOOR)))
                score=LEX_W_EMB*lexical+(1-LEX_W_EMB)*dense
            else:
                dense=cosine(qv,dv); score=.62*lexical+.38*dense
            out.append({'doc':d,'score':round(score,4),'lexical':round(lexical,4),'dense':round(dense,4),'dense_model':'bge-small-en-v1.5' if use_emb else 'hashed-char-ngrams'})
        return sorted(out,key=lambda x:x['score'],reverse=True)[:k]
