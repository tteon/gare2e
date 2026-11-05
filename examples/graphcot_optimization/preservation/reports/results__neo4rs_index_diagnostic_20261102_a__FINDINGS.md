> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/neo4rs_index_diagnostic_20261102_a/FINDINGS.md`; SHA256 `829527d3372a21ea5d4a570c68f13599f70e723b4209c3069f98a7f039258d9c`.

# L4 index plan diagnostic

96 records: EXPLAIN32, PROFILE32, normal32 across index off/on and reversed orders. All PROFILE/normal results matched recorded expected rows. Each measured query had a per-case warmup; diagnostic timings remain separate from production cohorts.

**No Index operator appeared in any of the32 actual PROFILE plans.** The unchanged correlated L4 query did not use TRANSFER(ts) as its access path. Index-on timing associations therefore do not establish a direct relationship-index speedup. Schema-triggered recompilation/statistics, warm state and shared resource effects remain possible. Prefer the independently confirmed heap/cache profile without this unused index.

Clone graph label/type counts match the previous preserved baseline and remain identical before/after. Temporary gug_p96_index_diagnostic removed, owned DB port closed. Evidence zip SHA25660744884ec75de35181f3f5f10f29fe0026950c778bff4725bc096d993855080.
