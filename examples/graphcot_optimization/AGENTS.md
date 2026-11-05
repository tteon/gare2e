# Graph-CoT optimization example

Read README.md, TUTORIAL.md, RESULTS.md, PROTOCOL.md and VLLM_HANDOFF.md before
changing this example. Root AGENTS.md remains the repository contract.

- Keep this a self-contained teaching/example surface, not a new SDK or runtime backend default.
- `lab.py verify/demo` and the notebook require no DB/model/provider and prove only offline contracts.
- Preserve trusted workspace binding, exact64bit integer identifiers, independent oracle,
  template-only Cypher, explicit transaction/full result drain and failure denominators.
- Original copied source hashes are in evidence/source-manifest.json. Any modified source
  needs an explicit derived record; never silently relabel it as the measured original.
- Never publish credentials, private connection addresses, raw prompts/answers/logs or stores.
- Live DB/model/scale changes require the human operator's authorized resources and new run roots.
  Do not launch cloud jobs or stop peers based on tutorial examples.
- Keep precision/output-budget/backend differences visible. Do not call finite batches capacity.
- Test: `python3 -m unittest discover -s tests/seocho -p test_graphcot_optimization_example.py -v`
  from repository root; notebook/chart reproduction and exact remaining live gaps go in VALIDATION.md.
