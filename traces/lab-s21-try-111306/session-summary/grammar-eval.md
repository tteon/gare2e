
## grammar-none-s1-k8: cases=90 answered=46 terminals={'final': 46, 'unsupported_answer': 44}
  free/cypher            turns= 234 tpot_p50=26.1 ms  latency_p50=3440.8 ms  out_tok_p50=136.5  truncated@512=0
  free/message           turns=  90 tpot_p50=39.9 ms  latency_p50=260.3 ms  out_tok_p50=6.0  truncated@512=1
  first constrained turn latency: min=None p50=None max=None ms; zero-row events per case p50=0.0 sum=150

## grammar-onto-s1-k8: cases=90 answered=50 terminals={'unsupported_answer': 39, 'final': 50, 'budget_exceeded': 1}
  constrained/cypher     turns= 215 tpot_p50=49.4 ms  latency_p50=6622.0 ms  out_tok_p50=146  truncated@512=5
  free/message           turns=  89 tpot_p50=54.6 ms  latency_p50=486.0 ms  out_tok_p50=6  truncated@512=0
  first constrained turn latency: min=1330 p50=5859.5 max=112136 ms; zero-row events per case p50=0.0 sum=148

## grammar-xgr-s1-k8: cases=90 answered=48 terminals={'unsupported_answer': 41, 'final': 48, 'budget_exceeded': 1}
  constrained/cypher     turns= 219 tpot_p50=67.7 ms  latency_p50=8191.9 ms  out_tok_p50=141  truncated@512=5
  free/message           turns=  89 tpot_p50=79.6 ms  latency_p50=547.2 ms  out_tok_p50=6  truncated@512=0
  first constrained turn latency: min=1427 p50=6997.4 max=141459 ms; zero-row events per case p50=0.0 sum=154

## m9b-grammar-none-s1-k8: cases=90 answered=45 terminals={'final': 45, 'unsupported_answer': 45}
  free/cypher            turns= 238 tpot_p50=9.7 ms  latency_p50=1248.8 ms  out_tok_p50=133.5  truncated@512=4
  free/message           turns=  90 tpot_p50=17.5 ms  latency_p50=169.0 ms  out_tok_p50=6.0  truncated@512=0
  first constrained turn latency: min=None p50=None max=None ms; zero-row events per case p50=0.0 sum=127

## m9b-grammar-onto-s1-k8: cases=90 answered=48 terminals={'final': 48, 'unsupported_answer': 35, 'budget_exceeded': 7}
  constrained/cypher     turns= 226 tpot_p50=35.8 ms  latency_p50=5018.3 ms  out_tok_p50=137.0  truncated@512=31
  free/message           turns=  83 tpot_p50=38.4 ms  latency_p50=396.6 ms  out_tok_p50=6  truncated@512=0
  first constrained turn latency: min=797 p50=4612.8 max=84320 ms; zero-row events per case p50=0.0 sum=140
