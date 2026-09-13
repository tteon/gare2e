#!/usr/bin/env bash
# lab-s5 watch: admission → instance → remote execution → quiet queue progress → unit end.
cd /home/hadry/lab/seocho-room/seocho
S=/tmp/claude-1000/-home-hadry-lab-seocho-room-seocho/db552673-9a6b-4d1a-ba7f-91867fa1e658/scratchpad
UNIT=seocho-vast-lab-s19; PFX=lab-s19-try-
prev_state=""; prev_q=""; ticks=0; URL=""
while true; do
  R=$(ls -td .seocho/benchmarks/agentic-os/colocated-appliance/runs/${PFX}* 2>/dev/null | head -1)
  if ! systemctl --user is-active --quiet $UNIT; then
    echo "$(date +%H:%M:%S) unit ended; receipt: $(python3 -c "import json;d=json.load(open('$R/vast-colocated-lifecycle.json'));print(d.get('state'),'instance=',d.get('instance_id'),'destroy_absent=',d.get('destroy_verified_absent'),'copy=',d.get('artifact_copy_verified'))" 2>/dev/null)"; break
  fi
  if [ -n "$R" ] && [ -f "$R/vast-colocated-lifecycle.json" ]; then
    st=$(python3 -c "import json;d=json.load(open('$R/vast-colocated-lifecycle.json'));o=d.get('offer') or {};s=d.get('startup') or {};print('state=%s instance=%s dph=%s host=%s geo=%s'%(d.get('state'),d.get('instance_id'),o.get('dph_total'),s.get('host_id'),s.get('geolocation')))" 2>/dev/null)
    case "$st" in *"instance=None"*) st="";; esac
    if [ -n "$st" ] && [ "$st" != "$prev_state" ]; then echo "$(date +%H:%M:%S) $(basename $R) $st"; prev_state="$st"; fi
    if [[ "$st" == *"state=remote_execution"* ]]; then
      if [ -z "$URL" ]; then ID=$(python3 -c "import json;print(json.load(open('$R/vast-colocated-lifecycle.json')).get('instance_id'))"); URL=$(timeout 60 vastai ssh-url "$ID" 2>/dev/null); echo "$URL" > "$S/lab-s19.sshurl"; fi
      PORT=${URL##*:}; HOST=${URL#ssh://}; HOST=${HOST%%:*}
      q=$(timeout 60 ssh -p "$PORT" -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new "$HOST" 'tail -1 /artifacts/appliance-phases.private.tsv 2>/dev/null | cut -f2-3 | tr "\t" "="; cut -d, -f1,3,4 /artifacts/queue-receipt.jsonl 2>/dev/null | tr -d "{}\"" | tr "\n" ";"; echo; c=0; for d in /artifacts/run-*/e2e; do c=$((c+$(grep -c complete $d/progress.tsv 2>/dev/null || echo 0))); done; echo "lanes_complete=$c"' 2>/dev/null | grep -v "Welcome\|Have fun" | tr '\n' ' ')
      key=${q%% lanes_complete*}
      if [ -n "$key" ] && [ "$key" != "$prev_q" ]; then echo "$(date +%H:%M:%S) appliance: $q"; prev_q="$key"; fi
      ticks=$((ticks+1)); if [ $((ticks % 30)) -eq 0 ]; then echo "$(date +%H:%M:%S) heartbeat: $q"; fi
      sleep 120; continue
    fi
  fi
  sleep 60
done
