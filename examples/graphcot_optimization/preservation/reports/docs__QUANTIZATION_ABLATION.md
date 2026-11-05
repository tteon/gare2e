> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/QUANTIZATION_ABLATION.md`; SHA256 `7fd62b9b4284cb08c80e3f14b8a74bcf230318adab253d0d712f92134dc3deae`.

# Quantization ablation — closed, 2026-10-30

비교는 BF16 / FP8 W8A16 / AWQ INT4 W4A16이다. FP16은 실행하지 않았다. 추가 정밀도 실험은 중단한다. 원본 결과는 results/{bf16,fp8,awq}, 요약은 results/summary.json, 상세 방법은 EXPERIMENT_DESIGN.md에 보존한다.

| 가설 | 관측 | 판정 |
|---|---|---|
| 양자화가 tool 선택/인자 형식을 손상한다 | 각 arm 고정 선택 12/12, 숫자 교란·no-tool 8/8 통과 | 이 소표본에서는 지지되지 않음. 동등성 입증은 아님 |
| 양자화가 Cypher 의미 정확도를 낮춘다 | snapshot strict BF16 5/6, FP8 4/6, AWQ 5/6; L3 alias를 제외하면 6/6,5/6,5/6 | alias, 반복 예산, 의미 오류를 분리해야 함. 인과 미확인 |
| 실제 graph 정답이 맞으면 query도 올바르다 | 반례 데이터에서 모든 arm L5 distinct-owner 누락; L6 BF16 JOIN 중복, FP8/AWQ 방향 누락·중복 | 반증. snapshot 정답과 의미 검증을 별도 평가 |
| 빠른 decode가 곧 빠른 agent E2E다 | decode 평균 2208/1418/1127ms; 6개 live episode LLM 호출 15/17/20회 | 호출 증가가 이득을 상쇄할 수 있음. 정밀도가 호출 증가의 원인인지는 미확인 |
| A100 FP8는 native FP8 연산 이득이다 | MarlinFP8ScaledMMLinearKernel, W8A16 | 해당 설명은 부정확. weight 저장/메모리 경로와 커널 차이 |
| tool I/O가 현재 최대 병목이다 | C1 queue 약 .02ms, 모델 구간이 큼; L6 DB 약72ms 대 E2E 약2.68s(controlled) | 현재 작은 DB/C1에서는 지지 약함. 포화·큰 결과·scale에서는 미검증 |

평가축은 (A) 도구 필요성/선택/JSON/인자 정확성, (B) 생성 Cypher 실행 가능성·의미 반례·최종 답, (C) 호출 수·재시도·종료, (D) TTFT/decode/DB/E2E로 분리한다. 실패도 분모와 latency에 포함한다. 최종 응답 예산 4회 소진을 tool schema 오류로 세지 않는다.

제약: 모델 순서 고정, 단일 반복 블록, 작은 문제 집합, thinking off, 보정 데이터/정밀도별 weight equivalence 불확실. seed와 prompt를 맞춰도 통계적 비열등성이나 양자화 단독 인과를 주장할 수 없다. 남는 가설은 rounding에 따른 선택 경계 변화, calibration 및 kernel 차이, 출력 경로 변화와 장문 재시도 증폭이다. 이들은 검증 완료로 표시하지 않는다.

다음 기준선은 BF16으로 고정한다. 이는 이번 결과에서 최고 속도 모델을 선택했다는 뜻이 아니라 정밀도 변수를 고정하는 실험 통제이다.


## 사용자 요청 BF16 반복 대조군 추가 (2026-10-30)

원래 FP8/AWQ arm을 재실행하지 않고 같은6개 자유 Cypher episode를 BF16으로10회 반복했다. 이 추가군의 token batch는8192로, 원래2048과 다르므로 직접 복제 대조군으로 해석하지 않는다. 원래 실행 인자에 맞춘 별도3회 대조군도 완료했고 모두15 LLM calls/9 tool calls로 같았다. 상세는 BF16 반복 결과 [see original archived attachment]에 기록했다. 모든 반복에서 **LLM 호출15회, tool 호출9회, tool 실행 오류3회**로 동일했다. strict정답50/60, L3의path→accounts alias만 정규화하면60/60이다. 원시 데이터/집계는 `results/runtime/bf16-combined/summary.json`, 절차는 `RUNTIME_STUDY_PROTOCOL.md`다. 첫4회 후 실행이 종료되어 나머지6회는 같은 서버에서 별도 파일로 이어갔으며 이 간격을 보존했다.

따라서 추가군의batch8192/고정seed42/temperature0/직렬실행 조건에서 “BF16 반복만으로15~20회가 흔들린다”는 설명은 관측되지 않았다. 그러나 FP8/AWQ는 여전히 단일 반복이며 weight/calibration/kernel 차이를 통제하지 못했으므로 양자화만의 인과는 미확인이다. 의미 반례 실패도 이 결과로 해소되지 않는다.

수정할 표현: 낮은 bit의 weight 저장은 메모리 절약 가능성이 있지만, 기존 실험의KV cache는 모든 arm에서12GiB로 고정했다. 따라서 KV 용량 증가를 실측한 것이 아니다. 이 소표본에서는 고정 tool 선택의 손상을 관측하지 않았고, 모델 호출 경로 차이가 decode 이득을 상쇄할 가능성은 남는다. 호출 수와 wall-time 변동은 별개이며, 이번6-episode 블록의 E2E 합계는약70.8~105.7초로 변했다. 그 시간 차이의 원인을 양자화나 NUMA로 귀속하지 않는다.
