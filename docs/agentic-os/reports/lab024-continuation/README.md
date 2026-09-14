# Lab 024 continuation — TP 배치와 K8 grammar 실측

[인터랙티브 보고서](index.html)에서 조건과 task를 선택하면 Agent Execution,
Runtime Performance, Tool Call을 같은 시간축에서 확인할 수 있습니다.
[이전 22개 조건의 결과](../lab024-characterization/README.md)와 합쳐 총 27개
비교 조건을 측정했습니다. 호스트와 runtime 환경이 달라 세션은 분리합니다.
[비교 CSV](../../../../traces/lab-s23b/session-summary/characterization.csv),
[파일 해시·수집 검증 manifest](../../../../traces/lab-s23b/session-summary/manifest.json).

## 관측 결과

| 조건 | K | output tok/s/GPU | task/s | task당 입력 tokens | task당 출력 tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| TP=2 control | 64 | 439.59 | 1.873 | 4,734.37 | 347.65 |
| 2×TP=1, sticky router | 64 | 273.73 | 1.240 | 4,217.91 | 322.79 |
| Grammar 제약 0%, CPU16 | 8 | 139.43 | 0.732 | 2,276.84 | 325.19 |
| Grammar 제약 50%, CPU16 | 8 | 91.73 | 0.574 | 2,019.33 | 302.93 |
| Grammar 제약 100%, CPU16 | 8 | 70.64 | 0.371 | 2,018.24 | 302.32 |

처리량은 warmup을 제외한 공통 구간 약88.7–89.2초의 engine 확정 출력 counter와
완료 task 수로 계산하며, 각 구간에서 실제 관측된 물리 GPU 2개로 나눴습니다.
Task당 토큰 평균은 구간에 완전히 포함된 task만 사용하므로 위 task/s와 모집단이
다릅니다. 포함 task 수는 TP2 103, replicas2 57, grammar 순서대로57/43/25입니다.
각 조건 1회 순서대로 실행한 탐색 결과이며, 신뢰구간이나 semantic accuracy는 없습니다.

TP2보다 sticky replicas2의 output tok/s/GPU가37.7%, task/s가33.8% 낮았습니다.
그러나 두 replica의 평균 running request가47.60 대13.33으로 불균형했습니다.
이 결과는 현재 sticky routing과 workload의 결과이며, 균형 잡힌 TP 배치의 본질적
우열이나 모든 동시성에서의 최적 설정을 뜻하지 않습니다. 생성 길이와 완료 task
구성도 달라 동일 출력량을 고정한 GPU kernel 비교가 아닙니다.

K8 grammar는 제약비율 증가에 따라 처리량이 감소했습니다. 요청별 decode 중앙값은
2.89/4.39/6.52초, TPOT 중앙값은25.83/41.32/53.07ms였습니다. 포함 task에서 실제
제약 query 비율은0/55.91/100%로 기록됐습니다. Grammar 내부 CPU step 시간은
미측정이므로 전체 차이를 grammar CPU 비용 하나로 단정할 수 없습니다.
이전 K64 CPU16의462.96/135.19/66.22 tok/s/GPU와 비교할 수 있으나, 별도 호스트와
CUDA 호환 환경에서 실행했으므로 순수한 K 효과를 분리한 비교는 아닙니다.

## 세 가지 관측 범주

| 관측 범주 | 이번에 확보한 결과 | 해석 범위 |
| --- | --- | --- |
| Agent Execution | 1,156 task, 호출 수·실제 입력/출력·repair·종료, task clock/ID | 완료나 final은 정답 판정이 아님 |
| Runtime Performance | 5,077 client requests, 같은 구간 tok/s/GPU·task/s, GPU·server 공급/KV 샘플 | 3,158개만 server span과 정확히 연결; 아래 replica 누락 참조 |
| Tool Call | 4,285 tool events, 기록된 tool ID·부모 request·query shape·rows/bytes·오류·단계 시간 | lexical query 형태이며 DB 실행 계획 아님; bytes는 tokens가 아님 |

모든1,156 task가 명시적 case clock/task ID를, 모든5,077 요청이 step ID와
UTF-8 context 구성 bytes를, 모든4,285 tool이 명시적 ID/부모 ID/shape를 기록했습니다.
포함 cohort의 Cypher683건은 모두 형태를 분류했습니다. System/schema·기존 생성·조회
결과·오류 등의 context 구성은 **bytes**이며 구성별 토큰 수로 변환하지 않았습니다.
출력 요청 종류는 tool-call 응답 전체와 메시지 응답을 나누며, Cypher 인자나 reasoning
토큰만의 양은 미측정입니다. PROFILE은 throughput 구간에 삽입하지 않았습니다.

### Runtime 연결 누락과 수정

TP2와 grammar의 포함 cohort859개 요청은 모두 trace_id + client span_id로 정확히
연결됩니다. 전체 pass에서는 이 네 조건의3,159개 중3,158개가 연결되며, 누락1개는
측정 cohort 밖에 있습니다. 반면 replicas2의1,918개 요청은 모두 정확한 부모 연결이
없습니다(포함 cohort229개 포함). 서버 span 자체는 존재하지만 배포된 router의
header allowlist가 traceparent/tracestate를 전달하지 않아 parent가 없습니다.

따라서 replica 요청별 queue/TTFT/prefill/decode는 **미측정**으로 유지합니다.
시간이나 토큰 수로 추측해 연결하지 않습니다. Client latency, tool 시간, 공통 창의
engine counters와 replica별 metrics는 별도로 유효합니다. W3C header 전달 수정과
회귀 테스트는 종료 후 로컬에서 완료했으며, 새 live replica 실행으로 검증하지는
않았습니다. 향후 수집에는 content-free router receipt JSONL도 포함하도록 수정했습니다.

### Replica별 공급·KV

| Backend | 공통 창 전체 관측 | 샘플 수 | 평균 running requests | 평균 KV fraction | 최대 KV fraction |
| --- | --- | ---: | ---: | ---: | ---: |
| 0 | 예 | 89 | 47.60 | 0.6477 | 0.7212 |
| 1 | 예 | 89 | 13.33 | 0.1695 | 0.2326 |

두 backend의 KV fraction 합은 전체 GPU KV 사용률이 아닙니다. 보고서는 합계임을
명시하고 global fraction은 미측정으로 둡니다. 별도 read-only observer가 각
backend에 1초 주기로 GET을 하나씩 추가했습니다. 시작/끝과 최대2.5초 gap 조건으로
두 backend 모두 공통 측정 창 전체 coverage를 확인했습니다. 샘플링 추가 비용은
이번 측정 환경의 일부입니다. Observer는 workload나 routing 정책을 변경하지 않았습니다.

TP2의 공통 구간 최대 KV는17.98%, 모든 조건의 preemption counter 증가는0입니다.
포함 cohort의 최대 실제 입력은3,062 tokens입니다. Replica0의 더 높은 KV 점유만으로
용량 부족이나 eviction을 주장할 수 없습니다. 실제 decode sequence 수, GPU kernel
공백, grammar step CPU, DB page-cache residency/GC, KV offload 전송은 미측정입니다.
긴 context·재사용 간격·offloading·DB/KV DRAM 배분 실험은 이번 범위에 포함되지 않았습니다.

## 조건과 증거

- lab-s23b, 2026-09-14 20:59:41–21:54:49 KST. Gate105 및5개 비교 모두 exit0.
- A100 SXM4 80GB ×2, NVLink300,38 logical CPUs, host RAM515966MB.
  Grammar는 CPU affinity16; TP 비교는 같은 호스트의 기본 CPU 배분.
- Driver570.172.08, pinned image의 CUDA13 forward compatibility 사용.
  두 GPU BF16 matmul, bidirectional peer, 실제 compat libcuda mapping/driver API13000
  live probe 및 vLLM/Dozer readiness/실제 graph trace gate 통과.
- vLLM0.27.1 image ID
  `sha256:0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967`.
  Qwen/Qwen3.6-27B revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`, bf16.
- MAPLE Physics90, balanced40-repeat backlog, graph digest
  `bc267591dc3d7aaf8c46262e1effa24b2cc2362b66f967d9f686ca70413efa5e`.
  Workspace grbench-maple-physics, single agent/S1/non-reasoning.
- Neo4j5.26.3/Dozer, runtime SHA256
  `b15a6e6c9dacac1a35c804220e2e16e0e1dc3805d006c3ab4d69f39365916751`.
  Heap8g/pagecache24g 고정; co-located 서비스, 별도 서버 실험 아님.
- Max_model_len32768/max_num_seqs128/max_num_batched_tokens8192, prefix cache,
  max6 turns/output2048 per request/tokenbudget16384 per task.
  Persistent workers, 매 worker 첫 완료 후10초 warmup,90초 dispatch 후 drain.
- 배포 source42ed5bd1, v55 SHA256
  `48d05226e7a8533a2fa562f6aecd811f5568a532349b1330f73932cc28871504`.
  추가 observer SHA256
  `db21bba777c78249ea2eeaad438d71b3c1e2abc404bafcf2411a9151478aca3a`.

Essential archive3,974,580bytes/2,409files의 원격·로컬 SHA256 일치 및 추출 완료
receipt를 보존했습니다. SHA256은
`dd5aa025f879acddd79f78074c6f11f37fc2fd39e6facb195e9692d35219bac7`입니다.
전송 archive는 성공적인 추출 후 collector가 삭제하며 원본 추출 파일을 유지합니다.
`--collect-bulk essential-only`로 bulk 로그 전체 복사는 생략됐습니다. 따라서 원본
lifecycle은 `_PartialArtifacts`/`artifact_copy_verified=false`로 남습니다. 이는
정상 완료된 실험이나 검증된 essential 수집의 실패를 의미하지 않으며 전체 bulk가
보존됐다는 주장도 하지 않습니다. 추출 증거와 lifecycle을 수정하지 않았습니다.

소유 instance51011956은 부모 controller가 종료했고 provider 목록에서 부재를
검증했습니다. Watchdog도 parent_teardown_verified_absent로 정상 종료했습니다.
3,307.95초 × disk-inclusive $2.09037037/h = **추가 약$1.9208**(최종 청구서 아님).
승인된 추가$5.40/90분 이내이며 lab024 합계 약$14.0435, 이전 전체 누계 포함 약$116.84.
다른 인스턴스는 수정하지 않았습니다.

## 재생성·검증

```sh
PYTHONPATH=src uv run python scripts/benchmarks/summarize_agentic_os_characterization.py \
  --run-root "$RUN_ROOT" --output-dir "$DERIVED_ROOT"
```

새 디렉터리에 집계하며 원본 receipt를 덮어쓰지 않습니다. 보고서의 CSV.gz는
content-free 전체 task/request/tool 기록입니다. 공개 파일에 prompts/answers/query
원문, credentials, provider payload, 접속 주소를 포함하지 않았습니다.
분석 구조는 [착안 논문 §4–6](https://arxiv.org/pdf/2605.26297)을 따르되, 논문의
resident-cache 조건을 이 실험의 offloading/포화 증거로 사용하지 않습니다.

최종 로컬 검증: appliance/report focused36 tests와 Ruff 통과. JavaScript
`node --check` 및 headless Chrome에서 TP2/replica 화면 각각11개 표·1개 SVG를
검증했습니다. Basic CI는1112 passed/3 skipped이나, 마지막 identity check가
기존 SSH fixture5건을 계속 거부해 전체 green은 아닙니다. Mock/로컬 검사는
계측 계약 검증이며 새로운 GPU 성능이나 live trace 수정 검증이 아닙니다.
