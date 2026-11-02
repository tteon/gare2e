# 실험·진단 목록

원본 디렉터리 44개와 별도 파일 19개를 회수했다. 상태는 당시 파일에 선언된 값이며 완료 재판정이 아니다. `gug-runtime-20261030`은 당시 빈 디렉터리였다.

| 원본 디렉터리 | 파일 | 소스 | summary | root/run 선언 상태 |
| --- | ---: | ---: | ---: | --- |
| `gug-checkpoint-restore-20261101-a` | 3 | 0 | 0 | stopped |
| `gug-checkpoint-restore-20261101-b` | 3 | 0 | 0 | stopped |
| `gug-eval-20261030` | 104 | 39 | 4 | root/run 상태값 없음 |
| `gug-financial-tools-20261031-a` | 592 | 159 | 5 | complete |
| `gug-handoff-isolation-20261101-a` | 267 | 239 | 0 | failed |
| `gug-handoff-isolation-20261101-b` | 284 | 265 | 0 | failed |
| `gug-handoff-isolation-20261101-c` | 1,931 | 266 | 28 | complete, failed |
| `gug-hard-tools-20261101-a` | 455 | 50 | 122 | screened |
| `gug-hard-tools-heldout-20261101-a` | 83 | 5 | 18 | measured |
| `gug-hard-tools-pool-20261101-a` | 41 | 5 | 0 | measured |
| `gug-neo4j-combined-20261101-a` | 71 | 33 | 0 | failed |
| `gug-neo4j-combined-20261101-b` | 72 | 33 | 0 | failed |
| `gug-neo4j-combined-20261101-c` | 1,048 | 33 | 56 | complete |
| `gug-neo4rs-20261030` | 911 | 22 | 16 | root/run 상태값 없음 |
| `gug-neo4rs-adapter-20261101-a` | 290 | 228 | 0 | root/run 상태값 없음 |
| `gug-neo4rs-adapter-20261101-b` | 170 | 122 | 1 | root/run 상태값 없음 |
| `gug-neo4rs-default-20261101-a` | 179 | 131 | 1 | root/run 상태값 없음 |
| `gug-neo4rs-e2e-20261102-a` | 2,694 | 214 | 24 | complete |
| `gug-neo4rs-e2e-checkpoint-20261102-a` | 3 | 0 | 0 | stopped |
| `gug-neo4rs-followup-20261102-a` | 869 | 347 | 77 | complete |
| `gug-neo4rs-index-diagnostic-20261102-a` | 18 | 0 | 0 | complete |
| `gug-neo4rs-memory-confirm-20261102-a` | 438 | 308 | 20 | complete |
| `gug-neo4rs-restored-20261102-a` | 15 | 0 | 0 | root/run 상태값 없음 |
| `gug-neo4rs-tuning-20261101-a` | 1,715 | 153 | 280 | complete |
| `gug-parameter-output-diagnostic-c` | 7 | 1 | 0 | root/run 상태값 없음 |
| `gug-parameter-tools-20261031-a` | 112 | 18 | 0 | failed |
| `gug-parameter-tools-20261031-b` | 62 | 19 | 0 | failed |
| `gug-parameter-tools-20261031-d` | 315 | 19 | 3 | complete |
| `gug-physical-scale-20261031-a` | 1,811 | 159 | 19 | complete, failed, screening |
| `gug-runtime-20261030` | 0 | 0 | 0 | root/run 상태값 없음 |
| `gug-runtime-20261030-b` | 135 | 0 | 0 | root/run 상태값 없음 |
| `gug-runtime-bf16-matched-20261030` | 4 | 0 | 0 | root/run 상태값 없음 |
| `gug-sf1-fullcache-20261031-a` | 235 | 156 | 1 | complete |
| `gug-sf1-optimization-20261031-a` | 4,638 | 233 | 45 | complete, failed |
| `gug-single-tool-20261030` | 1,171 | 7 | 41 | complete |
| `gug-study-20261030` | 2,175 | 0 | 38 | root/run 상태값 없음 |
| `gug-vanilla-20261030` | 61 | 0 | 4 | root/run 상태값 없음 |
| `gug-window-20261031` | 56 | 3 | 2 | failed |
| `gug-window-20261031-b` | 612 | 6 | 5 | interrupted_foreign_gpu |
| `gug-window-20261031-c` | 165 | 3 | 2 | failed |
| `gug-window-20261031-d` | 120 | 3 | 2 | failed |
| `gug-window-20261031-e` | 144 | 3 | 2 | failed |
| `gug-window-20261031-f` | 2,323 | 3 | 16 | failed |
| `gug-window-20261031-g` | 225 | 3 | 3 | measurement |

별도 전송·배포 묶음과 복구 스크립트 19개는 private S3 보존본의
`collection/standalone/`에 있다. 파일별 경로와 SHA는 그 보존본의
`collection/catalog/packages.json`과 `files.jsonl`을 따른다.
원본 저장 위치와 전체 보존본 SHA는 [manifest.json](manifest.json)에 있다.
