# A single-host experimental appliance, not the production SEOCHO deployment.
# It deliberately keeps vLLM, DozerDB, agent runtime and telemetry on one Vast
# instance so CPU contention and GPU feeding can be measured without SSH in the
# request path.  Pin both parent images in the experiment manifest.
ARG VLLM_IMAGE=vllm/vllm-openai:v0.27.1
ARG DOZERDB_IMAGE=graphstack/dozerdb:5.26.3.0

FROM otel/opentelemetry-collector-contrib:0.119.0 AS otelcol
FROM ${DOZERDB_IMAGE} AS dozerdb
FROM ${VLLM_IMAGE}

USER root
COPY --from=otelcol /otelcol-contrib /usr/local/bin/otelcol-contrib

# Reuse the tested DozerDB distribution and JRE rather than downloading an
# unpinned graph database at experiment startup.  The appliance entrypoint
# invokes the upstream startup script so NEO4J_AUTH semantics remain intact.
COPY --from=dozerdb /var/lib/neo4j /var/lib/neo4j
COPY --from=dozerdb /opt/java/openjdk /opt/java/openjdk
COPY --from=dozerdb /startup /startup

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl procps util-linux tini \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/opt/java/openjdk \
    NEO4J_HOME=/var/lib/neo4j \
    PATH=/var/lib/neo4j/bin:/opt/java/openjdk/bin:${PATH} \
    VLLM_PORT=8000 \
    NEO4J_BOLT_PORT=7687 \
    SEOCHO_APPLIANCE_ARTIFACTS=/artifacts

WORKDIR /opt/seocho
COPY . /opt/seocho
RUN python -m pip install --no-cache-dir ".[otel]" \
    && chmod +x /opt/seocho/docker/agentic-os-appliance/entrypoint.sh \
    && mkdir -p /artifacts /data /logs /plugins

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/seocho/docker/agentic-os-appliance/entrypoint.sh"]
