# Lab session launchers and helpers (labs 013–022)

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
