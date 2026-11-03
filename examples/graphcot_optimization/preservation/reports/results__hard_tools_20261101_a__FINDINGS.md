> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/hard_tools_20261101_a/FINDINGS.md`; SHA256 `30e0ae717bba1f7b1868facbb549b79c4f4de3e329aceab832649a937dc3f5ab`.

# SF1 반복·검증 인자·풀 제어 결과

2026-11-01 · `gug-305` · DB-only. 모델/정밀도 변경 없음. **현재 최적화 구성 전체를 vLLM 인수인계 완료로 판정하지 않는다.**

## 검증 규모

| 구간 | 조건/요청 | 결과 |
|---|---|---|
| 독립 oracle 인자 | 5도구×24계약 | 양성60/음성60; tuning80/heldout40; 중복 인자0 |
| 의미 반례 | 원본·후보174검사 |174/174 일치, fixture 잔여0 |
| SF1 tuning 정확성 | 80계약×2쿼리 |160/160 일치 |
| EXPLAIN / PROFILE | 각각80 |오류0, PROFILE 결과80/80 일치 |
| 반복 부하 | 5도구×C1/8/16/32×2쿼리×3블록, 120셀 |3,840 offered =3,798 ok +42 failed +0 dropped; warm960/960 |
| 별도 인자 검증 |40계약×C1/8/32×2쿼리×3블록,18셀 |720/720; 양성360/음성360, warm90/90 |
| L4 풀 제어 추가 비교 |C32, pool8/16,3블록,6셀 |192 offered=164 ok+28 failed; warm48/48 |

실패는 모든 분모에 포함한다. 정확한 답을 반환한 경우와 요청 실패를 구분한다. finite burst이며 지속 가능한 서비스 용량·p99 또는 E2E 개선의 증거는 아니다.

## 도구별 판단

H512/P1024MiB, Bolt workers32, client pool16 고정. 표의 시간은 세 블록의 client DB span 중앙값 범위이며 pool 대기·드라이버 비용을 포함한다.

| 도구 | C1 원본 → 후보 | C32 관찰 | 판단 |
|---|---|---|---|
| L4 유입 집계 |1,915–2,021 →223–229ms |후보에서 C16/C32 메모리 오류42건 |완료 요청 시간만으로 채택 불가. 동시 실행 제한·시간 인덱스·메모리를 함께 검증 |
| L3 시간순 자금 경로 |64.1–64.7 →20.9–22.3ms |원본207–254 →후보166–172ms; 오류0 |중복 중간 상태 제거가 이번 데이터·조건에서 반복 개선. 후속 기준선 후보 |
| L2 도달 탐색 |약10ms |양 arm의 쿼리가 동일함 |측정 변동 대조군; 개선으로 집계하지 않음 |
| P1 빠른 경유 |약6–7ms |개선 방향이 일정하지 않음 |원본 유지 |
| A1 공유 접속 |약1ms |후보가 세 블록 모두 약간 느림 |원본 유지; SF1의 현재 인자는 무거운 업무 대표가 아님 |

L4 실패42건은 모두 `Neo.TransientError.General.MemoryPoolOutOfMemoryError`: `dbms.memory.transaction.total.max`358.4MiB 한도에서 추가2MiB 할당 실패. 데이터/정답 오류가 아니다. 이는 page cache miss 또는 물리 디스크 병목과 별개다.

## 검증 인자가 통과해도 단독 집중 부하는 실패할 수 있다

720건 검증은 도구마다 양성4/음성4를 섞은40계약 배치다. 한 배치의 L4는8건이며 그중 양성4건이다. L4 양성32건을 집중시킨 tuning 부하와 같은 메모리 압력이 아니다. 따라서 heldout720/720은 **새 인자의 의미 정확성 및 이 혼합 패턴의 성공**을 확인하며, 단독 L4 오류가 해결됐음을 뜻하지 않는다.

## 풀 크기 개입: 오류를 줄이는 대신 대기를 받는다

같은 L4 후보·인자·H512/P1024·workers32·사용자C32에서 클라이언트 pool만 바꿨다. 같은 tuning 양성8계약을4회씩 실행했다. 두 설정의 순서를 교차하고3블록 반복했다.

| pool | 성공/전체 | 배치 종료 시간(3블록) | 실패 |
|---|---|---|---|
|16|68/96|3.47 /3.75 /3.47초|28건 모두 같은 트랜잭션 메모리 오류 |
|8|96/96|6.05 /5.59 /5.44초|0 |

pool8은 이번 부하에서 정상 완료를 확보했지만 대기를 늘렸다. pool16의 짧은 종료 시간에는 실패한 요청이 섞여 있어 동일 성공률의 속도 개선 비교가 아니다. pool8을 모든 도구의 최종 공통 설정으로 확정하지 않는다. 다음에는 L4 전용 동시 실행 제한과 다른 가벼운 도구의 대기 전파를 혼합 부하에서 비교해야 한다. 이 튜닝 뒤 최종 채택 검증에는 새 heldout을 준비한다.

## Scale 및 자원 판정

SF1은 이미 트랜잭션 메모리 경합과 L3 query 개선을 검증하기에 충분했다. 반면 P1/A1을 무거운 DB 업무로 주장할 근거는 부족하다. SF10은 이 접근 패턴·working set 범위를 넓히는 다음 후보이며, 데이터가 커진다고 모든 쿼리가 비례해 느려지는 것은 아니다.

SF10 원본을 저장/압축 해제하지 않고 전체 스트리밍 검사했다. 원본 CSV 행 기준 노드5,376,981, 관계26,633,151, 유효 snapshot CSV4,062,000,952bytes, 압축2,743,383,151bytes. SHA256 `50d48cb68df5bc9563dc4d1f670507b8329431336d651b85d6a9952c3cc8923d`. 아직 canonical 변환·endpoint/중복/property 감사·Neo4j 적재는 하지 않았다. `._*.csv` 메타데이터2개를 발견해 변환기가 명시적으로 제외하고 기록하도록 수정했으며 회귀 테스트를 통과했다.

원본·변환본·DB 복제본·임시파일을 보존하는 현재 방식의 보수적 계획치는 약71.1GB이고 관측 여유는17.1GB였다. 실제 SF10 store 크기는 미측정이며 이 계획치는 확정 필요량이나 상한이 아니다. 현재 보존형 workflow의 용량 gate를 통과하지 못해 적재하지 않았다. 스트리밍/복제 축소를 통한 예산 재설계 또는 추가 디스크가 먼저 필요하다. 무관한 데이터 삭제·신규 임대·인스턴스 변경은 하지 않았다.

주 실험 resource406표본 중57표본에 외부 GPU 작업이 있었다. heldout79표본·pool38표본에서는 GPU 작업이 관측되지 않았다. CPU 자원 독점은 보장하지 않는다. Java `read_bytes` 누적 관측 증가는 주 실험180KiB, heldout/pool0이었다. 이 자료로 disk-bound 또는 NUMA 병목을 입증하지 않는다. PROFILE은 별도 진단으로 측정 latency에 합치지 않았다.

## 근거 및 남은 작업

- 반복 원시 자료·해시·오류: `final-evidence/screen-analysis.json`, `raw-validation.json`, `per-tool-comparison.json`, `plans.jsonl`.
- heldout: `../hard_tools_heldout_20261101_a/validation.json` 및 원시 요청별 trace.
- 풀 제어: `../hard_tools_pool_20261101_a/validation.json` 및 원시 trace.
- SF10: `../sf10_capacity_20261101_a/archive-inventory.json`, `capacity-assessment.json`.
- source/query hash 검증, snapshot 정답 대조, source store hash 보존, fixture cleanup, owned DB 종료 확인. 기존 install-smoke4,083노드/27,429관계 및 property 동일 확인.

다음 순서는 L4 선택도 기반 query/index와 메모리·입장 제한 조합 → 공통 설정·새 heldout/혼합·지속 부하 → 고정 모델 B0/B1 E2E다. SF10은 용량 계획과 새로운 데이터 기반 인자/정답을 확보한 후 실행한다. 이번 결과만으로 `gug-305`를 닫지 않는다.
