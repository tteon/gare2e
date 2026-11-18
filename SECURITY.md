# GARE2E Security Policy

This policy covers the graph-agent experiment harness, evidence importers and
result viewer in this repository. The inherited SEOCHO package/version does not
establish a separate GARE2E release-support or compliance commitment.

## Reporting a vulnerability

Use the repository's Security tab to submit a private vulnerability report when
that feature is enabled. If it is unavailable, arrange a private contact with
the repository owner through their published GitHub profile contact information.
Do not put credentials, private datasets or exploit payloads in a public issue.

Include the source commit, affected component, reproduction steps and potential
impact. Share a minimal redacted example rather than original model/provider or
experiment payloads. This project does not publish a response-time SLA.

## Result viewer and imports

- The viewer binds to loopback by default. Non-loopback use requires
  `GARE2E_PLATFORM_TOKEN`; the operator supplies HTTPS at a proxy.
- The shared token is an access boundary, not per-user SSO or RBAC. Import owner
  names are provenance, not authenticated identities.
- Imports use the existing allowlists, directory-boundary checks and checksums.
  Diagnostic attachments require contributor review; checksums do not establish
  that an attachment contains no sensitive content.
- Keep the registry private and mount it read-only in viewer deployments.
  Importing or browsing a run does not upload it to another observability service.

See [the platform contract](docs/agentic-os/EXPERIMENT_PLATFORM.md) and
[trace sharing](docs/agentic-os/TRACE_SHARING.md) for exact behavior and limits.

## Experiment data and services

Keep credentials, provider responses, raw prompts/answers, exact query inputs,
connection addresses and unreviewed profiler captures outside Git and shared
exports. Use bounded owned-resource lifecycle controls for live runs and retain
private artifact permissions. External model, DB and cloud services have their
own security/configuration requirements; the local viewer does not certify them.

The repository makes no blanket claim of encryption-at-rest, production
hardening, regulatory compliance or security certification. Review the actual
services and deployment being used. Unrelated standalone SDK issues should be
reported to [upstream SEOCHO](https://github.com/tteon/seocho) through its current
private reporting channel.
