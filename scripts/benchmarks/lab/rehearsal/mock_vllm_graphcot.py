"""Scripted OpenAI-compatible mock: hop calls RetrieveNode -> NeighbourCheck -> NodeDegree -> final answer."""
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _last_tool_ids(messages):
    for m in reversed(messages):
        if m.get("role") == "tool":
            try:
                rows = json.loads(m.get("content") or "[]")
                return [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]
            except Exception:
                return []
    return []

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, payload):
        body = json.dumps(payload).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        return self._send(200, {"object": "list", "data": [{"id": "Qwen/Qwen3.6-27B"}]})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages", []); step = sum(1 for m in msgs if m.get("role") == "assistant")
        ids = _last_tool_ids(msgs)
        if step == 0:
            call = {"name": "RetrieveNode", "arguments": json.dumps({"text": "alice smith"})}
        elif step == 1:
            call = {"name": "NeighbourCheck", "arguments": json.dumps({"node_id": ids[0] if ids else "none", "relation": "paper"})}
        elif step == 2:
            call = {"name": "NodeDegree", "arguments": json.dumps({"node_id": "a1", "relation": "paper"})}
        else:
            call = None
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"call{step}", "type": "function", "function": call}]} if call else {"role": "assistant", "content": "alice smith wrote 2 papers."}
        sys.stderr.write(f"MOCK step {step}: {call['name'] if call else 'final'} tools_in_request={len(req.get('tools', []))}\n")
        return self._send(200, {"id": "m", "object": "chat.completion", "created": int(time.time()), "model": req.get("model"),
            "choices": [{"index": 0, "finish_reason": "tool_calls" if call else "stop", "message": message}],
            "usage": {"prompt_tokens": 500 + 100 * step, "completion_tokens": 30, "total_tokens": 530 + 100 * step, "prompt_tokens_details": {"cached_tokens": 400 if step else 0}}})
ThreadingHTTPServer(("127.0.0.1", 8124), H).serve_forever()
