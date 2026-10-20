"""Prepared bridge to existing cohort.run_case/prepare; no alternative data loader.
Caller replaces the original submit-all block and removes run_case's old admission_commit.
No native deployment or state-telemetry producer is implied by this adapter.
"""

from arrival_seam import *
from concurrent.futures import ThreadPoolExecutor, wait
import threading


def run_cohort_queue(
    *,
    a,
    runtime,
    prepared,
    run_case,
    collector_record,
    plan,
    contract,
    arm,
    epoch_ns,
    external_D_ns,
    hard_stop_ns,
    producer=None,
):
    specs = prepared[-1]
    if [s["case_id"] for s in specs] != [s["case_id"] for s in plan["arrivals"]]:
        raise ValueError("arrival_case_order_mismatch")
    gate = CollectorQueue(
        contract,
        arm=arm,
        run_id=a.out.name,
        block_id=getattr(a, "block_id", a.out.name),
    )
    futures = {}
    results = []
    # Strong PS/PSG needs a frozen state and forecast producer; never silently run generic as PSG.
    if producer is None and arm != "static":
        raise ValueError("missing_native_producer")
    if arm != "static" and contract.get("calibration_status") != "FROZEN_STATIC":
        raise ValueError("state_policy_calibration_not_frozen")
    pool = ThreadPoolExecutor(max_workers=contract["capacity"])
    result_lock = threading.RLock()
    completed = {}
    closing = False
    cancel_event = threading.Event()

    def persist():
        rejected = {e["target"] for e in gate.events if e["kind"] == "rejected"}
        rows = []
        for ep in gate.times:
            row = completed.get(ep)
            if row is None:
                row = dict(
                    episode_id=ep,
                    status="reject" if ep in rejected else "unknown",
                    owner_settlement="not_dispatched"
                    if ep not in futures
                    else "unknown",
                )
            spec = next(s for s in specs if s["episode_id"] == ep)
            rows.append({**spec, **row})
        for name, value in [
            ("arrival-events", gate.events),
            ("arrival-times", gate.times),
            ("queue-results", rows),
        ]:
            dest = a.out / (name + ".json")
            tmp = dest.with_suffix(".tmp")
            tmp.write_text(json.dumps(value, indent=2))
            tmp.replace(dest)
        return rows

    def collect(ep, future):
        with result_lock:
            try:
                completed[ep] = future.result()
            except Exception as exc:
                completed[ep] = dict(
                    episode_id=ep,
                    status="failure",
                    error=type(exc).__name__,
                    owner_settlement="unknown",
                )
            if closing:
                persist()

    try:

        def enqueue(p, commit):
            item = next(s for s in specs if s["episode_id"] == p.episode_id)
            if item["episode_id"] != p.episode_id:
                raise ValueError("native_episode_mismatch")
            collector_record(
                a.out,
                "admission_commit",
                dict(
                    target_episode=p.episode_id,
                    action="grant",
                    policy=arm,
                    queue_commit_seq=commit["seq"],
                    arrival_times=gate.times[p.episode_id],
                ),
            )
            future = pool.submit(
                run_case,
                a,
                runtime,
                item,
                prepared,
                cancel_event=cancel_event,
                campaign_cutoff_ns=hard_stop_ns,
            )
            futures[p.episode_id] = future
            future.add_done_callback(lambda f, ep=p.episode_id: collect(ep, f))
            return dict(
                episode_id=p.episode_id,
                request_id="native-dispatch-" + p.episode_id,
                scope="executor submission, NOT engine acceptance",
            )

        def poll():
            for ep, future in list(futures.items()):
                if not future.done():
                    continue
                del futures[ep]
                try:
                    result = future.result()
                except Exception as exc:
                    results.append(
                        dict(
                            episode_id=ep,
                            status="failure",
                            error=type(exc).__name__,
                            owner_settlement="unknown",
                        )
                    )
                    raise RuntimeError("native_failure_unknown_settlement") from exc
                results.append(result)
                # run_case only retires owner on accepted terminal exit0/86. Any other outcome stays unknown.
                if result.get("exit_code") not in (0, 86):
                    raise RuntimeError("native_nonsettled_exit")
                gate.terminal(
                    ep, no_active_work=True, evidence_id="native-run-case-retired-" + ep
                )

        def decision_step():
            if producer is None:
                return gate.step(enqueue)
            with producer.lock:
                producer.update(gate)
                gate.emit("decision_generation", producer_generation=producer.version)
                return gate.step(enqueue)

        drive(
            plan,
            epoch_ns,
            gate,
            enqueue,
            poll,
            external_D_ns=external_D_ns,
            hard_stop_ns=hard_stop_ns,
            decision_step=decision_step,
        )
    finally:
        # Cancellation propagates to each owned native process wait. Never infer engine settlement.
        cancel_event.set()
        _, not_done = wait(list(futures.values()), timeout=8)
        if not_done:
            collector_record(
                a.out,
                "cleanup_incomplete",
                dict(remaining=len(not_done), scope="thread/native wait unknown"),
            )
        with result_lock:
            closing = True
            pool.shutdown(wait=False, cancel_futures=True)
            rows = persist()
    if not_done:
        raise RuntimeError("native_cleanup_not_reaped")
    return rows
