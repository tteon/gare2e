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

from seocho.eval.experiment_observability import experiment_identity


def get(url: str) -> str:
    with urlopen(url, timeout=2) as response:
        return response.read().decode()


def counter(metrics: str, name: str) -> float:
    values = re.findall(r'^' + re.escape(name) + r'(?:_total)?(?:\{[^\n]*\})? ([0-9.eE+-]+)', metrics, re.M)
    return sum(float(value) for value in values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--collector', default='/usr/local/bin/otelcol-contrib')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a workload command is required after --')
    os.environ['SEOCHO_EXPERIMENT_OBSERVABILITY'] = '1'
    identity = experiment_identity()
    endpoint = os.environ.get('SEOCHO_OBSERVABILITY_ENDPOINT', '')
    if not re.fullmatch(r'(localhost|127\.0\.0\.1):[0-9]{1,5}', endpoint):
        parser.error('SEOCHO_OBSERVABILITY_ENDPOINT must be a local SSH tunnel host:port')
    version = subprocess.check_output([args.collector, '--version'], text=True).strip()
    if version != 'otelcol-contrib version 0.119.0':
        parser.error('Collector 0.119.0 is required; upgrade the tested pin deliberately')
    sockets = []
    try:
        for port in (4317, 4318, 13133, 8888):
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
    config = Path(__file__).resolve().parents[2] / 'examples/observability/otel-vast.yaml'
    status = {**identity, 'started_unix_s': time.time(), 'outcome': 'incomplete',
              'collector_version': version,
              'collector_config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
              'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest()}
    collector = workload = None
    interrupted = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        if workload is not None and workload.poll() is None:
            os.killpg(workload.pid, signum)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with (artifacts / 'collector.log').open('x') as log:
            collector = subprocess.Popen([args.collector, '--config=' + str(config)], env=env, stdout=log, stderr=log)
            deadline = time.monotonic() + 30
            while True:
                if interrupted or collector.poll() is not None:
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
                if interrupted or collector.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Collector did not receive a metrics scrape')
                time.sleep(0.2)
            workload = subprocess.Popen(command, env=env, start_new_session=True)
            while workload.poll() is None:
                if collector.poll() is not None:
                    os.killpg(workload.pid, signal.SIGTERM)
                    raise RuntimeError('collector exited during workload')
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
                delivered = all(
                    counter(metrics, 'otelcol_exporter_sent_' + kind) >= counter(metrics, 'otelcol_receiver_accepted_' + kind) > 0
                    for kind in ('spans', 'metric_points')
                )
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
        if workload is not None and workload.poll() is None:
            os.killpg(workload.pid, signal.SIGTERM)
            try:
                workload.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(workload.pid, signal.SIGKILL)
                workload.wait()
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
