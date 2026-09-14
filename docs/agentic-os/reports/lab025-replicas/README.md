# Lab025 — replica runtime 연결과 task affinity 실측

[세 범주 차트](../characterization-charts/index.html#condition=28)에서 lab025 조건을
선택하거나 [task별 연결 timeline](index.html)을 확인할 수 있습니다.
[비교 CSV](../../../../traces/lab-s24b/session-summary/characterization.csv),
[trace admission](../../../../traces/lab-s24b/session-summary/trace-admissions.json),
[router 배정/부하](../../../../traces/lab-s24b/session-summary/router-balance.json),
[수집 manifest](../../../../traces/lab-s24b/session-summary/manifest.json).

## 결과

| 조건 (K64) | output tok/s/GPU | task/s | task당 입력 tokens | task당 출력 tokens | 포함 task |
| --- | ---: | ---: | ---: | ---: | ---: |
| TP=2 | 426.09 | 1.887 | 4,395.81 | 321.69 | 106 |
| 2×TP=1, content sticky | 322.63 | 1.385 | 4,159.73 | 305.60 | 63 |
| 2×TP=1, task round-robin | 378.24 | 1.720 | 4,242.95 | 310.79 | 91 |

Task-rr의 output tok/s/GPU는 sticky보다17.24%, task/s는24.19% 높았습니다.
TP2보다는 각각11.23%,8.86% 낮았습니다. 같은 호스트에서 순차 실행한 단일 표본이며
confidence interval은 없습니다. 생성 길이·완료 task 구성·prefix hit도 달라 순수한
GPU kernel 속도 차이로 해석하지 않습니다. 정답 품질은 미채점입니다.

이전 lab024 replica는 W3C header 누락으로 stage 연결이0/1,918이었습니다.
이번 수정본은 **TP2 2,251/2,251, sticky 2,065/2,065, task-rr 2,016/2,016**
요청의 server parent와 queue/TTFT/prefill/decode를 정확히 연결했습니다.
Replica 요청은 router receipt도 전부 정확히 한 개 연결되고 두 backend를 관측했습니다.
각 설정의 단일 graph gate도 통과했습니다. 시간이나 token 수로 join을 추정하지 않았습니다.
과거 누락된 데이터는 그대로 미관측으로 남깁니다.

## Runtime Performance Characterization

| 조건 | 평균 running requests (backend0 / 1) | 평균 KV 사용률 (backend0 / 1) | prefill / decode 비중 |
| --- | --- | --- | --- |
| TP2 | 단일 engine; 차트 참고 | 단일 engine; 차트 참고 | 3.65% / 96.35% |
| Sticky replicas | 41.46 / 18.72 | 56.84% / 25.56% | 4.58% / 95.42% |
| Task-rr replicas | 30.26 / 29.11 | 38.42% / 36.48% | 4.23% / 95.77% |

두 replica의 표본은 각각89개이며 각 측정 창 전체를 덮습니다. 평균 running 요청의
불균형이 줄었습니다. Running은 실제 decode sequence 수와 동일한 계측이 아닙니다.
KV fraction 합계를 전체 사용률로 표시하지 않습니다. 모든 조건에서 관측 preemption은0,
최대 포함 request input은3,062 tokens입니다. 긴 context/offloading 효과를 검증한 조건이 아닙니다.

Stage 비중의 분모는 포함 task에서 정확히 연결된 요청들의 Σ(prefill+decode)입니다.
전체 task wall time이나 GPU 독점 시간이 아닙니다. Queue 중앙값은0이나 grammar CPU,
engine step별 decode batch, GPU kernel 사이 공백은 직접 계측하지 않았습니다.

## Tool Call / Agent Execution Characterization

총1,371 task,6,332 LLM requests,5,491 tool events를 저장했습니다. 세 조건의
측정 창에 완전히 포함된260 task /1,056 requests를 설명 차트에 사용합니다.
Tool 종류·query shape·평균 지연 상위3·오류율 상위3·LLM 진행도별 활동을 분리합니다.
0행은 정상 빈 결과이며 query shape는 DB 실행 계획이나 실제 disk read가 아닙니다.

Agent 차트는 LLM 호출과 tool-bearing round, peak input과 누적 input, 생성 응답
종류, 종료 유형, ordinal별 context 증가를 보여줍니다. `final`은 정답 성공이 아닙니다.
Task당 token 평균의 포함 cohort와 공통 구간 throughput counter의 모집단은 다릅니다.
Task-rr는 신규 trace ID의 첫 배정을 교대로 하고 같은 task의 후속 turn은 같은
backend로 보냅니다. 동일 질문의 서로 다른 task까지 같은 backend로 보내지는 않습니다.
비교 pass에서 sticky task 배정은232/212, task-rr는219/219이며 task affinity
위반은0이었습니다. Gate는 이 배정 수에서 제외했습니다. 배정 수가 같다고
생성량이나 GPU 실행 부하가 같다는 뜻은 아닙니다.

## 실행과 보존

- Run lab-s24b, source `1e0608de`, bundle SHA256
  `8a4c7aad21acc988cac8311df1b730585072ec7d45091eafda079bb69d073884`.
- A100 SXM4 80GB×2, NVLink300, host458442,32 effective CPU, 약513GB host RAM.
  Native driver595.71.05; 이번에는 CUDA forward-compat override를 사용하지 않았습니다.
- vLLM image0.27.1 (image SHA256
  `0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967`),
  Qwen/Qwen3.6-27B bf16 revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`.
  Neo4j5.26.3/Dozer runtime SHA256
  `b15a6e6c9dacac1a35c804220e2e16e0e1dc3805d006c3ab4d69f39365916751`, Physics90/S1,
  single agent/non-reasoning, graph digest
  `bc267591dc3d7aaf8c46262e1effa24b2cc2362b66f967d9f686ca70413efa5e`.
  Dozer heap8g/pagecache24g 고정, max_model_len32768, max_num_seqs128,
  max_num_batched_tokens8192. 별도 CPU affinity 제한 없음.
- 설정별 restart/readiness → graph/trace gate → warmup →90s dispatch + drain 순서.
  공통 inner window는88.82–89.56초. Gate와 해당 비교는 같은 engine을 유지하며,
  다음 serving/router 설정으로 바꿀 때 재시작합니다. TP2 config의 sticky 필드는
  미사용 기본값이며 단일 engine에는 replica router가 없습니다.
- Gate3개와 비교3개 exit0, remote command exit0. Essential archive2,853 files /
  5,324,095bytes, remote/local SHA256
  `e4ea6040b582b495964566a2cd099b50904138ddb6a7a2a06cf88a2c64b601f9` 일치,
  extraction complete. Bulk는 의도적으로 생략했으므로 lifecycle은
  `failed/_PartialArtifacts`, `artifact_copy_verified=false`로 보존합니다.
  성능/trace gate 실패가 아니라 essential-only 수집 정책의 상태입니다.
- 소유 instance51057078 종료/부재 확인, watchdog 종료와 provider 재조회도 확인.
  Create1789421771.2381241 → finish1789424143.9911594,2,372.75초.
  $2.38592593/h 기준 약**$1.5726**. 이전 extension$1.9208을 합쳐$3.4933/$5.40,
  총 누계는 종전 근사치 기준 약$118.42입니다. 최종 청구서가 아닌 시간당 요금 추정입니다.

로컬 원본은 `.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s24b`,
파생본은 그 안의 `characterization-v1`입니다. 원본 prompt/answer/log/자격증명은
Git에 올리지 않았습니다. 이어받을 때 HANDOFF§15와 request025를 먼저 읽으세요.
