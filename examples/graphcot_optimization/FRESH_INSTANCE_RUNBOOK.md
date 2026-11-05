# 새 인스턴스에서 SF1 Neo4j 복구 및 실험 재현

이 문서는 **비공개 gare2e 저장소에 접근할 수 있는 동료**를 위한 실행 순서입니다.
백업 복호화 → 빈 Neo4j에 논리 복원 → 전체 데이터 감사 → neo4rs → DB 기준 측정 →
선택적으로 고정 BF16 E2E 순서로 진행합니다. 과거 실행 결과를 읽는 데 GPU는 필요 없습니다.

> 백업은 실행 중인 Neo4j의 physical backup이 아닙니다. 원래의 store 배치,
> transaction log, page-cache 상태까지 복제하지 않습니다. 같은 논리 데이터와
> 쿼리를 복구한 뒤 **새 인스턴스의 B0**를 측정하세요. 과거 속도와 동일하다고 가정하지 않습니다.

## 0. 준비물과 고정 조건

| 준비물 | 내용 |
|---|---|
| 접근 권한 | `tteon/gare2e` private repo 및 Release 다운로드 권한, 인증된 `gh` |
| 복구 키 | 운영자가 **별도 안전한 경로로 전달한 age identity**. Git/Release에는 없음 |
| 도구 | Linux amd64, Python 3.12 권장, Git, curl, age, zstd, Rust/Cargo, C compiler, patch |
| DB | Neo4j Community **5.26.0**, Docker 또는 Java 21을 사용하는 native 설치 |
| DB 실험 자원 계획 | 16 vCPU / RAM 64 GiB / 여유 디스크 50 GiB를 출발 계획으로 권장. 검증된 최소 사양은 아님 |
| E2E 추가 자원 | 원 실험은 A100 80 GB 한 장. 같은 모델 파일 약55.6 GB를 별도로 확보. RAM128 GiB/디스크200 GB 이상을 계획하되 실제 여유량 확인 |
| 제외 항목 | 모델 weights, 기존 venv/toolchain, 실행 중인 DB store는 백업에 없음 |

Streaming import/audit는 전체 JSON을 RAM에 적재하지 않습니다. 다만 DB benchmark와
기존 E2E의 독립 oracle는 1.67 GB JSON을 Python 객체로 읽어 훨씬 많은 RAM을 사용합니다.
CPU affinity는 코어 독점 예약이 아닙니다. 공유 CPU/GPU 작업과 cgroup 한도를 확인하세요.

[고정 버전·해시](config/recovery-pins.json), [모델 파일별 SHA](config/pinned-model-files.json),
[과거 결과](RESULTS.md), [연구 목록](preservation/STUDIES.md)를 함께 전달하세요.
Neo4j 5.26은 Java17/21을 지원합니다([공식 요구 사항](https://neo4j.com/docs/operations-manual/current/installation/requirements/)).

```bash
# Ubuntu 24.04 예시. Docker를 쓰면 host Java는 불필요합니다.
sudo apt-get update
sudo apt-get install -y git curl age zstd gh python3-venv build-essential pkg-config libssl-dev patch jq
# gh, Docker 또는 Rust/Cargo는 조직에서 승인한 설치 경로로 준비합니다.
gh auth status
lscpu
free -h
df -h
cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max 2>/dev/null || true
# E2E만 해당: nvidia-smi 및 다른 GPU 사용 작업을 확인합니다.
```

## 1. 코드와 암호화 백업 다운로드

이 문서의 코드는 PR [#7](https://github.com/tteon/gare2e/pull/7)에 있습니다.
main에 merge되기 전에는 아래 branch를 사용합니다. 복구 시 `git rev-parse HEAD`도 기록하세요.
Release tag는 **보존 시점 코드**이며, 나중에 추가한 이 복구 helper를 포함하지 않습니다.

```bash
umask 077
export GUG_HOME="$HOME/gug-recovery-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$GUG_HOME"
git clone --branch codex/graphcot-optimization-tutorial \
  https://github.com/tteon/gare2e.git "$GUG_HOME/gare2e"
cd "$GUG_HOME/gare2e"
export LAB="$PWD/examples/graphcot_optimization"
git rev-parse HEAD > "$GUG_HOME/code-commit.txt"
python3 -m venv "$GUG_HOME/venv"
source "$GUG_HOME/venv/bin/activate"
python -m pip install -r "$LAB/requirements-recovery.txt"

export DOWNLOAD="$GUG_HOME/download"
gh release download gug-research-preservation-20261104 \
  --repo tteon/gare2e --dir "$DOWNLOAD"
# 운영자가 전달한 키 파일의 실제 경로로 설정합니다. 키 내용을 출력하지 마세요.
export AGE_IDENTITY="$HOME/.config/gug/private-recovery.agekey"
test -r "$AGE_IDENTITY"
chmod 600 "$AGE_IDENTITY"
python "$DOWNLOAD/restore_research_backup.py" verify \
  --manifest "$DOWNLOAD/BACKUP-MANIFEST.json" --identity "$AGE_IDENTITY" \
  > "$GUG_HOME/backup-verify.json"
export RESTORED="$GUG_HOME/restored"
python "$DOWNLOAD/restore_research_backup.py" restore \
  --manifest "$DOWNLOAD/BACKUP-MANIFEST.json" --identity "$AGE_IDENTITY" \
  --output "$RESTORED" > "$GUG_HOME/backup-restore.json"
```

7개 암호화 volume 합계1,672,089,562 bytes, 논리 파일35,631개입니다.
복구 helper는 ciphertext와 복호화 객체의 해시·크기·경로를 모두 확인합니다.
`RESTORED`는 존재하지 않는 새 디렉터리여야 합니다. 복구된 동일 내용 파일은 hard link일 수
있으므로 원본을 직접 편집하지 않습니다. 암호 해제 자료·인증정보는 Git 밖에 보관합니다.

## 2. 실제 복원 대상 찾기와 실행용 사본 준비

```bash
python "$LAB/recovery.py" locate --restored "$RESTORED" > "$GUG_HOME/paths.json"
export DATASET=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["paths"]["dataset"])' "$GUG_HOME/paths.json")
export SNAPSHOT=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["paths"]["snapshot"])' "$GUG_HOME/paths.json")
export STAGE="$GUG_HOME/staged"
python "$LAB/recovery.py" stage --restored "$RESTORED" --output "$STAGE" \
  > "$GUG_HOME/staging.json"
```

| 항목 | 복구된 위치 (`$RESTORED/` 기준) / 검증 값 |
|---|---|
| SF1 snapshot | `local/results/physical_scale_a/sf1/graph-snapshot.json` |
| dataset manifest | 같은 폴더의 `dataset.json` |
| snapshot SHA256 | `16539c614f2a8557c6ac7b2254c259e0bacc298df687d036eea75881cceff73f` |
| canonical graph | 556,082 nodes / 3,101,291 relationships |
| 최종 메모리 실험용 sidecar 포함 | 1,030,851 nodes / 4,370,240 relationships |
| frozen32 / hard40 | `local/results/neo4rs_memory_e2e_20261102_a/workloads/` 아래 각 폴더 |
| 고정 Cypher | `local/handoff/sf1-vllm-v1/queries/` — package의 `queries/sf1_optimized/`와 동일 |

`stage`는 scripts/configs/queries/handoff와 두 workload를 **독립 복사**합니다.
복사한 evaluator에만 authenticated DB canary와 LLM endpoint 환경변수 지원을 추가하고
원본/수정본 SHA를 기록합니다. old controller의 PID·holder·절대경로는 이식하지 않습니다.
`remote/`는 원격에서만 있던 파일 모음이므로 완전한 실행 디렉터리로 간주하지 않습니다.

## 3. 본인 소유의 빈 Neo4j 시작

**A/B 중 하나만** 선택합니다. 기존 다른 DB의 data 디렉터리를 연결하지 마세요.
loopback Bolt17690, tool HTTP18103, LLM18000은 다른 작업이 사용하지 않는지 확인합니다.
비밀번호는 shell history에 쓰지 않고 입력하여 환경변수로 전달합니다. 같은 사용자/root는
process 환경을 읽을 수 있으므로 공유 서버에서는 계정·접근 제어도 필요합니다.

```bash
export NEO4J_USER=neo4j
read -r -s -p 'New owned Neo4j password: ' NEO4J_PASSWORD; echo
export NEO4J_PASSWORD
export NEO4J_URI=bolt://127.0.0.1:17690
export GUG_DB_ROOT="$GUG_HOME/neo4j-owned"
mkdir -p "$GUG_DB_ROOT/conf"
cp "$LAB/config/baseline-memory.conf" "$GUG_DB_ROOT/conf/neo4j.conf"
```

### A. Docker 사용 가능할 때

Docker-in-Docker가 허용되지 않는 GPU 임대 컨테이너라면 B를 사용하세요.
Docker conf mount에서는 container 내부 listen address를 설정해야 합니다
([공식 설정 문서](https://neo4j.com/docs/operations-manual/current/docker/configuration/)).
Host 포트는 loopback에만 publish합니다.
아래 image는 linux/amd64 manifest까지 고정했습니다.

```bash
export NEO4J_IMAGE=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["docker_linux_amd64_image"])' "$LAB/config/recovery-pins.json")
export NEO4J_CONTAINER="gug-recovery-$(date -u +%Y%m%dT%H%M%SZ)"
# 마운트 디렉터리 소유권은 컨테이너 neo4j 사용자에 맞춥니다.
sudo mkdir -p "$GUG_DB_ROOT/data" "$GUG_DB_ROOT/logs"
sudo chown -R 7474:7474 "$GUG_DB_ROOT/data" "$GUG_DB_ROOT/logs"
cat >> "$GUG_DB_ROOT/conf/neo4j.conf" <<'EOF'
server.default_listen_address=0.0.0.0
server.directories.data=/data
server.directories.logs=/logs
server.http.enabled=false
server.https.enabled=false
dbms.security.auth_enabled=true
EOF
# 비밀정보 없는 conf는 컨테이너 사용자가 읽을 수 있어야 합니다.
chmod 755 "$GUG_DB_ROOT/conf"
chmod 644 "$GUG_DB_ROOT/conf/neo4j.conf"
export NEO4J_AUTH="$NEO4J_USER/$NEO4J_PASSWORD"
docker run -d --name "$NEO4J_CONTAINER" --platform linux/amd64 \
  -p 127.0.0.1:17690:7687 \
  -v "$GUG_DB_ROOT/data:/data" -v "$GUG_DB_ROOT/logs:/logs" \
  -v "$GUG_DB_ROOT/conf:/conf:ro" \
  -e NEO4J_AUTH "$NEO4J_IMAGE"
unset NEO4J_AUTH
```

`NEO4J_AUTH`는 **빈 data의 최초 초기화**에만 적용됩니다. 기존 인증정보를 재설정하는
명령이 아닙니다([공식 Docker 문서](https://neo4j.com/docs/operations-manual/current/docker/introduction/)).
처음 import가 느리면 별도 preparation profile을 쓸 수 있지만, 결과 측정 전 반드시
baseline으로 재시작하고 아래 audit를 통과해야 합니다.

### B. Docker 없이 native 설치

```bash
sudo apt-get install -y openjdk-21-jre-headless
export NEO4J_DIST="$GUG_HOME/neo4j-dist.tar.gz"
curl -fL https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz -o "$NEO4J_DIST"
python - "$LAB/config/recovery-pins.json" "$NEO4J_DIST" <<'PY'
import hashlib,json,sys
assert hashlib.sha256(open(sys.argv[2],'rb').read()).hexdigest()==json.load(open(sys.argv[1]))['neo4j_archive_sha256']
PY
tar -xzf "$NEO4J_DIST" -C "$GUG_HOME"
export NEO4J_BIN="$GUG_HOME/neo4j-community-5.26.0/bin"
export NEO4J_CONF="$GUG_DB_ROOT/conf"
cat >> "$NEO4J_CONF/neo4j.conf" <<EOF
server.directories.data=$GUG_DB_ROOT/data
server.directories.logs=$GUG_DB_ROOT/logs
server.directories.run=$GUG_DB_ROOT/run
server.directories.transaction.logs.root=$GUG_DB_ROOT/transactions
server.default_listen_address=127.0.0.1
server.bolt.listen_address=127.0.0.1:17690
server.bolt.advertised_address=127.0.0.1:17690
server.http.enabled=false
server.https.enabled=false
dbms.security.auth_enabled=true
EOF
# 빈 owned data에서 한 번만 실행합니다.
"$NEO4J_BIN/neo4j-admin" dbms set-initial-password "$NEO4J_PASSWORD"
"$NEO4J_BIN/neo4j" start
```

native 중지는 **같은 `NEO4J_CONF`**에서 `neo4j stop`을 사용하세요. 과거 실험의 PID나
`pkill java`를 사용하지 않습니다. 원격 포트 공개가 필요하면 방화벽/터널을 별도 구성하세요.

### 공통 readiness

```bash
python - <<'PY'
import os,time
from neo4j import GraphDatabase
for i in range(120):
    try:
        with GraphDatabase.driver(os.environ['NEO4J_URI'],auth=(os.environ['NEO4J_USER'],os.environ['NEO4J_PASSWORD'])) as d:
            d.verify_connectivity()
            print(d.execute_query('CALL dbms.components() YIELD versions RETURN versions').records)
        break
    except Exception:
        if i==119: raise
        time.sleep(1)
PY
```

## 4. Canonical graph + 월별 hub sidecar 복원 및 전체 감사

```bash
python "$LAB/recovery.py" import --dataset "$DATASET" --snapshot "$SNAPSHOT" \
  --sidecar --output "$GUG_HOME/import-sf1"
python "$LAB/recovery.py" audit --dataset "$DATASET" --snapshot "$SNAPSHOT" \
  --sidecar --output "$GUG_HOME/audit-sf1"
```

기존 데이터가 있으면 import는 거부합니다. 실패한 import는 일부 데이터와 증거를
남기며 자동 삭제/재시작하지 않습니다. 새로운 owned DB를 만들기 전에 실패 원인을 확인하세요.
`CREATE`로 병렬 관계를 보존하며 금액 cents/원문, timestamp, 64bit 계좌 ID를 유지합니다.
월별 `TransferBucket`은 source account × UTC month로 재구축합니다.

성공 기준은 단순 count가 아닙니다. `audit.json`의 `validated=true`와 다음 항목을 확인합니다.

- 모든 canonical node labels/properties, relationship type/endpoints/properties 및 병렬 multiplicity 일치.
- sidecar의 모든 속성/관계 일치. 새 실행 경로인 `_projection_owner`만 비교에서 제외.
- `entity_id`, `bucket_id` uniqueness와 `account_anchor` 복합 RANGE index 및 기본 node/relationship LOOKUP이 ONLINE.
- 뜻하지 않은 추가 데이터·constraint·index 없음. 특히 별도 TRANSFER 시간 index를 섞지 않음.

Audit는 SQLite를 scratch로 사용하므로 디스크 공간을 확보하세요. PROFILE은 별도 진단에만
사용하며 production query timing에 포함하지 않습니다. 새로운 저장소의 파일 크기도 기록하세요.

## 5. neo4rs adapter 빌드와 실행

```bash
python "$LAB/prepare_neo4rs.py"
cargo test --locked --lib --manifest-path "$LAB/neo4rs_adapter/Cargo.toml"
cargo build --release --locked --manifest-path "$LAB/neo4rs_adapter/Cargo.toml"
export GUG_BOLT=127.0.0.1:17690
export GUG_DB_USER="$NEO4J_USER" GUG_DB_PASSWORD="$NEO4J_PASSWORD"
export GUG_LISTEN=127.0.0.1:18103 GUG_TOOL_URL=http://127.0.0.1:18103
export GUG_POOL=16 GUG_ADMISSION=16 GUG_FETCH=1000
export GUG_RUNTIME_THREADS=1 GUG_TIMEOUT_MS=15000 GUG_L4_CAP=0
# 전경 실행. 다음 명령은 같은 환경을 설정한 두 번째 shell에서 실행하세요.
"$LAB/neo4rs_adapter/target/release/gug-neo4rs-adapter" "$STAGE/contract.json" \
  > "$GUG_HOME/adapter.jsonl" 2>&1
```

`prepare_neo4rs.py`는 crate SHA와 transaction lease patch의 전후 SHA를 검사합니다.
이미 vendor가 있으면 덮어쓰지 않습니다. Ctrl-C는 이 foreground adapter만 중지합니다.
메타데이터 endpoint가 응답해도 실제 DB 쿼리 성공 검증은 별도로 필요합니다.

```bash
curl -fsS "$GUG_TOOL_URL/metadata" > "$GUG_HOME/adapter-metadata.json"
python "$LAB/lab.py" verify
# C1 정확성 검사: 독립 oracle와 실제 반환값을 비교합니다.
python "$LAB/lab.py" benchmark-db --backend neo4rs --query-set sf1_optimized \
  --dataset "$DATASET" --snapshot "$SNAPSHOT" \
  --cases "$STAGE/workloads/frozen32/db-cases.json" \
  --pool 16 --concurrency 1 --repeat 1 --output "$GUG_HOME/db-smoke-frozen32"
```

## 6. DB 재측정: 먼저 B0, 그다음 메모리 후보

아래는 새로운 finite cohort 측정입니다. 과거182/46-cell controller 전체의 자동 재현은
아닙니다. 각 arm을 별도 3 block으로 반복하고 arm 순서를 B0/M → M/B0 → B0/M처럼
교차합니다. 먼저 각 arm에서 같은 workload C1 warmup을 별도 output에 실행하세요.
캐시가 warm인지 cold인지 기록하고 OS drop_caches를 공유 서버에서 실행하지 않습니다.

```bash
# 예: baseline, block1. 각 arm/block에 새 디렉터리를 지정합니다.
export ARM=B0 BLOCK=1
for workload in frozen32 hard40; do
  for c in 8 32; do
    python "$LAB/lab.py" benchmark-db --backend neo4rs --query-set sf1_optimized \
      --dataset "$DATASET" --snapshot "$SNAPSHOT" \
      --cases "$STAGE/workloads/$workload/db-cases.json" --pool 16 \
      --concurrency "$c" --repeat 1 \
      --output "$GUG_HOME/db-$ARM-b$BLOCK-$workload-c$c"
  done
done
```

메모리 후보는 heap1024 MiB/page cache1024 MiB, B0는512/256 MiB입니다.
Bolt workers32/pool16/admission16/fetch1000, 쿼리·데이터·auth는 고정합니다. 아래 설정 병합은 나머지 paths/listen 설정을 보존합니다.

```bash
# Docker 경로: owned container를 중지한 상태에서만 profile 변경
# baseline 복귀 시 candidate-memory.conf 대신 baseline-memory.conf를 사용합니다.
docker stop "$NEO4J_CONTAINER"
python - "$LAB/config/candidate-memory.conf" "$GUG_DB_ROOT/conf/neo4j.conf" <<'PYCFG'
import sys
from pathlib import Path
fragment=Path(sys.argv[1]).read_text().splitlines()
updates=dict(line.split('=',1) for line in fragment if line and not line.startswith('#'))
p=Path(sys.argv[2]); lines=p.read_text().splitlines()
lines=[line for line in lines if line.split('=',1)[0] not in updates]
p.write_text('\n'.join(lines+[k+'='+v for k,v in updates.items()])+'\n')
PYCFG
docker start "$NEO4J_CONTAINER"
# readiness → 새 경로 audit → warmup → ARM=M 측정 순서로 진행합니다.
```

Native는 `neo4j stop` 후 memory fragment의 **6개 설정만** 바꾸고 나머지 owned paths/port/auth
설정을 유지한 뒤 `neo4j start`합니다. 설정 파일 전체를 fragment로 덮어쓰지 마세요.
사용자C32가 DB 동시성32를 뜻하지 않습니다. DB-only와 E2E의 분모/대기 구간을 분리하세요.
`summary.json`과 raw `requests.jsonl`에서 offered/failed,
정답 일치, per-tool 분포, latency 및 adapter admission/Bolt 구간을 함께 검토합니다.
실패 arm timing을 speedup으로 계산하지 않습니다.

## 7. 선택: 같은 BF16 모델을 붙인 E2E

**DB 복원만 원하는 동료는 여기까지 실행할 필요가 없습니다.** 모델/환경의 별도 준비가
필요하며, 아래는 새 인스턴스 운영자가 readiness를 확인하는 경로입니다. 이번 runbook
작성에서 모델 weights를 다시 다운로드하거나 새 GPU E2E를 실행하지 않았습니다.

```bash
# 모델 다운로드 전 license/access, GPU 전용 사용 시간, 디스크 여유를 확인합니다.
python -m pip install huggingface_hub
export GUG_MODEL_PATH="$GUG_HOME/model"
python - "$LAB/config/pinned-model-files.json" "$GUG_MODEL_PATH" <<'PY'
import json,sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id='Qwen/Qwen3.8-27B',
 revision='1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0',
 allow_patterns=list(json.load(open(sys.argv[1]))),local_dir=sys.argv[2])
PY
python "$LAB/recovery.py" check-model --model "$GUG_MODEL_PATH" \
  --manifest "$LAB/config/pinned-model-files.json" > "$GUG_HOME/model-verified.json"
```

원 실험 기록은 vLLM0.31.0 / torch2.13.0 / transformers5.17.0입니다.
이 버전 조합의 GPU wheel/driver 호환성을 별도 model venv에서 먼저 확인하세요.
설치 불가 시 임의 최신 버전으로 바꾼 결과를 동일 재현으로 표시하지 마세요.
아래 flags와 실제 `vllm serve --help`를 대조하고, 시작 로그/전체 pip freeze를 남깁니다.

```bash
# 별도 model shell: 이 venv로 recovery/oracle 환경을 덮어쓰지 않습니다.
python3 -m venv "$GUG_HOME/model-venv"
source "$GUG_HOME/model-venv/bin/activate"
python -m pip install 'vllm==0.31.0' 'torch==2.13.0' 'transformers==5.17.0'
python -m pip freeze > "$GUG_HOME/model-pip-freeze.txt"
# 설치/driver 실패 시 중단하고 환경 차이를 기록합니다.
# 호환 vLLM이 설치된 별도 model shell에서 실행
vllm serve "$GUG_MODEL_PATH" --served-model-name gug-qwen \
  --host 127.0.0.1 --port 18000 --dtype bfloat16 --tensor-parallel-size 1 \
  --max-model-len 8192 --max-num-seqs 8 --max-num-batched-tokens 8192 \
  --kv-cache-memory-bytes 12884901888 --mamba-ssm-cache-dtype float32 \
  --language-model-only --enable-prefix-caching \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
  --tokenizer "$GUG_MODEL_PATH" --chat-template "$GUG_MODEL_PATH/chat_template.jinja" \
  --generation-config "$GUG_MODEL_PATH" \
  --enable-request-id-headers
```

실행 전에 `/v1/models`와 작은 tool-call 응답을 확인합니다. Scheduler trace가 필요하면
`$STAGE/scripts`를 `PYTHONPATH`에 추가하고 저장된 프로토콜에 따라
`--scheduler-cls gug_scheduler_trace.TracedAsyncScheduler`를 지정합니다.
이 wrapper는 vLLM 내부 API에 의존합니다. Trace join이 빠졌다면 engine queue나
scheduler 원인을 단정하지 않습니다.

```bash
# recovery venv shell. query/token 내용을 바꾸는 proxy를 끼우지 않습니다.
export GUG_LLM_BASE_URL=http://127.0.0.1:18000
python "$STAGE/scripts/handoff_isolation_eval.py" \
  --out "$GUG_HOME/e2e-B0-b1-hard40-c32" \
  --config "$STAGE/workloads/hard40" --snapshot "$SNAPSHOT" \
  --query-dir "$STAGE/handoff/sf1-vllm-v1/queries" \
  --bolt-uri "$NEO4J_URI" --backend neo4rs --tool-endpoint "$GUG_TOOL_URL" \
  --pool 16 --concurrency 32 --repeat 1 --mode live \
  --max-tokens 2048 --episode-timeout 180
```

frozen32/hard40 × C8/C32 × B0/M × 3 counterbalanced blocks =24 cells.
각 cell은 별도 output과 `--repeat 1`을 사용합니다. `repeat>1`로 인한 기존 ID 집계 충돌을 피합니다.
모델 호출은 tool 선택 → 실제 DB 호출 → 답변 생성 순서이며, 여러 tool을 반복 호출하는
multi-turn 연구가 아닙니다. 기존 `run_neo4rs_memory_e2e.py` / `neo4rs_service.py`를
수정 없이 실행하지 마세요. 이전 store 절대경로, holder/PID 검사, 포트와 복구 절차를 포함합니다.

**GPU 보호:** 이 단일 evaluator에는 controller의 2초 주기 foreign-GPU guard가 없습니다.
공유 환경에서는 새 환경의 PID/start-time 소유 기록과 2초 guard를 이식·검증한 다음
24cell을 시작하세요. 순간적인 GPU idle은 독점 사용 보장이 아닙니다. 타인의 process는
종료하지 않습니다. Prefix cache reset은 본인 전용 model server에서만 수행합니다.
RAM/memfd holder를 사용한다면 모든 consumer가 종료될 때까지 유지하고,
이전 인스턴스의 PID나 memfd 링크를 재사용하지 마세요.

## 8. 합격 조건·인수인계·종료

1. Backup verify/restore 성공, code commit과 snapshot/model SHA 저장.
2. Import/audit 성공, 전체 데이터와 schema 일치, version/limits/설정/store 크기 기록.
3. 실제 neo4rs smoke 전부 정답, offered = ok + failed + dropped. Timeout/cancel도 분모에 포함.
4. DB-only/E2E, warmup/측정, PROFILE/본 쿼리 분리. E2E에서는 tool/인자/최종 답변 정확도,
   tokens, result bytes, queue, TTFT, decode, DB, retry, dispatch lag 확인.
5. 실패와 부정적인 결과 보존. 과거864/864는 새 인스턴스의 합격 증거가 아님.
6. 비교 종료 후 baseline으로 복귀하고 readiness/audit 재확인. 중지는 본인 서비스만 수행.
   Docker는 기록한 container 이름으로 `docker stop "$NEO4J_CONTAINER"`, native는 같은 conf로 stop.
   data/key/model은 자동 삭제하지 않고 원본 백업도 그대로 보관.

이번에 확인한 범위와 증거는 [RECOVERY_VALIDATION.md](RECOVERY_VALIDATION.md)에 있습니다.
새 VM의 전체 SF1 복원과 모델 재측정은 이전 대상에서 위 절차를 실행한 뒤 완료됩니다.
