> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/VLLM_RESEARCH_HANDOFF_SUMMARY.md`; SHA256 `878bbc3fb6d1cffe0b1d19813bc55767b9e995b236d548874de543cba04a8e82`.

# Graph-CoT / Neo4j → vLLM 연구 인수인계 요약

작성 2026-11-03 · 기존 완료 실험을 재검토한 자료. 새 원격 실험·설정 변경·모델 변경은 하지 않았다. 아래 수치는 서로 다른 실험의 결과이며 합산하거나 개선 배수를 곱하지 않는다. 최신 비교는 **고정 BF16 + neo4rs memory E2E**다. 과거 동결 bundle과 현재 다른 협업자의 FP8/multi-turn 실행은 별도 기준선이다.

## 먼저 전달할 핵심

> FinBench SF1에서 Cypher 접근량과 동시 transaction 메모리 문제를 확인하고, 쿼리·heap/page cache·pool/admission·Bolt를 따로 그리고 조합해서 검증했다. DB 시간과 성공률은 개선됐다. 하지만 같은 evidence와 token 작업량을 유지한 실제 모델 E2E에서는 전체 배치가 항상 빨라지지 않았고, 사용자 C32에서는 오히려 느려지는 경우가 반복됐다. 다음 과제는 DB 반환 후 모델 재진입의 admission·batching·긴 응답의 tail을 개선하는 것이다. CPU scheduler 계산 포화나 KV 재계산을 이미 입증했다는 뜻은 아니다.

Neo4j부터 vLLM까지의 결과 한 장 [see original archived attachment]

그림 A는 과거 Python/Cypher 실험, B는 neo4rs DB-only 메모리 실험, C/D는 실제 BF16 E2E다. 각 패널의 workload와 반복 수를 함께 읽는다. D의 빈 앞 구간은 해당 episode의 이전 작업이며 GPU idle을 뜻하지 않는다. PDF [see original archived attachment], 수치·출처 JSON [see original archived attachment], 생성 코드 [see original archived attachment].

## 1. 실행 구조와 연구 목적

```text
독립 사용자 요청 × C                 C는 사용자 수, DB 동시 실행 수가 아님
       │
       ▼
LLM pass 1: 도구 선택 + 실제 arguments 생성
       │  tool_calls JSON
       ▼
Registry: 타입/범위/시간/계좌 구간 검증
       │  검토된 template 선택; workspace는 신뢰된 dataset에서 주입
       ▼
HTTP /tool → Rust neo4rs adapter
       │  admission → connection acquire + BEGIN
       │  RUN → 첫 행 → 전체 drain → COMMIT
       ▼
Neo4j: query plan / execution / heap / page cache / Bolt workers
       │  rows + bytes + 구간별 timings
       ▼
LLM pass 2: evidence와 기존 context로 final 생성
       │  enqueue → engine queue → first schedule → decode
       ▼
정답·evidence·token·실패·사용자별 E2E 검증
```

핵심 완료 연구는 **single-agent, tool 1회, model 2회**다. 다중 사용자는 독립 context를 사용한다. 자유 Cypher 생성·반복 수정 또는 runtime multi-agent의 검증 결과로 바꾸어 설명하지 않는다. 초기 자유 Cypher/정밀도 pilot과 최신 고정-template 성능 연구는 평가 대상이 다르다.

`model-only`는 도구 없이 답하는 독립 기준선이다. `live handoff`는 실제 DB를 실행한다. `replay`는 같은 evidence를 반환하고 DB 실행을 생략하거나 기록된 반환 지연을 재현한다. 입력·호출 수가 다른 model-only와 handoff의 시간 차이를 순수 tool overhead라고 부르지 않는다. Live/replay 차이도 batching 변화가 포함될 수 있다.

## 2. 8개 업무 tool과 조절한 비용 축

L1~L6은 단일한 난이도 점수가 아니라 접근 패턴 구분이다. 실제 비용은 데이터 분포·degree·선택도·양성/음성·결과 크기·동시성에 따라 달라진다.

| ID | 실제 tool name | 의미 / 주요 인자 | 분석한 비용 |
|---|---|---|---|
| L1 | `list_transfer_recipients` | 직접 송금 수취인; account/min_amount/limit | point lookup + 1-hop + 중복 제거 |
| L2 | `trace_transfer_reachability` | 1~3-hop 도달 계좌; account/max_hops/limit | degree × hop 확장 |
| L3 | `trace_time_ordered_fund_paths` | 시간순 3회 송금 경로; account/time/amount/limit | 경로 중간 상태·시간 조건 |
| L4 | `find_fan_in_accounts` | 다수 송신자가 모인 계좌; time/min_senders/limit | 전역/상관 집계·transaction memory |
| L5 | `find_linked_account_pairs` | 보증·소유·공유 접속·송금 조건의 계좌 쌍 | 후보 선택 순서·JOIN/EXISTS |
| L6 | `rank_linked_account_flows` | 연관 쌍의 양방향 송금 합계; 상환 계좌 제외 | 후보 축소·집계·정렬/top-k |
| P1 | `trace_rapid_pass_through` | 시간 간격 제한 2회 송금 | temporal traversal·월별 bucket |
| A1 | `find_shared_access_accounts` | 공유 Medium 접근 계좌 | 공유 매체 fan-out·중복 제거 |

Medium을 실제 IP/device라고 단정하지 않는다. 공유 접속/연관 계좌는 조사 단서이며 불법성의 증명이 아니다. schema [see original archived attachment], 정의·바인딩 [see original archived attachment].

예시: 다음 인자는 **구조 설명용**이며 해당 계좌의 실측 결과를 주장하지 않는다.

```json
{"name":"list_transfer_recipients",
 "arguments":{"account":123456,"min_amount":1000,"limit":5}}
```

실제 L1 template은 다음과 같다. 모델이 query 문자열을 전달하는 인터페이스가 아니다.

```cypher
MATCH (a:Account {acct_no:$account,_workspace_id:$workspace})
      -[r:TRANSFER]->(b:Account {_workspace_id:$workspace})
WHERE r.amount >= $min_amount
RETURN DISTINCT b.acct_no AS account ORDER BY account LIMIT $limit
```

실제 Python 바인딩/실행 API를 이용한 설명용 snippet:

```python
# dataset은 snapshot hash 검증을 마친 Dataset 객체다.
registry = Registry(dataset, query_dir=chosen_query_dir)
bound = registry.bind("list_transfer_recipients",
                      {"account": 123456, "min_amount": 1000, "limit": 5})
with open_backend(registry, backend="neo4rs", endpoint=tool_endpoint,
                  pool=16, bolt_uri=bolt_uri) as backend:
    rows, spans = backend.execute(bound)
# oracle/expected는 이 실행 경로에 입력하지 않는다.
```

`Registry.bind`는 실제 call arguments를 검사하고 `workspace`를 dataset에서 채운다. L2 hop은 1/2/3의 검토된 template을 고르는 값이다. Rust도 타입/범위/unknown keys를 다시 검증한다. 현재 backend는 `/metadata`의 dataset/schema/query contract와 instance identity를 확인하고 `/tool`로 tool/arguments만 전송한다. 자동 Python fallback은 없다. 물리 query arm은 query-dir/SHA로 구분하며 legacy `query_version` label만 믿지 않는다.

## 3. 데이터·baseline을 혼동하지 않기

| 구분 | 내용 | 전달 시 주의 |
|---|---|---|
| 초기 작은 원본 | 4,083 nodes / 27,429 relationships, 기존 7687 | SF1이 아님. 큰 과거 store 파일과 현재 논리 working set도 다름 |
| 공식 SF0.1 | 55,604 nodes / 309,577 relationships | 인자·oracle을 SF별로 준비 |
| 공식 SF1 canonical | 556,082 nodes / 3,101,291 relationships | snapshot 전체 속성·병렬 관계 감사 |
| SF1 store sidecar | 월별 bucket 등 포함 총 1,030,851 nodes / 4,370,240 relationships | canonical count와 다름; 고정 도구는 원래 관계 조회 |
| SF10 | 원본 streaming inventory만 검사 | 이 연구에서 Neo4j 적재·SF10 E2E는 하지 않음 |

과거 SF1 bucket 실험에서 store는 약1.397→2.114GB(+51.4%)였다. 큰 파일이 곧 disk-bound라는 증거는 아니다. 현 연구 SF1 최대 transfer outdegree597은 초대형 hub의 검증이 아니다. physical-scale 결과 [see original archived attachment], 공유 환경 [see original archived attachment].

| 비교 기준 | DB/driver | 모델·출력 예산 | 역할 |
|---|---|---|---|
| 과거 동결 `sf1-vllm-v1` | optimized query, Python pool16, H512/P256, workers32 | BF16, context8192, output800 | 이미 E2E 검증한 불변 bundle |
| 최신 memory E2E 기준 arm | 같은 동결 query, release neo4rs pool/admission16, H512/P256 | BF16, context8192, output2048 | 아래24cell의 기준 |
| 최신 memory E2E 후보 arm | 위 조건에서 H1024/P1024만 변경 | 동일 모델/생성 예산 | DB 후보의 실제 E2E 검증 |
| 11/3 별도 협업 실행 | SF1 17690 직접 Bolt, original query, Python bridge | FP8 config, context32768/output4096, thinking high | 64건 Poisson 도착·세션1/4 비교. 본 BF16 결과와 미혼합 |

마지막 행은 11/3 읽기 전용 프로세스·코드 확인 시점의 관측이다. 이 보고서의 검증된 속도 결과로 포함하지 않는다. 서비스 이름과 checkpoint config는 구분한다. 다른 작업을 중단하거나 그 설정을 바꾸지 않았다.

## 4. 단계별 검증 결과

| 단계 | 변경/통제 | 관측과 판정 | 근거 |
|---|---|---|---|
| 정밀도 pilot | BF16 / FP8 W8A16 / AWQ W4A16 | 선택12/12씩; 6episode model calls15/17/20. 양자화 단독 인과 미확인 | ablation [see original archived attachment] |
| 파라미터 tool | scenario ID 대신 실제 업무 인자 | 실제 call→query→DB 경로 검증; 6tool144/144 후 8tool256/256 | 업무 결과 [see original archived attachment] |
| 물리 scale | SF0.1/SF1, memory/Bolt/bucket | SF1 PC64→256MiB에서 C8 성공20/32→32/32; heap/Bolt만 증가하면20/32 유지 | scale [see original archived attachment] |
| SF1 Cypher E2E | reference/optimized, cache/C/rate | C1 전체시간 약21% 감소; C8 개선, C32 burst 역전 | query E2E [see original archived attachment] |
| 반환 timing 분리 | 실제 DB vs 같은 evidence/timing replay | DB 업무 실행 없이도 C32 역전 재현; 단순 final-cap8 악화 | isolation [see original archived attachment] |
| 어려운5tool | 120 oracle 계약, 양성/음성, 3block | 3,840건 중42 transaction-memory 오류; 별도720/720 | hard tools [see original archived attachment] |
| neo4rs 조합 | heap/cache/pool/worker/query/index | 238cell; 최소 후보456/456, 실패/누락0 | tuning [see original archived attachment] |
| 실제 memory E2E | 같은 BF16, 2memory ×2workload ×2C ×3block | 24cell864/864; DB 개선, Hard40/C32 배치+3.28% | 최신 E2E [see original archived attachment] |

과거 외부 GPU 간섭으로 중단한 window 연구의14/18cell은 별도 partial이며 완료 결과로 합치지 않는다. Beads·AGENTS·ownership/hash/원시 결과 보존 체계도 구성했지만 Git/Dolt remote 동기화 완료는 주장하지 않는다.

## 5. Neo4j에서 무엇이 개선됐는가

### Cypher 접근량

256MiB cache, 도구별4개 normal 실행의 중앙값. PROFILE은 별도 진단이다.

| 도구 | 기존→개선 DB초 | PROFILE DB hits 기존→개선 |
|---|---:|---:|
| L4 |6.234→0.234|약7.8–8.0M→0.61–0.62M|
| L5 |7.586→1.283|46.28M→6.83M|
| L6 |4.611→1.114|26.01M→6.57M|

개선 방식은 후보를 먼저 좁힌 뒤 필요한 관계를 탐색·집계하는 것이다. 방향/서로 다른 소유자/병렬 송금/상환 제외/정렬/LIMIT 의미를 반례로 검증했다. 초기 공식 L5/L6 인자는 빈 결과였으므로 이 성능을 양성 전체로 일반화하지 않는다. 더 큰 cache에서도 개선이 유지돼 query work 자체가 중요했다. query 코드 [see original archived attachment].

### 선택도와 index

이전 진단에서 L4의 좁은 시간1%구간은 reference+TRANSFER(ts)4.25ms 대 indexed optimized353.0ms였다. 실제 `DirectedRelationshipIndexSeekByRange`가 reference에서만 나타났다. 넓은 구간은 반대로 optimized가 유리했다. 이후 neo4rs 후보의 빠른 index-on 조건은32PROFILE 모두 L4에서 index 연산자가 없었으므로 **그 속도 차이를 직접 index 효과로 인정하지 않았다**. 두 연구는 query shape/파라미터별로 다르며 모순이 아니다. 임계값 자동 routing은 미완료다. 진단 [see original archived attachment].

### Heap/page cache와 admission 조합

아래는 후속3block 배치시간 중앙값이다. H/P 단위MiB.

| 구성 | mixed 정답/전체 | mixed초 | L4집중 정답/전체 | L4집중초 |
|---|---:|---:|---:|---:|
| H512/P256 |240/240|10.026|42/96|실패 arm, 속도 비교 제외|
| H1024/P256 |240/240|9.621|96/96|9.546|
| H1024/P1024 |240/240|3.602|96/96|3.633|

최소 후보는 H1024/P1024/pool16/admission16/workers32/fetch1000/Rust thread1이다. Heap에 연동되는 transaction-memory 한도도 바뀌므로 순수 GC 효과로 부르지 않는다. 작은 pool은 메모리 사용량을 줄이지만 대기로 timeout을 만들 수 있다. 초기 hard-L4 pool8은96/96, pool16은68/96이었고, 성공률이 다른 arm의 빠른 종료를 성능 이득으로 세지 않았다. workers64나 thread4만으로 문제는 해소되지 않았다.

### Graph modeling·GC·disk·NUMA

월별 bucket은768/768 정답이었지만 E2E 이득이 명확하지 않았고 저장공간+51.4%였다. 채택된 범용 개선으로 부르지 않는다. 물리 read_bytes 증분은 주요 비교에서0 또는 미미했고 page-cache 예산 효과를 물리 disk miss 감소로 증명하지 못했다. GC 알고리즘 교체·CXL·대형 hub·NUMA 원격 접근 비용은 미검증이다. affinity는 CPU 예약이나 memory binding이 아니다.

## 6. neo4rs: 성능과 transaction 안전성을 구분

현재 기본 backend는 neo4rs다. 0.8.0 vendored Txn lease 패치가 있으며 **upstream 그대로의 Rust vs Python 성능 비교가 아니다**. 정상 COMMIT/ROLLBACK에서만 연결을 재사용하고 취소/오류 시 socket을 폐기한다. 부분 결과/PULL 취소/실행 timeout/구문 오류/rollback/소유 proxy TCP 절단에서 transaction 정리와 후속 pool 재사용을 검사했다. 서버 cleanup은 비동기이며 전체 fault lifecycle·장시간 안정성을 완료했다고 하지는 않는다.

실제 Rust 실행 흐름 발췌(오류 처리/계측 일부 생략):

```rust
let mut tx = g.start_txn().await?; // connection acquire + BEGIN 포함
let mut stream = tx.execute(q).await?;
while let Some(row) = stream.next(tx.handle()).await? {
    // schema에 맞춰 decode; 모든 결과 소비
}
drop(stream);
tx.commit().await?;
// 여기까지 완료하고 evidence를 LLM에 전달한다.
```

설명용 축약으로 실행 가능한 함수 전체가 아니다. 실제 구현 [see original archived attachment], lease patch [see original archived attachment], HTTP backend [see original archived attachment].

| 계측 필드 | 실제 경계 | 곧바로 해석하면 안 되는 것 |
|---|---|---|
| tool_admission/admission | 도구별/전체 semaphore 대기 | server queue |
| acquire_begin | 연결 획득 + transaction 시작 | 순수 pool wait |
| run / first_row / drain | 실행·메시지·전송·decode 혼합 | 각각 순수 planning/실행/네트워크 |
| commit | client가 관측한 commit 구간 | 서버 CPU 시간 |
| 외부 db_ms | HTTP/client/adapter 전체 | 순수 Neo4j 실행시간 |

## 7. 실제 BF16 E2E에서 남은 문제

고정 BF16/TP1/seq8/batch8192/context8192/KV12GiB/OMP4. 동일8개 도구/schema/고정Cypher, release neo4rs. **메모리 arm만 변경**했다. 출력2048/episode180s는 과거800/120s 연구와 다르므로 절대시간을 직접 합치지 않는다.

| workload / 사용자C | H512/P256 배치초 | H1024/P1024 배치초 | 후보 변화 |
|---|---:|---:|---:|
| Frozen32 /8 |48.671|48.678|+0.02%|
| Frozen32 /32 |46.578|46.038|−1.16%|
| Hard40 /8 |131.709|130.668|−0.79%|
| Hard40 /32 |132.933|137.297|**+3.28%**|

3block 중앙값의 비율이다. Hard40/C32는 각 paired 반복도+4.54/+4.04/+0.40%로 모두 느렸다. 반면 request p95는94.362→92.491s로 약1.98% 개선됐다. Batch drain과 p95는 다른 목표다.

검증: measured864/864, warmup156/156, 실패/drop0. 1,020개 독립 context /2,040개 model call 모두 engine join. 432개 paired 입력은 evidence bytes/hash·prompt/completion token·실제 scheduled token이 동일했다. Canary2,163/2,163, monitor error0. 이는 알려진 계약의 성능·정확성 검증이며 unseen/free-form 정확성 증명은 아니다.

관측상 사용자C32에도 **tool HTTP 실제 overlap peak3~4**, model waiting peak24였다. 따라서 DB-only C32 집중 부하를 실제 E2E에서 재현한 것이 아니다. Hard40/C32 fan-in HTTP 중앙값은3.377→1.384초였지만 같은 마지막 L3 요청의 final queue는 다음처럼 늘었다.

| block | 기준 queue초 | 후보 queue초 | final 출력 |
|---|---:|---:|---:|
| 1 |42.847|50.253|1,728tokens 동일|
| 2 |43.960|49.845|1,728tokens 동일|
| 3 |48.164|48.710|1,728tokens 동일|

Final decode는 약63.5~64.3초로 비슷했다. block2의 tool readiness는23.904→23.572초로 빨라졌지만 first schedule은68.055→73.474초로 늦어졌다(batch 시작 기준). 단순 queue 길이보다 **enqueue·first schedule·completion의 절대시점**이 중요하다. 3block 원그림 [see original archived attachment].

독립된 이전 timing-only 연구에서도 업무 DB 실행 없이 짧은 tool 지연만으로 C32 burst 역전(완료episode/초−7.48~−9.33%)이 재현됐고 C8은+23.82~+24.71%였다. 따라서 DB CPU 경합은 그 역전의 필수 조건이 아니다. 단순 final-cap8은 대기를 client로 옮기고−1.48~−2.57% 악화해 채택하지 않았다. 모든 shared CPU/GPU 영향을 제거했다는 뜻은 아니다.

## 8. vLLM 담당자에게 요청할 다음 검증

아래는 **검증 완료 해법이 아니라 제안**이다. 과거 동결 baseline과 최신 neo4rs 중 어떤 기준을 쓸지 먼저 명시하고 한 실험 안에서 고정한다. 현 별도 FP8/multi-turn 작업의 수치를 BF16 single-tool 대조군으로 쓰지 않는다.

| 순서 | 변경 변수 | 유지할 것 | 확인할 것 |
|---|---|---|---|
| B0 | 기존 기준선 재현 | model/SHA, DB/query/index, tools, 입력/출력 예산 | oracle/evidence/tokens/실패 분모, 실제 overlap |
| B1 | engine seq8→16 단독 | KV12GiB/batch8192/정책 등 | KV/preemption/실배정량, tail·품질; 준비 검사 후 실행 |
| B2 | final 재진입 admission/phase 정책 | 선택한 seq와 같은 workload | FCFS/priority-all0/phase 후보 대조, initial starvation |
| B3 | 유망 요인 조합 | 같은 데이터·정밀도 | 단독효과와 상호작용, per-tool fairness |
| B4 | 지속 closed/open-loop | 고정 offered mix | burn-in/steady/drain 분리, offered 성공률·SLO·backlog |

정책에 oracle 정답 길이/미래 completion token을 사용하지 않는다. Final reentry 우선권도 긴 요청·첫 요청의 기아를 만들 수 있다. 단순 cap8 재적용보다 절대 admission·remaining work·batch overlap을 계측해 개입을 설계한다. KV eviction/recompute/prefix hit은 metric/trace가 있을 때만 원인으로 언급한다. 큐가 길다는 이유만으로 CPU scheduler 연산 병목이라고 부르지 않는다.

권장 trace 계약(설계 예시):

```python
event = {
    "run_id": run_id, "occurrence_id": unique_occurrence_id,
    "user_id": user_id, "tool": tool_name, "phase": "initial|final",
    "offered_ns": offered, "dispatch_ns": dispatch,
    "tool_ready_ns": tool_ready, "engine_enqueue_ns": enqueue,
    "first_schedule_ns": first_schedule, "completed_ns": completed,
    "prompt_tokens": input_tokens, "completion_tokens": output_tokens,
    "scheduled_tokens": actual_scheduled_tokens,
    "evidence_sha256": evidence_sha, "result_bytes": byte_count,
    "retry_count": retries, "status": "ok|failed|dropped",
}
# 같은 clock domain 안에서만 차이를 계산한다.
# client monotonic과 engine clock을 검증 없이 직접 빼지 않는다.
assert offered_count == ok_count + failed_count + dropped_count
```

위 event는 새 구현을 완료했다는 뜻이 아니다. 실험마다 unique occurrence ID를 사용하고, 동일 clock domain 내에서 차이를 계산한다. 여러 프로세스/호스트의 timestamp는 clock 관계 검증 후 연결한다. PROFILE은 timed workload와 분리한다. 평균·p95·최대/배치 drain·도구별 성공률을 함께 보고, 성공한 요청만 골라 capacity를 산정하지 않는다.

## 9. 재현 코드와 전달 파일

| 목적 | 코드/자료 |
|---|---|
| 실제8tool/schema/query binding | financial_tools.py [see original archived attachment], schemas [see original archived attachment] |
| 기본 backend/계측 | tool_backend.py [see original archived attachment], Rust execute [see original archived attachment], 설정 [see original archived attachment] |
| 기존 동결 handoff | 설명 [see original archived attachment], agent prompt [see original archived attachment], manifest [see original archived attachment] |
| 최신 E2E controller/분석/검증 | run [see original archived attachment], analyze [see original archived attachment], validate [see original archived attachment], mechanism [see original archived attachment] |
| 결과·원시 검증 | FINDINGS [see original archived attachment], validation [see original archived attachment], mechanism [see original archived attachment] |
| DB 후보 | recommendation [see original archived attachment], E2E outcome [see original archived attachment] |
| 운영·체크포인트 | 현재 연구 서비스 [see original archived attachment], 환경 안내 [see original archived attachment], checkpoint [see original archived attachment] |

보고서·그림만 재생성하는 로컬 명령:

```sh
<instance-path> scripts/plot_vllm_handoff_summary.py
python3 scripts/validate_results.py
```

새 원격 실행 명령은 이 요약에서 자동 실행하지 않는다. 과거 controller를 다시 실행하거나 종료된 RAM holder 경로를 serving-ready로 사용하지 않는다. 현재 공유 GPU/DB 사용 시간과 소유권을 확인하고 fresh owned root에서 준비해야 한다. 과거 GPU 간섭 partial은 보존하며, 타인의 프로세스·checkpoint·기존7687은 변경하지 않는다. 이 전달 자료 작성은 동료에게 실제 전송/remote sync 성공을 의미하지 않는다.
