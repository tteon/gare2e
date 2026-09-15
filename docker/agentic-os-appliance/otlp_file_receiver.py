#!/usr/bin/env python3
"""Minimal OTLP/gRPC trace receiver that appends spans to a JSONL file.

The Vast appliance cannot reach a collector on the operator's machine, so for
live validation (lab 010) the instance runs this receiver on 127.0.0.1:4317:
vLLM (``--otlp-traces-endpoint``) and the receipt exporter both push to it and
the file leaves the instance with the other artifacts.  One JSON line per span:
trace/span/parent ids (hex), name, kind, start/end ns, status, attributes and
the resource ``service.name`` — enough to check that server-side vLLM spans
nest under the runners' client spans in the same trace.

Only opentelemetry-proto and grpcio are needed; both ship with the vLLM image.
"""
from __future__ import annotations

import argparse
import json
import signal
import threading
from concurrent import futures
from pathlib import Path

import grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc

_KINDS = {0: "UNSPECIFIED", 1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}
_STATUS = {0: "UNSET", 1: "OK", 2: "ERROR"}


def _value(v):  # AnyValue -> python
    which = v.WhichOneof("value")
    if which is None:
        return None
    if which == "array_value":
        return [_value(x) for x in v.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _value(kv.value) for kv in v.kvlist_value.values}
    return getattr(v, which)


class _Receiver(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.count = 0
        path.parent.mkdir(parents=True, exist_ok=True)

    def Export(self, request, context):  # noqa: N802 - grpc servicer name
        lines = []
        for rs in request.resource_spans:
            resource = {a.key: _value(a.value) for a in rs.resource.attributes}
            for ss in rs.scope_spans:
                for s in ss.spans:
                    lines.append(json.dumps({
                        "schema_version": "seocho.otlp_file_receiver_span.v1",
                        "service_name": resource.get("service.name"),
                        "scope": ss.scope.name,
                        "trace_id": s.trace_id.hex(),
                        "span_id": s.span_id.hex(),
                        "parent_span_id": s.parent_span_id.hex() or None,
                        "name": s.name,
                        "kind": _KINDS.get(s.kind, str(s.kind)),
                        "start_unix_ns": s.start_time_unix_nano,
                        "end_unix_ns": s.end_time_unix_nano,
                        "status": _STATUS.get(s.status.code, str(s.status.code)),
                        "attributes": {a.key: _value(a.value) for a in s.attributes},
                    }, default=str))
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + ("\n" if lines else ""))
            self.count += len(lines)
        return trace_service_pb2.ExportTraceServiceResponse()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", default="127.0.0.1:4317")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4), options=[("grpc.max_receive_message_length", 64 * 1024 * 1024)])
    receiver = _Receiver(args.output)
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(receiver, server)
    server.add_insecure_port(args.listen)
    server.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    server.stop(grace=5).wait()
    print(json.dumps({"spans_received": receiver.count, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
