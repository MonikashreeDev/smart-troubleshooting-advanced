from __future__ import annotations
import json, mimetypes, os, time
mimetypes.add_type('application/manifest+json','.webmanifest'); mimetypes.add_type('text/javascript','.js'); mimetypes.add_type('image/svg+xml','.svg')
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from engine.pipeline import TroubleshootingEngine
from engine.dialogue import Dialogue
from engine.predictive import Predictor

ROOT=Path(__file__).parent
ENGINE=TroubleshootingEngine(ROOT / 'data')
DIALOGUE=Dialogue(ENGINE)
PREDICTOR=Predictor(ENGINE)

class Handler(BaseHTTPRequestHandler):
    server_version='SGTE/1.0'
    def _json(self, status, payload):
        raw=json.dumps(payload, ensure_ascii=False, separators=(',',':')).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/health': return self._json(200, ENGINE.health())
        if path=='/v1/metrics': return self._json(200, ENGINE.metrics())
        if path=='/v1/catalog/status': return self._json(200, ENGINE.asset_status())
        if path=='/v1/devices': return self._json(200, {'devices':PREDICTOR.devices()})
        if path=='/v1/predict':
            dev=(parse_qs(urlparse(self.path).query).get('device') or [''])[0]
            try: return self._json(200, PREDICTOR.predict(dev))
            except ValueError as e: return self._json(404,{'error':str(e)})
        if path.startswith('/v1/session/'):
            try: return self._json(200, DIALOGUE.history(path.split('/')[3]))
            except ValueError as e: return self._json(404,{'error':str(e)})
        if path=='/sw.js': path='/web/sw.js'
        target=ROOT/('web/index.html' if path=='/' else path.lstrip('/'))
        if ROOT not in target.resolve().parents or not target.is_file(): return self._json(404,{'error':'not_found'})
        raw=target.read_bytes(); self.send_response(200)
        self.send_header('Content-Type',mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>200000: raise ValueError('body_too_large')
        body=json.loads(self.rfile.read(n) or b'{}')
        if not isinstance(body,dict): raise ValueError('json_object_required')
        return body
    def do_POST(self):
        path=urlparse(self.path).path
        try:
            body=self._body()
            offline=bool(body.get('offline',False))
            if path=='/v1/troubleshoot':
                query=body.get('query',''); siis=body.get('siis_response')
                if not isinstance(query,str) or not query.strip(): raise ValueError('query_required')
                if siis is not None and not isinstance(siis,str): raise ValueError('siis_response_must_be_string')
                return self._json(200,ENGINE.troubleshoot(query,siis,offline=offline))
            if path=='/v1/session':
                msg=body.get('message','')
                if not isinstance(msg,str) or not msg.strip(): raise ValueError('message_required')
                return self._json(200,DIALOGUE.start(msg[:600],offline=offline))
            if path.startswith('/v1/session/') and path.endswith('/turn'):
                sid=path.split('/')[3]
                turn={k:body[k] for k in ('type','text','option','offline') if k in body}
                if isinstance(turn.get('text'),str): turn['text']=turn['text'][:600]
                return self._json(200,DIALOGUE.turn(sid,turn))
            if path=='/v1/feedback':
                sid=body.get('source_id'); out=body.get('outcome')
                if not isinstance(sid,str) or not isinstance(out,str): raise ValueError('source_id_and_outcome_required')
                return self._json(200,ENGINE.record_feedback(sid,out))
            if path=='/v1/telemetry':
                return self._json(200,PREDICTOR.ingest(body.get('device_id'),body.get('day',0),body.get('metric'),body.get('value')))
            return self._json(404,{'error':'not_found'})
        except (TypeError,ValueError,json.JSONDecodeError) as e: return self._json(400,{'error':str(e)})
        except Exception: return self._json(500,{'error':'internal_error'})
    def log_message(self, fmt, *args): pass

if __name__=='__main__':
    host=os.getenv('HOST','0.0.0.0'); port=int(os.getenv('PORT','8000'))
    print(f'Smart Guided Troubleshooting Engine: http://{host}:{port} (AI {"on" if ENGINE.ai.enabled else "off"})',flush=True)
    import threading; threading.Thread(target=ENGINE._ensure_embeddings,daemon=True).start()  # warm catalog embeddings
    ThreadingHTTPServer((host,port),Handler).serve_forever()
