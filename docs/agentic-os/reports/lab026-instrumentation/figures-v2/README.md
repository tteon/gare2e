# Lab026 characterization charts

These Runtime / Tool / Agent figures use the five measured conditions in
[the source report](../README.md). [chart-data.json](chart-data.json) records
numeric data and source hashes. Failed K128 has no valid window and is absent
from throughput curves. Baseline-r1 is one same-host sweep, not a confidence
interval; the recovery run is kept separate after changing client thread policy.

Task distributions include only tasks fully inside the common measurement
window. Throughput uses engine output-counter deltas and two physical GPUs.
Request times overlap; summing them does not yield GPU busy time. Final
termination is not scored answer correctness. Missing measurements stay unknown.

Regenerate with `render_agentic_os_characterization_figures.py --source
<lab026-instrumentation> --output <new-directory>`; existing exports are preserved.
