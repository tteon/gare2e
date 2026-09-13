"""Scripted OpenAI-compatible mock for the grammar lane rehearsal: query turns get Cypher, answer turns get text."""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
LOG = sys.argv[1]
SCRIPT = [
    "MATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'alice smith'})-[:GRBENCH_REL {relation_name: 'author'}]->(p:GRBenchNode {_workspace_id: $workspace_id}) RETURN p.feature_title AS title",  # wrong direction -> 0 rows
    "```cypher\nMATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'alice smith'})-[:GRBENCH_REL {relation_name: 'paper'}]->(p:GRBenchNode {_workspace_id: $workspace_id}) RETURN p.feature_title AS title\n```",
]
state = {"calls": 0}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        last = body["messages"][-1]["content"]
        constrained = "structured_outputs" in body
        if "answer the question concisely" in last:
            content = "Alice Smith wrote 'phase separation dynamics' and 'magnetodisk periodicities'."
            kind = "answer"
        else:
            content = SCRIPT[min(state["calls"], len(SCRIPT) - 1)]
            state["calls"] += 1
            kind = "cypher"
        with open(LOG, "a") as fh: fh.write(json.dumps({"kind": kind, "constrained": constrained, "grammar_bytes": len((body.get("structured_outputs") or {}).get("grammar", ""))}) + "\n")
        out = {"id": "x", "object": "chat.completion", "model": body["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 400 + 50 * state["calls"], "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": 300}}}
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
HTTPServer(("127.0.0.1", 8125), H).serve_forever()
