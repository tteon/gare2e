#!/usr/bin/env python3
"""Run one experiment with a local persistent Collector; never rent/delete GPUs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time
from urllib.request import urlopen

def experiment_identity() -> dict[str, str]:
    """Use the existing explicit experiment identity contract."""
    pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
    identity = {key: os.getenv('SEOCHO_' + key.upper(), '') for key in
                ('project_id', 'campaign_id', 'experiment_id', 'run_id')}
    if any(not pattern.fullmatch(value) or '..' in value for value in identity.values()):
        raise ValueError('safe project/campaign/experiment/run IDs are required')
    return identity


def get(url: str) -> str:
    with urlopen(url, timeout=2) as response:
        return response.read().decode()


def counter(metrics: str, name: str) -> float:
    values = re.findall(r'^' + re.escape(name) + r'(?:_total)?(?:\{[^\n]*\})? ([0-9.eE+-]+)', metrics, re.M)
    return sum(float(value) for value in values)


COUNTERS = tuple('otelcol_' + prefix + '_' + kind
                 for prefix in ('receiver_accepted', 'exporter_sent')
                 for kind in ('spans', 'metric_points'))


def collector_snapshot(metrics: str) -> dict[str, float]:
    return {name: counter(metrics, name) for name in COUNTERS}


def delivery_delta(current: dict[str, float], baseline: dict[str, float]) -> bool:
    if any(current[name] < baseline[name] for name in COUNTERS):
        raise RuntimeError('Collector counters reset during run')
    delta = {name: current[name] - baseline[name] for name in COUNTERS}
    # Continuous scrapes and pre-baseline in-flight exports straddle snapshots.
    # Metrics require progress in both directions, not equal delta counts.
    # The caller separately requires an observed empty queue for three seconds;
    # this is transport progress, not per-run delivery proof (Tempo GET is separate).
    return (delta['otelcol_receiver_accepted_spans'] > 0
            and delta['otelcol_exporter_sent_spans'] >= delta['otelcol_receiver_accepted_spans']
            and delta['otelcol_receiver_accepted_metric_points'] > 0
            and delta['otelcol_exporter_sent_metric_points'] > 0)


def signal_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def cleanup_group(workload: subprocess.Popen, grace_s: float = 15) -> bool:
    """Bound cleanup of the owned session, including survivors of its leader."""
    def alive() -> bool:
        workload.poll()  # Reap the direct child even when descendants remain.
        try:
            os.killpg(workload.pid, 0)
            return True
        except ProcessLookupError:
            return False

    signal_group(workload.pid, signal.SIGTERM)
    deadline = time.monotonic() + grace_s
    while alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if alive():
        signal_group(workload.pid, signal.SIGKILL)
    workload.wait(timeout=5)
    deadline = time.monotonic() + 3
    while alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    return not alive()  # Unknown/still present must never be reported as reaped.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--collector', default='/usr/local/bin/otelcol-contrib')
    parser.add_argument('--collector-config', type=Path, default=Path(__file__).resolve().parents[2] / 'docker/agentic-os-appliance/vast-runtime/collector.yaml')
    parser.add_argument('--external-collector', action='store_true', help='Reuse the deployment-owned Collector; never stop it')
    parser.add_argument('--timeout-s', type=float, default=0, help='Workload deadline; 0 disables')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.timeout_s < 0:
        parser.error('--timeout-s must be nonnegative')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a workload command is required after --')
    os.environ['SEOCHO_EXPERIMENT_OBSERVABILITY'] = '1'
    identity = experiment_identity()
    endpoint = os.environ.get('SEOCHO_OBSERVABILITY_ENDPOINT', '')
    if endpoint != 'otel-collector:4317' and not re.fullmatch(r'(localhost|127\.0\.0\.1):[0-9]{1,5}', endpoint):
        parser.error('SEOCHO_OBSERVABILITY_ENDPOINT must be loopback or the private Compose collector')
    version = subprocess.check_output([args.collector, '--version'], text=True).strip()
    if version != 'otelcol-contrib version 0.119.0':
        parser.error('Collector 0.119.0 is required; upgrade the tested pin deliberately')
    sockets = []
    try:
        for port in (() if args.external_collector else (4317, 4318, 13133, 8888)):
            probe = socket.socket()
            sockets.append(probe)
            probe.bind(('127.0.0.1', port))
    finally:
        for probe in sockets:
            probe.close()
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=False, mode=0o700)
    queue = artifacts / 'otel-queue'
    queue.mkdir(mode=0o700)
    env = {**os.environ, 'SEOCHO_TRACE_OTLP_ENDPOINT': 'http://127.0.0.1:4317',
           'SEOCHO_TRACE_CAPTURE_CONTENT': 'false', 'OTEL_SERVICE_NAME': 'graphcot-agent',
           'SEOCHO_OTEL_QUEUE_DIR': str(queue),
           'SEOCHO_VLLM_METRICS_TARGET': os.getenv('SEOCHO_VLLM_METRICS_TARGET', '127.0.0.1:8000')}
    config = args.collector_config.resolve()
    status = {**identity, 'started_unix_s': time.time(), 'outcome': 'incomplete',
              'collector_version': version, 'external_collector': args.external_collector,
              'collector_config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
              'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest()}
    collector = workload = None
    interrupted = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        if workload is not None and workload.poll() is None:
            signal_group(workload.pid, signum)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with (artifacts / 'collector.log').open('x') as log:
            if not args.external_collector:
                collector = subprocess.Popen([args.collector, '--config=' + str(config)], env=env, stdout=log, stderr=log)
            deadline = time.monotonic() + 30
            while True:
                if interrupted or (collector is not None and collector.poll() is not None):
                    raise RuntimeError('collector stopped before readiness')
                try:
                    get('http://127.0.0.1:13133/')
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('collector readiness timeout') from None
                    time.sleep(0.2)
            # Save the actual metric surface, not assumptions from latest docs.
            metrics = get('http://' + env['SEOCHO_VLLM_METRICS_TARGET'] + '/metrics')
            (artifacts / 'vllm-metrics-before.prom').write_text(metrics)
            deadline = time.monotonic() + 20
            while counter(get('http://127.0.0.1:8888/metrics'), 'otelcol_receiver_accepted_metric_points') == 0:
                if interrupted or (collector is not None and collector.poll() is not None) or time.monotonic() >= deadline:
                    raise RuntimeError('Collector did not receive a metrics scrape')
                time.sleep(0.2)
            baseline_metrics = get('http://127.0.0.1:8888/metrics')
            (artifacts / 'collector-before.prom').write_text(baseline_metrics)
            baseline = collector_snapshot(baseline_metrics)
            last_snapshot = baseline
            status['collector_counters_before'] = baseline
            workload = subprocess.Popen(command, env=env, start_new_session=True)
            workload_started = time.monotonic()
            next_health = workload_started
            while workload.poll() is None:
                if interrupted:
                    raise RuntimeError('workload interrupted')
                if args.timeout_s and time.monotonic() - workload_started >= args.timeout_s:
                    status['workload_timed_out'] = True
                    raise TimeoutError('workload deadline exceeded')
                if collector is not None and collector.poll() is not None:
                    raise RuntimeError('collector exited during workload')
                if args.external_collector and time.monotonic() >= next_health:
                    get('http://127.0.0.1:13133/')
                    current = collector_snapshot(get('http://127.0.0.1:8888/metrics'))
                    delivery_delta(current, last_snapshot)  # Fail closed on observed reset.
                    last_snapshot = current
                    next_health = time.monotonic() + 2
                time.sleep(0.2)
            status['workload_exit_code'] = workload.returncode
            # SDK atexit flush completes before workload exits. Wait for batch
            # admission before checking both traces and metrics export queues.
            deadline = time.monotonic() + 30
            empty_since = None
            while time.monotonic() < deadline:
                metrics = get('http://127.0.0.1:8888/metrics')
                (artifacts / 'collector-final.prom').write_text(metrics)
                values = re.findall(r'^otelcol_exporter_queue_size(?:\{[^\n]*\})? ([0-9.eE+-]+)', metrics, re.M)
                if interrupted:
                    raise RuntimeError('workload interrupted')
                current = collector_snapshot(metrics)
                delivery_delta(current, last_snapshot)
                last_snapshot = current
                delivered = delivery_delta(current, baseline)
                status['collector_counters_delta'] = {name: current[name] - baseline[name] for name in COUNTERS}
                if values and all(float(value) == 0 for value in values) and delivered:
                    empty_since = empty_since or time.monotonic()
                    if time.monotonic() - empty_since >= 3:
                        status['collector_queue_drained'] = True
                        break
                else:
                    empty_since = None
                time.sleep(0.5)
            status['outcome'] = 'ok' if workload.returncode == 0 and status.get('collector_queue_drained') and not interrupted else 'incomplete'
    except Exception as error:
        # Error messages/argv can contain private values. Keep only the class.
        status['error_class'] = type(error).__name__
    finally:
        if workload is not None:
            try:
                status['workload_group_empty'] = cleanup_group(workload)
            except (OSError, subprocess.TimeoutExpired) as error:
                status['workload_group_empty'] = False
                status['cleanup_error_class'] = type(error).__name__
            if not status['workload_group_empty']:
                status['outcome'] = 'incomplete'
        if collector is not None and collector.poll() is None:
            collector.terminate()
            try:
                collector.wait(timeout=15)
            except subprocess.TimeoutExpired:
                collector.kill()
                collector.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        status['finished_unix_s'] = time.time()
        status['private_delivery_verified'] = False  # Separate uploader receipts are required.
        (artifacts / 'observability-status.json').write_text(json.dumps(status, indent=2) + '\n')
    return 0 if status['outcome'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
