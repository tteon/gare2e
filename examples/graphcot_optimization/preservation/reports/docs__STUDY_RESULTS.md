> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/STUDY_RESULTS.md`; SHA256 `4e44096b378e9f4aa933e169e3981245cd50c9d7f0e6de9ec6a8ac188fa2eb31`.

# BF16 model/hardware & multi-user study

선택된 설정: **seq8**. 두 역순 블록의 정확도/throughput/p95 기준으로 선택했다. 전역 최적값이나 통계적 유의성을 증명한 것은 아니다.

**검증 완료: 38개 셀, 총 2,460건 중 정답 완료 2,345건, admission drop 115건, 실행/정답 실패 0건.** 모든 셀은 reference handoff/replay 반복 workload이며 free-form Cypher 품질 평가가 아니다.

선택 설정: A100 SXM4 80GB, BF16, TP1, max_num_seqs8, batch-token8192, OMP4, KV12GiB, max_model_len8192, speculative off. 원본 그래프4083nodes/27429edges는 변경하지 않았다.


## 설정 비교

| run | 정답/요청 | episode/s | E2E median ms | p95 ms | 평균 server queue ms |
|---|---:|---:|---:|---:|---:|
| tune-b0-batch2048/load | 48/48 | 1.298 | 5914.9 | 7639.9 | 1314.6 |
| tune-b0-control/load | 48/48 | 1.293 | 5795.5 | 7584.1 | 1312.2 |
| tune-b0-omp1/load | 48/48 | 1.298 | 5800.4 | 7646.9 | 1309.1 |
| tune-b0-seq8/load | 48/48 | 2.096 | 3454.4 | 5478.9 | 0.0 |
| tune-b1-batch2048/load | 48/48 | 1.311 | 5740.2 | 7644.9 | 1304.5 |
| tune-b1-control/load | 48/48 | 1.305 | 5838.2 | 7609.1 | 1306.3 |
| tune-b1-omp1/load | 48/48 | 1.309 | 5794.1 | 7584.5 | 1303.0 |
| tune-b1-seq8/load | 48/48 | 2.096 | 3428.8 | 5517.8 | 0.0 |

## 동시 사용자 수

| run | 정답/요청 | episode/s | E2E median ms | p95 ms | 평균 server queue ms |
|---|---:|---:|---:|---:|---:|
| closed-b0-c1 | 60/60 | 0.367 | 2624.1 | 4150.3 | 0.0 |
| closed-b0-c16 | 60/60 | 2.153 | 6788.5 | 9092.2 | 1480.3 |
| closed-b0-c2 | 60/60 | 0.695 | 2697.5 | 4322.0 | 0.0 |
| closed-b0-c4 | 60/60 | 1.260 | 2921.4 | 4705.0 | 0.0 |
| closed-b0-c8 | 60/60 | 2.111 | 3436.9 | 5647.5 | 0.0 |
| closed-b1-c1 | 60/60 | 0.367 | 2625.0 | 4088.9 | 0.0 |
| closed-b1-c16 | 60/60 | 2.139 | 6823.4 | 9014.7 | 1485.7 |
| closed-b1-c2 | 60/60 | 0.686 | 2704.5 | 4360.3 | 0.0 |
| closed-b1-c4 | 60/60 | 1.250 | 2965.9 | 4782.6 | 0.0 |
| closed-b1-c8 | 60/60 | 2.111 | 3418.2 | 5489.4 | 0.0 |

## 도착률 (각60초+drain)

| run | 정답/요청 | episode/s | E2E median ms | p95 ms | 평균 server queue ms |
|---|---:|---:|---:|---:|---:|
| open-b0-r0.5 | 30/30 | 0.495 | 2673.4 | 4305.2 | 0.0 |
| open-b0-r1 | 60/60 | 0.972 | 2861.2 | 4572.5 | 0.0 |
| open-b0-r2 | 120/120 | 1.921 | 3353.0 | 5426.7 | 0.7 |
| open-b0-r4 | 182/240 | 2.190 | 24582.7 | 29866.6 | 8812.5 |
| open-b1-r0.5 | 30/30 | 0.494 | 2649.8 | 4258.6 | 0.0 |
| open-b1-r1 | 60/60 | 0.971 | 2878.7 | 4587.6 | 0.0 |
| open-b1-r2 | 120/120 | 1.923 | 3358.4 | 5384.4 | 0.6 |
| open-b1-r4 | 183/240 | 2.218 | 24283.6 | 28341.7 | 8649.3 |

| run | drops | failures | max waiting | 중앙30초 완료/s | 중앙 도착 cohort median ms |
|---|---:|---:|---:|---:|---:|
| open-b0-r0.5 | 0 | 0 | 0 | 0.500 | 2648.0 |
| open-b0-r1 | 0 | 0 | 0 | 1.000 | 2883.4 |
| open-b0-r2 | 0 | 0 | 1 | 2.000 | 3327.3 |
| open-b0-r4 | 58 | 0 | 56 | 2.000 | 27107.6 |
| open-b1-r0.5 | 0 | 0 | 0 | 0.500 | 2590.9 |
| open-b1-r1 | 0 | 0 | 0 | 1.000 | 2865.5 |
| open-b1-r2 | 0 | 0 | 0 | 1.967 | 3362.4 |
| open-b1-r4 | 57 | 0 | 56 | 1.967 | 26808.0 |

## L1~L6 접근 패턴

| run | 정답/요청 | episode/s | E2E median ms | p95 ms | 평균 server queue ms |
|---|---:|---:|---:|---:|---:|
| access-b0-fanout | 48/48 | 0.875 | 7403.8 | 14413.5 | 0.0 |
| access-b0-reference | 48/48 | 2.103 | 3475.8 | 5627.0 | 0.0 |
| access-b0-selective | 48/48 | 1.459 | 3353.3 | 10337.1 | 0.0 |
| access-b1-fanout | 48/48 | 0.876 | 7346.9 | 14421.0 | 0.0 |
| access-b1-reference | 48/48 | 2.116 | 3410.8 | 5531.6 | 0.0 |
| access-b1-selective | 48/48 | 1.456 | 3318.9 | 10288.4 | 0.0 |

## L1 편중 workload

| run | 정답/요청 | episode/s | E2E median ms | p95 ms | 평균 server queue ms |
|---|---:|---:|---:|---:|---:|
| hot-b0 | 48/48 | 2.458 | 2918.3 | 3886.9 | 0.0 |
| hot-b1 | 48/48 | 2.480 | 2885.2 | 3892.3 | 0.0 |

## 동일 두 번 모델 호출의 live/replay

| run | 정답/요청 | episode/s | E2E median ms | p95 ms | 평균 server queue ms |
|---|---:|---:|---:|---:|---:|
| io-b0-handoff | 48/48 | 1.229 | 3022.2 | 4853.2 | 0.0 |
| io-b0-replay | 48/48 | 1.269 | 2939.8 | 4685.0 | 0.0 |
| io-b1-handoff | 48/48 | 1.244 | 2965.2 | 4785.6 | 0.0 |
| io-b1-replay | 48/48 | 1.264 | 2933.6 | 4736.2 | 0.0 |

## 해석 범위

- Model-only는 정답 evidence를 미리 준 1회 호출. Handoff는 도구 선택+실제 DB+최종 응답의 2회 호출이다. 차이를 순수 I/O 비용으로 해석하지 않는다. Replay는 같은 두 호출에서 DB만 저장된 결과로 대체한다.
- 모든 요청은 독립 single-agent context. hot 조건은 L1 workload 비중도 달라지므로 pure key-skew 효과가 아니다.
- physical scale은 1이며 노드/관계를 늘리지 않았다. 18 access 변형과 기존54 logical-domain 셀은 physical SF 확대가 아니다.
- prefix caching을 켜고 여섯 case warmup 뒤 측정했다. Warmup이 cache hit를 보장하지는 않으며, 실제 hit counter를 별도 집계한다. 시작/종료 drain과 sample 수를 포함한 유한 실험이다. 1초 GPU/queue 샘플은 순간 최고치나 kernel-level attribution을 보장하지 않는다.
- CPU quota는 약30.72core. cgroup 수치는 Neo4j와 다른 서비스도 포함하며 throttled_usec 합을 request wall latency로 보지 않는다.
- 요청 manifest의 baseline 필드는 기준 설정이며 실제 각 서버 인자는 server-owner.json 및 analysis.json의 server_config를 따른다.
- 실패와 drop은 원시 results와 분모에 남긴다. 토큰 수, client dispatch lag, server queue, DB 시간은 analysis.json에서 별도 확인한다.

실패가 있는 run: []


## 종합 관측

총 38개 측정 셀: offered 2460, correct 2345, failures 0, admission drops 115. Warmup은 별도 기록하고 제외했다.
- Block 1: C8→C16 throughput 2.111→2.153/s, median 3.44→6.79s. 선택된 seq8 설정의 포화이며 A100의 절대 하드웨어 한계는 아니다.
- Block 1: 4/s에서 182/240 완료, drop 58; 평균 server queue 8.81s/model call. 두 번째 호출의 client 첫 delta median 11.45s.
- Block 1: live/replay median 3022.2/2939.8ms. 이는 DB 제거를 포함한 시스템 효과이며 단순 차이를 DB 자체 실행시간이라고 부르지 않는다.
- Block 1: fanout/reference 생성 token 비율 2.64; DB median 27.8/24.6ms; server decode 평균 3758.0/1540.7ms/model call.
- Block 2: C8→C16 throughput 2.111→2.139/s, median 3.42→6.82s. 선택된 seq8 설정의 포화이며 A100의 절대 하드웨어 한계는 아니다.
- Block 2: 4/s에서 183/240 완료, drop 57; 평균 server queue 8.65s/model call. 두 번째 호출의 client 첫 delta median 11.15s.
- Block 2: live/replay median 2965.2/2933.6ms. 이는 DB 제거를 포함한 시스템 효과이며 단순 차이를 DB 자체 실행시간이라고 부르지 않는다.
- Block 2: fanout/reference 생성 token 비율 2.64; DB median 26.7/39.8ms; server decode 평균 3752.3/1529.1ms/model call.

모델 로그의 attention cache block은 Mamba page 정렬 때문에 784 tokens이다. 기준 prompt는112–328tokens, 실제 prefix hits는0이었다. prefix caching을 켰다는 사실을 cache reuse 이득으로 해석하지 않는다.

현재 증거에 따른 우선순위: (1) 검증한 seq8 batching 유지, (2) 요청 admission/대기 및 두 번째 모델 재진입 관측, (3) 의미를 보존하는 result/token budget 관리, (4) DB query rewrite 유지. CPU thread/affinity나 통신 micro-optimization을 주병목으로 단정할 근거는 없다. Long-context, 실제 physical SF, bursty arrival, free-form Graph CoT에 대한 일반화는 별도 검증 대상이다.

## 시나리오별 접근 패턴 E2E

| profile | case | 두 반복 E2E median ms | 두 반복 출력 token median / episode |
|---|---|---:|---:|
| reference | L1 | 2861.1 / 2821.1 | 54 / 54 |
| reference | L2 | 5412.4 / 5423.0 | 110 / 110 |
| reference | L3 | 3583.0 / 3526.7 | 70 / 70 |
| reference | L4 | 3397.9 / 3364.5 | 65 / 65 |
| reference | L5 | 2968.8 / 2938.8 | 56 / 56 |
| reference | L6 | 3618.3 / 3642.5 | 69 / 69 |
| fanout | L1 | 5606.1 / 5651.9 | 129 / 129 |
| fanout | L2 | 9011.6 / 8930.9 | 208 / 208 |
| fanout | L3 | 13785.0 / 13788.9 | 324 / 324 |
| fanout | L4 | 14272.6 / 14278.6 | 333 / 333 |
| fanout | L5 | 2618.4 / 2679.0 | 56 / 56 |
| fanout | L6 | 3152.8 / 3111.2 | 69 / 69 |
| selective | L1 | 3378.2 / 3406.0 | 71 / 71 |
| selective | L2 | 10181.0 / 10233.5 | 225 / 225 |
| selective | L3 | 7045.4 / 7124.7 | 155 / 155 |
| selective | L4 | 3129.5 / 3150.1 | 65 / 65 |
| selective | L5 | 2740.5 / 2685.0 | 56 / 56 |
| selective | L6 | 3290.4 / 3290.6 | 69 / 69 |

L 번호는 의미/쿼리 구성의 단계이며 runtime 순서를 보장하지 않는다. 반환 행/byte/token과 탐색 비용을 함께 보고한다.

Measured saturation curves [see original archived attachment]