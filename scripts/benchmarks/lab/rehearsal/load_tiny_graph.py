import time, sys
from neo4j import GraphDatabase
WS = "grbench-maple-physics"
nodes = [
    ("a1", "author", {"feature_name": "alice smith"}), ("a2", "author", {"feature_name": "bob lee"}),
    ("p1", "paper", {"feature_title": "phase separation dynamics", "feature_year": "1990"}),
    ("p2", "paper", {"feature_title": "magnetodisk periodicities", "feature_year": "2012"}),
    ("p3", "paper", {"feature_title": "turbulence in plasmas", "feature_year": "2001"}),
    ("v1", "venue", {"feature_name": "physical review letters"}),
]
adj = {"p1": {"author": ["a1"], "venue": ["v1"], "reference": ["p3"], "cited_by": ["p2"]},
       "p2": {"author": ["a1", "a2"], "venue": ["v1"], "reference": ["p1"], "cited_by": []},
       "p3": {"author": ["a2"], "venue": ["v1"], "reference": [], "cited_by": ["p1"]},
       "a1": {"paper": ["p1", "p2"]}, "a2": {"paper": ["p2", "p3"]}, "v1": {"paper": ["p1", "p2", "p3"]}}
for attempt in range(30):
    try:
        d = GraphDatabase.driver("bolt://127.0.0.1:7699", auth=("neo4j", "rehearsalpass")); d.verify_connectivity(); break
    except Exception:
        time.sleep(3)
else:
    sys.exit("bolt not ready")
with d.session() as s:
    s.run("MATCH (n) DETACH DELETE n")
    for nid, nt, feats in nodes:
        s.run("CREATE (n:GRBenchNode {id:$id, node_type:$nt, source_node_id:$id, _workspace_id:$ws}) SET n += $f", id=nid, nt=nt, ws=WS, f=feats)
    for src, rels in adj.items():
        st = next(nt for nid, nt, _ in nodes if nid == src)
        for rel, targets in rels.items():
            for t in targets:
                s.run("MATCH (a:GRBenchNode {id:$a, _workspace_id:$ws}), (b:GRBenchNode {id:$b, _workspace_id:$ws}) CREATE (a)-[:GRBENCH_REL {relation_name:$r, source_node_type:$st, _workspace_id:$ws}]->(b)", a=src, b=t, r=rel, st=st, ws=WS)
    print("nodes", s.run("MATCH (n) RETURN count(n) AS c").single()["c"], "rels", s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"])
d.close()
