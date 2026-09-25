# ADR-0228: Graph query evidence in the existing experiment platform

Status: Accepted
Date: 2026-09-25
Bead: seocho-ix30

## Context

Graph-agent optimization must connect semantic decisions to actual DB attempts,
plans, quality and hardware observations. Existing gare2e already has execution,
OTel trace export and a result viewer. Missing plan counters could be collapsed
to zero, while tool calls could include several physical queries.

## Decision

Extend the existing executor and immutable platform. Record opted-in query
attempts, distinct physical submissions, errors and reuse with exact trace and
query identities. Preserve nullable counters and plan topology. Diagnostic
PROFILE is a separate execution scope. Begin with explicitly snapshot-bound,
session-local exact-result reuse; general rewrites need separate proof gates.

OTel is the common trace/log contract, Prometheus aggregates bounded metrics,
Tempo stores traces, Grafana links operational views, and LangSmith is an
optional evaluation/OTel destination. The experiment UI owns comparison and
quality contracts. A Linux loopback companion is optional; SaaS upload is never
implicit. Private query inputs remain outside shared registry artifacts.

## Consequences

Operators can inspect query failures, reuse provenance, plans and missing
quality in the existing platform. Historical evidence remains immutable and
unverified comparisons remain blocked. No inference from DB Hits to physical
I/O or from tool duration to GPU kernel activity is allowed. Backend and pilot
validation are reported separately from general performance claims.
