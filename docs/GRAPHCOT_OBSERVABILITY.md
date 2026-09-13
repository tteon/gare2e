# GraphCoT 실험 계측 운영

GraphCoT 실험은 기존 SEOCHO OTel을 사용한다. `AgentExecutionRecorder`와
`LLMRequestTimer`는 실행 횟수와 클라이언트 시간을 요약하며 OTel을 대체하지
않는다. private request/response와 도구 결과는 기존 S3 uploader가 보관한다.

## 중앙 스택

임대 GPU 밖의 상시 호스트에서 실행한다. 개발용 anonymous Admin 대신
로그인을 활성화하고 비밀번호를 별도 권한 제한 파일로 전달한다.

```bash
export SEOCHO_GRAFANA_PASSWORD_FILE=/secure/grafana-password
export SEOCHO_BIND_HOST=127.0.0.1
docker compose -f examples/observability/docker-compose.observability.yml \
  -f examples/observability/docker-compose.experiment.yml \
  --profile observability up -d
```

Collector 0.119.0, Tempo 2.7.2, Prometheus 2.55.1, Grafana 11.5.1을 사용한다.
Collector의 큐와 나머지 저장소는 Docker volume에 남는다. GPU에서 이 호스트로
SSH tunnel을 연결하고 로컬 포트(예: `127.0.0.1:14317`)를 중앙 4317에 연결한다.
OTLP·DB·vLLM 포트를 인터넷에 공개하지 않는다. 중앙 volume은 호스트 삭제를
견디는 백업이 아니므로 호스트 수명과 보관 기간을 별도로 관리한다.

## Vast에서 한 run 실행

기존 `docker/agentic-os-appliance/Dockerfile`에 Collector 바이너리와 SEOCHO
`otel` extra를 포함했다. 기존 이미지 사용 시 새 이미지를 빌드해 digest를
기록해야 한다. 기존 인스턴스의 serving 설정을 임의로 변경하지 않는다.
Vast SSH/Jupyter 모드는 image ENTRYPOINT를 바꿀 수 있으므로 기존 bootstrap이
entrypoint를 실제 호출하는지 확인한다. 환경 변수는 필요한 이름만 전달한다.

```bash
export SEOCHO_PROJECT_ID=graphcot
export SEOCHO_CAMPAIGN_ID=20260913-observability
export SEOCHO_EXPERIMENT_ID=00-smoke
export SEOCHO_RUN_ID=20260913T000000Z-baseline-r1
export SEOCHO_OBSERVABILITY_ENDPOINT=127.0.0.1:14317
export SEOCHO_VLLM_METRICS_TARGET=127.0.0.1:8000
export SEOCHO_PRIVATE_TRACE_CONFIG=/secure/private-trace-config.json

python scripts/benchmarks/run_observed_experiment.py \
  --artifacts /artifacts/20260913T000000Z-baseline-r1-observability \
  -- bash docker/agentic-os-appliance/run_lab_pass.sh
```

vLLM 요청도 같은 trace에 연결하려면 새 배포의 기존 arm 인자에
`--otlp-traces-endpoint http://127.0.0.1:4317`을 추가한다. 기존
`VLLM_EXTRA_ARGS_OVERRIDE`/restart 경로를 사용하며 serving 소유자가 적용한다.
상세 진단 run에만 `--collect-detailed-traces all`을 추가한다. vLLM 0.27.1은
이 옵션의 blocking/성능 비용을 명시하므로 비교 arm에 일관되게 적용하고
별도 overhead를 측정한다. PyTorch kernel-level profile은 별도 실험으로 남긴다.
[고정 버전 설정](https://github.com/vllm-project/vllm/blob/v0.27.1/vllm/config/observability.py)

`PASS_ID`, `SEOCHO_E2E_PHASE`와 workload 입력은 기존 runner 계약대로 설정한다.
이미 존재하는 artifacts 경로와 점유된 계측 포트는 거부한다. 동시에 두 run을
실행하지 않는다. wrapper는 Collector readiness, 실제 `/metrics` snapshot,
workload 종료 코드, 전송 큐 drain을 확인한다. Collector 장애 시 workload를
종료하고 불완전 상태를 남긴다. 임대·삭제 작업은 수행하지 않는다.

private config의 기존 split/selection/serving pin 검증은 그대로 필요하다.
관측 모드에서 S3 경로는
`<authorized_prefix>/private/<project>/<campaign>/<experiment>/<run>/calls/`
이며 local spool도 같은 네 ID로 분리된다. SHA 이름의 JSONL·manifest를 보관하고
manifest에 native trace ID와 object key를 넣는다. ID에 경로 구분자·`..`를
허용하지 않는다. 기존 비관측 모드의 경로와 결과 형식은 유지한다.

GTSQA의 두 runner와 GRBench/Cypher Agents SDK runner가 private 업로드를
연결한다. GRBench 원문은 보관하지만 source split/graph provenance 검증을
완료하기 전에는 `external_source_provenance_unverified`로 training eligibility를
거부한다. SPARQL 수집을 Cypher 품질 결과로 대체하지 않는다.

## 조회와 해석

Grafana의 **GraphCoT 실험** 대시보드와 Tempo Explore를 사용한다.
`{ span.run_id = "run ID" }`로 실행을 찾고 private manifest의 `trace_id`로
직접 조회할 수 있다. case → LLM → HTTP attempt와 SDK 도구의 native span이
연결된다. raw 데이터와 예외 메시지는 export하지 않는다. 호출 경로에 추가한
W3C trace header는 요청 body·sampling·generation 설정을 바꾸지 않는다.

HTTP attempt span은 send→response headers 경계다. 논리 LLM span은 SDK 반환까지
포함하며 retry를 포함할 수 있다. streaming token 시간이나 GPU 시간과 같지 않다.
도구의 private event에는 당시 활성 span ID가 들어가며, 직접 ReAct 경로에서는
실행 전에 생성된 event의 ID가 case 부모를 가리킬 수 있다. 도구 실행 자체는
별도의 `graphcot.tool` span으로 조회한다.

vLLM의 queue/prefill/decode·KV·spec 지표는 서버 집계다. 동시 실행에서 이를
개별 호출 시간으로 역산하지 않는다. 실제 버전의 metric 이름과 HELP를 저장한
snapshot에서 확인한다. 미지원 값은 unavailable로 남긴다. 요청별 GPU 진단은
고정 vLLM 버전의 server tracing 지원을 검증하거나 별도 sampled profiling
run으로 수행한다. PyTorch/CUDA profiling을 매 token 동기화로 구현하지 않는다.

기존 CPU/GPU/DozerDB sampler와 run artifact를 계속 사용한다. 서비스 로그는
private run artifact로 보관하며 중앙 로그 전문 검색 서비스는 추가하지 않았다.
collector.log에도 운영 정보가 있으므로 artifacts 디렉터리를 비공개로 유지한다.

## 종료와 복구

`observability-status.json`의 `outcome=ok`는 workload 종료와 Collector 큐
drain만 의미한다. private S3 검증이나 Tempo 조회 성공을 의미하지 않는다.
`private_delivery_verified=false`는 별도의 uploader receipt 확인이 필요하다는
뜻이다. 완료 판정에는 private `delivery-status.json`의 pending/failed=0,
SHA 검증 receipt, 실제 Tempo 조회가 추가로 필요하다.

전송 장애 후에는 artifacts의 `otel-queue`를 지우지 않는다. 동일 binary와
`otel-vast.yaml`, 원래 `SEOCHO_OTEL_QUEUE_DIR`를 지정하여 Collector만 재시작하면
재전송한다. private 수집은 writer 종료 후 기존 uploader `--recover`로 재개한다.
run ID를 바꾸어 원본 spool을 다른 run으로 업로드하지 않는다. recovery가
끝나기 전 인스턴스를 삭제하면 디스크의 미전송 데이터는 복구할 수 없다.
GPU 수명은 master registry의 해당 소유자가 결정한다.

## 검증 명령

```bash
python -m pytest -q tests/seocho/test_graphcot_observability.py \
  tests/seocho/test_agentic_graph_agents.py tests/seocho/test_private_trace_store.py
SEOCHO_TEST_OTELCOL=/path/to/otelcol-contrib python -m pytest -q \
  tests/seocho/test_graphcot_collector_live.py
bash scripts/ci/run_basic_ci.sh
```

첫 검사는 OTel SDK 계약, 두 번째는 실제 Collector와 테스트 OTLP/metrics
endpoint 사이의 전송 복구를 검증한다. 테스트 endpoint는 실제 vLLM·Tempo가
아니므로 GPU 성능이나 전체 배포 검증의 증거가 아니다. 본 실험 전에 실제
모델·DB·중앙 스택의 한 사례 smoke와 tracing off/on overhead 확인을 수행한다.
