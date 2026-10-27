# Optional clean dependency rebuild; never includes model weights or credentials.
FROM node:22-bookworm-slim AS node
FROM pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential python3.12-venv openjdk-21-jre-headless curl ca-certificates procps tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && python -m venv /opt/gare2e-pilot/venv
ENV PATH=/opt/gare2e-pilot/venv/bin:${PATH} \
    CUDA_HOME=/opt/gare2e-pilot/venv/lib/python3.12/site-packages/nvidia/cu13
COPY docker/agentic-os-appliance/vast-runtime/constraints.txt /opt/gare2e-pilot/constraints.txt
RUN pip install --no-cache-dir -c /opt/gare2e-pilot/constraints.txt \
    vllm==0.31.0 neo4j==5.28.2 nvidia-cuda-nvcc==13.0.88 \
    nvidia-cuda-crt==13.0.88 nvidia-nvvm==13.0.88 duckdb pyarrow pyoxigraph \
    && ln -s lib "$CUDA_HOME/lib64" \
    && ln -s libcudart.so.13 "$CUDA_HOME/lib/libcudart.so"
RUN curl -fL --retry 2 https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz -o /tmp/neo4j.tar.gz \
    && echo 'ad8ac3398606145502b8f489530bbd39333707ae4668156af16b6086b5b037d7  /tmp/neo4j.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/neo4j.tar.gz -C /opt/gare2e-pilot \
    && rm /tmp/neo4j.tar.gz
