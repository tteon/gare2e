> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/sf1_optimization_a/final-evidence/FINDINGS.md`; SHA256 `007e1db41be94abe3faadbe468364b32afc57121373f00c9279191d0116cfbc9`.

# SF1 최적화 실험 결과 — 2026-10-31

동일 SF1·기존 BF16 모델에서 Cypher 재작성은 DB 실행 비용을 줄였고, C1·C8의 E2E와 작은 cache에서의 성공률을 개선했다. 유한 C32 burst에서는 처리량이 오히려 감소했다. 고정 도착률 과부하에서는 더 많은 offered 요청을 완료했다. 따라서 DB 단독 속도와 전체 서비스 효용을 함께 평가해야 한다.

## 실행과 정확성

- 메인 DB: 두 역순 블록192건=158성공+34서버 timeout+0drop. 측정96건과 warmup96건을 분리한다. 실패는 기존 쿼리의64/128MiB 조건에만 있다.
- 메인 E2E:44조건,1,792offered=1,697정답 완료+24DB timeout+71client-cap drop. 별도 warmup352건 중4DB timeout. 모든 실패/드롭을 보존했다.
- 실제 모델 도구 선택·인자·schema는1,721/1,721 일치했고, DB 완료 후 최종 답은1,697/1,697 oracle/evidence와 일치했다. Drop71건에는 모델을 호출하지 않았다. 요청 ID2,144개, 실제 생성한 독립 context2,073개 모두 중복 없음.
- 양성·반례24검사 통과: 병렬 송금, 중복 소유/보증/접속, 반대 방향 보증·송금, 동일 소유자, 자기 송금, 상환 제외, 시간·범위·LIMIT 경계. 임시 fixture0개 확인. PROFILE12건과 별도4GiB EXPLAIN12건 검증.
- 모델 경로·파일 메타데이터·config/index hash 및 server flags는 이전 fixed-model 연구와 동일하다. BF16/TP1/seq8/batch8192/KV12GiB/OMP4/context8192/output800/thinking off/seed42 유지. 정밀도 비교와 모델 교체는 실행하지 않았다.

## Cypher 개선과 full-cache 대조

L4 `find_fan_in_accounts`: 수신 계좌별 상관 집계와 정렬/LIMIT 계약을 이용해 전역 집계 접근을 줄였다. L5 `find_linked_account_pairs`, L6 `rank_linked_account_flows`: 보증 관계를 출발점으로 소유 계좌 후보를 좁히고, 방향·서로 다른 소유자·공유 매체·상환 제외·병렬/양방향 합계 의미를 보존했다. 기존 쿼리는 유지하고 `queries/sf1_optimized/`를 별도 버전으로 제공한다. 물리 구현은 condition/manifest의 `reference`·`optimized`와 실제 query SHA로 식별한다. `bound_call.query_version`의 기존 `reference-v1` 호환 필드는 변경하지 않았으며, 이를 물리 구현 선택으로 해석하지 않는다.

| 도구 | DB 단독256MiB 기존→개선 | 4GiB 기존→개선 | PROFILE256MiB DB hits 기존→개선 |
|---|---|---|---|
| L4 fan-in | 6.234→0.234초 | 2.740→0.195초 | 약7.8–8.0M→0.61–0.62M |
| L5 linked pairs | 7.586→1.283초 | 7.324→1.217초 | 46.28M→6.83M |
| L6 linked flows | 4.611→1.114초 | 4.338→1.062초 | 26.01M→6.57M |

DB 단독 값은 각 도구/버전의 측정4표본(두 인자×두 순서) 중앙값이다. PROFILE은 별도 진단이며 운영 시간에 섞지 않았다. 실제 E2E C8의 DB span은 동시 실행/driver 대기가 포함돼 이 표와 다르다: 그림 [see original archived attachment].

전체 store약2.115GB보다 큰4GiB cache에서도48/48건 정확했고 개선 효과가 유지됐다. 기존/개선 각각의512MiB 대비 차이는 최대약7.2%/211ms로, 사전에 정한 추가 E2E 기준(20%와0.5초 동시 충족)에 도달하지 않았다. 추가4조건 GPU 재실험은 실행하지 않았다. 이는4GiB E2E 검증이나 전역 최적점의 증거가 아니다. 12개 EXPLAIN의 operator-type tree 구조는 대응256MiB PROFILE과 모두 같다. 4GiB 결과 [see original archived attachment], 판정 [see original archived attachment].

## E2E: 부하 형태에 따라 효과가 달랐다

| 조건 | 기존 | 개선 | 관측 |
|---|---|---|---|
| C1/cache256,32건 전체 완료 | 311.644/311.091초 | 245.338/244.944초 | 약21% 시간 감소, 양쪽 모두32/32 |
| C8/cache128 | 두 블록 모두20/32 | 두 블록 모두32/32 | DB timeout 해소 |
| C8/cache256 성공 eps | 0.556/0.556 | 0.650/0.659 | 약17–18% 증가, 모두32/32 |
| C8/cache512 성공 eps | 0.578/0.578 | 0.662/0.664 | 약14–15% 증가, 모두32/32 |
| C16/cache256 성공 eps | 0.699/0.710 | 0.694/0.693 | 증가하지 않음, 모두32/32 |
| C32/cache256 성공 eps | 0.762/0.771 | 0.691/0.701 | 약9% 감소, 모두32/32 |

각 칸의 두 수치는 두 역순 블록이다. 유한 cohort 완료/초이며 지속 용량이 아니다. C8의 개선 live0.650/0.659와 matched replay0.665/0.669의 차이는 작았다. Replay 차이는 유도된 batching/queue 변화도 포함하므로 순수 Bolt 비용이나 이론적 성능 상한으로 해석하지 않는다. 모든 건강한 closed 대조쌍과 replay 쌍은 각32/32 evidence+prompt/completion token 일치. Cache128은 실패를 제외한20쌍만 일치하며 이를 전체32건 동등성으로 부르지 않는다.

동시성 그림 [see original archived attachment], cache 그림 [see original archived attachment], 관측 타임라인 block0 [see original archived attachment], block1 [see original archived attachment].

C32 첫 블록에서 두 번째 LLM 호출 평균 client TTFT는6.974→11.018초, 첫 호출은7.312→7.402초였다. 서버 queue-time 주기 샘플은 두 조건63/64완료를 포함했고 평균6.557→8.557초였다. 최대 waiting은 양쪽24였다. DB span 감소와 이후 모델 대기 증가가 함께 관측됐다. 정확한 engine admission order와 GPU batch 구성은 계측하지 않았고 CPU throttling도 있어, 역전의 원인을 특정 scheduler/kernel 또는 CPU 병목으로 확정하지 않는다. 타임라인 TTFT는 content/tool-call delta 도착 기준으로 queue·prefill·parser를 포함한다.

## 고정 도착률, drop와 수락 mix

- 0.5episode/s: 기존146.476/146.525초, 개선138.125/138.151초. 모두64/64완료. 예정 도착 창126초 이후 drain은약20.5→12.1초.
- 1episode/s: 기존90.718/90.993초, 개선85.603/83.797초. 모두64/64완료.
- 2episode/s, client cap32: 기존은 두 블록 모두44완료+20drop; 개선49완료+15drop,48완료+16drop. 합산 offered128건 대비88→97완료(68.75%→75.78%,약7.03%p).

마지막 조건에서 각 블록의 공통 성공 offered slot은39개다. 기존만 성공한5개, 개선만 성공한10/9개가 있어 수락된 도구 mix가 다르다. 따라서 조건부 완료/초만으로 capacity를 순위화하지 않는다. Drop은 DB 거부가 아닌 client admission 결과다. 모델에 들어간 요청의 정확성과 offered 전체의 완료 비율을 분리했다. Generator lag p95의 셀별 최대는약2.06ms였다. arrival pair [see original archived attachment], 수락/완료 곡선 [see original archived attachment], 완료율 그림 [see original archived attachment].

## 풀·자원 관측과 남은 원인

C32 개선 쿼리에서 pool8, heavy-tool cap2, Bolt64는 두 블록 모두약0.69–0.70eps로 큰 개선이 없었다. 정상 driver pool16+별도 canary1은 서버 Bolt32보다 작다. 실제 측정 DB/driver span peak도 서버 상한보다 작다. RETURN1 canary 실패0, 셀별 p95 최대약18.3ms였다. 이 범위에서 Bolt thread 수 증설을 주요 해결책으로 채택하지 않는다. Driver는 공식 Python5.28.2이며 neo4rs 성능/취소 안전성을 새로 입증한 연구가 아니다.

측정된 process read_bytes delta는 모두0이었다. 물리 disk 병목, page-cache miss 경로, DRAM bandwidth/NUMA contention은 입증하지 않았다. CPU quota는30.71999core 상당이나 전용 CPU 예약은 아니다. 측정 구간별 throttled period 비율약2.9–10.4%, CPU PSI full 누적 증가/관측시간약1.16–4.54%가 기록됐다. `throttled_usec`는 병렬 누적값이며 wall-time 지연 비율로 나누지 않는다. 사후 process audit에 기존 `tempo`, `otelcol-contrib`, `grafana`, `prometheus` 등이 있었다. 해당 서비스는 이번 run 소유가 아니므로 중단하지 않았다. 재현된 비교 결과는 이 공유 환경에 한정하며, 세부 queue 원인 입증에는 CPU/관측 서비스 조율과 요청별 engine trace 또는 timing-only replay가 필요하다. queue·자원 [see original archived attachment], 사후 CPU audit [see original archived attachment].

## 데이터와 재현 범위

공식 SF1 canonical556,082nodes/3,101,291edges를 사용했다. 테스트 store에는 이전 연구의 고정 monthly-bucket sidecar474,769nodes 및 추가1,268,949edges가 공존한다(총1,030,851nodes/4,370,240edges). 이 sidecar와 기존 인덱스는 모든 조건에서 유지했고 현재8개 reference/optimized 도구는 원래 관계를 조회한다. 원본 없는 plain FinBench store의 절대 성능이나 물리 layout 동등성을 주장하지 않는다. `inputs/constant-projection/`과 별도4GiB graph-state/fixture 전후 비교에 상태를 기록했다.

각 도구2인자×2문구,8도구의 균등 혼합이다. 공식 L5/L6는 빈 결과였고 양성은 별도 소형 fixture로 검증했다. L4의 조기 LIMIT 이득은 시간/최소 송신자 선택도에, L5/L6 후보 탐색은 보증·소유 fan-out에 의존하므로 다른 인자 분포의 성능은 별도 검증이 필요하다. 실제 양성 workload의 성능, 대형 hub(현재 TRANSFER최대 outdegree597), 자유 생성 Cypher·multi-turn 수정, sustained capacity와 글로벌 최적점은 후속이다. Agent/vLLM/Neo4j는 같은 서버의 loopback 경로이며 WAN tool RPC를 측정한 것이 아니다. Oracle 준비·warmup·startup·PROFILE은 measured E2E에서 제외했다.

## 검증·정리

메인 archive `fe10055517d9602998106cfa339ffcc335cd9ccbd09d245c5d56ae6dddb1d74c`, raw4,635파일; 보충 archive `3a3b0136da939d578502de2ffb606694a8fab903a98a90277ea5548aef47bd61`, raw232파일의 archive와 개별 SHA를 확인했다. 실제 emitted call 재바인딩, query/schema/workload/source/data hash, token 합계, context 격리, 모든 denominator, GPU guard 기록을 검증했다.7개 scientific PNG를 시각 검토했고, 동일 figure의 PDF도 출력했다. 기존 Neo4j4083nodes/27429edges 및 속성 보존, 소유 PID/start72기록 모두 종료, GPUcompute job0,17688/18000닫힘, 원본7687유지 확인. 최종 정리 [see original archived attachment].

원시 실패와 초기 L4 문법 수정 전 `db/` 기록을 보존했다. 실제 DB 측정은 새 `db-b/`이며 dev 실패와 합치지 않았다. 과거 window-study18조건 미완료 및 quantization 결과와 합산하지 않는다. 기존 reference 템플릿과 서비스는 보존했고 최적화 버전은 명시적으로 선택해 비교한다. 고동시성 burst 역전 때문에 전체 workload의 무조건적 기본값 교체는 보류한다.
