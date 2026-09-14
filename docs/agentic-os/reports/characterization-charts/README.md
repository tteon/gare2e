# Graph Agentic RAG — 세 범주의 실측 차트

[차트 보고서 열기](index.html). 조건을 선택하면 같은 실행의 Runtime, Tool,
Agent figure가 함께 바뀝니다. SVG를 개별 다운로드할 수 있고, 전체 디렉터리를
내려받으면 네트워크 없이 열립니다. [차트 수치와 입력 SHA256](chart-data.json).

2026-09-15 사용자의 observability 정교화 요청에 따라 기존 표 중심 보고서를
차트로 확장했습니다. 실험 축을 추가하지 않는 사후 분석입니다. 원본 보고서와
CSV는 수정하지 않았습니다. 각 조건에 Runtime6, Tool5, Agent6개 패널이 있으며,
추가로 같은 세션의 baseline/MTP K 곡선을 제공합니다.

## 출처와 조건

- [lab024 초기22개 조건](../lab024-characterization/README.md)
- [lab024 후속5개 조건](../lab024-continuation/README.md)
- [lab025 replica 검증3개 조건](../lab025-replicas/README.md)

총30개 비교 조건,90개 범주별 SVG와 공통 처리량 곡선입니다.

하드웨어, vLLM/model revision, DB/data, warmup, K, 수집 검증 한계는 위 보고서와
각 manifest를 따릅니다. 서로 다른 세션/호스트를 동일 통제군으로 합치지 않습니다.
초기 세션은 전체 복구 파일을 로컬 검증했으나 독립 remote digest checkpoint가
없습니다. 후속 세션은 essential archive의 remote/local SHA가 일치하며 bulk는
의도적으로 수집하지 않았습니다. 차트를 그렸다고 원본의 검증 수준이 바뀌지 않습니다.

## 논문과의 대응

구조의 출처는 [Agentic AI Workload Characteristics, §4–6](https://arxiv.org/pdf/2605.26297)입니다.
논문의 측정값이나 막대 높이를 옮기지 않고 우리 trace/usage/metrics를 사용했습니다.

| 범주 | 논문의 그림/표 | 우리 차트와 적용 범위 |
| --- | --- | --- |
| Runtime | Fig7 | Task별 LLM/tool interval union을 합친 wall-time 구성. 겹침과 미귀속 시간 별도 |
| Runtime | Fig8 | 요청별 관측 cached input / uncached input / output 평균 tokens |
| Runtime | Fig9 | 정확히 연결된 요청의 prefill/decode 합계 비율 |
| Tool | Fig10–11 | Tool 이름과 Cypher 작업 종류. Bash 호출은 없으므로 graph 분류로 대체 |
| Tool | Tables1–2 | Graph 작업 종류별 평균 지연 상위3개와 오류율 상위3개, 표본 수/반환 bytes |
| Tool | Fig12 | LLM call 진행도 사분위별 schema discovery / query / 직전 오류 이후 query |
| Agent | Fig3–4 | LLM 호출 수·tool-bearing round, peak input과 누적 input 분포 |
| Agent | Fig5–6 | 응답 종류별 생성량, 종료 유형별 peak input. Semantic accuracy는 미채점 |

## 분모와 미관측 처리

**처리량:** 공통 측정 창의 확정 output counter / 초 / 관측 물리 GPU 수.
TP rank의 같은 출력을 중복 합산하지 않습니다. Task/s는 같은 창의 완료 수입니다.
GPU 수를 관측하지 못한 조건은 output tok/s/GPU가 미관측으로 남습니다.

**행동 cohort:** 측정 창 안에서 시작하고 끝난 task만 사용합니다. Task당 입력/출력,
turn/context 분포, tool 분포는 이 cohort 기준입니다. 처리량 counter의 모집단과
같지 않으므로 task/s × 평균 생성량으로 tok/s를 재구성하지 않습니다. 느린 grammar
조건은 포함 task가5–6개뿐이며 짧게 끝난 task를 과대표집할 수 있습니다.

**Task 시간:** 각 task의 시작/종료로 clip한 interval들의 union을 계산합니다.
LLM-only, tool-only, overlap, unattributed가 그 task wall time으로 합쳐집니다.
이 값을 task들에 걸쳐 합산한 wall-time 가중 비율이며 GPU 사용 시간은 아닙니다.
모든 호출의 interval이 있는 task만 시간 차트에 포함하고 n을 표시합니다.
LLM client 시간에는 네트워크/대기/서버 실행이 포함됩니다. 미귀속 시간은
GPU idle이나 CPU 계산 시간으로 단정하지 않습니다.

**LLM 단계:** W3C parent가 정확히 한 개 연결되고 prefill/decode가 유한한 비음수인
요청만 사용합니다. 분모는 Σ(prefill+decode)입니다. Queue나 network가 빠진
요청 실행 단계 비율이며 전체 task wall time이 아닙니다. TTFT는 다른 단계와
겹치므로 stack에 더하지 않습니다. Lab-s23b replica는 exact join0/229이므로
단계 차트는 미관측입니다. 다른 조건의 비율로 채우지 않습니다.

**요청 공급/KV:** running/waiting 요청의 표본 평균과 engine별 KV 사용률의
평균/최댓값을 표시합니다. 실제 decode sequence 수나 active/cached KV 분리는
미관측입니다. Replica는 완전한 측정 창이 확인된 backend별 표본만 쓰며,
router가 합산한 KV fraction은 전체 사용률로 사용하지 않습니다.

**Cache:** usage의 input/cached/output가 모두 있고 0≤cached≤input인 요청만
평균에 포함합니다. Uncached=input−cached이며 신규로 추가된 token과 같지 않습니다.
Cache hit이 decode 계산을 없애거나 긴 KV가 무료임을 뜻하지 않습니다.
현재 실제 입력은 짧고 preemption/압박이 작아 offloading 가설을 검증하지 않습니다.

**Tool:** 빈 행은 valid empty이며 실패가 아닙니다. 오류율은 error / (error +
empty_valid + nonempty); unclassified는 분모에서 제외합니다. 전체 calls와 classified
n은 JSON에 따로 보존합니다. Tool latency는 클라이언트가 관측한 호출 경과 시간이며
DB server CPU 시간이 아닙니다. Cypher shape는 query family의 어휘 분류이지 실행
계획/DB hits가 아닙니다. 반환 크기는 bytes이고 token 수로 환산하지 않았습니다.

**활동 변화:** 부모 LLM 호출의 ordinal을 task 전체 LLM 호출 수로 정규화해
Q1–Q4를 정합니다. 각 사분위의 연결된 tool call 수로 나눈 비율입니다. 이전 tool이
error이고 현재 tool이 query인 경우만 “Query after error”로 분류합니다. 의도/추론
내용을 분석한 값이 아니며 모든 graph query는 read-only입니다. Parent 미상은
분모에서 제외하고 개수를 표시합니다. 과거 직렬 interval로 복원한 parent는
recorded parent와 구분하여 provenance를 보존합니다.

**Agent:** tool-bearing round는 연결된 부모 LLM 호출의 고유 개수입니다. 하나의
LLM 응답이 여러 tool을 불러도 한 round로 세며 parent 미상 시 하한입니다. Boxplot은
median/IQR/1.5IQR whiskers, 이상점은 그림에서 숨기되 원래 수치는 JSON에 보존합니다.
Peak request input과 모든 request input 합계를 구분합니다. Peak input은 실제 KV
residency가 아닙니다. Context growth의 후반 ordinal에는 살아남은 task만 있습니다.
`terminal=final`은 정답 성공 판정이 아니고 output kind는 전체 응답 분류입니다.
Reasoning/Cypher 인자만의 token 구성과 judge 기반 성공/실패는 미관측입니다.

## 재현

Matplotlib/NumPy가 있는 Python에서 새 출력 디렉터리를 지정합니다. Python 표준
라이브러리만으로 수치 집계와 테스트가 가능하며 그림 렌더링 때만 Matplotlib을
가져옵니다. 원본 CSV.gz 또는 plain CSV를 모두 지원합니다. 기존 출력은 덮어쓰지 않습니다.

```bash
python3 scripts/benchmarks/render_agentic_os_characterization_figures.py \
  --source docs/agentic-os/reports/lab024-characterization \
  --source docs/agentic-os/reports/lab024-continuation \
  --source docs/agentic-os/reports/lab025-replicas \
  --output /tmp/agentic-os-chartbook-new
```

검증: 시간 중첩/clip, 누락·불가능 cache, 중복 server match, valid empty, 활동
사분위/미상 parent 등10개 수치 계약 테스트 통과. 기존 router 테스트를 포함하면
14개 통과. Chrome에서 Runtime/Tool/Agent SVG를 렌더링해 축·레이블·미관측 표시를
확인했습니다. 이 테스트는 실측 데이터의 집계 계약을 검증하며 성능 측정이 아닙니다.

`bash scripts/ci/run_basic_ci.sh`도 실행했습니다. Python1112개 통과/3개 skip과
runtime/module/import/root/doc 계약은 통과했으나, 마지막 identity check가 기존
Vast SSH fixture의5개 주소를 거부했습니다. 전체 basic CI가 green이라는 뜻은
아닙니다. 이 차트 변경과 무관한 기존 실패이며 상세 로그는 로컬에 보존했습니다.
