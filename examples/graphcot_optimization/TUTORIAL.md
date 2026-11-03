# 튜토리얼: 빠른 DB가 항상 빠른 agent를 만들까요?

## 1. 먼저 비용 경계를 그립니다

```text
user → model① → tool arguments → template → adapter → Bolt → Neo4j
                                                           │
user ← final ← decode ← model queue ← evidence/result bytes ┘
```

model-only는 입력/호출 수가 다른 독립 기준선입니다. live tool과 동일 evidence를
돌려주는 replay 비교도 모델 batching이 바뀔 수 있어 순수 Bolt overhead가 아닙니다.

## 2. tool의 실제 인자를 바인딩합니다 — 서비스 불필요

```bash
python3 examples/graphcot_optimization/lab.py bind \
  --tool list_transfer_recipients \
  --arguments '{"account":1,"min_amount":0,"limit":5}'
```

`query`, `parameters`, `query_sha256`를 확인하세요. account ID는64bit 정수여서
JavaScript Number로 중간 변환하면 손실될 수 있습니다. Python/Rust 정수와
JSON 원문을 보존하세요. `workspace`를 arguments에 추가하면 거부합니다.
L2의 max_hops는1/2/3의 검토된 template을 선택하며 임의 문자열을 붙이지 않습니다.

8개 도구:

| 유형 | 이름 | 주요 비용 |
|---|---|---|
| L1 | list_transfer_recipients |1-hop/중복 제거|
| L2 | trace_transfer_reachability |hop/degree|
| L3 | trace_time_ordered_fund_paths |3-hop/시간순 조건|
| L4 | find_fan_in_accounts |집계/동시 transaction memory|
| L5 | find_linked_account_pairs |관계 후보/서로 다른 소유자|
| L6 | rank_linked_account_flows |양방향 합계/상환 제외/정렬|
| P1 | trace_rapid_pass_through |시간 제한2-hop|
| A1 | find_shared_access_accounts |공유Medium/중복 제거|

L1~L6은 단조 난이도 점수가 아닙니다. 실제 비용은 인자·분포·degree·반환량에
달렸습니다. 공유Medium은 IP/device 또는 동일 소유권·불법성의 증명이 아닙니다.

## 3. 합성 반례로 정답 계약을 익힙니다

```bash
python3 examples/graphcot_optimization/lab.py demo
```

합성 graph의1→2송금은2개지만 L1은 DISTINCT이므로 수취인은1행입니다.
이 결과는 **독립 Python oracle**입니다. 실제 query 결과가 아닙니다.
`sf1_optimized`로 바꿔도 같은 L1계약이 유지됩니다.

```bash
python3 examples/graphcot_optimization/lab.py demo --query-set sf1_optimized
```

연습: 잘못된 시간 구간, bool account, 범위 밖 hop, unknown argument를 넣어
실행 전에 거부되는지 확인하세요. 실제 DB 의미 동등성은 별도 반례/live gate가 필요합니다.

## 4. 저장된 실험을 해석합니다

[RESULTS.md](RESULTS.md)와 `evidence/memory-e2e.json`을 읽습니다.
Hard40/C32에서 batch시간은+3.28%였지만 request p95는약1.98% 개선됐습니다.
이는 모순이 아닙니다. 모든 요청의 꼬리와 마지막1건의 완료 시간은 다른 목표입니다.
`render.py`로 같은 수치를 새 경로에 그릴 수 있습니다. replay/chart를 새 성능 측정으로
부르지 않습니다.

## 5. 실제 DB 실험 — 소유한 환경에서만 선택 실행

이 단계는 tutorial 기본 실행에 포함되지 않습니다. 본인 Neo4j, read 권한,
canonical snapshot/인자/독립oracle 및 실제환경 설정이 필요합니다. CLI는 데이터를
적재하거나 서버를 기동하지 않습니다. 단순 demo graph를 SF1이라고 부르지 않습니다.

Neo4j5.26용 메모리 설정 조각은 [baseline](config/baseline-memory.conf)과
[candidate](config/candidate-memory.conf)에 있습니다. 전체 설정이 아니며 인증/경로/포트는
본인 서비스에서 유지합니다. Heap 변경은 기본 transaction-memory 한도도 바꿀 수 있습니다.
소유한 인스턴스에만 적용하고 SHOW SETTINGS/인덱스/제약/graph 상태를 보존하세요.

입력 snapshot 구조(전부 예시):

```json
{"nodes":[{"eid":"a","labels":["Account"],
 "props":{"acct_no":1,"_workspace_id":"YOUR_SCOPE"}}],"edges":[]}
```

관계는 `src,dst,type,props` 형식이며 `TRANSFER.amount/ts`, Account.acct_no,
소유/보증/접속 방향 등은 `core/parameter_oracle.py` 계약에 맞춰야 합니다.
snapshot은 서비스의 실제 데이터와 별도로 대조하세요. SHA 검사는 파일 동일성만
검증하며 서버에 같은 graph가 적재됐음을 보장하지 않습니다.

dataset.json:

```json
{"dataset_id":"YOUR_DATASET","workspace":"YOUR_SCOPE",
 "snapshot_sha256":"SHA256_OF_EXACT_SNAPSHOT_FILE",
 "schema_version":"gug-finbench-logical-v1","physical_scale":null}
```

cases.json:

```json
[{"id":"case-001","tool":"list_transfer_recipients",
  "arguments":{"account":1,"min_amount":0,"limit":5}}]
```

신뢰된 contract export:

```bash
python examples/graphcot_optimization/lab.py export-contract \
  --dataset "$DATASET_JSON" --snapshot "$SNAPSHOT_JSON" \
  --query-set sf1_optimized --output "$NEW_CONTRACT_JSON"
```

neo4rs adapter는 별도 Rust toolchain과 네트워크 의존성이 필요합니다.

```bash
python examples/graphcot_optimization/prepare_neo4rs.py
cd examples/graphcot_optimization/neo4rs_adapter
cargo test --locked --lib
cargo build --locked --bin gug-neo4rs-adapter --release
# 본인 환경에서 GUG_BOLT/GUG_LISTEN/GUG_DB_USER/GUG_DB_PASSWORD를 별도 설정
# GUG_POOL=16, GUG_ADMISSION=16, GUG_FETCH=1000, GUG_TIMEOUT_MS=15000
./target/release/gug-neo4rs-adapter "$NEW_CONTRACT_JSON"
```

이 명령은 DB가 준비된 뒤 운영자가 선택 실행합니다. adapter는 prototype이며
인증 proxy 없이 외부에 공개하지 않습니다. 비밀번호는 Git/로그에 넣지 않습니다.
`/health`는 프로세스 응답이며 dataset/DB readiness가 아닙니다. Lease patch가
upstream과 다른 점, HTTP disconnect/blackhole/lifecycle 추가 gate는
[PROTOCOL.md](PROTOCOL.md)에 있습니다.

다른 shell, repository root에서 준비된 adapter로 측정:

```bash
# GUG_TOOL_URL을 본인 adapter endpoint로 설정
python examples/graphcot_optimization/lab.py benchmark-db \
  --dataset "$DATASET_JSON" --snapshot "$SNAPSHOT_JSON" --cases "$CASES_JSON" \
  --query-set sf1_optimized --backend neo4rs --pool 16 --concurrency 8 \
  --repeat 3 --output "$NEW_RUN_DIR"
```

기본 backend는 neo4rs입니다. metadata의 dataset/schema/query와 pool을 확인합니다.
Python 직접 Bolt 대조는 `--backend python`과 NEO4J_URI/USER/PASSWORD로 선택합니다.
Python-direct와 Rust-over-HTTP의 차이를 순수 driver 속도로 해석하지 마세요.
기존 연구와 다른 Python direct 경로이며 matched transport control이 필요합니다.

기본 warmup0으로 표시하며 warm 상태는 운영자가 관리합니다. 공식 연구에서는
워밍업·측정·PROFILE을 분리했고 실행 순서를 교차했습니다. 여기의 `--repeat 3`은
유한 요청 반복이지3개 counterbalanced block을 자동 실행하는 기능이 아닙니다.
새 output 경로와 occurrence ID를 사용하고 실패도 분모에 남깁니다.

## 6. 모델 E2E로 확장

이 CLI는 모델을 기동하지 않습니다. [VLLM_HANDOFF.md](VLLM_HANDOFF.md)의 계약으로
자신의 agent runtime에 연결하세요. 먼저 model/SHA·정밀도·도구·답/token 예산을
고정한 B0를 확보합니다. DB-only 개선을 E2E 개선으로 가정하지 마세요.
