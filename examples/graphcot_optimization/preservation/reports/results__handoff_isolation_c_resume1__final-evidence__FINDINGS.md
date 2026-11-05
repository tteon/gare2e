> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/handoff_isolation_c_resume1/final-evidence/FINDINGS.md`; SHA256 `2fca3e6bc6ee002c78066b40d833a43e423ea061fa90666335dc6d4a132be8ce`.

# Tool 반환 타이밍·엔진 재진입 분리 결과 — 2026-11-01

## 이번 실험에서 확인한 결론

**Tool 반환 타이밍만 바꿔도 C32의 유한 burst 종료 성능 역전이 재현됐다.** 업무 Cypher 실행을 제거한 지연 재현에서, 짧은 지연은 긴 지연 대비 완료율을7.48–9.33% 낮췄다. 두 CPU 배치와 두 실행 순서에서 같은 방향이었다. 실제 DB 비교도8.43–11.77% 낮았다. 반면 C8의 같은 지연 재현은23.82–24.71% 개선됐다. 따라서 이 효과는 동시성과 뒤따르는 모델 요청의 배정 순서에 따라 달라진다.

입력/출력 token·반환 byte/evidence뿐 아니라 대응 traced 요청의 실제 배정 token 수도 같았다. 서로 다른 출력량 또는 cache로 인해 추가 token이 배정된 것으로 이 차이를 설명할 근거는 이번 비교에서 없었다. 업무 DB CPU 작업이 없어도 역전이 나타났으므로 DB CPU 경합은 이 현상의 필수 조건이 아니다. 공유 CPU 또는 GPU kernel의 모든 영향을 배제했다는 뜻은 아니다.

긴 답변의 **실제 최초 스케줄이 늦어지는** 패턴을 확인했다. 예를 들어 split 배치의 같은 `trace_time_ordered_fund_paths-v0-p0` 응답437tokens는 첫 순서에서24.715→30.120초에 최초 배정됐고, 완료는41.509→45.943초로 늦어졌다. 역순에서는23.582→30.543초에 배정돼40.953→46.417초에 완료됐다. 모든 시간은 batch 시작 기준이다. 마지막25%구간에서 함께 token을 배정받은 호출 수도 줄었다. 이는 GPU kernel이나 HBM 대역폭을 직접 측정한 결과는 아니며, 긴 응답이 뒤로 밀리는 finite-burst drain 관측이다. 평균 큐 대기의 증가에는 enqueue가 빨라진 영향도 섞여 있으므로 절대 배정·완료 시점을 따로 검토했다.

Cypher 개선의 이익과 시나리오 간 지연은 함께 나타났다. 두 순서 평균으로 overlap의 fan-in 사용자 평균 E2E는35.047→28.547초로 개선됐지만, 긴 시간순 경로 답변의 최대 E2E는41.834→45.847초로 늘었다. 현재 문제를 모든 사용자에게 동일하게 적용되는 DB 지연 또는 모델 품질 저하로 요약하지 않는다.

**최종 요청 cap8은 채택하지 않는다.** 엔진 대기는 약3초로 줄었지만 클라이언트 대기 중앙값이 약9–10초로 옮겨졌고, 완료율은1.48–2.57% 낮아졌다. 실험 소유 CPU 배치를 분리해도 역전이 해소되지 않았다. 공유 cgroup에서 throttled-period 비율1.78–7.99%가 관측됐고 다른 서비스는 그대로 두었으므로 완전한 CPU 격리 또는 NUMA 원인의 판정은 남는다. 측정된 Scheduler 메서드 wall-time 중앙값은0.33–0.60ms였으며, 이 수치나 큐 길이만으로 CPU scheduler 계산 포화를 주장하지 않는다.

| 가설 | 판정 | 이번 근거 |
|---|---|---|
| Tool readiness 변화만으로 C32 역전이 생길 수 있다 | 이 고정 burst에서 지지 | DB 업무 실행 없이 두 CPU 배치·두 순서에서 재현, token/evidence/배정량 일치 |
| DB CPU 경합이 역전의 필수 원인이다 | 이 비교에서는 지지되지 않음 | timing-only에서도 같은 방향 |
| 단순 final admission cap8이 해소한다 | 지지되지 않음 | 대기가 client로 이동하고 완료율1.48–2.57% 감소 |
| 소유 프로세스 CPU affinity 분리로 해소된다 | 이 개입에서는 지지되지 않음 | 두 배치 모두 같은 방향; 전용 CPU 격리는 아님 |
| 엔진 trace의 비용이 약9% 역전을 만든다 | 이 대조에서는 지지되지 않음 | off/on의 완료시간 차이−0.91%,+0.18%; 동일한 instant replay |
| 실제 서비스의 지속 처리능력이 감소했다 | 미검증 | 32건 finite burst이며 steady window가 없음 |

다음 실험은 **gug-w06(미실행)**으로 분리했다. 모델·SF를 먼저 바꾸지 않고, 긴 최종 응답의 재진입 순서와 engine 슬롯8/16을 각각 비교한 뒤 장시간 부하에서 시나리오별 지연·공정성·완료율을 평가한다. 기존 query를 의도적으로 느리게 만드는 것을 개선안으로 채택하지 않는다. Positive L5/L6·독립 degree·실제 disk/NUMA와 자원 조율은 기존 gug-1mf에 남겼다.

## 최종 감사

28/28셀,896/896 measured와224/224warmup이 정답 완료했다. 고유 context1120개/model 호출2240개 중 trace off 두 대조를 제외한2080개 호출을 모두 엔진과 연결했다. Monitor 오류0, canary1289/1289 성공이다. 원시 파일1926개와 archive SHA를 검증했고, 수정 전후 control source를 보존해 timed 코드의 byte 동일성을 확인했다.

4개 PNG를 실제로 시각 검토했으며, 동일 figure의 PDF도 출력했다. Timeline은 세 조건의 공통 x축을 사용하고 CPU 비교의 대기 그림은 행별 공통 y축을 사용한다. 새 정리 감사 [see original archived attachment]에서 소유 PID/start54개 기록 모두 비활성, GPU compute job0,17688/18000닫힘, 원본7687열림과4083nodes/27429edges·전체 속성 동일을 확인했다. 종료된 소유 memfd의 stale symlink4개만 제거했고 디스크 weight 파일과 타인의 학습 checkpoint/서비스는 보존했다. Git/Dolt 원격 동기화는 실행하지 않았다.

같은 BF16 체크포인트·SF1·도구 계약을 유지한 28개 finite cohort, 두 역순 block의 결과다. C32는32건 burst의 drain을 포함한 완료율이며 지속 서비스 용량 측정이 아니다. 그래프 도구1회와 모델2회로 구성한 single-agent 세션을 여러 사용자로 실행했다. 자유 Cypher 생성·multi-turn 수정 또는 runtime multi-agent 검증은 포함하지 않는다.

## 고정 구성

모델은 BF16/TP1/seq8/batch8192/KV12GiB/OMP4, context8192/output800/temp0/seed42/thinking off다. Neo4jCommunity5.26.0은 heap512MiB/pagecache256MiB/Bolt32, Python driver pool16+canary1, production query deadline15s를 유지했다. vLLM0.31.0/torch2.13.0/transformers5.17.0/driver5.28.2/httpx0.28.1이며 하드웨어는 hardware-verified.json에 있다. Arm은 conditions.version 및 실제 Cypher SHA로 구분한다. bound_call.query_version은 기존 Registry의 legacy label을 유지하므로 이 필드만으로 arm을 집계하지 않는다.

## 비교 결과

단위는 정답 완료 episode/초다. 각 셀32 offered이며 실패/drop을 포함한 분모와 종료시간을 사용한다.

| CPU 배치 | 순서 block | 비교 | 기준 | 개입 | 변화 |
|---|---|---|---|---|---|
| overlap | 1 | 실제DB reference→optimized | 0.7675 | 0.6977 | -9.09% |
| overlap | 1 | 지연재현 reference→optimized | 0.7702 | 0.6996 | -9.17% |
| overlap | 1 | 짧은지연→최종요청 cap8 | 0.6996 | 0.6853 | -2.03% |
| overlap | 2 | 실제DB reference→optimized | 0.7646 | 0.7001 | -8.43% |
| overlap | 2 | 지연재현 reference→optimized | 0.7618 | 0.6989 | -8.26% |
| overlap | 2 | 짧은지연→최종요청 cap8 | 0.6989 | 0.6809 | -2.57% |
| split | 1 | 실제DB reference→optimized | 0.7709 | 0.6965 | -9.65% |
| split | 1 | 지연재현 reference→optimized | 0.7649 | 0.6935 | -9.33% |
| split | 1 | 짧은지연→최종요청 cap8 | 0.6935 | 0.6832 | -1.48% |
| split | 2 | 실제DB reference→optimized | 0.7813 | 0.6894 | -11.77% |
| split | 2 | 지연재현 reference→optimized | 0.7661 | 0.7088 | -7.48% |
| split | 2 | 짧은지연→최종요청 cap8 | 0.7088 | 0.6973 | -1.62% |

| C8 지연 재현 | 기존 지연 | 개선 지연 | 변화 |
|---|---|---|---|
| block1 | 0.5299 | 0.6562 | +23.82% |
| block2 | 0.5298 | 0.6607 | +24.71% |

Replay는 업무 Cypher 실행을 생략하고, 이전 C32 두 순서의 tool+인자별 DB span 중앙값을 async sleep으로 재현한다. 동일한 RETURN1 canary 및 모니터링은 유지했다. 각 지연 profile은 고유16개 인자 계약에 적용했으며32개 요청의 내용/반환 byte/token을 대조했다. C8 replay는 이 고정 profile의 동시성 효과이며 새 C8 실제DB 처리율 측정으로 바꾸어 부르지 않는다.

## 큐 길이와 절대 스케줄 시점

아래는 C32 split 배치다. enqueue가 빨라지면 실제 admission이 같아도 queue wait는 늘 수 있다. 따라서 batch 시작 이후의 절대 시점과 별도로 비교한다. 단위 초, final 모델 호출32개의 평균.

| block | 조건 | enqueue | 최초 token 배정 | queue wait | HTTP 완료 | 마지막25%구간 평균 scheduled 호출 수 |
|---|---|---|---|---|---|---|
| 1 | live-reference | 14.584 | 21.468 | 6.885 | 26.257 | 3.811 |
| 1 | live-optimized | 11.172 | 21.993 | 10.821 | 26.828 | 2.049 |
| 1 | replay-reference-timing | 14.822 | 21.920 | 7.098 | 26.707 | 3.866 |
| 1 | replay-optimized-timing | 11.389 | 22.586 | 11.197 | 27.408 | 2.110 |
| 1 | replay-optimized-final8 | 19.129 | 22.113 | 2.984 | 26.891 | 1.912 |
| 2 | live-reference | 15.335 | 21.296 | 5.962 | 26.036 | 4.236 |
| 2 | live-optimized | 11.392 | 22.009 | 10.617 | 26.793 | 1.948 |
| 2 | replay-reference-timing | 14.535 | 21.753 | 7.218 | 26.582 | 3.826 |
| 2 | replay-optimized-timing | 11.546 | 22.295 | 10.749 | 27.098 | 2.497 |
| 2 | replay-optimized-final8 | 19.452 | 22.433 | 2.981 | 27.245 | 2.126 |

## 시나리오 간 E2E 영향

실제DB C32 비교. 각 tool/조건의 두 block×4요청=8개 E2E를 모두 사용한다. 단위 초이며 평균과 최대를 함께 보존한다. 개별 요청과 DB span은 analysis.json에 있다.

| CPU 배치 | 도구 | reference 평균/최대 | optimized 평균/최대 |
|---|---|---|---|
| overlap | find_fan_in_accounts | 35.047/39.167 | 28.547/30.039 |
| overlap | find_linked_account_pairs | 27.073/30.854 | 22.463/27.288 |
| overlap | find_shared_access_accounts | 19.766/22.338 | 24.732/28.649 |
| overlap | list_transfer_recipients | 24.362/26.065 | 28.303/31.911 |
| overlap | rank_linked_account_flows | 25.242/31.532 | 24.939/31.516 |
| overlap | trace_rapid_pass_through | 27.372/38.988 | 29.730/43.198 |
| overlap | trace_time_ordered_fund_paths | 28.126/41.834 | 29.432/45.847 |
| overlap | trace_transfer_reachability | 24.547/30.638 | 28.174/34.930 |
| split | find_fan_in_accounts | 34.816/38.957 | 28.907/30.556 |
| split | find_linked_account_pairs | 27.649/31.180 | 22.572/27.961 |
| split | find_shared_access_accounts | 18.942/21.512 | 24.249/28.698 |
| split | list_transfer_recipients | 23.780/25.189 | 27.858/31.967 |
| split | rank_linked_account_flows | 24.762/31.390 | 23.815/31.261 |
| split | trace_rapid_pass_through | 27.013/39.233 | 29.467/43.224 |
| split | trace_time_ordered_fund_paths | 27.655/41.493 | 29.308/46.394 |
| split | trace_transfer_reachability | 24.485/30.290 | 28.227/34.889 |

전체 batch 종료율과 개별 업무 latency를 함께 평가해야 한다. 일부 Cypher 사용자의 개선과 다른 긴 응답 사용자의 지연이 동시에 나타날 수 있다. 각 셀의 tool별 offered는 동일하며 drop에 따른 수락 mix 차이는 없다.

`최초 token 배정`은 엔진 Scheduler의 첫 배정 시점이며 GPU kernel 실행 시작을 직접 잰 값은 아니다. 마지막 구간의 scheduled 호출 수는 SchedulerOutput에 token이 배정된 호출 수이고 실제 CUDA graph의 padded batch 크기나 HBM 대역폭 측정이 아니다. 개별 요청의 admission·완료 시점은 analysis.json [see original archived attachment]의 per_tool 및 각 cell/engine-joined.json에 보존했다.

## 계측 대조 및 CPU 범위

| block | trace off 완료시간 s | trace on 완료시간 s | on 시간 변화 |
|---|---|---|---|
| 1 | 46.480 | 46.059 | -0.91% |
| 2 | 46.087 | 46.168 | +0.18% |

계측은 설치된 AsyncScheduler 메서드의 입력과 반환 객체를 그대로 위임하고 enqueue/배정 event만 기록했다. scheduler 정책은 바꾸지 않았다. off/on은 C32 instant replay에서의 대조이며 모든 workload에 대한 보편적 overhead 상한은 아니다.

모델 CPU16–31/OMP4를 고정했다. overlap은 DB16–31·client16–19, split은 DB0–15·client32–35이다. 이 affinity 개입은 전용 코어 예약이나 NUMA binding이 아니다. 기존 Tempo·원본 Neo4j·관측 서비스를 보존했고 공유 cgroup quota30.72cores를 유지했다. CPU 배치 결과로 공유 quota/메모리 대역폭 간섭이 없다고 입증하지 않는다. resource 로그와 매 셀 전후 process/affinity snapshot을 보존했다.

## 검증과 실행 이력

- Measured 896 = 896정답 완료 + 0실패 + 0drop; warmup 224 중0실패.
- 1120개의 독립 사용자 context, 2240 model 호출. 계획된 trace off 대조를 제외하고2080호출의 엔진 join을 검증했다.
- 대응 성공 요청의 token/evidence 일치: True; traced pair의 실제 scheduled token 일치 True. 실제 tool/인자 Registry 재바인딩, 독립 expected/최종답 재파싱, JSON byte/hash, source/config/입력 hash 및 전체 분모를 점검했다.
- 원시 archive SHA256 `8b88e27301bd6bac28f83a3a6e3a9b810739bd2525941b7e7b76d1ba4dfbfa97`, 파일1926개. 기계 검증 [see original archived attachment], 집계표 [see original archived attachment], 원시 오류 [see original archived attachment].
- 첫 a/b는 checkpoint 경로 부재 및 중복 인자 coverage assertion으로 측정 전 종료했다. 실제 c의 cell0–4 이후 예정된 모델 재시작 경계에서 legacy 종료 함수의 /proc exit race가 발생했다. 종료 처리만 수정해 미실행 cell5부터 같은 논리 run을 이어갔다. 기존 측정 파일을 덮어쓰지 않았고 source-v1/owner/status/cleanup을 resume1-history에 보존했다. Timed evaluator/server/query/config는 두 제어기 revision 사이 byte가 같다.
- 원래 checkpoint가 없어 원본 target-hub metadata의 Qwen/Qwen3.8-27B revision1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0으로 복원했다. 이전 config/index/template hash·파일크기와 새18개 weight LFS SHA를 검증했다. 과거 weight byte SHA가 없어 복원 원본과 과거 가중치의 직접 byte 비교는 불가능하다. 모든 새 비교에는 동일하게 복원한 가중치를 사용했다.
- 디스크 reserve5GiB를 유지하려고2개 shard만 소유 memfd에 두고 나머지는 소유 disk 파일로 고정했다. 초기 로딩/복원은 측정 밖이다. 최종 소유 모델·DB·checkpoint stage의 정리 및 기존 graph 보존을 별도로 확인했다.

## 해석의 한계

공식 SF1 canonical556082nodes/3101291edges에 기존 월별bucket sidecar가 고정된 store를 사용했다. 현재 도구는 원본 관계를 조회한다. L5/L6 공식 요청은 빈 결과이고 양성 반례는 이전 query 검증에서 별도로 확인했다. 새로운 SF/degree/disk/NUMA·4GiB E2E·neo4rs·정밀도 변경은 이번 실험에 포함하지 않았다.

두 실행 순서와 고정16개 인자 계약의 finite cohort이다. 지속 arrival rate·drop tradeoff·장시간 용량 또는 모든 실제 사용자 분포로 일반화하지 않는다. 긴 최종 응답의 입장 순서와 drain 패턴 관측을 실제 GPU kernel/대역폭 또는 CPU scheduler 계산 병목의 입증으로 바꾸지 않는다.

## 그림

처리율 비교 [see original archived attachment] · 엔진/클라이언트 대기 [see original archived attachment] · 첫 방향 timeline [see original archived attachment] · 역순 timeline [see original archived attachment]

판정과 시각 검토는 위 INTERPRETATION 원문 및 figure-review.json에 기록했다.
