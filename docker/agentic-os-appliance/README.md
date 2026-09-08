# AgenticOS co-located Vast appliance

This image is the single-host experimental topology for the graph-agent paper:
vLLM, DozerDB, the agent workload and content-free samplers run on one Vast
instance. It is intentionally distinct from SEOCHO's production compose
deployment and from the split-host pilot.

The entrypoint starts DozerDB and vLLM, waits for both readiness gates, and
then runs the command supplied after the image name through
`run_agentic_os_colocated_workload.py`. The wrapper writes host-process, GPU,
local vLLM metric and private workload-log artifacts under
`SEOCHO_APPLIANCE_ARTIFACTS` without synchronously scraping metrics in the
request critical path.

Required environment variables are `VLLM_MODEL`, `VLLM_MODEL_REVISION` and
`NEO4J_PASSWORD`. Set `SEOCHO_GRBENCH_IMPORT_DIR` to a directory holding the
`export_grbench_import_csv.py` output (`grbench-nodes.csv.gz`,
`grbench-rels.csv.gz`, `grbench-import-manifest.json`) to bulk-import the
GRBench projection offline with `neo4j-admin database import` before DozerDB
starts; the e2e then only verifies counts. Without it the e2e projects
`graph.json` through Bolt under `SEOCHO_GRBENCH_LOAD_TIMEOUT_S` (default
1800 s), which is too slow for MAPLE/Physics on a paid instance.
`SEOCHO_GRBENCH_WORKSPACE_ID` (default `grbench-maple-physics`) must match the
manifest and is forwarded to every Text2Cypher runner.
Optional `VLLM_CPUSET`, `DOZERDB_CPUSET` and `SEOCHO_SAMPLE_INTERVAL_S` make
CPU placement and sampler cadence explicit in the run manifest. Copy and
checksum the artifact directory before destroying the paid instance.
