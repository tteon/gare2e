> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/SINGLE_TOOL_AXES_RESULTS.md`; SHA256 `c464b287809b9b7947083cb60e1e04b5cf0f9d1754ed77179867e244de750c4a`.

# Single-tool L1–L6 실행 결과

고정 템플릿의 도구 호출 1회와 모델 호출 2회로 구성한 독립 사용자 실험이다. Multi-turn Cypher 수정은 포함하지 않았다. BF16/seq8/batch8192/OMP4/KV12GiB를 고정했다.

주 실험 34셀, 1632episode: 정답 1632, 실패 0, drop 0. 각 셀은 측정48건과 별도 warmup6건이며 두 블록의 실행 순서를 뒤집었다. Generator192건과 calibration6건은 주 실험 집계에서 제외했다.

**해석 범위:** 유한 요청 screening이다. 긴 steady-state, open-loop 용량, 물리 scale, NUMA contention 또는 scheduler CPU 병목을 입증하는 실험이 아니다. 상세 통제는 protocol [see original archived attachment], 원시 자료는 `results/single_tool_axes/`에 있다.

## 결과에 따른 판정

- Baseline C8→32에서 throughput은 약2.07–2.09→2.15–2.17eps로 거의 평탄하지만 p95는5.50–5.54→14.83–15.29초다. Running 최대8, waiting 최대0→24, 평균 모델 queue0→3.70–3.73초로 seq8 설정의 queue pressure가 반복 관측됐다. Scheduler CPU 병목의 입증은 아니다.
- Compact/expanded의 동일 inner pattern과 같은 DB hits에도 E2E 비용은 크게 다르다. 결과 표현·출력 토큰·decode를 함께 다뤄야 한다. Count-only의 정보 손실을 허용하지 않는 과제에 이 속도 차이를 그대로 적용할 수 없다.
- 동일 evidence에1초 지연을 주입하면 C8 throughput은 두 블록에서21.2%/18.5% 감소했다. C32는−1.4%/+1.9%로 방향이 일관되지 않았다. 높은 동시성에서 tool 대기가 다른 요청의 모델 작업과 겹칠 수 있으며 finite-window 스케줄링도 영향을 준다. Tool 최적화 이득은 동시성에 조건부다.
- 다음 확인은 동일 fixed-template 의미를 유지한 장시간 부하에서 C8/16 경계와 arrival rate를 검증하는 것이다. 사용자 지시에 따라 multi-turn Cypher 수정은 별도 후속 시나리오로 둔다. 높은C를 운영값으로 채택하거나 custom scheduler를 수정할 근거는 아직 부족하다.

## 동시 사용자와 scheduler

| Profile | C | 블록 | 정답 eps | E2E median / p95(s) | 모델 queue 평균(s/call) | waiting 최대 | KV 최대(%) | preemption |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 8 | 1 | 2.087 | 3.394 / 5.502 | 0.000 | 0 | 12.85 | 0 |
| baseline | 8 | 2 | 2.070 | 3.485 / 5.544 | 0.000 | 0 | 12.85 | 0 |
| baseline | 16 | 1 | 2.148 | 6.734 / 8.817 | 1.461 | 8 | 12.85 | 0 |
| baseline | 16 | 2 | 2.150 | 6.762 / 8.692 | 1.459 | 8 | 12.85 | 0 |
| baseline | 32 | 1 | 2.166 | 10.962 / 14.832 | 3.700 | 24 | 12.85 | 0 |
| baseline | 32 | 2 | 2.153 | 10.988 / 15.290 | 3.728 | 24 | 12.85 | 0 |
| compact | 8 | 1 | 3.413 | 2.324 / 2.572 | 0.000 | 0 | 12.85 | 0 |
| compact | 8 | 2 | 3.481 | 2.245 / 2.635 | 0.000 | 0 | 12.85 | 0 |
| compact | 16 | 1 | 3.467 | 4.473 / 4.901 | 0.976 | 8 | 12.85 | 0 |
| compact | 16 | 2 | 3.573 | 4.369 / 4.735 | 0.949 | 8 | 12.85 | 0 |
| compact | 32 | 1 | 3.567 | 7.548 / 9.115 | 2.358 | 24 | 12.85 | 0 |
| compact | 32 | 2 | 3.596 | 7.571 / 8.998 | 2.358 | 24 | 12.85 | 0 |
| expanded | 8 | 1 | 1.168 | 5.483 / 10.377 | 0.000 | 0 | 12.85 | 0 |
| expanded | 8 | 2 | 1.160 | 5.465 / 10.389 | 0.000 | 0 | 12.85 | 0 |
| expanded | 16 | 1 | 1.198 | 11.149 / 16.640 | 2.472 | 8 | 12.85 | 0 |
| expanded | 16 | 2 | 1.198 | 11.148 / 16.572 | 2.464 | 8 | 12.85 | 0 |
| expanded | 32 | 1 | 1.187 | 19.566 / 27.261 | 6.220 | 24 | 12.85 | 0 |
| expanded | 32 | 2 | 1.190 | 19.353 / 27.252 | 6.176 | 24 | 12.85 | 0 |

Server queue는 두 모델 호출을 합친 전역 histogram delta 평균이다. waiting/KV는 약1초 간격 표본의 최대값이다. 이 값들을 turn2의 서버 대기시간으로 직접 대입하지 않는다. 개별 turn의 client TTFT는 `analysis.json`에 보존했다.

| Profile / case | 블록1 C8 반환 bytes | 두 호출 output tokens | Tool median(ms) |
|---|---:|---:|---:|
| baseline / L1 | 44 | 54 | 2.52 |
| baseline / L2 | 148 | 110 | 2.89 |
| baseline / L3 | 52 | 70 | 3.72 |
| baseline / L4 | 64 | 65 | 43.86 |
| baseline / L5 | 46 | 56 | 82.93 |
| baseline / L6 | 71 | 69 | 63.09 |
| compact / L1 | 17 | 37 | 3.08 |
| compact / L2 | 18 | 38 | 4.45 |
| compact / L3 | 17 | 37 | 5.78 |
| compact / L4 | 19 | 39 | 29.87 |
| compact / L5 | 16 | 36 | 69.53 |
| compact / L6 | 16 | 36 | 73.61 |
| expanded / L1 | 143 | 93 | 3.72 |
| expanded / L2 | 254 | 148 | 5.32 |
| expanded / L3 | 299 | 226 | 7.88 |
| expanded / L4 | 432 | 230 | 39.23 |
| expanded / L5 | 46 | 56 | 73.09 |
| expanded / L6 | 71 | 69 | 66.21 |

Concurrency screening [see original archived attachment]

## 고정 evidence의 tool 지연 대조군

| 주입(ms) | C | 블록 | 정답 eps | median / p95(s) | 모델 queue 평균(s/call) |
|---:|---:|---:|---:|---:|---:|
| 0 | 8 | 1 | 2.146 | 3.345 / 5.529 | 0.000 |
| 0 | 8 | 2 | 2.117 | 3.412 / 5.504 | 0.000 |
| 50 | 8 | 1 | 2.132 | 3.378 / 5.403 | 0.000 |
| 50 | 8 | 2 | 2.085 | 3.453 / 5.609 | 0.000 |
| 250 | 8 | 1 | 1.989 | 3.601 / 5.659 | 0.000 |
| 250 | 8 | 2 | 1.982 | 3.610 / 5.812 | 0.000 |
| 1000 | 8 | 1 | 1.691 | 4.233 / 6.277 | 0.000 |
| 1000 | 8 | 2 | 1.726 | 4.160 / 6.321 | 0.000 |
| 0 | 32 | 1 | 2.183 | 10.654 / 14.525 | 3.662 |
| 0 | 32 | 2 | 2.122 | 10.979 / 14.864 | 3.761 |
| 50 | 32 | 1 | 2.176 | 10.821 / 14.645 | 3.684 |
| 50 | 32 | 2 | 2.165 | 10.860 / 14.541 | 3.686 |
| 250 | 32 | 1 | 2.174 | 10.878 / 14.576 | 3.563 |
| 250 | 32 | 2 | 2.174 | 10.764 / 14.602 | 3.550 |
| 1000 | 32 | 1 | 2.153 | 10.964 / 14.705 | 3.288 |
| 1000 | 32 | 2 | 2.162 | 10.700 / 14.722 | 3.266 |

Replay delay는 비동기 sleep이며 DB transaction이나 connection을 점유하지 않는다. 따라서 실제 느린 DB의 CPU/I/O·pool 경합을 재현하지 않는다. 높은 동시성에서 다른 사용자의 모델 작업이 지연을 일부 가릴 수 있으나 multi-agent 검증은 아니다.

Replay delay [see original archived attachment]

## Generator 및 통제 검증

- 블록1: 총C8에서 1프로세스 대비 2프로세스 throughput 차이 +0.668%. 동시 시작 skew 0.649ms. 현재 부하의 5% diagnostic gate 통과: True.
- 블록2: 총C8에서 1프로세스 대비 2프로세스 throughput 차이 +0.656%. 동시 시작 skew 0.254ms. 현재 부하의 5% diagnostic gate 통과: True.
- Profile/case 내부 result hash·byte 및 두 모델 호출의 prompt/completion token 수 동일: True.
- 이 검증은 현재 E2E rate에 한정된다. DB-only 고부하나 더 높은 arrival rate까지 generator를 검증한 것은 아니다.

## L1–L6 비용 축: 실행 가능한35조건

35조건에는 기준/중복 control이 포함되며 query+parameter의 서로 다른 조합은31개다. 물리 그래프는4083nodes/27429edges로 고정했다. 모든 조건의 독립 snapshot oracle, EXPLAIN, PROFILE이 통과했다. 모든35조건을 E2E sweep한 것은 아니며, E2E는 baseline/compact/expanded 각6조건의 동일 비율 혼합이다.

| 조건 | inner 전체 rows | 반환 rows | DB hits | 일반 실행 median(ms) | EXPLAIN wall(ms) | 반환 bytes |
|---|---:|---:|---:|---:|---:|---:|
| L1-baseline | 2 | 2 | 16 | 4.93 | 1306.79 | 44 |
| L2-baseline | 4 | 4 | 31 | 5.26 | 274.98 | 148 |
| L3-baseline | 1 | 1 | 37 | 5.53 | 199.07 | 52 |
| L4-baseline | 1 | 1 | 89556 | 56.34 | 273.99 | 64 |
| L5-baseline | 1 | 1 | 577880 | 63.95 | 376.86 | 46 |
| L6-baseline | 1 | 1 | 712682 | 68.13 | 361.82 | 71 |
| L1-rewrite | 2 | 2 | 16 | 2.51 | 5.87 | 44 |
| L2-rewrite | 4 | 4 | 31 | 2.56 | 3.71 | 148 |
| L3-rewrite | 1 | 1 | 37 | 2.77 | 4.58 | 52 |
| L4-rewrite | 1 | 1 | 89556 | 23.63 | 4.64 | 64 |
| L5-rewrite | 1 | 1 | 83605 | 14.31 | 177.02 | 46 |
| L6-rewrite | 1 | 1 | 105732 | 11.30 | 217.53 | 71 |
| L1-compact | 15 | 1 | 94 | 2.55 | 67.96 | 17 |
| L2-compact | 465 | 1 | 3538 | 4.33 | 56.13 | 18 |
| L3-compact | 70 | 1 | 4624 | 14.27 | 83.97 | 17 |
| L4-compact | 1776 | 1 | 91331 | 25.14 | 86.53 | 19 |
| L5-compact | 1 | 1 | 577880 | 66.81 | 153.08 | 16 |
| L6-compact | 1 | 1 | 712682 | 63.14 | 206.53 | 16 |
| L1-expanded | 15 | 8 | 94 | 3.12 | 83.06 | 143 |
| L2-expanded | 465 | 8 | 3538 | 5.18 | 61.29 | 254 |
| L3-expanded | 70 | 8 | 4624 | 6.85 | 86.00 | 299 |
| L4-expanded | 1776 | 8 | 91331 | 28.10 | 72.84 | 432 |
| L5-expanded | 1 | 1 | 577880 | 78.61 | 171.33 | 46 |
| L6-expanded | 1 | 1 | 712682 | 65.91 | 194.75 | 71 |
| L1-amount100 | 14 | 1 | 92 | 2.30 | 3.59 | 17 |
| L1-amount30000 | 5 | 1 | 74 | 1.96 | 7.06 | 16 |
| L2-hops1 | 15 | 1 | 65 | 2.36 | 44.67 | 17 |
| L2-hops2 | 97 | 1 | 644 | 2.45 | 43.54 | 17 |
| L2-hops3 | 465 | 1 | 3538 | 3.81 | 11.77 | 18 |
| L3-amount100 | 65 | 1 | 4245 | 5.32 | 4.61 | 17 |
| L3-amount30000 | 5 | 1 | 552 | 2.09 | 9.24 | 16 |
| L3-timewindow | 0 | 1 | 80 | 2.08 | 3.06 | 16 |
| L4-timewindow | 0 | 1 | 67801 | 16.76 | 4.24 | 16 |
| L4-senders100 | 1 | 1 | 89556 | 26.51 | 7.31 | 16 |
| L4-senders400 | 1 | 1 | 89556 | 24.39 | 3.77 | 16 |

일반 실행 값은4회 중 첫 회를 뺀3회의 median이며 PROFILE과 분리했다. 순차 probe의 JVM/cache warming 영향이 있어 최적화 효과 크기의 엄밀한 A/B 추정으로 쓰지 않는다. EXPLAIN wall에는 통신·driver·cache lookup이 포함되며 순수 planner CPU 시간이 아니다.

Compact/expanded는 같은 inner pattern에서 count와 최대8개 상세 행을 반환한다. 여섯 쌍의 PROFILE DB hits가 같아도 CPU allocation·집계·직렬화 비용까지 같다는 뜻은 아니다. Baseline과 이 두 profile은 anchor/선택도도 다르므로 순수 projection 비교는 compact↔expanded이다. 반환 내용이 바뀌는 count-only를 상세 근거가 필요한 제품의 대체 최적화로 채택하지 않는다.

L3/L4 time-window는0행, L4 sender threshold100/400은 같은 결과였다. 이 조건을 숨기지 않으며 난이도 증가로 자동 간주하지 않는다. L2의1hop과2/3hop은 PROFILE expansion operator도 달라서 순수 hop 수만의 기계적 비용으로 해석하지 않는다. L 번호는 의미 시나리오이고 비용 순위가 아니다. 의미 반례 검증은 기존 별도 결과를 유지한다.

## 재현과 보존

`python3 scripts/validate_single_tool_axes.py`는 artifact/source/config/data hashes, 요청 분모, 독립 user ID, 두 모델 호출, warmup 및 원본 그래프 보존·소유 서비스 정리를 검사한다. 서버 실행 인자와 원시 Prometheus/resource 시계열도 함께 보존했다. `python3 scripts/report_single_tool_axes.py`로 이 표를 재생성할 수 있다. 그림은 `scripts/plot_single_tool_axes.py`로 생성한다.

## 자원 표본

아래 cgroup 값은 실험 프로세스 외 기존 서비스도 포함한다. 약1초 표본의 첫/끝 구간 집계이며 프로세스별 CPU profiler 또는 NUMA 관측을 대신하지 않는다. GPU utilization 역시 busy 비율이지 compute/memory 대역폭 포화의 직접 증거가 아니다. 소수의 CPU throttled periods가 기록됐으므로 CPU contention 부재를 주장하지 않는다. Memory.current에는 file cache 등도 포함되며 Neo4j heap이나 active working set으로 읽지 않는다.

| Cell | GPU median(%) | cgroup 평균 cores | memory 최대(GiB) | throttled periods |
|---|---:|---:|---:|---:|
| b0-baseline-c16 | 100 | 2.38 | 208.24 | 9 |
| b0-baseline-c32 | 100 | 2.97 | 208.22 | 12 |
| b0-baseline-c8 | 100 | 2.92 | 208.26 | 13 |
| b0-compact-c16 | 100 | 4.79 | 208.20 | 12 |
| b0-compact-c32 | 100 | 5.75 | 207.93 | 13 |
| b0-compact-c8 | 100 | 2.87 | 208.33 | 8 |
| b0-expanded-c16 | 100 | 2.70 | 208.27 | 19 |
| b0-expanded-c32 | 100 | 2.67 | 208.35 | 22 |
| b0-expanded-c8 | 100 | 2.71 | 208.27 | 21 |
| b1-baseline-c16 | 100 | 2.85 | 208.24 | 10 |
| b1-baseline-c32 | 100 | 2.63 | 208.27 | 9 |
| b1-baseline-c8 | 100 | 5.08 | 208.02 | 20 |
| b1-compact-c16 | 100 | 1.86 | 208.24 | 3 |
| b1-compact-c32 | 100 | 2.66 | 208.29 | 6 |
| b1-compact-c8 | 100 | 1.88 | 208.26 | 3 |
| b1-expanded-c16 | 100 | 2.73 | 208.32 | 20 |
| b1-expanded-c32 | 100 | 3.87 | 208.28 | 29 |
| b1-expanded-c8 | 100 | 2.84 | 208.29 | 23 |
| d0-delay0-c32 | 100 | 1.36 | 208.25 | 3 |
| d0-delay0-c8 | 100 | 2.47 | 208.21 | 10 |
| d0-delay1000-c32 | 100 | 2.58 | 208.32 | 10 |
| d0-delay1000-c8 | 100 | 5.17 | 207.93 | 24 |
| d0-delay250-c32 | 100 | 2.43 | 208.33 | 9 |
| d0-delay250-c8 | 100 | 2.72 | 208.29 | 12 |
| d0-delay50-c32 | 100 | 1.20 | 208.24 | 3 |
| d0-delay50-c8 | 100 | 1.31 | 208.30 | 3 |
| d1-delay0-c32 | 100 | 2.75 | 208.36 | 13 |
| d1-delay0-c8 | 100 | 2.60 | 208.38 | 12 |
| d1-delay1000-c32 | 100 | 1.80 | 208.31 | 6 |
| d1-delay1000-c8 | 100 | 2.12 | 208.35 | 11 |
| d1-delay250-c32 | 100 | 2.43 | 208.43 | 8 |
| d1-delay250-c8 | 100 | 2.92 | 208.23 | 14 |
| d1-delay50-c32 | 100 | 2.56 | 208.41 | 9 |
| d1-delay50-c8 | 100 | 5.09 | 208.27 | 20 |
