"""Grammar-constrained Cypher for the GRBench Text2Cypher lane (lab 020, guided decoding).

An EBNF (xgrammar dialect) for the *read-only* Cypher subset the GRBench
agents need: MATCH / OPTIONAL MATCH patterns, WHERE, WITH, RETURN, ORDER BY,
LIMIT, function calls (count, collect, toLower, coalesce, ...), string /
number / parameter literals.  Two policy rules are enforced by construction
instead of by the runtime validator:

* every labelled node pattern is ``(:GRBenchNode {_workspace_id: $workspace_id, ...})``
  — ``missing_grbench_node_label`` and ``missing_workspace_scope`` cannot occur;
* no write clauses, no CALL, no UNION, no subqueries — ``write_clause_forbidden``
  and ``unsafe_call_forbidden`` cannot occur.

``unanchored_scan`` remains a runtime check (it is semantic: the grammar cannot
know whether a WHERE predicate touches an indexed property).  The grammar is
sent as ``structured_outputs.grammar`` in the chat-completion request; vLLM
compiles it with xgrammar and masks every decode step.
"""

from __future__ import annotations

import hashlib

CYPHER_READ_GRAMMAR = r'''
root ::= ws? match_clause (ws (match_clause | with_clause))* ws return_clause (ws order_clause)? (ws limit_clause)? ws? ";"?

match_clause ::= ("OPTIONAL " ws?)? "MATCH " ws? pattern (ws? "," ws? pattern)* (ws where_clause)?
pattern ::= node (ws? rel ws? node)*
node ::= "(" ws? ident ws? ")" | "(" ws? ident? ws? ":GRBenchNode" ws? "{" ws? "_workspace_id" ws? ":" ws? "$workspace_id" (ws? "," ws? prop_kv)* ws? "}" ws? ")"
prop_kv ::= ident ws? ":" ws? value
rel ::= "-[" rel_body "]->" | "<-[" rel_body "]-" | "-[" rel_body "]-"
rel_body ::= ws? ident? ws? ":GRBENCH_REL" (ws? "{" ws? "relation_name" ws? ":" ws? value ws? "}")? ws?

where_clause ::= "WHERE " ws? condition
condition ::= bool_term (ws ("AND" | "OR") ws bool_term)*
bool_term ::= ("NOT " ws?)? (comparison | "(" ws? condition ws? ")")
comparison ::= value ws? cmp_op ws? value | value " IN " list | value " IS NULL" | value " IS NOT NULL" | value " CONTAINS " value | value " STARTS WITH " value | value " ENDS WITH " value
cmp_op ::= "=" | "<>" | "<=" | ">=" | "<" | ">" | "=~"
list ::= "[" ws? (value (ws? "," ws? value)*)? ws? "]"

with_clause ::= "WITH " ws? ("DISTINCT " ws?)? proj_list (ws order_clause)? (ws limit_clause)? (ws where_clause)?
return_clause ::= "RETURN " ws? ("DISTINCT " ws?)? proj_list
proj_list ::= proj (ws? "," ws? proj)*
proj ::= value (" AS " ident)?
order_clause ::= "ORDER BY " ws? order_item (ws? "," ws? order_item)*
order_item ::= value (" ASC" | " DESC")?
limit_clause ::= "LIMIT " [0-9]+

value ::= func_call | property | string | number | param | list | "*" | ident
property ::= ident "." ident
func_call ::= ident "(" ws? ("DISTINCT " ws?)? (value (ws? "," ws? value)*)? ws? ")"
string ::= "'" ([^'\\] | "\\" [^\n])* "'" | "\"" ([^"\\] | "\\" [^\n])* "\""
number ::= "-"? [0-9]+ ("." [0-9]+)?
param ::= "$" ident
ident ::= [A-Za-z_] [A-Za-z0-9_]*
ws ::= [ \t\n]+
'''

CYPHER_READ_GRAMMAR_DIGEST = hashlib.sha256(CYPHER_READ_GRAMMAR.encode("utf-8")).hexdigest()

# Sample statements the grammar must accept (the shapes the S1 agents wrote in labs 013–021).
GRAMMAR_POSITIVE_SAMPLES = (
    "MATCH (p:GRBenchNode {_workspace_id: $workspace_id, feature_title: 'phase separation dynamics'})-[:GRBENCH_REL {relation_name: 'author'}]->(a:GRBenchNode {_workspace_id: $workspace_id}) RETURN a.feature_name AS author",
    "MATCH (p:GRBenchNode {_workspace_id: $workspace_id}) WHERE p.feature_title CONTAINS 'magnetodisk' RETURN p.id AS id, p.feature_title AS title LIMIT 10",
    "MATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'Hiroshi Furukawa'})-[:GRBENCH_REL {relation_name: 'paper'}]->(p:GRBenchNode {_workspace_id: $workspace_id}) RETURN count(DISTINCT p) AS papers",
    "MATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'Karoly Szego'})-[:GRBENCH_REL {relation_name: 'paper'}]->(p:GRBenchNode {_workspace_id: $workspace_id})-[:GRBENCH_REL {relation_name: 'author'}]->(c:GRBenchNode {_workspace_id: $workspace_id}) WHERE c.id <> a.id AND p.feature_year = '1990' RETURN DISTINCT c.feature_name AS collaborator",
    "MATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'Karoly Szego'})-[:GRBENCH_REL {relation_name: 'paper'}]->(p:GRBenchNode {_workspace_id: $workspace_id})-[:GRBENCH_REL {relation_name: 'author'}]->(c:GRBenchNode {_workspace_id: $workspace_id}) WHERE c.id <> a.id WITH c, count(p) AS shared ORDER BY shared DESC LIMIT 1 RETURN c.feature_name AS collaborator, shared",
    "MATCH (a:GRBenchNode {_workspace_id: $workspace_id}) WHERE toLower(a.feature_name) = toLower('Piet Hut') MATCH (a)-[:GRBENCH_REL {relation_name: 'paper'}]->(p:GRBenchNode {_workspace_id: $workspace_id}) OPTIONAL MATCH (p)<-[:GRBENCH_REL {relation_name: 'reference'}]-(q:GRBenchNode {_workspace_id: $workspace_id}) RETURN p.feature_title AS title, count(q) AS cited ORDER BY cited DESC LIMIT 1",
    "MATCH (p:GRBenchNode {_workspace_id: $workspace_id}) WHERE p.id IN ['3126056174', '1484949664'] RETURN p.id AS id, coalesce(p.feature_title, p.feature_name) AS name",
    "MATCH (p1:GRBenchNode {_workspace_id: $workspace_id, feature_title: 'a'})-[:GRBENCH_REL {relation_name: 'author'}]->(a:GRBenchNode {_workspace_id: $workspace_id})<-[:GRBENCH_REL {relation_name: 'author'}]-(p2:GRBenchNode {_workspace_id: $workspace_id, feature_title: 'b'}) RETURN a.feature_name AS author",
    "MATCH (p:GRBenchNode {_workspace_id: $workspace_id, id: '3126056174'})-[r:GRBENCH_REL]->(m:GRBenchNode {_workspace_id: $workspace_id}) RETURN r.relation_name AS relation, count(*) AS degree",
    "MATCH (v:GRBenchNode {_workspace_id: $workspace_id}) WHERE v.node_type = 'venue' AND v.feature_name STARTS WITH 'physical review' RETURN v.feature_name AS venue LIMIT 5;",
    "MATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: \"O'Brien\"})-[:GRBENCH_REL {relation_name: 'paper'}]->(p:GRBenchNode {_workspace_id: $workspace_id}) WITH collect(p.feature_title) AS titles RETURN titles, size(titles) AS n",
)
GRAMMAR_NEGATIVE_SAMPLES = (
    "MATCH (p:GRBenchNode) RETURN p",                                    # label without workspace scope
    "MATCH (p:GRBenchNode {_workspace_id: $workspace_id}) SET p.x = 1 RETURN p",  # write clause
    "CALL db.labels() YIELD label RETURN label",                          # procedure call
    "MATCH (p:Paper {_workspace_id: $workspace_id}) RETURN p",           # foreign label
    "MATCH (p:GRBenchNode {_workspace_id: $workspace_id}) RETURN p UNION MATCH (q:GRBenchNode {_workspace_id: $workspace_id}) RETURN q",
    "MATCH (p:GRBenchNode {_workspace_id: $workspace_id}) DETACH DELETE p",
)

__all__ = ["CYPHER_READ_GRAMMAR", "CYPHER_READ_GRAMMAR_DIGEST", "GRAMMAR_NEGATIVE_SAMPLES", "GRAMMAR_POSITIVE_SAMPLES"]
