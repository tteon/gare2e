> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/NEO4RS_INTERVENTION_RESULTS.md`; SHA256 `61b3faf61b945de378002e1ba3cbe52483e74b652cff250cc3744cf34de3091c`.

# Neo4j driver·CPU·Cypher·concurrency 개입 결과

2026-10-30, Beads `gug-bbf`. DB matrix, E2E 및 HTTP 부하 생성기 대조군을 완료했다. 원본 Neo4j는 변경하지 않았다.

## DB 단독: 반복되는 효과

고정: logical clone, heap512MiB/pagecache64MiB, Bolt worker32, client pool16, fetch1000, transaction timeout15s. 두 역순 블록, 각 셀8초 closed-loop arrival 후 drain. PROFILE은 timed workload에 섞지 않았다.

| CPU 허용 코어 | 경로 | Query | C | 처리량(qps), 두 블록 범위 | p95(ms), 두 블록 범위 |
|---|---|---|---|---|---|
| 4 | Python | reference | 8 | 150.3–159.3 | 132.3–143.7 |
| 4 | Python | optimized | 8 | 514.9–564.7 | 40.7–45.7 |
| 4 | Rust | reference | 8 | 152.3–157.8 | 136.8–141.5 |
| 4 | Rust | optimized | 8 | 499.4–510.3 | 46.8–47.2 |
| 16 | Python | reference | 8 | 272.9–288.8 | 67.9–68.9 |
| 16 | Python | optimized | 8 | 867.6–871.2 | 22.8–23.4 |
| 16 | Rust | reference | 8 | 308.2–310.4 | 66.0–66.3 |
| 16 | Rust | optimized | 8 | 898.5–962.6 | 22.0–23.4 |

56개 셀의139,649요청 모두 oracle 통과. 실패/누락0. L1–L6는 동일한 시나리오이고 optimized는 L5/L6만 바꿨다. CPU16/Python/C8에서 query rewrite는 처리량약3.0–3.2배, p95약68→23ms였다. CPU4→16은 두 블록 모두 처리량 증가 및 p95 감소를 보였으나 언어·query별 효과 크기는 다르다.

L5/L6 baseline과 optimized 모두 기존 의미 반례4건을 통과한 query와 동일하다. 중복 Medium, 역방향 이체 합계, 동일 owner 및 상환 제외를 포함한 반례 기록은 `results/counterexamples/results.json`에 보존되어 있다. 새 validator는 benchmark query가 그 검증된 query와 같은지도 확인한다.

실행계획상 원래 query는 Account label scan2470개에서 시작해 이체 관계를 확장했고, rewrite는 GUARANTEE relationship type scan400개에서 시작한다. L5의 후속 후보 행도10882→1327개로 줄었다. 따라서 관측된 이득은 선택적인 시작점과 중간 행 감소로 설명된다. 이전 별도 PROFILE의 DB hits는 L5 578315→83605, L6 720169→105732였다. GUARANTEE 비율·owner fanout·선택도가 바뀌면 이 이득도 다시 측정해야 한다. 근거는 `results/runtime/neo4j-study/rewrites-b0.json`과 역순 반복이다.

CPU4/C8에서 JVM 평균CPU 약3.7–3.9코어, CPU16/reference/C8에서약7.0–7.6코어를 사용했다. RETURN1 canary p95는 CPU4 약6–11ms, CPU16 약1–2ms였다. CPU 허용 범위가 이 부하에서 제한 요인이었음을 뒷받침하지만 NUMA locality나 vLLM과의 동시 CPU contention을 입증하지 않는다. DB 단독 셀에서는 모델 서버를 구동하지 않았다. CPU affinity는 코어 예약이 아니고 CPU quota30.72는 여전히 공유된다.

C32/light 및 optimized 일부 셀의 부하 생성기는 단일 코어의80–98%를 사용했다. 해당 qps/p95를 Neo4j 전역 한계나 Rust/Python 우열로 해석하지 않는다. Neo4j client pool16에 C32를 주었으므로 tool client의 pool 대기도 포함된다. CPU16/C8 optimized에서 Rust/Python 차이는 두 블록약10.5%/3.6%로 변했으며 한 번의 최고값을 채택 근거로 쓰지 않는다.

## Lifecycle와 비교 범위

neo4rs0.8.0/Cargo.lock 고정. pool1 probe는 소비 완료 handle이 pool lease를 잡는 현상, 부분 stream drop과 PULL 취소 후 server transaction이 다음 재사용/reset까지 남는 현상을 확인했다. 정상 full-drain 경로의 정답 통과와 취소 안전성은 다르다. 즉시 cleanup adoption gate를 통과하지 못해 현재 단순 Rust wrapper는 운영 경로에 채택하지 않는다. syntax FAILURE 후 재사용은 회복했다.

첫 matrix는 startup health stream handle을 보유하여 Rust pool을1개 덜 쓸 수 있었으므로 전체 비교에서 제외했다. `drop(health)` 수정·재빌드 이후의 `matrix-v2`만 집계했다. 제외 원시 기록은 보존한다.

Pinned source의 version offer는 Bolt4.1/4.0이고 동일 bytes의 독립 handshake는4.1을 선택했다. Python 실제 server-info는5.8이다. 두 HTTP boundary는 같지만 runtime/JSON/protocol/driver가 달라 순수 언어 효과나 순수 Bolt overhead 비교가 아니다. execute/drain 측정 경계도 driver pipelining에 따라 달라 total stack 중심으로 비교한다.

## E2E 및 HTTP harness 대조군

두 역순 블록의16개 셀 각각48개 측정 episode와6개 warmup. 총768개 측정/96개 warmup 모두 정답. 각 episode는 독립 context, tool1회와 모델2회다. BF16/seq8/batch8192/OMP4/KV12GiB 및 DB CPU16을 고정했다. 이는 reference tool handoff 평가이며 자유 생성 Cypher의 의미 정확도를 대신하지 않는다.

per-case 결과 bytes/hash와 각 turn의 prompt/output token 수 일치: **True**. 같은 모델 CLI/source hash를 확인했다.

| 경로 | C | 처리량(episode/s) | 중앙 E2E(s) | p95 E2E(s) | 중앙 DB/tool(ms) |
|---|---|---|---|---|---|
| direct-reference | 8 | 2.08–2.10 | 3.40–3.45 | 5.50–5.51 | 19.80–20.83 |
| direct-reference | 16 | 2.12–2.12 | 6.83–6.87 | 8.76–8.79 | 19.94–20.27 |
| direct-optimized | 8 | 2.11–2.12 | 3.43–3.47 | 5.55–5.62 | 12.80–18.80 |
| direct-optimized | 16 | 2.13–2.16 | 6.69–6.89 | 8.90–8.92 | 12.04–13.31 |
| python-http | 8 | 2.10–2.11 | 3.42–3.47 | 5.55–5.59 | 12.24–12.59 |
| python-http | 16 | 2.14–2.15 | 6.70–6.75 | 8.86–8.94 | 12.45–15.02 |
| rust-http | 8 | 2.08–2.12 | 3.43–3.52 | 5.51–5.64 | 9.28–10.49 |
| rust-http | 16 | 2.12–2.14 | 6.75–6.93 | 8.92–9.02 | 9.70–10.14 |

| 개입 | C | 처리량 변화%, block0 / block1 | p95 변화%, block0 / block1 | 반복 성능 gate |
|---|---|---|---|---|
| query | 8 | +1.21 / +1.14 | +2.17 / +0.86 | False |
| query | 16 | +1.84 / +0.30 | +1.49 / +1.70 | False |
| http-transport | 8 | -0.19 / -0.56 | -1.33 / +0.70 | False |
| http-transport | 16 | -0.74 / +0.67 | -0.68 / +0.42 | False |
| driver | 8 | +0.74 / -1.73 | -0.62 / +0.92 | False |
| driver | 16 | -1.05 / -0.05 | +1.75 / -0.25 | False |

C8: 모델 요청당 queue 평균0.012–0.013ms, decode 평균1525.3–1560.2ms. E2E 전체가 아닌 개별 모델 요청의 서버 histogram delta 평균이다.

C16: 모델 요청당 queue 평균1465.843–1495.448ms, decode 평균1511.6–1545.0ms. E2E 전체가 아닌 개별 모델 요청의 서버 histogram delta 평균이다.

gate는 두 블록 모두 처리량>5% 증가, p95 악화<=5%, 정확도 유지다. 이것은 사전에 정한 실용적 기준이며 통계적 동등성 검정이 아니다. Rust는 별도 cleanup gate도 실패해 운영 경로로 채택하지 않는다. Query rewrite는 의미 반례와 DB 작업 감소 근거가 있으므로 workload variant로 유지하되, E2E 개선을 입증했다고 표현하지 않는다.

### HTTP 부하 생성기 대조군

| 경로 | Query | C | 처리량(qps) | p95(ms) | 부하 생성기 CPU(1코어 비율) |
|---|---|---|---|---|---|
| python | reference | 8 | 274.1–289.7 | 67.2–72.5 | 0.24–0.28 |
| python | reference | 32 | 501.5–564.3 | 113.5–133.2 | 0.42–0.46 |
| python | optimized | 8 | 815.0–896.0 | 22.9–23.9 | 0.65–0.68 |
| python | optimized | 32 | 1193.8–1346.2 | 39.7–49.2 | 0.91–0.95 |
| rust | reference | 8 | 285.7–298.0 | 66.6–67.0 | 0.31–0.43 |
| rust | reference | 32 | 589.0–590.9 | 98.8–99.5 | 0.47–0.47 |
| rust | optimized | 8 | 924.9–937.8 | 22.5–22.7 | 0.71–0.71 |
| rust | optimized | 32 | 1331.1–1353.6 | 38.4–39.5 | 0.96–0.97 |

별도16셀/98,025요청, 실패0. DB pool16은 그대로 두고 부하 생성기의 HTTP client를 worker별로 분리했다. shared-client 결과와 시간상 분리되어 있으므로 엄밀한 randomized causal estimate는 아니다. 80% 이상 CPU 셀은 여전히 load-generator ceiling 가능성을 표시한다. 원래 keep-alive 한도 가설은 실제 설정이 무제한임을 확인하고 철회했으며, keep-alive 한도 실험은 수행하지 않았다.

따라서 원래 C32의 처리량 하락을 Neo4j 포화로 해석하는 가설은 유지할 수 없다. Python/optimized/C32는 사용자별 HTTP client 대조군에서1194–1346qps, p9539.7–49.2ms를 기록했다. 다만 이 대조군도 단일 부하 생성기 코어의91–97%를 사용하므로 더 높은 처리량의 DB 한계는 아직 측정하지 못했다. 다음 고부하 실험은 이 경로를 출발점으로 사용하고, 생성기를 여러 프로세스로 나누어 서버 부하와 독립적으로 여유를 확인해야 한다.

### 적용할 실험 기준선

`configs/neo4j_intervention_control.json`에 Python driver/pool16, 모델 C8 기준선/C16 queue stress, clone CPU16 및 메모리 control을 남겼다. 원본·개선 Cypher를 모두 보존한다. 원본 Neo4j 서비스 설정을 변경한 것은 아니며, CPU16/heap512/pagecache64를 전역 최적값으로 선언하지 않는다. neo4rs cleanup 선행 작업은 Beads `gug-dqa`로 deferred 상태다.

### 완료 및 보존

합계 DB72셀/237,674요청과 E2E768episode 검증. 별도 warmup96episode 제외. 원본 install-smoke graph의4083nodes/27429relationships 내용 hash 불변, 원본 HTTP7474 정상. 소유한 clone/tool/model 서비스는 종료했다. 결과 파일 sha256와 Cargo.lock/source, 잘못된 초기 matrix의 제외 사유까지 보존했다.

## 재현과 한계

- 통제: 실험 protocol [see original archived attachment], 전체 가설 판정 [see original archived attachment].
- 원시 결과는 `results/neo4rs/`, 집계는 `scripts/analyze_neo4rs.py`, 무결성/정확도/격리/정리 검증은 `scripts/validate_neo4rs.py`.
- 메모리는 작은11.3MiB logical clone의 재현 가능한 control이다. 원본11.8GiB store 또는 다른 working-set의 전역 최적값이 아니다.
- 8초 DB 창/두 반복은 제한된 비교다. 독립 통계 표본으로 요청 수를 부풀리지 않는다. 장시간 open-loop, 큰 결과, hot-key 편중, physical scale은 별도 검증 대상이다.
- NUMA memory binding은 EPERM. 메모리 locality 비교와 원본 서비스 재설정은 수행하지 않았다.
