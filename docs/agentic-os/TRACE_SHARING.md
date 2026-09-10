# Agentic-OS trace 공유 절차 (S3)

이 문서는 gare2e 실험(SEOCHO graph-agent e2e)의 **trace·텔레메트리 산출물을 S3로 공유하고 함께 분석하는 절차**입니다.
동료가 처음 받는 문서라는 전제로, 접속 설정 → 내려받기 → 분석 → 자기 결과 올리기 순서로 적었습니다.
자격증명(access key / secret)은 이 문서와 리포에 **절대 넣지 않습니다**. 소유자가 별도 채널(비밀번호 관리자 등)로 전달합니다.

---

## 1. 무엇을 공유하나

한 실험 세션(lab-sN)은 Vast.ai 인스턴스 한 대에서 큐 방식으로 여러 pass를 돌립니다. 세션이 끝나면 패커가
**content-free 산출물만** 골라 하나의 패키지로 만듭니다. 프롬프트·쿼리·답변·증거 본문은 어떤 파일에도 없고,
digest(sha256)·시간·개수·결과 코드만 있습니다.

| 파일 | 내용 | 형식 |
|---|---|---|
| `manifest.json` | 파일 목록과 sha256, 스키마 버전, 원본 번들·큐 digest, leak-scan 결과 | JSON |
| `queue-receipt.jsonl` | pass별 exit code·wall | JSONL |
| `run-<pass>/otel/spans.parquet` | receipt에서 만든 OTel span (case → chat → tool → graph-db) | Parquet(zstd) |
| `otel/collector-spans.parquet` | 인스턴스 내 OTLP 수신기가 받은 span (vLLM 서버 span 포함: queue/TTFT/prefill/decode) | Parquet |
| `run-<pass>/otel/request-stages.csv`, `lane-stages.csv` | 요청별·레인별 스테이지 표 (vLLM 엔지니어용) | CSV/MD |
| `session-summary/*.csv` | pass-serving, lane-client, lane-funnel, lane-bottleneck | CSV/MD |
| `run-<pass>/catalog/*.csv` | attempts, groups(funnel), failures | CSV |
| `run-<pass>/workload/gpu.csv`, `host-process.csv`, `vllm-metrics.parquet` | 1 Hz 샘플러 (GPU util/전력, 프로세스 CPU/RSS, vLLM `/metrics`) | CSV/Parquet |
| `run-<pass>/e2e/.../run-receipt.json`, `receipts.jsonl` | 케이스별 receipt (턴·툴·funnel·trace id) | JSON/JSONL |
| `appliance-phases.tsv` | 인스턴스 부트스트랩 단계 타임스탬프 | TSV |

Parquet 안의 중첩 필드(`attributes`, `metrics`)는 JSON 문자열입니다. DuckDB의 `json_extract`로 꺼냅니다.

## 2. 버킷 구조

```
s3://seocho-agentic-os-traces/
└── seocho-agentic-os/
    └── traces/
        ├── index.json                # 전체 run 목록 (run id, 날짜, lab 요청 번호, 번들 sha, 크기, manifest 경로)
        ├── lab-s8-try-214132/        # 세션 하나 = prefix 하나 (= 로컬 run 디렉터리 이름)
        │   ├── manifest.json
        │   ├── queue-receipt.jsonl
        │   ├── otel/…, session-summary/…, run-<pass>/…
        └── lab-s10-…/
```

- prefix 이름은 로컬 run 디렉터리 이름(`lab-sN-try-HHMMSS`)과 같습니다. gare2e 리포의 `traces/<run>/manifest.json`, `traces/index.json`이 같은 내용의 사본이라, **리포에서 어떤 코드(번들 sha)로 만든 trace가 어디 있는지**를 먼저 보고 S3에서 내려받습니다.
- 버킷은 public access 차단, SSE-S3 암호화, 90일 lifecycle이 설정돼 있습니다. 객체는 덮어쓰지 않고 run 단위로 추가만 합니다.

## 3. 접속 설정 (처음 한 번)

1. AWS CLI v2 설치 (관리자 권한 불필요):
   ```bash
   curl -sS -o awscliv2.zip https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip
   unzip -q awscliv2.zip && ./aws/install --install-dir "$HOME/.local/aws-cli" --bin-dir "$HOME/.local/bin"
   aws --version
   ```
2. 소유자로부터 받은 키를 **터미널에서 직접** 입력합니다(채팅·메일·리포에 붙이지 않음):
   ```bash
   aws configure --profile seocho-traces      # Access Key ID / Secret / region(ap-northeast-2) / output(json)
   chmod 600 ~/.aws/credentials
   ```
3. 확인:
   ```bash
   aws sts get-caller-identity --profile seocho-traces
   aws s3 ls s3://seocho-agentic-os-traces/seocho-agentic-os/traces/ --profile seocho-traces
   ```
   두 번째 명령에 run prefix 목록이 보이면 준비 끝입니다.

키 관리 규칙: 키는 `~/.aws/credentials`에만 둡니다. 코드·노트·스크린샷에 나타나면 즉시 소유자에게 알려 비활성화·재발급합니다.
소유자는 90일마다 키를 회전하고 CloudTrail로 사용 내역을 확인합니다.

## 4. 내려받기

```bash
RUN=lab-s8-try-214132
aws s3 cp s3://seocho-agentic-os-traces/seocho-agentic-os/traces/index.json - --profile seocho-traces | python3 -m json.tool | head -40
aws s3 sync s3://seocho-agentic-os-traces/seocho-agentic-os/traces/$RUN ./traces/$RUN --profile seocho-traces
python3 -c "import json;m=json.load(open('traces/$RUN/manifest.json'));print(m['file_count'],m['bytes'],m['provenance'])"
```

`manifest.json`의 `provenance.source_bundle_sha256`가 그 trace를 만든 코드 번들입니다. gare2e의 `traces/<run>/manifest.json`과 sha가 같아야 합니다.

## 5. 분석

### 5.1 스테이지 표 (vLLM 관점)
`run-<pass>/otel/lane-stages.csv`: 레인별 `gap_before`(툴·DB 시간), `client_latency`, `server_e2e`, `queue`, `ttft`, `prefill`, `decode`의 p50/p90과 케이스 wall 대비 비중.
`request-stages.csv`: 요청 하나가 한 행. `client_minus_server_s`는 네트워크·HTTP·직렬화, `server_unaccounted_s`는 vLLM e2e에서 queue+prefill+decode를 뺀 잔여입니다.

### 5.2 DuckDB로 바로 질의 (내려받지 않고도 가능)
```sql
-- pip install duckdb ; duckdb 셸에서
INSTALL httpfs; LOAD httpfs;
SET s3_region='ap-northeast-2';  -- 자격증명은 ~/.aws 프로필 사용: CREATE SECRET (TYPE S3, PROVIDER CREDENTIAL_CHAIN, PROFILE 'seocho-traces');
CREATE SECRET (TYPE S3, PROVIDER CREDENTIAL_CHAIN, PROFILE 'seocho-traces');

-- vLLM 서버 span: decode가 5초 넘는 요청
SELECT name, json_extract_string(attributes,'$."gen_ai.latency.time_in_model_decode"') AS decode_s,
       json_extract_string(attributes,'$."gen_ai.usage.completion_tokens"') AS out_tok
FROM read_parquet('s3://seocho-agentic-os-traces/seocho-agentic-os/traces/lab-s8-try-214132/otel/collector-spans.parquet')
WHERE name='llm_request' AND CAST(decode_s AS DOUBLE) > 5 ORDER BY CAST(decode_s AS DOUBLE) DESC LIMIT 20;

-- 레인별 요청 수와 client 지연 p90
SELECT lane, count(*) AS requests, quantile_cont(client_latency_s, 0.9) AS p90
FROM read_csv_auto('s3://seocho-agentic-os-traces/seocho-agentic-os/traces/lab-s8-try-214132/run-medium-k4-otel/otel/request-stages.csv')
GROUP BY lane;
```

### 5.3 Tempo/Grafana에서 trace 보기
패키지에는 케이스 receipt가 들어 있으므로 exporter로 자기 collector에 재생할 수 있습니다.
```bash
cd gare2e   # 리포 루트, PYTHONPATH=src:.
python3 scripts/benchmarks/export_agentic_os_otel.py traces/$RUN/run-medium-k4-otel/e2e/*-tool-* \
  --jsonl /tmp/$RUN-spans.jsonl --otlp-endpoint http://127.0.0.1:4317 --service-name seocho-agentic-os
```
`examples/observability/docker-compose.observability.yml`로 Collector+Tempo+Grafana를 띄우면 Grafana → Explore → Tempo에서
`{ resource.service.name = "seocho-agentic-os" }` 로 검색됩니다. trace id는 `sha256(run_id + "\x1f" + case_id)[:32]` 이므로 receipt의 `trace.trace_id`로 바로 열 수 있습니다.
vLLM 서버 span(`llm_request`)은 `otel/collector-spans.parquet`에 있고, 같은 trace id·`parent_span_id`(= 우리 chat span)로 붙습니다.

## 6. 자기 실험 결과 올리기

세션이 끝나 로컬 `runs/<run>/` 디렉터리에 아티팩트가 내려온 뒤:
```bash
cd gare2e && export PYTHONPATH=src:.
python3 scripts/benchmarks/pack_agentic_os_traces.py \
  .seocho/benchmarks/agentic-os/colocated-appliance/runs/<run> \
  --output /tmp/traces/<run> \
  --s3-uri s3://seocho-agentic-os-traces/seocho-agentic-os/traces/<run> \
  --aws-profile seocho-traces --lab-request <NNN> \
  --index traces/index.json        # 리포에 있는 index.json 사본; run 행을 추가하고 버킷 루트에 다시 올립니다
```
- 패커는 허용 목록 파일만 고르고 JSONL을 Parquet으로 바꾸고 leak-scan(토큰 필드, 공개 IPv4, SSH 키, API 키 패턴)을 통과해야만 업로드합니다. 걸리면 exit 3, 업로드 없음.
- `--index`가 가리키는 리포 사본 `traces/index.json`에 run 행이 추가되고 같은 파일이 `seocho-agentic-os/traces/index.json`으로 올라갑니다(업로더 IAM 사용자는 ListBucket/GetObject가 없어 버킷 쪽을 읽지 못하므로 리포 사본이 원본입니다). 이어서 `/tmp/traces/<run>/manifest.json`을 `traces/<run>/manifest.json`으로 복사해 `index.json`과 함께 커밋합니다(원본 데이터는 커밋하지 않음). 오래 남길 run은 `--keep`.
- 올리면 안 되는 것: `*.private.*`(프롬프트·답변·증거·로그), `vast-colocated-lifecycle.json` 원본(인스턴스 payload에 세션 토큰·IP 포함). 패커가 걸러 주지만 수동으로 `aws s3 cp` 할 때는 직접 확인하세요.

## 7. 크기와 보존

pass당 약 10 MB(Parquet 후 3~5 MB), 전체 코호트 세션은 수백 MB입니다. 90일 뒤 lifecycle 규칙이 적용되므로 오래 남길 run은 `index.json`에 `keep: true`를 표시하고 소유자에게 알립니다. 분석 표(CSV/MD)와 manifest는 리포에 남으므로 데이터가 만료돼도 결론은 추적됩니다.

## 8. 문제가 생기면

| 증상 | 확인 |
|---|---|
| `AccessDenied` | 프로필 이름(`--profile seocho-traces`), prefix(`seocho-agentic-os/`) 확인. 다른 prefix는 정책상 막혀 있습니다. |
| `InvalidAccessKeyId` | 키가 비활성화·회전됐을 수 있음. 소유자에게 확인. |
| 패커 exit 3 (leak) | 출력의 `hits` 경로를 보고 원인 파일 제외 후 재실행. 정상 데이터에 공개 IP가 있으면 패턴을 조정합니다(소유자와 상의). |
| Parquet 읽기 오류 | `pip install pyarrow duckdb` 버전 확인; `attributes`는 문자열 컬럼입니다. |

관련 문서: `docs/agentic-os/HANDOFF.md`(실험 실행 인수인계), `examples/observability/README.md`(OTel 계약과 로컬 스택), `.claude/skills/vast-agentic-e2e/SKILL.md`(Vast 실행 절차).
