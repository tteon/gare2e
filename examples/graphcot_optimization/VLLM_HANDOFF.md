# vLLM 개선 담당자에게 전달할 계약

## 설명할 motivation

DB최적화로같은답을더빨리반환해도,후속모델요청의admission과batching이바뀌면
finite burst의마지막긴응답이늦어질수있습니다. 최신실제E2E는DBHTTP시간개선에도
Hard40/C32배치+3.28%였습니다. 이것은검증된관측이며새scheduler해법의효과가아닙니다.

## 먼저 어느baseline인지선택

| 기준 | backend/DB | generation |
|---|---|---|
|과거동결E2E|Python,pool16,H512/P256,workers32,optimizedquery|BF16/context8192/output800/thinkingoff|
|최신memoryE2E|releasepatchedneo4rs,pool/admission16,H512/P256vsH1024/P1024|BF16/context8192/output2048/episode180s|

8tools,Frozen32또는Hard40같은known계약입니다. Rawprompt/answer는Git에없으므로
original실험을그대로재실행하려면별도허용된privateworkload가필요합니다.
자신의새workload는새B0로등록합니다. FP8/multi-turn/다른context와혼합비교하지않습니다.

## 제안하는실험순서 — 미실행계획

1. **B0재현:**model/weightsSHA·정밀도·DB/query/index·생성예산고정. 실제serviceversion,
   graph감사/정답/oracle/evidence/token/실패분모를확인합니다.
2. **B1slots:**seq8→16단독. KV12GiB/batch8192/context등고정,readiness/memory검사후실행.
   실제running/waiting/preemption/KV/배정량과도구별지연을봅니다.
3. **B2재진입:**FCFS/priority-all0/phase우선권대조. 설치vLLMAPI/정책호환을먼저확인합니다.
   기존단순finalcap8은악화해채택하지않았습니다. Initial starvation도평가합니다.
4. **B3조합:**유망개입만factorial비교해주효과와상호작용을분리합니다.
5. **B4지속부하:**uniqueoccurrence,burn-in/steady/drain,고정offeredmix로SLO/성공률/backlog.

Oracle정답길이/미래completiontokens를우선순위입력에사용하지않습니다.
Queue가길다는것만으로CPUschedulercompute병목이라고하지않습니다.
KVeviction/recompute/prefixcache문제는실제metric/trace가있을때위원인으로언급합니다.

## Agent에게줄지시문

```text
examples/graphcot_optimization/README.md, RESULTS.md, PROTOCOL.md를읽으세요.
먼저offline verify/demo를실행하고,live실험은데이터/모델/서비스소유권과허가를확인한뒤
새runroot에서실행하세요. 기본SDK/runtime/다른사람의서비스는변경하지마세요.

DB/query/model/precision/schema/출력예산을고정한B0부터확보하세요.
최적화정책과engine slots를동시에바꾸지말고주효과후조합을검증하세요.
각요청은run_id/uniqueoccurrence/user/tool/phase로연결하고
offered→dispatch→toolready→engineenqueue→firstschedule→completion을기록하세요.
같은evidence,입출력token,actualscheduledtoken을대조하세요.
offered=ok+failed+dropped,워밍업별도,평균/p95/tail/도구별공정성을보고하세요.
Finitebatch를steadycapacity로부르거나mock숫자를live성능으로제시하지마세요.
검증된사실/원인가설/미실행개입을구분해서인수인계를남기세요.
```

Graph와LLM을함께계측하려면기존저장소의
[Graph-system observability](../../docs/agentic-os/GRAPH_SYSTEM_OBSERVABILITY.md)와
[harness](../../docs/agentic-os/HARNESS.md)를함께참조합니다. 이예제는기존하네스나
SEOCHO의workspace_id정책을우회하는새productionAPI를추가하지않습니다.
