> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/NEO4J_RUNTIME_FINDINGS.md`; SHA256 `178b8646390ff298e375b9175690cf48550ed74753d8b20ab71da4465c59dec6`.

# BF16 반복과 Neo4j 런타임 검증

메모리·Bolt·Cypher 실행 수치는 측정 결과표 [see original archived attachment]에 기록했다. 원시 기록은 `results/runtime/`, 절차는 `RUNTIME_STUDY_PROTOCOL.md`, 작업 추적은 gug-bg9이다.

현재 workload에서는 page cache 증설의 일관된 개선이 없었고, Cypher L5/L6 재작성의 개선이 가장 뚜렷했다. 다만 복원 DB의 물리 파일은 약11.3MiB로 원본 약11.8GiB와 다르다. 이 결과로 원본 page cache를 줄이거나 대규모 데이터의 최적 메모리를 확정하지 않았다.

긴 워밍업 비교에서도 일부 셀의 시간 추세가 남았으므로 전역 최적점을 주장하지 않는다. 원본 설정은 보존했고, 별도 DB의 heap512MiB/pagecache64MiB는 비교용 운영점이다. 설정과 적용 범위는 `configs/neo4j_runtime_baseline.json`에 저장했다.

BF16 호출 수 대조군은 별도 결과 [see original archived attachment]에 기록했다. 원래 인자3회와 batch8192 보조군10회를 분리했으며, 모두6episode당15 LLM 호출이었다.

## 해석 원칙

메모리·하드웨어 → Bolt → Cypher 순서로 한 요인씩 비교하는 접근이 적절하다. 다만 처음부터 정답·기준 Cypher·인덱스·실행계획은 고정해야 한다. 마지막에 쿼리를 재작성한 후에는 메모리·동시성 설정을 다시 확인해야 한다. 실행계획 변화가 working set과 thread 점유 시간을 바꾸기 때문이다.

“최적 메모리”는 하나의 영구 상수가 아니다. 물리 데이터 크기, 활성 working set, 결과 바이트, 동시 transaction 수가 바뀌면 다시 검증해야 한다. 현재 작은 FinBench snapshot의 값으로 이후 scale/multi-user 실험 전체를 고정하면 안 된다.

## 실제 하드웨어와 메모리

- Neo4j5.26.0 Community; Python 공식 드라이버5.28.2. Rust bolt-rs는 현재 경로에 없다.
- 컨테이너 CPU quota 약30.72코어, memory limit 약342GiB. 호스트의256논리 CPU나 약1TiB RAM을 할당 가능 자원으로 취급하지 않는다.
- 기존 Neo4j heap initial512MiB/max2GiB, pagecache2GiB, Bolt worker min5/max400. JVM PSS 약3.94GiB, VmSwap0.
- 동시 실행 스냅샷에서 vLLM API+engine PSS 합계 약3.58GiB. GPU weight/KV 메모리는 이 CPU DRAM 수치와 별개다.
- cgroup anon 약8.77GiB, file189.59GiB. file에는 shmem43.74GiB가 포함되므로 중복 합산 금지. 당시 memory pressure avg10/60/300은0; 누적 OOM 카운터0. 이 스냅샷은 DRAM 부족 가설을 지지하지 않지만, 다른 시점의 압력이나 bandwidth contention을 배제하지는 않는다.
- GPU는 NUMA0. Neo4j resident page는 약65% NUMA0, vLLM engine은 약90% NUMA1. 이는 배치 관측이며 remote access 비용 측정이 아니다. child-process `set_mempolicy` probe가 EPERM을 반환해 강제 메모리 배치 비교는 수행하지 못했다. CPU affinity만 변경한 결과를 NUMA memory 효과로 해석하지 않는다.
- 현재 논리 그래프는4083nodes/27429relationships인데 기존 store 약11.8GiB, transaction logs 약47GiB. `neo4j-admin server memory-recommendation --memory=16g`는 native data/index 약11800MiB라고 보고한다. 이것은 파일 기반 sizing 입력이며 활성 working set 측정이 아니다. 과거 데이터의 빈 공간 등 원인은 가능성이며 파일 크기만으로 확정하지 않는다.

기존 DB를 재시작하거나 로그를 삭제하지 않았다. 별도 인스턴스의 논리 복원은 물리 배치를 바꾸므로 기존 store의 압축/복사 또는 같은 physical-layout A/B로 부르지 않는다.

## Bolt와 계측의 경계

기존 `study_load.py`/`evaluate.py`는 auto-commit query의 결과를 모두 소비하고 session을 닫은 다음 LLM을 호출한다. 정상 경로에서 LLM decode 동안 transaction을 유지하는 구조가 아니다. 다만 `asyncio.to_thread`의 외부 취소는 실행 중인 동기 드라이버 호출을 즉시 중단하지 않는다. query timeout과 정리 완료를 확인해야 한다.

클라이언트 pool은 connection을 관리하고, 서버 pool은 처리 작업/활성 transaction의 thread 점유와 관련된다. “실행 요청 + 열린 transaction”은 중복될 수 있으므로 단순 합산하지 않는다. pool 최대치의 합보다 server worker를 크게 설정하는 규칙 역시 보편적 최적값이 아니다. 실제 활성 transaction, CPU quota, heavy/light mix, burst, timeout과 client admission을 함께 측정한다.

RUN 반환 시간은 pool 획득·네트워크·서버 처리 등을 포함한다. PULL 이후 drain도 실행·전송·decode가 섞인다. client timing만으로 server queue와 planning을 정확히 분리했다고 주장하지 않는다. canary RETURN1도 합성 지연이며, 별도 pool이 아니면 client pool 대기도 섞인다. jstack의 WAITING만으로 idle-pinned transaction을 입증할 수 없다.

Neo4j5는 literal을 자동 파라미터화할 수 있다. 따라서 값이 바뀐 query string마다 전체 plan miss라는 설명은 부정확하다. 명시적 parameter와 안정된 query 구조는 계속 권장하지만, 캐시 적중 여부를 직접 관측하지 못하면 plan-cache 원인으로 확정하지 않는다.

## 고정 설정과 다음 scale 실험

현재 생산 경로의 메모리 설정은 그대로 보존한다. initial=max heap은 다음 통제된 재시작 시 비교할 후보이며 자동 적용한 변경이 아니다. 파일 기반 recommendation에서 나온 heap5GiB/pagecache7GiB를 그대로 적용하지 않는다. 이 서버에는 vLLM·shmem·관측 프로세스도 함께 있고, 큰 store가 현재 working set과 같지 않다.

이후 규모를 늘릴 때는 `(working-set bytes / pagecache bytes)`, heap에서 수용 가능한 동시 query 수, 결과 byte, query shape, degree/hop/property 선택도를 별도 축으로 둔다. CPU 분할은 실제 quota 내에서 계획하고 NUMA 배치 권한과 메모리 bandwidth 계측이 가능한 환경에서 재검증한다. 메모리를 크게 주는 것과 CPU/NUMA 경합이 줄었다는 것은 다른 주장이다.

## 근거 문서

- [Neo4j5 memory configuration](https://neo4j.com/docs/operations-manual/5/performance/memory-configuration/): heap initial/max, pagecache, OS/native memory 구분.
- [Neo4j5 parameter auto-parameterization](https://neo4j.com/docs/cypher-manual/5/syntax/parameters/): literal 변경과 plan 재사용의 관계.
- [Bolt thread pool](https://neo4j.com/docs/operations-manual/current/performance/bolt-thread-pool-configuration/): active transaction과 thread 점유. 최신 문서의 모든 설정을5.26지원으로 간주하지 않고 실제 SHOW SETTINGS를 보존했다.
- [Python implicit transactions](https://neo4j.com/docs/python-manual/current/query-advanced/): full consumption과 transaction 종료. 최신 문서의6.2 auto-retry 변경은 배포된5.28.2에 적용하지 않았다.
