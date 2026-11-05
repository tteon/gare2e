> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/physical_scale_a/final-evidence/FINDINGS.md`; SHA256 `31c02e7aafd34f462bb08c00a321809ed2bc4090ddfa94b8e40ea992f2da44f2`.

# 고정 모델의 physical-scale E2E 결과

2026-10-31. 기존 BF16 체크포인트를 유지해 공식 FinBench snapshot SF0.1/SF1 위의 사용자 정의 업무 도구8개를 비교했다. 모델 교체·양자화 재평가는 실행하지 않았다. 아래는 관측 결과이며, 원시 자료의 실패도 모두 분모에 포함한다.

## 실행 범위와 정확성

- SF0.1:55,604노드/309,577관계. SF1:556,082노드/3,101,291관계. Canonical 원본의 전체 속성과 병렬 관계를 대조했다. 각 데이터셋의 인자와 oracle을 별도로 만들었다.
- 각 SF7조건×32요청=224건, 별도56warmup. 조건은 C8기준선, C16, C8replay, C8pagecache256MiB, C8heap1GiB, C8Bolt64, C8월별bucket이다. 도구1회, 성공 시 모델2회 호출하는 독립 context다.
- 측정448offered=388ok+60failed+0dropped. SF0.1은224/224, SF1은164/224. Warmup112건 중97성공/15실패. 실패75건(측정60+warmup15)은 전부 Neo4j15초 쿼리 timeout이었다.
- 실제 도구 선택448/448, 인자448/448, 스키마448/448. DB 완료 후 최종 답은388/388 oracle과 일치했다. DB 실패 요청은 최종 답을 생성하지 않았으므로 모델 정답률의 성공으로 세지 않는다.
- 두 SF의 모델 명령, 체크포인트 파일 메타데이터·config/index 해시가 일치한다. A10080GB, vLLM0.31.0, Neo4j Community5.26.0, 공식 Python driver5.28.2를 사용했다. neo4rs 비교는 이번 cohort에 포함되지 않는다.

## 가장 큰 관측: SF1의 page cache 경계

동일 모델과 C8에서 page cache만64→256MiB로 늘리자 실패하던 세 도구가 모두15초 제한 안에 완료됐다.

| SF1 조건 | 성공/32 | 성공 완료/배치초 | 비고 |
|---|---:|---:|---|
| C8,heap512MiB,PC64MiB,Bolt32 |20/32|0.330|세 도구12건 DB timeout|
| C16,나머지 동일 |20/32|0.473|모델 waiting 최대8, DB timeout 유지|
| C8,replay |32/32|0.667|oracle evidence 반환, DB 실행 생략|
| C8,PC256MiB |32/32|0.542|모든 도구 완료|
| C8,heap1GiB,PC64MiB |20/32|0.328|heap 증가만으로 해결 안 됨|
| C8,Bolt64,PC64MiB |20/32|0.326|worker 증가만으로 해결 안 됨|
| C8,월별bucket,PC64MiB |20/32|0.329|P1 변경은 다른 세 도구 timeout을 해결하지 않음|

PC256의 성공 완료율은 기준선 대비 약64% 높았다. 이는 유한 배치의 관측이며 지속 가능 throughput/SLO 용량을 뜻하지 않는다. 기준선의 성공20건과 PC256의 성공32건은 서로 다른 부분집합이므로 성공 요청 median만 직접 비교하면 생존자 편향이 생긴다. PC256도 최적점으로 확정한 것은 아니다.

## 도구별로 달라지는 DB 영향

두 SF 모두 PC256 live와 replay32/32쌍에서 evidence와 두 모델 호출의 prompt/completion token 수가 일치했다. 모델과 도구 스키마를 유지한 대조다. 각 도구4쌍의 E2E live−replay median:

| 도구 | SF0.1 | SF1 | SF1 PC256의 실제 DB wall median |
|---|---:|---:|---:|
| 유입 집중 계좌(find_fan_in_accounts) |+0.118초|+9.515초|10.259초|
| 연결 계좌쌍(find_linked_account_pairs) |+0.594초|+9.412초|9.797초|
| 연결 계좌 거래순위(rank_linked_account_flows) |+0.370초|+5.645초|5.643초|

다른5개 도구는 이 차이가 작거나 음수였다. Live/replay는 DB 실행 유무뿐 아니라 연속 batching과 요청 간 간섭도 바꾸므로 이 차이를 순수 Bolt 비용이라고 부르지 않는다.8개 도구 전체의 matched median 차이는 SF1에서도57ms에 불과하다. 전체 median이 세 무거운 도구의 병목을 가릴 수 있으므로 per-tool 결과가 필요하다.

별도 PROFILE에서 SF1 L4는 약7.8–8.0M, L5는46.3M, L6는26.0M DB hits였다. Instrumented 실행20–60초는 production latency로 사용하지 않는다. 특히 L5/L6은 현재 공식 데이터 요청에서 빈 결과이지만 많은 관계를 방문한다. 선택도가 높은 후보를 먼저 만드는 동등 Cypher가 다음 후보이며, 양성 의미 반례 검증을 유지해야 한다.

## 나머지 두 개선 축

- Bolt:SF0.1의36개 DB-only 셀1152건 중51건 실패는 모두Bolt4였다(25NoThreadsAvailable,26배치 deadline 미완료). SF1에서는 Bolt32→64가 E2E timeout을 없애지 못했다. 서버 worker만 크게 늘리는 전략을 채택할 근거는 없다. 이 부하는 새 driver/pool 생성도 포함한다.
- 월별 bucket:두 SF의 독립 DB 비교 총768/768건 정답 일치. SF1 C32에서 P1 DB median10.50→8.59ms였지만, E2E P1의 명확한 개선은 확인하지 못했다. SF1 저장소는1,396,850,705→2,114,420,753bytes(+51.4%)로 증가했다. 최대 transfer out-degree597로 대형 hub 효과를 검증한 것은 아니다.
- Heap/GC:heap1GiB에서 GC 횟수는 줄었지만 SF1의 세 도구 timeout은 유지됐다. GC 집계는 startup/warmup을 포함하므로 구간별 인과나 전역 무관성을 주장하지 않는다.

## 메모리 효과와 물리 디스크를 구분

모든 DB screening cell의 Java 프로세스 before/after I/O가 존재했고 read_bytes 증가는0이었다. PROFILE의 page-cache hit/miss도 모두0으로 보고되어 메커니즘 판정에 유용하지 않았다. 따라서 PC256의 효용은 관측했지만 물리 disk saturation이나 miss 감소량을 입증한 것은 아니다. OS file cache의 도움, page copying, 메모리 대역폭·CPU 비용은 후속 계측 대상이다.

SF1 store checkpoint 후 여유 공간은 수GiB 수준이었다. 이 인스턴스에서 SF3 확장을 자동 수행하지 않았고, read-only cgroup mount 때문에 isolated memory cap도 적용하지 못했다. Host cache eviction·새 임대·다른 작업 삭제는 하지 않았다.

## 모델 큐 관측

SF0.1 C8→C16:모두32/32성공, E2E median6.224→14.408초, 최대waiting0→8, finite completion0.673→0.693eps. 모델 실행 한도8에서 대기가 증가했다. CPU scheduler, tokenizer 또는 kernel이 원인이라는 판정은 하지 않는다. Per-process 샘플은 Java/engine을 포함하지만 frontend/tokenizer의 완전한 분해 trace는 없다.

## 해석 범위와 다음 순서

고정 순서1회 E2E, 도구당4요청의 짧은 screening이다. SF 간 anchor·인자·evidence가 달라 raw latency 차이를 순수 scale 인과로 보지 않는다. DB-only SF0.1은 bucket 구성 전, SF1은 구성 후라 cross-SF physical footprint도 동일하지 않다. 두 SF의 E2E는 각각 sidecar가 존재하는 조건에서 모든 비교를 수행했다.

DB-only SF1은1152offered=521ok+631failed:229쿼리timeout,59NoThreadsAvailable,343배치deadline미완료. 특히 C1의45초 배치 제한은 뒤에 대기 중인 요청까지 미완료로 세므로 개별 쿼리 실패율·지속 용량으로 해석하지 않는다.

L5/L6 양성 coverage, 더 다양한 사용자·계좌/기간 선택도, open-loop·역순 반복 및 sustained p95는 남아 있다. 현재 결과는 다음 순서를 지지한다: PC256을 건강한 기준선 후보로 고정하고128/256/512 경계를 반복 확인 → L4/L5/L6 동등 plan 최적화 → 동일 데이터·시나리오·생성 예산에서 모델 비교를 별도 축으로 검토. 양자화 arm을 자동 재개하지 않는다.

## 증거와 정리

- 상세 집계 [see original archived attachment], SF0.1 E2E [see original archived attachment], SF1 E2E [see original archived attachment].
- SF1 성공·배치 완료율 [see original archived attachment], 도구별 live/replay [see original archived attachment]. 그림을 직접 검토했다.
- Archive SHA256:a8c0c2f41c9937affcc021f5bdae618d689614db5333d0420559e672b5ac2f3f.1834개 파일 해시와 세 canonical snapshot 해시 검증. 실제 분석 코드는analysis-source/에 별도 보존한다.
- 초기 PROFILE20초 실패·이전 부분 실행은 별도 보존했다. 진단은180초로 분리 재실행했고16/16 PROFILE oracle 일치. Production15초 제한은 유지했다.
- 원본4083노드/27429관계·속성 동일. 소유한 두 모델과 controller 종료,17688/18000닫힘, GPU유휴 확인. 소유 projection/store는 재현을 위해 보존했다.
- KT Wi-Fi 인증 중단은 로컬 모니터 연결에 영향을 줬고, 원격 독립 실행은 계속되어 완료됐다. Git/Dolt remote push는 수행하지 않았다.
