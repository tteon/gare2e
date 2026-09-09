#!/usr/bin/env python3
"""Minimal request router in front of N vLLM replicas (session B, lab request 008).

The appliance normally runs one vLLM (TP=2).  In replica mode
``restart_vllm.sh`` starts one TP=1 vLLM per GPU on ports ``base+1..base+N``
and this router on ``base`` so the workload keeps talking to one endpoint.

Policies (``--policy``):
  rr      per-request round robin.  Every turn of a case may land on a
          different replica, so a case's growing prefix is re-prefilled unless
          the replicas share KV (the LMCache arm).
  sticky  per-case: replica = sha256(first two message contents) mod N.  All
          turns of one case stay on one replica, so local prefix caching works
          and cross-replica sharing only helps the common system prefix.

Forwarding: ``POST /v1/chat/completions`` (routed); ``POST /reset_prefix_cache``
(fan-out to every replica); ``GET /metrics`` (Prometheus text merged by summing
samples with identical name+labels across replicas, so pass-level counter
deltas stay valid); everything else goes to replica 0.

Receipts: one JSON line per routed request in ``--receipt`` with policy,
replica index, status, latency and a digest of the routing key — never the
messages themselves.  Only aiohttp (shipped with vLLM) is required.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

SAMPLE_RE = re.compile(r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{[^}]*\})?\s+(?P<value>\S+)(?:\s+(?P<ts>\S+))?$")


def routing_key(body: dict[str, Any]) -> str:
    """Stable per-case key: digest of the first two message contents (system + first user)."""
    msgs = body.get("messages") or []
    parts = []
    for m in msgs[:2]:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            c = json.dumps(c, sort_keys=True)
        parts.append(str(c or ""))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()


def merge_metrics(texts: list[str]) -> str:
    """Sum Prometheus samples with identical name+labels across replicas; keep the first replica's HELP/TYPE."""
    meta: dict[str, list[str]] = {}
    order: list[str] = []
    sums: dict[tuple[str, str], float] = defaultdict(float)
    seen: set[tuple[str, str]] = set()
    sample_order: list[tuple[str, str]] = []
    for text in texts:
        for line in text.splitlines():
            if not line.strip():
                continue
            if line.startswith("#"):
                bits = line.split(None, 3)
                if len(bits) >= 3 and bits[1] in ("HELP", "TYPE"):
                    fam = bits[2]
                    if fam not in meta:
                        meta[fam] = []
                        order.append(fam)
                    if bits[1] == "HELP" and not any(x.startswith("# HELP") for x in meta[fam]):
                        meta[fam].append(line)
                    if bits[1] == "TYPE" and not any(x.startswith("# TYPE") for x in meta[fam]):
                        meta[fam].append(line)
                continue
            m = SAMPLE_RE.match(line)
            if not m:
                continue
            key = (m.group("name"), m.group("labels") or "")
            try:
                sums[key] += float(m.group("value"))
            except ValueError:
                continue
            if key not in seen:
                seen.add(key)
                sample_order.append(key)
    out: list[str] = []
    emitted_meta: set[str] = set()
    for name, labels in sample_order:
        fam = re.sub(r"_(bucket|sum|count|total|created|info)$", "", name)
        for cand in (name, fam):
            if cand in meta and cand not in emitted_meta:
                out.extend(meta[cand])
                emitted_meta.add(cand)
        val = sums[(name, labels)]
        out.append(f"{name}{labels} {val:g}" if val != int(val) or abs(val) > 1e15 else f"{name}{labels} {int(val)}")
    return "\n".join(out) + "\n"


class Router:
    def __init__(self, backends: list[str], policy: str, receipt: Path | None) -> None:
        self.backends = backends
        self.policy = policy
        self.receipt = receipt
        self.counter = 0
        self.session: ClientSession | None = None
        self.lock = asyncio.Lock()
        if receipt:
            receipt.parent.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        self.session = ClientSession(timeout=ClientTimeout(total=None, sock_connect=30))

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    def pick(self, body: dict[str, Any]) -> tuple[int, str]:
        n = len(self.backends)
        if self.policy == "sticky":
            key = routing_key(body)
            return int(key[:8], 16) % n, key
        idx = self.counter % n
        self.counter += 1
        return idx, ""

    def record(self, row: dict[str, Any]) -> None:
        if self.receipt:
            with self.receipt.open("a", encoding="utf-8") as h:
                h.write(json.dumps(row, sort_keys=True) + "\n")

    async def chat(self, request: web.Request) -> web.StreamResponse:
        raw = await request.read()
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        idx, key = self.pick(body)
        t0 = time.monotonic()
        assert self.session
        async with self.session.post(f"{self.backends[idx]}{request.rel_url}", data=raw, headers={"Content-Type": request.headers.get("Content-Type", "application/json")}) as r:
            payload = await r.read()
            status = r.status
            ctype = r.headers.get("Content-Type", "application/json")
        self.record({
            "schema_version": "seocho.agentic_os.vllm_router_receipt.v1",
            "policy": self.policy,
            "replica": idx,
            "status": status,
            "latency_ms": round((time.monotonic() - t0) * 1000, 3),
            "started_unix_s": round(time.time() - (time.monotonic() - t0), 3),
            "routing_key_sha256": key[:16] if key else None,
            "request_bytes": len(raw),
            "response_bytes": len(payload),
            "stream": bool(body.get("stream")),
        })
        return web.Response(body=payload, status=status, content_type=ctype.split(";")[0])

    async def fanout(self, request: web.Request) -> web.StreamResponse:
        raw = await request.read()
        assert self.session
        results = []
        for b in self.backends:
            async with self.session.post(f"{b}{request.rel_url}", data=raw) as r:
                results.append({"backend": b, "status": r.status, "body": (await r.text())[:2000]})
        ok = all(x["status"] < 300 for x in results)
        return web.json_response({"success": ok, "replicas": results}, status=200 if ok else 502)

    async def metrics(self, request: web.Request) -> web.StreamResponse:
        assert self.session
        texts = []
        for b in self.backends:
            try:
                async with self.session.get(f"{b}/metrics") as r:
                    if r.status == 200:
                        texts.append(await r.text())
            except Exception:  # noqa: BLE001 - one dead replica must not hide the others
                continue
        if not texts:
            return web.Response(status=503, text="no replica metrics\n")
        merged = merge_metrics(texts)
        merged += f"seocho_router_replicas_up {len(texts)}\nseocho_router_replicas_total {len(self.backends)}\n"
        return web.Response(text=merged, content_type="text/plain")

    async def passthrough(self, request: web.Request) -> web.StreamResponse:
        assert self.session
        raw = await request.read()
        async with self.session.request(request.method, f"{self.backends[0]}{request.rel_url}", data=raw or None) as r:
            payload = await r.read()
            return web.Response(body=payload, status=r.status, content_type=r.headers.get("Content-Type", "application/json").split(";")[0])

    async def health(self, request: web.Request) -> web.StreamResponse:
        assert self.session
        up = 0
        for b in self.backends:
            try:
                async with self.session.get(f"{b}/health") as r:
                    up += int(r.status == 200)
            except Exception:  # noqa: BLE001
                pass
        return web.json_response({"replicas_up": up, "replicas_total": len(self.backends), "policy": self.policy}, status=200 if up == len(self.backends) else 503)


def build_app(router: Router) -> web.Application:
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/v1/chat/completions", router.chat)
    app.router.add_post("/v1/completions", router.chat)
    app.router.add_post("/reset_prefix_cache", router.fanout)
    app.router.add_get("/metrics", router.metrics)
    app.router.add_get("/health", router.health)
    app.router.add_route("*", "/{tail:.*}", router.passthrough)

    async def on_start(_: web.Application) -> None:
        await router.start()

    async def on_stop(_: web.Application) -> None:
        await router.stop()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--backend", action="append", required=True, help="http://127.0.0.1:8001 (repeat per replica)")
    ap.add_argument("--policy", choices=("rr", "sticky"), default="sticky")
    ap.add_argument("--receipt", type=Path, help="content-free per-request routing receipt JSONL")
    args = ap.parse_args()
    router = Router(args.backend, args.policy, args.receipt)
    web.run_app(build_app(router), host=args.host, port=args.port, print=None, access_log=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
