> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/NEO4RS_TUNING_RESULTS.md`; SHA256 `ed9629072f89fdaac9e1a93ce81e1538b8e353c3fd94da334e230d154e004fa8`.

# SF1 neo4rs: 단독 개선과 조합 검증 결과

Subsequent fixed-BF16 E2E is now complete: 864/864correct and bounded performance outcomes [see original archived attachment]. DB gains did not imply universal E2E gains; the original DB-only findings below remain intact. Current baseline service moved to <instance-path>

현재 SF1 도구 분포에서 권장할 최소 후보는 **heap1GiB +page cache1GiB +pool16/admission16 +Bolt workers32**, 기존 Cypher/fetch1000/Rust thread1이다. L4별 cap과 추가 relationship index, 자동 접근 경로 분기는 채택하지 않는다. 재현 설정 [see original archived attachment]. 이 후보는 DB 실행 검증을 통과했으며, 실제 BF16 모델을 포함한 E2E 및 장시간 용량 검증은 다음 단계다. 기본 서비스는 비교 가능한 기존 설정으로 복구했고 후보는 별도 보존했다.

## 무엇을 검증했는가

고정 SF1·실제 업무 tool 인자·동일 독립 expected에 대해 단독12조건, 4요인16조합(L4cap/pool/L3rewrite/heap), 후속5조건(heap/cache/index/adaptive query)을 각3block 반복했다. 혼합 부하80요청(5tool×양성8/음성8)과 L4양성8×4=32요청에 C32를 가했다. 전역 index 생성은 소유 clone에서만 수행했다. 전체182+46+10=238측정 cells의12960요청은11917정답 완료+1043실패+0drop이며, 성공 응답의 정답 불일치는0건이다. 실패도 분모에 포함했다. 별도warmup695/695정답. PROFILE/EXPLAIN 및 fixture는 이 timed cells와 분리했다.

실패1043건은 transaction-memory470, deadline558, Bolt NoThreadsAvailable15로 분류했다. 첫 matrix의 source/config와 응답 instance ID·retry0, 인자·정답을 원시 로그로 재검증했다. 첫 matrix는 Python 대조6cells를 포함하며 runtime metadata 검증은 Rust232cells에 적용했다.

## 같은 후속 실험에서 조합을 비교한 결과

모든 시간은 **배치 완료 시간의3block 중앙값**이다. RPS/개별 query latency/steady capacity가 아니다.

| 구성 | 혼합 부하 정답/요청 | 혼합 시간 | L4 집중 정답/요청 | L4 집중 시간 |
|---|---:|---:|---:|---:|
| 기존 heap512/cache256MiB |240/240|10.026s|42/96|8.821s*|
| heap1024/cache256MiB |240/240|9.621s|96/96|9.546s|
| **heap1024/cache1024MiB** |**240/240**|**3.602s**|**96/96**|**3.633s**|
| 위 구성+TRANSFER(ts) index |240/240|2.416s|96/96|2.975s|
| 위 구성+index+L4 분기 |240/240|3.140s|96/96|6.249s|

*기존 L4 조건은54건 실패했다. 더 빨리 실패한 시간을 성공한 조건보다 유리하게 비교하지 않는다. Heap만 올린 성공 조건 대비 heap/cache 조합은 혼합 약2.67배, 집중 약2.63배 짧은 배치 시간을 보였다. 혼합 기존 조건도 모두 성공했으므로10.026→3.602s(약2.78배)의 같은 성공 분모 비교가 가능하다.

단일 첨부용 그림 [see original archived attachment], PDF [see original archived attachment]. 첫 matrix 전체 표 [see original archived attachment]와 원인·한계 [see original archived attachment]도 보존했다.

## 채택과 제외 근거

Heap512→1024MiB는 L4 동시 실행의 안정성을 개선했다. 다만 Neo4j 기본 transaction-memory 한도도 heap에 연동되므로, 이를 GC 튜닝만의 효과라고 부르지 않는다. Page cache만 올린 첫 단독 조건은 mixed240/240이지만 fanin52/96이었다. 안정성과 시간 개선을 함께 얻으려면 이 workload에서는 heap/cache 조합이 필요했다.

작은 pool/admission/L4cap은 메모리 동시 사용량을 낮출 수 있지만15s 예산 안에서는 대기가 늘어 타임아웃이 발생했다. Runtime threads4, fetch100, workers64만으로도 오류가 없어지지 않았다. L3 재작성은 이번 전체 분포에서 기본 쿼리를 일관되게 이기는 근거가 부족해 채택하지 않았다. 이것은 별도의 L3 단독 개선 결과를 반증하지 않으며, workload별 이득을 전체에 합산할 수 없다는 뜻이다.

Index 조건이 시간 순위상 가장 빨랐지만, 사후 양방향 순서 진단의**32PROFILE 모두 기본 L4에서 Index 연산자를 사용하지 않았다**. 통계/재계획·warm 상태·공유 자원 차이 등이 추가 이득의 후보이며, 직접 index 탐색 효과는 입증되지 않았다. 그래서 불필요한 인덱스를 필수 설정으로 넣지 않았다. 96개 진단 기록과 판단 [see original archived attachment]. 단순1%시간폭 기준의 L4 분기는 의미는 맞았지만 집중 부하를 느리게 해 제외했다. `gug-xox`의 일반적 선택도 라우팅 검증은 여전히 미완료다.

## 최소 후보 최종 확인

인덱스를 뺀 heap/cache 후보를 실제 release neo4rs 경로에서 따로 확인했다. 전체120계약120/120, 재사용40계약×C8/C32×3block240/240, 기존8tool32계약×3block96/96으로 **456/456정답, 실패/누락0**. Warmup50/50도 정답이다. 재사용 validation 배치 중앙값 C8 2.654s/C32 2.610s, 기존8tool32요청 중앙값1.520s. 원시 검증 요약 [see original archived attachment]. 과거 노출된 계약이며 unseen/generalization 증거로 부르지 않는다.

후속의 index 포함 후보도 reusedvalidation240/240와 기존96/96을 통과했고 C32 도구별p95는 전부 기존 baseline보다 낮았다. 이 검증을 index가 없는 최종 후보의 동일한 paired 비교로 대체하지 않는다. 최종 최소 후보는 별도456건 검증과 앞선 counterbalanced 조합336건으로 지지한다. 추가 쿼리의 의미 반례와 분기 경계는 독립 fixture182/182를 통과했고 해당 fixture 잔여0을 확인했다.

## 해석 범위와 복구

물리 disk read가 병목이라는 증거는 없다. Java 샘플 read_bytes 증분은 첫 matrix0, followup102400bytes, 최소후보0이었다. Page cache 이득을 곧바로 물리 disk I/O 감소로 설명하지 않는다. NUMA binding, CXL, GC 알고리즘, Linux filesystem, SF10, free-form multi-turn 및 모델 E2E는 이번 검증 범위 밖이다.

첫 matrix 중 외부 GPU 작업PID996731이271샘플에서 관측됐다. 우리 실험은 DB-only였으나 CPU/DRAM/cgroup은 공유하므로 완전한 자원 독점을 주장하지 않는다. 후속과 최소 후보 확인의 GPU process 샘플은 비어 있었다. Affinity는 코어 예약이 아니며3block finite cohort만으로 전역 최적·장시간 안정성을 입증할 수 없다.

각 run 소유 DB/adapter cleanup 확인, 임시 index 제거, fixture 제거 및 clone label/type 건수의 기존 baseline 일치를 확인했다. 기본 서비스는 `<instance-path>`에 기존 heap512/cache256 설정으로 복구했다. 원본7687의4083nodes/27429edges와 frozen handoff20파일 hash 동일, 열린 잔여 업무 transaction0. 모델은 기동하지 않았다. 첫 원본 확인은 인증 설정 누락으로 실패했고 외부 secret을 읽는 기존 인증 방식으로 수정해 통과했으며, 서비스 재기동은 중복 실행하지 않았다. 최종 복구 증거 [see original archived attachment].

Beads gug-p96의 범위는 이 DB 후보 검증이다. neo4rs 장애 lifecycle/steady/E2E 검증(gug-dqa)은 남겨 둔다. Git/Dolt 원격 동기화는 수행하지 않았다.
