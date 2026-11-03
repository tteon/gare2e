# 최적화 실험 계약

## DEV-DECISION

이 패키지는 연구/교육용 example입니다. SEOCHO SDK/DozerDB/runtime routing 기본값을
변경하지 않습니다. Source registry/query와 새portable wrapper를 구분합니다.
Offline oracle/demo와실제서비스 성능을분리합니다. Model/precision/scale변경은독립축입니다.

## DEV-CONSTRAINT

- 원본data/store/모델/이전결과불변. 새run directory와source/config/query/data hashes.
- 서비스별소유권확인. 타인process/데이터중단·삭제나클라우드임대는이튜토리얼에없음.
- credentials와raw prompts/answers/logs는Git에넣지않음. 운영자가허용한private data만사용.
- finite cohort와steady구간구분. shared CPUquota와hostRAM을구분. affinity≠reservation.
- read-onlytemplates;workspace는dataset에서주입. 불필요한index/fixture전역생성금지.

## DEV-API-CONTRACT

`tool name + actual arguments → checked template + parameters → DB rows`.
Expected/oracle는검증에만사용하고dispatch에넣지않습니다.
Snapshot SHA는파일동일성;실제DB동일성은서버감사로별도확인합니다.

Portable `benchmark-db`는neo4rs HTTP기본,Python direct Bolt명시선택입니다.
두경로는transport가달라순수driver비교가아닙니다. HTTP200도tool실패일수있어
response.status와instance_id를검사합니다. Query retry없음. Pool/network library의
연결재설정과packet재전송을application retry와동일시하지않습니다.

## DEV-ACCEPTANCE

```text
offered = correct_ok + failed + dropped
```

Tool선택/JSON/argument,query실행가능성/의미반례,DB결과,최종답을따로평가합니다.
Warmup·startup·PROFILE·일반쿼리timing을분리합니다. 성공한요청만비교하지않습니다.
양성/음성·시간경계·병렬관계·반대방향·서로다른owner·정렬/LIMIT을검사합니다.

CLI자체는cold-cache준비나3blockcounterbalance를자동제공하지않습니다.
운영자가같은인자/분모로A→B/B→A순서를교차하고warm상태를기록하세요.
더큰표본/held-out/장시간gate전에는범용최적설정을선언하지않습니다.

## 계측 경계

| 이름 | 포함되는것 | 직접알수없는것 |
|---|---|---|
|dispatch lag|executor/clientadmission대기|serverqueue|
|acquire_begin|connection획득+BEGIN|순수poolwait|
|RUN/firstrow/drain|server/transport/decode혼합|순수planning/IO비율|
|HTTP db span|client/HTTP/adapter/DB|순수Neo4j실행|
|engine queue|enqueue→firstschedule|CPU scheduler계산시간|
|TTFT|queue/prefill/parser등|순수queue|
|result bytes|encodedrows|token수|

사용자C,tooloverlap,DBactive transaction,serverworkers,modelslots는별도단위입니다.
Client/engineclock관계를확인하고동일clockdomain차이만계산합니다.

## neo4rs prototype 한계

RecordedTxnleasepatch는COMMIT/ROLLBACK성공시에만socket을pool로돌려주고
취소/오류Drop은socket을폐기합니다. Upstreamauto-commitAPI는그대로입니다.
HTTP요청은workerdeadline으로bounded하지만HTTPdisconnect가즉시handlerdrop을
보장하지않으며서버transaction정리도비동기입니다. `/health`는DBreadiness가아닙니다.
업무오류중미완료phase의0counter는관측된zero cost가아닙니다.

기존연구는부분결과취소/PULL취소/실행timeout/syntax/rollback/소유proxy절단을검사했습니다.
Exact BEGIN/COMMIT fault injection,blackhole,HTTPdisconnect,graceful shutdown,
boundedHTTPqueue,장시간steady/lifecycle은남은gate입니다. 이패키징에서Rustbuild/live재검증
여부는[VALIDATION.md](VALIDATION.md)에정확히기록합니다.
