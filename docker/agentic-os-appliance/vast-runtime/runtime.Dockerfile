# Fast rebuild from the already verified dependency layer. See base.Dockerfile
# to rebuild that layer from upstream rather than using the private registry.
ARG RUNTIME_BASE=jayyun98/gare2e-vast@sha256:e6a806183f35a58de0de5e7c6f6d27d45652801d1d44317723ce28edc28fdf29
FROM otel/opentelemetry-collector-contrib:0.119.0 AS collector
FROM ${RUNTIME_BASE}
USER root
COPY --from=collector /otelcol-contrib /usr/local/bin/otelcol-contrib
WORKDIR /opt/gare2e-pilot/source
COPY pyproject.toml README.md ./
COPY src/ src/
COPY runtime/ runtime/
COPY extraction/ extraction/
COPY examples/ examples/
COPY scripts/ scripts/
COPY docker/agentic-os-appliance/ docker/agentic-os-appliance/
RUN python -m pip install --no-cache-dir -c /opt/gare2e-pilot/constraints.txt . \
    && python -m pip install --no-cache-dir boto3==1.40.76 openai-agents==0.22.1 \
    && npm ci --prefix scripts/benchmarks/pi_native --ignore-scripts --omit=dev \
    && python -c 'import importlib.metadata as m; assert m.version("vllm")=="0.31.0"; assert m.version("torch")=="2.13.0"' \
    && node -e 'if(require("./scripts/benchmarks/pi_native/node_modules/@earendil-works/pi-coding-agent/package.json").version!=="0.80.10")process.exit(1)'
COPY docker/agentic-os-appliance/vast-runtime/start.sh docker/agentic-os-appliance/vast-runtime/serve.sh docker/agentic-os-appliance/vast-runtime/healthcheck.py docker/agentic-os-appliance/vast-runtime/download-model.py docker/agentic-os-appliance/vast-runtime/model-pin.json /opt/gare2e-pilot/
ENV PYTHONPATH=/opt/gare2e-pilot/source/src:/opt/gare2e-pilot/source/docker/agentic-os-appliance/vast-runtime \
    VLLM_NO_USAGE_STATS=1
RUN chmod 755 /opt/gare2e-pilot/*.sh
RUN python docker/agentic-os-appliance/vast-runtime/verify-install.py
ARG SOURCE_REVISION=unrecorded
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.title="gare2e-vast-runtime" \
      org.gare2e.vllm.version="0.31.0" org.gare2e.pi.version="0.80.10"
HEALTHCHECK --interval=30s --timeout=10s --start-period=10m --retries=3 CMD ["python", "/opt/gare2e-pilot/healthcheck.py"]
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/opt/gare2e-pilot/start.sh"]
