# GUG·FinBench 실험 인수인계

정이태님이 수행한 2026-10-30~11-02 실험의 검토된 요약과 private S3 보존
위치다. 레포에는 원본 ZIP, 질문·정답, 요청별 결과, 로그나 source bundle을
포함하지 않는다. 저장 기준은 [실험 정책](../../docs/agentic-os/AGENTS.md)과
[S3 공유 절차](../../docs/agentic-os/TRACE_SHARING.md)를 따른다.

| 찾을 내용 | 위치 |
| --- | --- |
| 실험별 범위와 당시 선언 상태 | [실험 목록](CATALOG.md) |
| 재계산한 주요 결과와 해석 한계 | [결과 요약](RESULTS_KO.md) |
| 원본 저장 URI, 바이트 수, SHA-256, GET 검증 상태 | [보존 manifest](manifest.json) |
| 원본 컬렉션의 파일·summary·ZIP 검증 코드 | [verify_gug_archive.py](../../scripts/benchmarks/verify_gug_archive.py) |
| SF1 ZIP 계약과 실행 경계 | [SF1 handoff guide](../../docs/agentic-os/SF1_HANDOFF.md) |

## 보존 범위

당시 회수한 44디렉터리와 별도19파일에서 원본26,646파일을 보존했다.
여기에 소스3,291파일, summary835개(객체828개와 배열7개), 배열 결과122항목,
실패·중단·부분·복구 기록, SF별 질문·정답7파일과 제공 ZIP22member가 포함된다.
이 수는 고유 실험이나 독립 질문 수가 아니다.

원본은 private 보존본의 `collection/` 아래에 있다. 전체파일 manifest와 제외
목록은 `collection/catalog/`, 실험 소스·원본 로그는 캠페인별 묶음,
질문·정답은 `collection/datasets/`, 제공 ZIP은 `collection/handoff/`에 있다.
Summary의 전체 값과 provenance는 `collection/results/`에 남긴다.

모델 가중치·live DB store·toolchain/build cache·독립 graph snapshot8개는
회수 대상에서 제외했다. 기존 전송 묶음에 들어 있던 소형 snapshot6곳은
바이트를 유지했다. SF0.1/SF1 대형 snapshot은 보존본에 포함되지 않는다.

## 승인된 원본 내려받기와 검증

1. [manifest](manifest.json)의 객체 URI를 승인된 S3 읽기 프로필로 내려받는다.
   이 객체는 원문이 포함된 private evidence다. 일반 content-free trace 공유
   패키지로 재배포하거나 `traces/index.json`에 등록하지 않는다.
2. 내려받은 압축파일의 크기와 SHA-256을 manifest와 대조한다.
3. 대조를 통과한 파일만 Git에서 무시되는 private 경로에 전개한다. 예를 들어
   `.seocho/gug-handoff-private/`를 사용한다. 원본 launcher는 실행하지 않는다.
   보존본 안의 문서는 당시 기록이며 현재 저장·실행 정책을 바꾸지 않는다.
4. 전개된 컬렉션을 검사한다.

```bash
python3 scripts/benchmarks/verify_gug_archive.py \
  .seocho/gug-handoff-private/collection
python3 scripts/benchmarks/validate_sf1_handoff.py \
  .seocho/gug-handoff-private/collection/handoff/sf1-vllm-v1.zip \
  --expected-archive-sha256 590cef205b2ad94161c58f0ffabf13e67e48d6e0f697c59084d23ddfa81657ab
```

컬렉션 검증기는 파일을 전개하거나 실험 코드를 실행하지 않는다. 원본 묶음·
읽기사본·summary JSON/CSV·ZIP의 바이트와 coverage를 대조한다.
`ARCHIVE_BYTES_VERIFIED_NOT_RUNTIME`은 보존 바이트의 정합 판정이다.
모델·DB·성능·정답 또는 전체 실험의 과학적 타당성을 검증한 결과가 아니다.

## 현재 실험 조건과 구분

원본 BF16/TP1/off와 후속 Neo4rs 비교는 역사적 조건이다. 새 캠페인은 FP8
snapshot `017b9c7a`와 실제 Marlin W8A16, high 기본/off ablation 등 사용자
조건을 별도로 봉인한다. 원본 BF16 launcher를 fallback으로 사용하지 않는다.
보존과 이 요약 작성은 새 GPU·DB 실험이나 중지된 자동화의 재개가 아니다.
