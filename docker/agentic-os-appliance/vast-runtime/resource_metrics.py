"""Expose the existing appliance samplers; never collect prompt/query content."""
from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

from prometheus_client import Gauge, start_http_server
from seocho.eval.agentic_os_observability import HostProcessSampler, NvidiaSmiSampler


def service_pids() -> dict[str, int]:
    roles = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            argv = (path / 'cmdline').read_bytes().split(b'\0')
            if any(b'org.neo4j.server.CommunityEntryPoint' in x for x in argv):
                roles['neo4j'] = int(path.name)
            if len(argv) > 1 and argv[1].endswith(b'/vllm') and b'serve' in argv:
                roles['vllm'] = int(path.name)
        except (OSError, ProcessLookupError):
            continue
    return roles


def main() -> None:
    root = Path(os.environ['SEOCHO_RESOURCE_ARTIFACTS'])
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    gpu = NvidiaSmiSampler(root / 'gpu.csv', interval_s=5)
    host = HostProcessSampler(root / 'host-process.csv', roles={"sampler":os.getpid(),**service_pids()}, interval_s=5, process_scope="process_tree",
                              graph_system_interval_s=5)
    utilization = Gauge('gare2e_gpu_utilization_pct', 'Existing nvidia-smi sampler percent', ['gpu'])
    memory = Gauge('gare2e_gpu_memory_used_bytes', 'Existing nvidia-smi sampler bytes', ['gpu'])
    cpu = Gauge('gare2e_process_cpu_seconds_total', 'Cumulative process-tree CPU seconds', ['role'])
    rss = Gauge('gare2e_process_rss_bytes', 'Sampled process-tree RSS bytes', ['role'])
    failures = Gauge('gare2e_resource_sampler_errors_total', 'Cumulative sample exceptions')
    last = Gauge('gare2e_resource_sample_unix_seconds', 'Last successful sampler loop')
    start_http_server(19100, addr='127.0.0.1')
    error_count = 0
    while not stop.is_set():
        try:
            current = {"sampler": os.getpid(), **service_pids()}
            if current != host.roles:
                host = HostProcessSampler(root / 'host-process.csv', roles=current, interval_s=5,
                                          process_scope='process_tree', graph_system_interval_s=5)
            for row in gpu.sample_once():
                label = str(row.get('gpu_index', row.get('index', 'unknown')))
                if row.get('gpu_utilization_pct') is not None:
                    utilization.labels(label).set(row['gpu_utilization_pct'])
                if row.get('memory_used_bytes') is not None:
                    memory.labels(label).set(row['memory_used_bytes'])
            for row in host.sample_once():
                role = row['role']
                # Field names are fixed by the existing sampler, no inferred values.
                if row.get('process_cpu_user_s') is not None and row.get('process_cpu_system_s') is not None:
                    cpu.labels(role).set(row['process_cpu_user_s'] + row['process_cpu_system_s'])
                if row.get('process_rss_bytes') is not None:
                    rss.labels(role).set(row['process_rss_bytes'])
            last.set(time.time())
        except Exception:
            error_count += 1
            failures.set(error_count)
        stop.wait(5)


if __name__ == '__main__':
    main()
