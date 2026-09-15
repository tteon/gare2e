# Lab session launchers and helpers

Start with [the portable harness guide](../../../docs/agentic-os/HARNESS.md) and
[experiment AGENTS.md](../../../docs/agentic-os/AGENTS.md). Read results or
regenerate charts without cloud access.

## Current throughput harness (labs024–025)

`launch_lab_s22.py` supports the original queue, `--continuation`, and
`--replica-audit`. It accepts `--workspace`, `--bundle`, `--preflight` and a fresh
`--run-root`; it validates private local inputs before provider operations.
Inspect `--help` or the session runner `--print-plan` first. Prior lab budgets
and source hashes are historical evidence, not defaults for a new paid run.

The canonical queue is `../run_agentic_os_throughput_session.py`. The
[component map](../../../docs/agentic-os/HARNESS.md#harness-components) links
trace gates, metric sampling, report exports and the chart renderer.

## Historical launchers (labs013–022)

These preserve the parameters used for past runs. Audit their paths, input
hashes and resource bounds before adapting one; they are not turnkey launch commands.

- `launch_lab_sNN.sh` — the exact fail-closed launcher used for session lab-sNN (offer query, NVLink rule,
  uploads, bundle version, queue file, remote command). Run as
  `systemd-run --user --unit seocho-vast-lab-sNN --property=KillMode=process --collect bash launch_lab_sNN.sh`.
  They reference `$SCRATCH`-free repo paths; adjust `RUNS`/bundle versions before reuse.
- `watch_lab_sNN.sh` — poller: admission → instance → remote queue progress → unit end; from lab-s20 it also
  starts `spec_scrape.sh` on the appliance.
- `spec_scrape.sh` — content-free /metrics scrape (spec_decode*, generation_tokens) every 2 s on the appliance.
- `zero_row_ab.py` — scratch version of `../summarize_agentic_os_zero_row_ab.py`.
- `grbench_entity_profile.py` — lab 018 entity/anchor profile of the GRBench questions (offline).
- `rehearsal/` — throwaway DozerDB tiny graph loader + scripted mock vLLM servers used to rehearse the hop
  lane and the grammar lane locally (`docker run … graphstack/dozerdb:5.26.3.0` on 7699).
