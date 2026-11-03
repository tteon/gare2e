> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/BF16_REPEATABILITY.md`; SHA256 `7c0081139bd903102d12ca73b76266177cc78492a55f6b7d241574047042017f`.

# BF16 반복 대조군 결과

원래 서버 실행 인자에 맞춘 3회 반복에서 **6개 episode당 LLM 호출은 모두15회, 실제 도구 호출은 모두9회**였다. 같은 조건에서15~20회가 자연스럽게 흔들리는 현상은 관측하지 못했다.

| 구분 | token batch | 반복 / episode | 반복당 LLM 호출 | 반복당 도구 호출 | strict / alias 정규화 정답 |
|---|---:|---:|---:|---:|---:|
| 원래 실행 인자 대조군 | 2048 | 3 / 18 | 15,15,15 | 9,9,9 | 15/18 · 18/18 |
| 별도 설정 보조군 | 8192 | 10 / 60 | 모두15 | 모두9 | 50/60 · 60/60 |

처음10회에는 후속 하드웨어 실험의batch8192가 사용됐다. 최종 로그 대조에서 원래2048과의 차이를 발견해, 이를 보조군으로 분리하고 원래`start_server.py bf16` 실행 인자로3회를 추가했다. 원본과 새 server-owner의 command 배열 일치, resolved compile range2048을 검증했다. 보조군은 첫4회 후 중단돼 나머지6회를 같은 서버에서 이어갔고 이 간격을 기록했다.

모든 반복에서 case별 LLM 호출은 L1~L6 순서로3/2/3/2/2/3회, 도구 실행 오류는3개였다. L1 문법 오류, L3 workspace 파라미터 누락, L6 변수 오류를 재시도한 경로다. L3의`path`를`accounts`로 바꾸는 좁은 alias 정규화만 적용했다. Snapshot 정답률이 높아도 기존 L5/L6 의미 반례 실패가 해결됐다는 뜻은 아니다.

원래 인자 대조군의6-episode 총시간은70.71/70.92/71.00초였다. 보조군은70.8~105.7초로 더 흔들렸지만 원인을 batch 크기·NUMA·양자화로 귀속하지 않는다. 두 설정을 교차한 latency 인과 실험이 아니다.

판정: 고정seed42, temperature0, thinking off, 직렬 실행, 최대4응답 조건에서 **BF16의 반복 변동만으로 기존15/17/20회 차이가 나왔다는 설명은 지지되지 않는다.** 그러나 FP8/AWQ는 여전히 단일 반복이고 weight·calibration·kernel 차이를 분리하지 못했으므로 양자화 단독 인과는 미확인이다. 새로운seed, 동시성, 긴 context에 대한 비열등성 증명도 아니다.

기존 실험의 KV cache는 모든 정밀도에서12GiB로 고정했다. 따라서 “낮은bit로 KV 공간이 늘었다”는 가능한 이점이지 그 실험에서 검증한 결과가 아니다. 도구 선택/JSON 형식, Cypher 의미, 재시도·종료, E2E를 계속 분리해 평가해야 한다.

원시 기록과 집계: `results/runtime/bf16-matched/`, `bf16-combined/`. 원래 인자 대조군 서버 기록: `results/runtime/matched-server/`. `python3 scripts/validate_runtime.py`가 두 설정의78episode와 command·source hash·원본 그래프 보존을 검증한다.
