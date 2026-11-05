> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/neo4rs_default_20261101_a/FINDINGS.md`; SHA256 `ca8cc37c7ca5d810a662e15d92ce27c17e23c02da8209f14d75b551632646bb3`.

# 기본 backend 전환 결과

사용자 명시 승인으로 활성 `handoff_isolation_eval.py`의 live tool backend를 neo4rs로 변경했다. `configs/tool_backend.json`에서 기본값을 읽고 `--backend python`은 명시적 대조/복구용이다. 자동 fallback은 없다. 과거 runner와 동결 SF1 package는 변경하지 않았다.

원격 release 빌드 완료. repo `<instance-path>`, 소유 서비스 root `service`. 독립 SF1 clone의 Neo4j17690 및 Rust HTTP18103을 기동했고 이번 검증 후 **의도적으로 실행 상태로 유지**했다. 모델 서버는 시작하지 않았다. 운영/종료는 `docs/NEO4RS_DEFAULT_BACKEND.md`의 PID/start-time 검사 기동기로 한다.

- backend 연결 단위 검사4/4: 기본 neo4rs dispatch, contract/pool 불일치, 원격 오류/재시작시 fallback 금지, bound query 변조 거부.
- 기본 factory에서 backend 인자를 생략한 실제 SF1 tool 요청: serial/C8/C32 각32, 총96=96ok+0failed+0dropped. 결과는 기존 expected와 동일.
- 실제 응답에 adapter instance/request ID와 Rust timing이 기록됐다. source/config SHA는 원격/로컬 동일하다.
- 최종 잔여 transaction0, 상태 검사에서 소유 Neo4j/adapter 모두 alive. pool16/admission16/fetch1000/runtime thread1/deadline15s/Bolt worker32.
- py_compile/기존 validate_results 통과, 동결 handoff manifest 동일.

이는 기본 실행 경로 변경 및 DB tool 경로 검증이다. BF16 모델 tool routing과 최종 생성을 다시 실행한 E2E 검증이 아니며, Python 대비 성능 개선·모든 장애조건 안전성을 입증하지 않는다. 기존 lease/lifecycle 검사는 `../neo4rs_adapter_20261101_b`에 보존했다. 정확한 BEGIN/COMMIT 경계·blackhole·HTTP disconnect·장시간 검증 항목은 gug-dqa에 남긴다.

기본 service와 client는 시작시 contract/schema/query<instance-path> URI를 대조한다. backend metadata는 구성 선언이며 DB 데이터의 독립적 증명은 아니다. 본 smoke는 검증된 SF1 snapshot SHA와 기존 소유 clone, 요청별 정답 비교를 사용했다.

패키지 후처리의 첫 SHOW TRANSACTIONS 명령은 shell quoting 오류로 실패해 수정 후 다시 실행했다. workload96건 자체에는 오류가 없었으며 최종 postflight.json은 정상 쿼리의 결과다.
