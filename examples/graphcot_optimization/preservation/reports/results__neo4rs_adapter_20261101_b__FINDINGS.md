> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/neo4rs_adapter_20261101_b/FINDINGS.md`; SHA256 `b26efcfa8f4966b6aa3583a1c1e5ce649e81e4239c01ae20c9ff5f67473e932b`.

# neo4rs 업무 어댑터 구축 및 SF1 검증

2026-11-01 · gug-dqa · 원격 `<instance-path>`.

## 변경

- 새 `neo4rs_adapter/`: 8개 업무 tool name + 실제 arguments → schema/범위 검증 → 고정 parameterized Cypher. 사용자 workspace/query 주입 불가.
- 정답/첫 expected row 기반 변환 제거. 명시적 integer/integer-array 출력 schema, 빈 결과 지원. 정답은 검증기에만 존재.
- 명시적 transaction 및 no application retry. 별도 worker/요청 deadline, admission semaphore. acquire+BEGIN / RUN / 첫 행 / drain / COMMIT / total 계측.
- vendored neo4rs0.8.0 Txn lease 패치: 정상 COMMIT/ROLLBACK만 pool 반환, 취소·오류 Drop에서는 socket 폐기. upstream auto-commit 경로는 변경하지 않음. 이것은 upstream 그대로의 neo4rs 성능 비교가 아니다.

## 확인한 결과

| 검사 | 결과 |
|---|---|
| Rust 단위 검사 |3/3 (범위/타입/64bit 정수/외부 scope 주입) |
| SF1 serial, C8, C32 |각32/32; offered96=ok96+failed0+dropped0 |
| Python explicit transaction 대조 |32/32 기존 expected와 동일; Rust도 동일 |
| partial stream drop / syntax failure |다음 pool 재사용 전에 대상 transaction 잔여 없음 관측 |
| transaction execution deadline / PULL 취소 |timeout 발생, 대상 transaction 종료 관측, pool1 후속 성공 |
| 명시적 rollback |정리 및 후속 실행 성공 |
| TCP transport 절단 |직접 소유 proxy socket 절단; error 반환, 재사용 전 대상 tx 없음15표본, 새 연결 실행 성공 |
| 최종 서버 transaction |관측용 SHOW 외 잔여0 |
| 소유 자원 정리 |DB/adapter/probe PID 시작시각 검증 후 종료;17690/17691/18103 closed |
| 재현성 |원격 실행 Rust·contract·lock SHA와 로컬 동일 |

DB는 기존 소유 hard-tools SF1 clone을 사용했다. heap512/pagecache256MiB/workers32, pool16/fetch1000. 원본7687과 동료 전달 store/config는 변경하지 않았다. 별도 모델 실행·SF확대·쿼리/index 변경은 없었다. CPU는 공유 환경이며 독점 성능 측정이 아니다.

첫 a 실행은 observer가 이전 phase로 다음 rollback 테스트의 transaction을 집계한 경합 때문에 assertion 실패했다. PULL 취소 transaction은 terminated 후 사라졌으나 다음 쿼리가 잘못 포함됐다. b는 phase별 쿼리 marker를 구분해 판정했다. a 원시 자료는 `../neo4rs_adapter_20261101_a`에 보존했다. 실패 이력을 삭제하거나 두 실행을 합산하지 않았다.

## 아직 입증하지 않은 것

- debug build의 정확성 smoke이다. Rust가 빠르다거나 E2E 개선됐다는 증거가 아니다.
- 기존 Python auto-commit baseline과 transaction semantics가 달라, 성능 비교는 Python-over-HTTP explicit transaction과 맞춰야 한다.
- RUN timeout probe는 transaction 시작을 포함한다. 정확한 BEGIN/COMMIT wire 경계 fault injection, blackhole, application HTTP timeout/disconnect, graceful shutdown 및 장시간 부하는 미검증이다.
- 소켓 폐기 뒤 서버 cleanup은 비동기다. 즉시 cleanup 보장이라고 표현하지 않는다.
- acquire_begin_ms와 drain_ms는 복합 구간이며 server queue/순수 Bolt overhead가 아니다.
- 새 hard-tools120계약·모든 파라미터 분포·semantic counterexample 전체를 이번 Rust 경로로 다시 검증하지 않았다. 기존32prompt 범위의 동일성이다.

따라서 기본 backend 전환/E2E adoption 및 gug-dqa 완료 처리는 보류한다. 다음은 failure gate 보강 → release matched-driver 비교 → L4 admission/pool → L3/L4 query/index → memory/GC → 고정 모델 E2E 순서다. 동료에게 전달한 SF1 baseline은 그대로 유지한다.

증거: `validation/summary.json`, `serial.json`, `c8.json`, `c32.json`, `lifecycle-observations.json`, `transport-validation.json`, `cleanup.json`; `source-hashes.json`, `evidence.zip`. a의 build.log에3개 단위 검사 결과가 있다.
