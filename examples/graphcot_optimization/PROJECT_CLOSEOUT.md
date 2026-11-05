# GUG 연구 정리 및 Vast 실행 종료 — 2026-11-05

현재 문서는 이전 실행을 설명하는 인수인계입니다. **원래 Vast 인스턴스57874888은
2026-11-05 18:34 KST에 실행 중지(Stop)를 확인했습니다.** Provider의 실제 상태는
`exited`, 요청 상태는 `stopped`였습니다. Destroy/디스크 삭제는 수행하지 않았으며
[Vast 안내](https://github.com/vast-ai/docs/blob/main/guides/instances/manage-instances.mdx)에 따라
저장공간 요금은 계속 발생할 수 있습니다. 과거 endpoint나 PID를 serving-ready로 간주하지 마세요.

## 연구 의도와 통제

금융 Graph-CoT 요청을 **모델 추론 → tool runtime/Bolt → Neo4j → 모델 답변 생성**으로
분해하고, 쿼리·메모리·동시 부하 개선이 실제 E2E까지 이어지는지 확인했습니다.
고정8개 업무 tool과 trusted parameterized Cypher를 사용했습니다. 단일 tool 호출·모델2회인
평가와 자유 Cypher 생성/재시도 실험을 구분했습니다. 사용자 동시성과 DB 동시성도 다릅니다.

```text
사용자 요청 → 고정 BF16 모델: tool 선택·인자 생성
           → HTTP tool adapter (neo4rs)
           → admission → connection pool → Bolt
           → 고정 Cypher / Neo4j 실행·결과 완전 소비·transaction 종료
           → 모델: evidence 기반 최종 답변
```

Offered/failed/dropped, dispatch lag, queue, TTFT, decode, DB span, bytes,
재시도와 최종 정확도를 분리했습니다. PROFILE은 별도 진단이며 timed query에 섞지 않았습니다.

## 수행한 연구와 판정

| 구간 | 수행 내용 | 보존한 결론 |
|---|---|---|
| 정밀도 ablation | BF16 /FP8 W8A16 /AWQ W4A16, 선택·인자·생성 Cypher·호출 수 분리 | 소표본에서 고정 tool 선택 손상은 관측하지 않음. LLM 호출15/17/20회 차이의 양자화 단독 인과는 미확인. FP16은 실행하지 않음 |
| BF16 대조 | 원래 인자3회, 보조 batch8192 조건10회 반복 | 해당 고정 seed/조건에서 호출 수가15회로 같았으나 FP8/AWQ 비열등성이나 양자화 인과를 입증하지 않음 |
| 단일 tool·동시 사용자 | L1–L6 축 분리, replay/실DB, generator 대조, queue 관측 | finite cohort와 지속 capacity를 구분. GPU 포화를 CPU scheduler 원인으로 단정하지 않음 |
| 업무 tool 구성 | 유입 집계·다중 hop·시간순 경로·연결 계좌·빠른 경유·공유 접속 등8tool | 실제 인자를 template에 바인딩하고 독립 oracle/의미 반례로 검증. L1–L6 번호가 latency 순서는 아님 |
| 물리 scale | SF1 canonical556,082nodes /3,101,291relationships 및 별도 sidecar | 논리 필터링과 물리 scale 구분. SF10은 용량 gate 미통과로 미적재 |
| Cypher | L4 수신 계좌별 집계+LIMIT 조기 종료, L5/L6 guarantee-first 후보 탐색 | DB 접근량 감소. 공식 SF1 L5/L6 양성 교집합은0이므로 이후 주 고부하 후보를 L4/L2/L3/P1/A1로 교체 |
| 메모리/Bolt/runtime | cache·heap·pool·admission·worker·fetch·Rust thread 단독/조합 | 최종 최소 DB 후보 H1024/P1024MiB, pool/admission16, workers32, fetch1000/thread1. 전역 최적값이나 순수GC 원인 아님 |
| index·접근 경로 | TRANSFER(ts), 시간폭별 reference/optimized 선택 | 선택도에 따라 유리한 경로가 달라짐. 최종 기본 L4의32PROFILE에 index 사용이 없어 필수 index 제외. 단순1%시간폭 분기도 제외 |
| neo4rs | pinned0.8.0/Cargo.lock, explicit transaction, full drain, lease patch | 성공 경로와 timeout/cancel lifecycle 안정성을 구분. lifecycle/steady gate는 완결했다고 표시하지 않음 |
| 최종 BF16 E2E | 24counterbalanced cells,864/864correct,0failed/drop,156warmup | DB 개선이 보편적 E2E 개선을 뜻하지 않음. Hard40/C32 배치132.933→137.297s(+3.28%), 실제 tool overlap peak3–4 |

[검토된 결과](RESULTS.md), [과거21개 보고서](preservation/reports/README.md),
[56개 연구 폴더 목록](preservation/STUDIES.md), [E2E 수치](evidence/memory-e2e.json).

## L4–L6 메모리 결과: 쿼리 효과와 분리

최적화 Cypher와 heap512MiB를 고정한 DB-only 결과입니다. 각 값은 두 인자×두 순서의
성공4/4개 중앙값(초)입니다. 4GiB는 별도 동일-query/heap 후속 대조입니다.

| Page cache | L4 | L5 | L6 |
|---|---:|---:|---:|
|64MiB|0.241|8.818|9.866|
|128MiB|0.229|3.088|3.096|
|256MiB|0.234|1.283|1.114|
|512MiB|0.207|1.192|1.072|
|4GiB|0.195|1.217|1.062|

L4는 재작성 후 추가 cache 효과가 작았고, L5/L6는256MiB 부근까지 크게 개선됐습니다.
다만 L5/L6는 공식 SF1에서 빈 결과이며, 물리 disk read가 원인이라는 증거는 아닙니다.

같은 후속3block의 L4 집중C32에서는 H512/P256이42/96정답(54transaction-memory 실패),
H1024/P256이96/96·batch9.546s, H1024/P1024가96/96·batch3.633s였습니다.
실패 arm의 빠른 종료를 speedup으로 계산하지 않습니다. Transaction-memory는 heap에
연동된 기본 한도를 사용했으며, heap을 고정하고 한도만 바꾸는 독립 실험은 미실행입니다.

[노드·관계 애니메이션과 MP4](animation/README.md)는 설명용 합성 그래프입니다.
SF1 profiler의 edge-by-edge trace나 시간/개수 비례 표현이 아닙니다.

## 동료가 인수인계받을 자료

1. [준비물·복원·neo4rs·재측정 실행 순서](FRESH_INSTANCE_RUNBOOK.md). Docker/native,
   전체 속성·병렬 관계·schema 감사와 고정 모델 파일 SHA를 포함합니다.
2. [기본 보존 Release](https://github.com/tteon/gare2e/releases/tag/gug-research-preservation-20261104):
   35,631logical files /22,695objects, 암호화7volume 합계1,672,089,562bytes.
3. [종료 시점 추가 보존 Release](https://github.com/tteon/gare2e/releases/tag/gug-retirement-20261105):
   마지막 GC 로그, 종료 상태/최종 inventory 기록, 최근 설명 자료. 기본 백업의 추가분으로
   31logical files /26objects, 암호화1volume 5,620,225bytes입니다.
4. [code/tutorial PR7](https://github.com/tteon/gare2e/pull/7). Branch에 업로드했으며 main 병합은 별도입니다.
5. **별도 안전한 경로로 전달받을 age 복호화 identity**. 키는 Git/Release/이전 인스턴스에 없습니다.

보존 범위는 GUG 연구 파일입니다. 모델 weights, live physical DB/transaction logs,
venv/toolchain, credential/비밀키, 다른 campaign은 포함하지 않습니다. Canonical SF1 논리
snapshot과 재구축 코드는 포함합니다. 논리 복원은 물리 store 배치·cache 이력까지 복제하지
않으므로 새 B0가 필요합니다.

## 종료 전 확인과 남은 연구

기본7volume의 로컬 ciphertext SHA/size를 재확인했고, GitHub assets의 존재/size도
확인했습니다. 기본 백업은 이전에 전체 volume GitHub readback/decrypt, 전체 object/path
검증, 실제 materialization과 복원 pilot validator를 통과했습니다. 최종 remote inventory는
26,903대상 파일이며 이전26,902개의 모든 경로가 존재했습니다. 변경분은 GC 로그862,110bytes와
보존 범위 밖 toolchain installer로, 후자는 기존 제외 방침을 유지했습니다.
GPU process는 없었고 관련 automation2개는 이미 PAUSED였습니다. 새 실험은 기동하지 않았습니다.

남은 과제는 heap과 독립적인 transaction-memory 제어, L5/L6 양성 부하, 장시간/open-loop
용량, neo4rs cancel/fault lifecycle, NUMA/물리I/O 원인, vLLM 개선, 자유 Cypher multi-turn입니다.
완료로 표시하지 않으며, 새 인스턴스에 baseline을 복원한 뒤 별도 실험으로 진행해야 합니다.
Git 코드 보존과 Dolt issue 동기화는 별개입니다. 로컬 `bd backup sync`를 수행하며,
Dolt remote 동기화는 주장하지 않습니다.
