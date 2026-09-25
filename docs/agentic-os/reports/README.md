# Lab report pages (content-free HTML, also published as Claude artifacts)

[Three-category characterization chartbook](characterization-charts/index.html)
adds selectable Runtime / Tool / Agent figures for30 measured lab024/lab025 conditions.
[Definitions and source reports](characterization-charts/README.md).

| file | labs | artifact |
|---|---|---|
| lab-s11-wider-cohort.html | 013 | https://claude.ai/code/artifact/04719482-b362-477d-b154-769afb6bb263 |
| experiment-0-observability-baseline.html | 014 | https://claude.ai/code/artifact/408db85a-26ac-4341-ac61-985310a48b70 |
| schema-context-axis.html | 015 | https://claude.ai/code/artifact/d9af04dd-cc57-409e-8d9e-9a35f11f8441 |
| hops-vs-queries.html | 016 · 017 · 018 | https://claude.ai/code/artifact/6853722a-06db-4090-8074-5f184cee079d |
| agentic-os-lab-synthesis.html | 001–016 synthesis (Korean) | https://claude.ai/code/artifact/61658b1c-152b-42a3-9ab3-bf14e506e4f4 |
| serving-axes-report.html | 019 · 020 · 021 · 022 (Korean) | https://claude.ai/code/artifact/30a1e812-1e31-4426-8fd9-ba71bfd82189 |

Pages are self-contained (inline CSS/SVG, Google Fonts only). Numbers come from the `session-summary/*.md`
tables under `traces/<run>/` and the `## Result` sections of `docs/agentic-os/lab/requests/*.md`.

Lab024: [linked Agent / Runtime / Tool report](lab024-characterization/index.html),
[findings and provenance](lab024-characterization/README.md). Self-contained HTML,
full content-free CSV.gz exports, no external UI dependencies.

Lab024 continuation: [TP/grammar three-view report](lab024-continuation/index.html),
[findings, trace gap and replica imbalance](lab024-continuation/README.md). Five
additional comparisons complete the executed27-condition matrix. Sessions remain
separate because host/runtime conditions differ.

Lab027: [query evidence platform and exact-reuse pilot](lab027-graph-evidence/README.md).
Fixed-query parity passed; agent comparison sessions had no cache hits. Includes
observability coverage, costs and verified teardown, without a throughput claim.
