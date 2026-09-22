# ADR-0227: Shared experiment results and trace inspection

Status: Accepted
Date: 2026-09-22
Scope: private gare2e experiment harness; no change to graph query behavior

## Context

Contributors need to compare engineering changes without opening every raw trace.
Existing characterization exports already separate Runtime, Tool and Agent, but
do not provide a shared registry, contributor provenance or all-task navigation.

## Decision

Use an immutable filesystem registry with an explicit workspace and owner per
import, source hashes, versioned numeric metric definitions, condition-level
comparison contracts and declared admission. Publish atomically under a lock.
The CLI is the writer; a FastAPI runtime shell exposes read-only results and a
browser UI following charts, individual results and evidence-linked insights.

Core normalization and analysis belong in `src/seocho/eval/experiment_platform.py`;
HTTP wiring and static UI belong under `runtime/`. Existing vendor-neutral
JSONL/OTel tracing and DozerDB/OpenAI Agents SDK assumptions remain unchanged.

Perfetto-compatible Chrome JSON exports contain actual task/request/tool intervals
with safe attributes and no invented GPU kernels or stage boundaries. Nsight
profiling remains a separate diagnostic source. Reviewed original reports and
exported SQLite files are checksummed attachments available through authenticated
downloads. Native profiler parsing, cross-clock event merging, SSO/RBAC, browser
uploads and job execution are not part of this first release.

## Consequences and validation

Contributors can add numeric metrics and new reviewed adapters without executing
code from result artifacts. Incompatible/missing contracts suppress percentages;
quality and statistical significance remain separate from throughput. Deployment
starts on loopback; shared binding requires a read token and team TLS proxy.
Owner labels are provenance rather than authenticated identity.

Unit/API tests cover immutable imports, workspace/auth boundaries, privacy,
comparison rules and trace units/overlap. Actual published artifact imports and
local HTTP/browser inspection validate the platform without new inference spend.
See [platform guide](../agentic-os/EXPERIMENT_PLATFORM.md) and
[ExecPlan](../agentic-os/PLATFORM_EXECPLAN.md) for remaining validation/deployment.
