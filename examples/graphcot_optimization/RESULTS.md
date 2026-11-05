# 검증된 연구 결과와 설명 범위

2026-11-04 검토 export. 이 문서는 GUG의 완료된 연구를 요약합니다. 이번
패키징에서 live DB/LLM 성능 실험은 하지 않았습니다. 서로 다른 연구의
workload·backend·출력 예산을 합치거나 개선 배수를 곱하지 않습니다.

## 연구 단계

| 단계 | 검증/관측 | 해석 |
|---|---|---|
| 정밀도 pilot |BF16/FP8 W8A16/AWQ W4A16 tool선택 각12/12;6episode 모델호출15/17/20|양자화만의 인과는 미확인;FP16 연구 아님|
| BF16 반복 |원래 설정3회, 보조설정10회 모두6episode당15model calls|두 batch 설정 구분;FP8/AWQ 반복 부족|
| 업무 tool 확장 |6parameter tool144/144 이후8business tool256/256|고정 template;자유Cypher 정확성 증명 아님|
| 물리 scale |SF0.1/SF1:448offered=388ok+60DBtimeout|실패도 분모에 포함|
| SF1 query E2E |44조건1792=1697correct+24DBtimeout+71clientdrop|C8이득과C32burst 역전 모두 보존|
| timing isolation |28cell896/896correct, warm224/224,2240model calls|업무DB 실행 없이도C32역전 재현|
| hard5tool |120cell3840=3798ok+42transaction-memory failures;별도720/720|인자/집중도에 따라 성공률 변화|
| neo4rs 조합 |238cell12960=11917correct+1043failed+0drop|transaction-memory470/deadline558/Bolt worker15|
| 최소 DB 후보 확인 |456/456correct, warm50/50|알려진 계약;unseen/steady 증거 아님|
| 고정BF16 memory E2E |24cell864/864correct, warm156/156|DB이득이 모든E2E이득으로 이어지지 않음|

연구 추적ID: query E2E gug-m5p 계열의 후속 자료, 조합 gug-p96,
최신E2E gug-7sf. 이번 packaging은 gug-anl. 개인 tracker 자체는 공개하지 않습니다.
수치 원본 식별은 아래 export provenance를 참조합니다.

## 데이터와 환경

- 공식SF0.1 canonical55,604nodes/309,577relationships.
- 공식SF1 canonical556,082nodes/3,101,291relationships.
- SF1 sidecar 포함 store는총1,030,851nodes/4,370,240relationships.
  고정 query는 원래 관계를 조회합니다. Plain FinBench 물리layout 성능으로 일반화하지 않습니다.
- SF10은 원본 streaming inventory만 검사했으며 적재/E2E는 미실행입니다.
- SF1의 최대 transfer outdegree597. 초대형hub/CXL/물리disk 포화는 검증하지 않았습니다.
- 최신E2E: A100-SXM4-80GB1장, Neo4jCommunity5.26.0,
  Qwen/Qwen3.8-27B pinned source revision1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0,
  BF16/TP1/vLLM0.31.0/seq8/batch8192/context8192/KV12GiB/OMP4.
- 컨테이너 CPU quota약30.72core/DRAM limit342GiB, 공유CPU/DRAM.
  호스트 전체 코어/RAM과 다르며 affinity는 자원 예약이 아닙니다.
- snapshot semantic oracle와 반례를 분리했습니다. 초기 자유Cypher pilot에서는
  실제snapshot 답이 같아도 distinct-owner/방향/중복 의미가 틀린 사례가 있었습니다.

## Cypher

Python DB-only,256MiB cache,도구/버전별4개 normal sample 중앙값.
PROFILE은 별도 진단입니다.

| 도구 | reference초 | optimized초 | PROFILE DB hits reference→optimized |
|---|---:|---:|---:|
|L4|6.234|0.234|약7.8–8.0M→0.61–0.62M|
|L5|7.586|1.283|46.28M→6.83M|
|L6|4.611|1.114|26.01M→6.57M|

후보를 먼저 좁혀 불필요한 탐색/중간집계를 줄였습니다. 반례는 병렬송금,
서로 다른 소유자, 역방향보증, 자기송금, 상환제외, 시간/정렬/LIMIT 경계를 포함합니다.
초기 공식L5/L6 요청은 빈 결과였고 이후 hard인자/양성oracle을 별도로 준비했습니다.
이 표를 모든 양성 인자 분포의 속도로 일반화하지 않습니다.

동일모델 SF1 E2E에서C1전체시간약21%감소,C8/cache256 완료episode/초는약17–18%증가,
C32finite burst에서는약9%감소했습니다. Open-loop rate2/cap32는128offered중88→97완료였지만
수락된 tool mix가 달라 조건부 throughput을 그대로capacity로 비교하지 않았습니다.

## Memory

neo4rs 후속3block 배치시간 중앙값. H/P는MiB.

| 구성 | mixed 정답/전체 | mixed초 | L4집중 정답/전체 | L4집중초 |
|---|---:|---:|---:|---:|
|H512/P256|240/240|10.026|42/96|실패 arm;속도순위 제외|
|H1024/P256|240/240|9.621|96/96|9.546|
|H1024/P1024|240/240|3.602|96/96|3.633|

최소 후보: H1024/P1024,pool/admission16,workers32,fetch1000,Rust thread1.
Heap에 연동되는 transaction-memory 한도도 바뀌므로 순수GC 효과가 아닙니다.
PC만 올린 조건도 집중 부하 오류가 남았습니다. 쿼리/집중도/동시 메모리를 함께 봅니다.
과거 hard-L4 pool8은96/96,pool16은68/96이었으나 작은pool은 대기를 늘렸습니다.

## Index / modeling / I/O

- 좁은1%시간범위는 reference+TRANSFER(ts)4.25ms 대 optimized+index353.0ms였고
  reference에서 실제DirectedRelationshipIndexSeekByRange를 확인했습니다.
  넓은 범위에서는optimized가 유리했습니다. 자동routing 임계값은 미완료입니다.
- 별도 neo4rs 후보의 빠른 index-on은32PROFILE 모두 기본L4에 index 연산자가
  없어서 직접index 효과로 인정하지 않았습니다. Query shape/인자가 다른 비교입니다.
- workers32→64 일괄 증설은 일관된 이득이 없었습니다.
- 월별bucket은768/768정답이지만 E2E이득 불명확,store약+51.4%였습니다.
- 주요Java read_bytes 증분이0또는미미했습니다. Page-cache 효과를 물리disk miss 감소로
  확정하지 않았습니다. GC알고리즘/NUMA memory binding/대형hub는 미검증입니다.

## 실제 BF16 + neo4rs E2E

동일모델·도구·query에서 H512/P256 vs H1024/P1024만 변경했습니다.
3block×2workload×2C×2memory=24cells. 단위는 finite cohort의 배치 완료초입니다.

| workload/C | 기준 | 후보 | 변화 |
|---|---:|---:|---:|
|Frozen32/8|48.671|48.678|+0.02%|
|Frozen32/32|46.578|46.038|−1.16%|
|Hard40/8|131.709|130.668|−0.79%|
|Hard40/32|132.933|137.297|+3.28%|

Hard40/C32의3paired block은각+4.54/+4.04/+0.40%로모두느렸습니다.
반면request p95는94.362→92.491초로약1.98%개선됐습니다.
Fan-in tool HTTP중앙값은3.377→1.384초였지만 실제overlap peak3~4,
model waiting peak24였습니다. 사용자C32를 DB C32로 해석하지 않습니다.

같은마지막L3요청/evidence/1728final tokens에서final engine queue는
42.847→50.253,43.960→49.845,48.164→48.710초로늘었습니다.
Decode는약63.5–64.3초로유사했습니다. Block2의tool ready는23.904→23.572초지만
first schedule은68.055→73.474초로늦어졌습니다. 절대시점은batch시작기준입니다.

432paired입력의 evidence bytes/hash,입출력token,실배정token이모두같았습니다.
1020독립context/2040model calls전부engine join,canary2163/2163,monitor오류0.
이것은known-contract/고정template 성능연구이며 free-form/multi-turn정확성 증명은 아닙니다.

이전 독립timing-only 연구에서도업무DB 없이C32완료episode/초−7.48~−9.33%,
C8은+23.82~+24.71%였습니다. DB CPU경합은그역전의필수조건이아닙니다.
단순final-cap8은대기를client로옮기고−1.48~−2.57%악화해채택하지않았습니다.
Specific scheduler정책 인과,CPU scheduler compute포화,KV재계산은 입증하지 않았습니다.

## Provenance / reproducibility tiers

1. Git에 있는 `evidence/overview.json`, `memory-e2e.json`으로 표/차트를 재생성할 수 있습니다.
2. `source-manifest.json`은 복사된 core/query/adapter/asset의 원본SHA를 보존합니다.
3. 원시archive·prompt/answer·개인접속·credentials는Git에없습니다. 요약으로 개별요청
   correctness나2930개raw hash검증을 독립 재수행할 수 있다고 주장하지 않습니다.
4. 원본 최신E2E archive 식별SHA256:
   ac34046883c2aba87292e91778c03fcf48bb5f6e650ab1b446a42da071a76197.
   당시2930개file hash검증을완료했지만 archive는별도관리합니다.
5. 과거 BF16 checkpoint byte identity와 최초연구의 동일성은당시 pinned source lineage에
   의존합니다. 새로운arm끼리는동일검증weights를공유했습니다.

최종서비스는비교가능한기준메모리로복구했으며메모리후보를전역배포하지않았습니다.
현재 live서비스 상태는 이 static보고서에서보장하지않습니다. 과거외부GPU간섭으로
중단된14/18window결과는완료연구로합산하지않습니다.
