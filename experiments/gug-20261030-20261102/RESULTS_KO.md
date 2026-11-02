# GUG·FinBench 결과 요약

이 문서는 원본 결과를 검토한 요약이다. 원본 835개 summary, 요청별 결과,
실패·부분 실행 로그, 소스와 질문·정답은 [manifest](manifest.json)의 private
S3 보존본에 있다. [실험 목록](CATALOG.md)은 당시 선언 상태를 표시하며,
완료를 새로 판정한 결과가 아니다.

## 재계산해 대조한 네 캠페인

원본 summary에서 다시 계산한 값이다. 앞서 검토한 근거 285파일은 전체
회수본과 SHA가 일치했다. 전체 원본 응답의 재채점이나 외부 연구자의 재현은
수행하지 않았다. 재계산 기록은 private 보존본의
`collection/results/summary-recomputation.json`에 있다.

| 원본 실험 | 결과 | 분모와 한계 |
| --- | --- | --- |
| `gug-handoff-isolation-20261101-c` | 28cells, measured896/896, warmup224/224 | BF16/off. live·replay와 reference·optimized를 구분해야 한다. |
| `gug-neo4rs-followup-20261102-a` | status46cells, 77summary 합2531offered/2477ok/54failed/0dropped | DB-only. warmup·mixed·fanin·validation을 포함한 파일 합이며 고유2531질문이 아니다. |
| `gug-neo4rs-memory-confirm-20261102-a` | status10cells, 20summary 합506/506 | DB-only, warmup 포함. |
| `gug-neo4rs-e2e-20261102-a` | 24cells, measured864/864, warmup156/156 | BF16/TP1/Neo4rs, 2048token·180초. 현재 FP8/high/800 조건과 다르다. |

## 메모리 E2E의 전체 시간

Baseline heap512/pagecache256MiB와 candidate heap1024/pagecache1024MiB를
각3block 측정한 batch 시간이다. 범위는 block별 최솟값~최댓값이며 변화는
`(candidate median / baseline median − 1) × 100`으로 계산했다.

| Workload / C | Baseline median s (범위) | Candidate median s (범위) | 시간 변화 |
| --- | ---: | ---: | ---: |
| frozen32 / 8 | 48.671 (48.630~48.690) | 48.678 (48.607~49.593) | +0.015% |
| frozen32 / 32 | 46.578 (46.423~46.791) | 46.038 (46.018~46.163) | −1.158% |
| hard40 / 8 | 131.709 (131.446~132.099) | 130.668 (130.585~130.855) | −0.790% |
| hard40 / 32 | 132.933 (131.345~135.980) | 137.297 (136.525~138.292) | +3.283% |

모든 조건에서 반복되는 E2E 개선은 확인되지 않았다. DB roundtrip 감소를 전체
성능 개선으로 대체하거나 이 수치로 GPU stall·scheduler·KV 인과를 확정하지
않는다. 과거 BF16·driver 비교를 현재 FP8 캠페인이나 T1~T5의 성과로
재분류하지 않는다.

## 실패와 데이터 범위

모델 경로 부재, profile 검사 실패, DB unavailable, resource guard,
중복 인자 오류와 외부 GPU 경합 중단도 보존했다. Window-g의 당시
`measurement`와 checkpoint의 `stopped`를 성공으로 바꾸지 않았다.

질문 원본은 SF0.01/0.1/1 DB 요청 각16개, SF0.1/1 LLM 질문 각32개,
SF1 최적화 입력16개와 LLM32개 등 7파일이다. 실제 질문·정답·쿼리는 Git에
포함하지 않는다. SF1 요청 원본 SHA는
`265a339a1bb652f139e28e8326ec245df377559c98347813aff40a577ab30b2c`이다.
소스·원본 결과·ZIP의 확인 방법은 [보존 안내](README.md)를 따른다.
