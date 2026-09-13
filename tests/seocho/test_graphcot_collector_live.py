"""Real Collector transport/retry gate; test endpoints are not vLLM/Tempo evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.mark.skipif(not os.getenv('SEOCHO_TEST_OTELCOL'), reason='requires pinned Collector binary')
def test_collector_forwards_after_endpoint_recovers(tmp_path):
    import grpc
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2, metrics_service_pb2_grpc

    traces = []
    metrics = []

    class Sink(trace_service_pb2_grpc.TraceServiceServicer):
        def Export(self, request, context):
            traces.append(request)
            return trace_service_pb2.ExportTraceServiceResponse()

    class Metrics(metrics_service_pb2_grpc.MetricsServiceServicer):
        def Export(self, request, context):
            metrics.append(request)
            return metrics_service_pb2.ExportMetricsServiceResponse()

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'# TYPE vllm:generation_tokens_total counter\nvllm:generation_tokens_total 2\n')

        def log_message(self, *args):
            pass

    endpoint = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=endpoint.serve_forever, daemon=True).start()
    sink = grpc.server(ThreadPoolExecutor(max_workers=2))
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(Sink(), sink)
    metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(Metrics(), sink)
    port = sink.add_insecure_port('127.0.0.1:0')
    # The port is reserved but no requests are served until start().
    env = {**os.environ, 'SEOCHO_PROJECT_ID': 'graphcot', 'SEOCHO_CAMPAIGN_ID': 'test',
           'SEOCHO_EXPERIMENT_ID': 'transport', 'SEOCHO_RUN_ID': 'run-1',
           'SEOCHO_OBSERVABILITY_ENDPOINT': f'127.0.0.1:{port}',
           'SEOCHO_VLLM_METRICS_TARGET': f'127.0.0.1:{endpoint.server_port}'}
    root = Path(__file__).resolve().parents[2]
    script = tmp_path / 'workload.py'
    script.write_text('from seocho.eval.experiment_observability import experiment_span\n'
                      'with experiment_span("graphcot.case"):\n'
                      ' with experiment_span("graphcot.llm"):\n'
                      '  pass\n')
    process = subprocess.Popen([sys.executable, str(root / 'scripts/benchmarks/run_observed_experiment.py'),
        '--artifacts', str(tmp_path / 'run'), '--collector', os.environ['SEOCHO_TEST_OTELCOL'],
        '--', sys.executable, str(script)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(5)
        sink.start()
        stdout, stderr = process.communicate(timeout=55)
        assert process.returncode == 0, (stdout, stderr, (tmp_path / 'run/collector.log').read_text())
        status = json.loads((tmp_path / 'run/observability-status.json').read_text())
        assert status['collector_queue_drained'] is True
        assert status['private_delivery_verified'] is False
        spans = [span for request in traces for resource in request.resource_spans
                 for scope in resource.scope_spans for span in scope.spans]
        assert {span.name for span in spans} == {'graphcot.case', 'graphcot.llm'}
        assert len({span.trace_id for span in spans}) == 1
        assert metrics
        assert (tmp_path / 'run/vllm-metrics-before.prom').exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=30)
        sink.stop(0)
        endpoint.shutdown()
