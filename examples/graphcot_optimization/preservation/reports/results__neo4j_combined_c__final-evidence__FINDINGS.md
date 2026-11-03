> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/neo4j_combined_c/final-evidence/FINDINGS.md`; SHA256 `dc4652ac1d6a1da97200f5bd81824e813667bbceb718ea45e3bea70c759fb125`.

# Neo4j 최적화 판정 — 2026-11-01

**현재 우선순위는 메모리·스레드를 일괄 증설하는 것이 아니라, 시간 선택도에 맞는 Cypher 접근 경로와 인덱스를 조합하는 것이다.** 쿼리를 개선한 후에는 메모리 추가 효과가 작아졌고, Bolt worker64 확대는 일관된 처리율 이득을 주지 않았다.

## 새로 확인한 핵심

같은 `find_fan_in_accounts` 계약에서도 유리한 쿼리가 바뀌었다. 넓은 시간 범위는 기존 optimized의 수신 계좌별 집계·LIMIT 접근이 빠르지만, 좁은 범위는 reference 쿼리와 `TRANSFER(ts)` 관계 범위 인덱스가 유리했다.

| L4 시간 범위 | Reference, 기존 인덱스 | Reference + 시간 인덱스 | Optimized + 시간 인덱스 |
|---|---:|---:|---:|
| 전체 기간 폭의1% 구간 |1967.3ms|4.25ms|353.0ms|
| 전체 기간 폭의10% 구간 |1972.2ms|30.99ms|192.5ms|

두 순서의 일반 실행 중앙값이며 PROFILE 시간은 제외했다. 시간 폭1%는 거래 건수의1%라는 뜻이 아니다. 새 진단 구간은 독립 Python oracle의 양성 결과5행과 일치했다. 1%구간의 reference PROFILE DB hits는6,251,880→4,458로 감소했고 `DirectedRelationshipIndexSeekByRange`를 확인했다. 반면 optimized의 상관 subquery 형태는 해당 인덱스를 활용하지 않았다. 넓은 범위에서는 optimized 약0.18–0.22초, reference 약2.1–2.9초로 반대였다.

따라서 **“기존 최적화 쿼리가 모든 파라미터에서 최선”이라는 일반화는 성립하지 않는다.** 지금 두 구간만으로 시간 폭10% 같은 자동 분기 임계값을 배포하지 않는다. 새 시간 위치·폭·min_senders·LIMIT의 held-out 검증은 `gug-xox`에 남겼다. 인덱스는 실험 복사본에서만 만들고 진단 후 제거해 원본과 후속 memory/Bolt 조건의 인덱스를 보존했다.

## 메모리는 query와 함께 봐야 한다

아래는 C32에서32개 요청 전체가 끝나는 시간이다. 각 칸은 두 역순 block이며, 지속 처리능력이 아니다. Driver pool32/Bolt32를 고정했다.

| Query | Heap / page cache | 완료시간 |
|---|---|---|
| Reference |512 /256MiB|12.128 /12.570s|
| Reference |512 /1024MiB|8.836 /8.653s|
| Optimized |512 /256MiB|1.537 /1.648s|
| Optimized |512 /1024MiB|1.547 /1.513s|

Reference에서는 cache 확대가 C32 완료시간을 약27–31% 줄였다. Optimized에서는 같은 증설이 한 순서에서 약0.7% 악화, 다른 순서에서 약8.2% 개선이었다. 현재 표본에서 일관된 큰 추가 이득은 아니다. Optimized C1/C8의 일부 cache 확대 이득은 있으므로 “cache는 언제나 무효”라고도 결론내리지 않는다.

같은 명목 heap+pagecache1.5GiB 예산에서 Reference C32는 H512/P1024가 H1024/P512보다 완료시간이 약15–22% 짧았다. H1024는 GC pause가 더 적었지만 전체 DB 시간이 더 짧아지지는 않았다. Heap 변경 시 전체 transaction memory 기본 한도도358.4→716.8MiB로 변하므로 순수 GC-only 개입으로 해석하지 않는다.

GC는 실제로 존재했다. Reference H512/P256 C32의 측정 창 GC pause 합은1.388/1.564초(창의11.44/12.44%)였고, Optimized에서는0.093/0.111초였다. 이는 G1 concurrent CPU 비용 전체를 측정한 값은 아니다. JVM UTC offset을 Python3.9에서 올바르게 파싱하도록 분석기를 수정하고 회귀 검사를 추가했다. 원시 GC log는 변경하지 않았다.

## Bolt는 worker보다 적절한 실행량이 중요했다

선택한 진단 메모리 H512/P1024, Optimized C32에서:

| Bolt workers / client pool | 두 block 완료시간 |
|---|---|
|32 /8|2.116 /2.197s|
|32 /16|1.522 /1.497s|
|64 /16|1.505 /1.545s|
|64 /32|1.666 /1.598s|

Driver 실행 동시성을8로 제한한 구성은 pool16보다 느렸다. Worker32→64는 pool16에서 두 순서의 방향이 달랐고, pool32 확대도 도움이 되지 않았다. 이 범위에서 **Bolt32/pool16을 유지하는 판단**을 지지한다. 이 연구가 memory×Bolt 전체 factorial이나 모든 workload의 최적값을 입증한 것은 아니다.

Canary 실패는0이었다. 첫 canary는 연결·handshake 비용을 포함하므로 별도로 남겼고, 그림은 첫 표본 이후 maximum을 표시한다. C32의 후속 표본 수는 적으므로 이를 안정적인 p99나 순수 server queue로 부르지 않는다.

## 채택 범위와 다음 단계

- 기존 운영 비교점인 H512/P256/Bolt32/pool16을 자동 교체하지 않는다. 이번 Bolt 대조의 직접 검증 튜플은 H512/P1024/Bolt32/pool16이며 전역 최적점이 아니다.
- 새로 유망한 것은 시간 선택도에 맞춘 L4 경로 선택이다. 검증된 reference/optimized 템플릿과 관계 시간 인덱스를 후보로 제공하고, held-out 조건과 라우팅 비용을 먼저 검증한다.
- 물리 `read_bytes` 증가는 전 셀0이었다. Host/OS cache를 지우지 않았고 현재 host DRAM 고갈을 만든 연구가 아니므로 disk/CXL/NUMA 병목 해소를 주장하지 않는다.
- 새로운 모델 E2E는 이번에 실행하지 않았다. 메모리·Bolt 추가만으로 강한 새 후보를 확보했다고 보기 어렵고, DB-only 이득을 agent 이득으로 바꾸어 말하지 않는다. 시간 선택도 기반 후보의 모델 E2E는 별도 검증 대상이다.

## 검증·보존

Memory/query48셀과 Bolt8셀, 측정1792/1792 및warmup448/448, 총2240고유 occurrence 모두정답이었다. 별도96개 진단 레코드(일반 측정32/PROFILE32/warmup32),128개EXPLAIN과24의미 반례를 확인했다. 원본 SF1 store 파일SHA 전후 동일, 원본 install-smoke workspace4083nodes/27429relationships 및전체속성 동일, fixture0, 원래 인덱스 복구를 검증했다.

원시1044파일과archive SHA256 `fc132bedc23d90f4cef54a9c2df676afb5dfdadb340c3e4b4698030fa75e3345`를 확인했다. 새 감사에서60개PID/start기록 모두비활성, 소유17689와이전17688/18000포트닫힘, 원본7687열림을확인했다. GPU는감사시점에비어있었다. 실험 중에는외부Draftfit작업1041개표본이관측됐으므로전용CPU/GPU·DRAM격리의근거로쓰지않는다. 해당작업은종료하지않았다.

초기a/b의과도한GPU독점guard중단은별도보존하고합산하지않았다. CPU-only DB실험은2초GPU/CPU/DRAM관측을유지하는record모드로수정했으며추론E2Eguard를완화하지않았다. SSH일시timeout으로수집기만재연결했고원격controller를중복실행하지않았다. Git/Dolt remote sync는실행하지않았다.


# Neo4j 조합 실험 결과

이 연구는 동일 SF1 복사본의 DB-only finite cohort 비교다. 기존 vLLM 모델·정밀도·SF는 바꾸지 않았고 E2E 향상을 실측한 결과로 해석하지 않는다.

## 정확성과 실행 범위

{'measured_offered': 1792, 'measured_ok': 1792, 'measured_failed': 0, 'measured_dropped': 0, 'warmup_offered': 448, 'warmup_ok': 448, 'warmup_failed': 0, 'warmup_dropped': 0}
고유 occurrence 2240, 메모리/query48셀+Bolt8셀. 원본 store 파일SHA 및 작은 원본 그래프 전체속성 보존, 복사본 노드/관계 총계·24의미 반례·fixture정리 검증 통과.

외부 GPU 정책=record, GPU 점유 관측 표본=1041. GPU를 사용하지 않는 DB 비교이며 공유 CPU/cgroup·DRAM과 background load를 완전히 격리하지 않았다.

## 인덱스·시간 선택도 진단

| Query | Request | Base ms | +TRANSFER(ts) ms |
|---|---|---|---|
|optimized|L4-time-fraction-0.01|343.960|353.037|
|optimized|L4-time-fraction-0.1|297.138|192.513|
|optimized|find_fan_in_accounts-v0-p1|195.571|195.358|
|optimized|find_fan_in_accounts-v1-p1|183.764|217.818|
|reference|L4-time-fraction-0.01|1967.312|4.248|
|reference|L4-time-fraction-0.1|1972.218|30.990|
|reference|find_fan_in_accounts-v0-p1|2737.395|2310.421|
|reference|find_fan_in_accounts-v1-p1|2732.293|2106.628|

각 값은 두 순서의 일반 실행 중앙값이며 PROFILE은 별도다. 좁은 시간 범위 진단은 새 파라미터와 독립 oracle로 검증했으며, 과거 E2E와 같은 prompt 집합으로 부르지 않는다. 진단 request에 남아 있는 기존 prompt metadata는 DB 실행 입력으로 쓰지 않았다. 실제 조회는 기록된 arguments/query SHA로 식별한다.

## Query×메모리×DB 동시성

각 칸은 두 역순 block의 전체 batch duration(초)이다. 실패를 제외한 성공 latency의 평균이 아니다.

| Query | Heap/PC MiB | C1 | C8 | C32 |
|---|---|---|---|---|
|reference|512/256|74.916 (32/32)/75.155 (32/32)|17.291 (32/32)/16.727 (32/32)|12.128 (32/32)/12.570 (32/32)|
|reference|512/512|59.047 (32/32)/61.341 (32/32)|12.295 (32/32)/12.453 (32/32)|9.329 (32/32)/9.130 (32/32)|
|reference|512/1024|56.346 (32/32)/57.838 (32/32)|13.026 (32/32)/12.080 (32/32)|8.836 (32/32)/8.653 (32/32)|
|reference|1024/512|58.923 (32/32)/56.642 (32/32)|11.867 (32/32)/11.787 (32/32)|11.353 (32/32)/10.182 (32/32)|
|optimized|512/256|10.601 (32/32)/11.104 (32/32)|1.918 (32/32)/1.720 (32/32)|1.537 (32/32)/1.648 (32/32)|
|optimized|512/512|9.731 (32/32)/10.440 (32/32)|1.669 (32/32)/1.604 (32/32)|1.537 (32/32)/1.737 (32/32)|
|optimized|512/1024|9.940 (32/32)/9.758 (32/32)|1.693 (32/32)/1.694 (32/32)|1.547 (32/32)/1.513 (32/32)|
|optimized|1024/512|10.017 (32/32)/10.178 (32/32)|1.629 (32/32)/1.812 (32/32)|1.491 (32/32)/1.517 (32/32)|

H512/P1024와 H1024/P512는 heap+pagecache 예산1.5GiB의 배분 비교이며 JVM/native/OS를 포함한 RSS가 같다는 뜻은 아니다. Heap 변경에 연동하는 transaction memory limit도 셀별 runtime-settings.json에 저장했다. GC pause·transaction 예산·cache 효과를 합쳐 GC 원인이라고 부르지 않는다.

## Bolt/pool 비교

사용 메모리 선택: {'heap': 512, 'pagecache': 1024, 'failed': 0, 'duration': 6.448440984357148}. 같은 cohort에서 선택한 screening 후보이며 독립 검증한 최적값은 아니다.

| Bolt/pool | Block | OK/offered | Duration s | Canary max ms |
|---|---|---|---|---|
|32/8|0|32/32|2.116|33.120|
|32/16|0|32/32|1.522|66.654|
|64/16|0|32/32|1.505|39.689|
|64/32|0|32/32|1.666|49.340|
|64/32|1|32/32|1.598|39.143|
|64/16|1|32/32|1.545|37.038|
|32/16|1|32/32|1.497|75.394|
|32/8|1|32/32|2.197|45.097|

Canary는 전용 pool의 RETURN1이며 network/driver/실행을 포함한다. 순수 server queue를 직접 측정한 값은 아니다.

## GC·물리 I/O와 제약

셀별 측정 창 GC pause 합계의 최대: 1564.168ms. 단발 pause 최대: 190.211ms.
Java physical read_bytes delta 합계: 0bytes. OS cache를 drop하지 않았고 Neo4j restart만으로 cold disk라고 부르지 않는다.
Jstat 미설치 시 unified GC log와 /proc/cgroup 관측을 사용하며 heap 전체 시계열 점유나 allocation rate를 실측했다고 주장하지 않는다.

공식 L5/L6 workload는 음성 사례이며 양성 의미는 별도 fixture로 검증했다. 최대 degree597로, 큰 hub/CXL/NUMA/물리 disk 포화·steady capacity·자유 Cypher/multi-turn은 입증하지 않았다. 초기 a/b GPU guard 중단은 별도 보존하고 합산하지 않는다.

전체 셀과 원시 오류: REPORT.md / analysis.json / failures.json. Source/config/data/query hash와 통계/index 상태, EXPLAIN/PROFILE은 별도 원시 파일에 있다. 채택 판단과 추가 E2E 여부는 INTERPRETATION.md에 기록한다.
