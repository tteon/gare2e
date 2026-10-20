"""Behavior checks of the retained queue with a deterministic collector clock."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arrival_seam import CollectorQueue, schedule, duration_schedule, drive
from retained_state_policy import State
from answer_receipt import AnswerReceipt


def contract():
    return dict(
        arms=["static", "state_history"],
        capacity=1,
        forecast_hold_ns=20_000_000,
        aging_ns=100_000_000,
        recheck_ns=2_000_000,
        forecast_ttl_ns=100_000_000,
        demand_threshold_tokens=1,
        state_ttl_ns=100_000_000,
        pressure_threshold=1,
        hint_ttl_ns=100_000_000,
    )


def test_hold_and_capacity_do_not_block_offers():
    clock = [0]
    q = CollectorQueue(contract(), arm="state_history", clock=lambda: clock[0])
    q.observe(State("s", 0, 0, 2, 0, 0))
    plan = {
        "arrivals": [
            dict(ordinal=i, case_id=str(i), offset_ns=i * 1_000_000) for i in range(3)
        ]
    }
    completed = {}
    order = []

    def enqueue(p, c):
        order.append(p.episode_id)
        completed[p.episode_id] = clock[0] + 5_000_000
        return dict(episode_id=p.episode_id, request_id=p.episode_id)

    def poll():
        for ep, end in list(completed.items()):
            if clock[0] >= end:
                q.terminal(ep, no_active_work=True, evidence_id="fixture-settled")
                del completed[ep]

    drive(
        plan,
        0,
        q,
        enqueue,
        poll,
        external_D_ns=200_000_000,
        hard_stop_ns=300_000_000,
        sleep=lambda _: clock.__setitem__(0, clock[0] + 1_000_000),
    )
    assert order == ["cpu-i00", "cpu-i01", "cpu-i02"]
    assert [v["t_offered"] for v in q.times.values()] == [0, 1_000_000, 2_000_000]
    assert q.times["cpu-i00"]["t_first_eligible"] == 0
    assert q.times["cpu-i00"]["t_commit"] == 20_000_000
    assert max(len(e.get("active", [])) for e in q.events) <= 1


def test_seed_duration_and_fixed_pilot_are_distinct():
    a = schedule(["a", "b", "c"], 42, 0.25)
    assert a == schedule(["a", "b", "c"], 42, 0.25)
    assert a["arrivals"][0]["offset_ns"] == 0
    b = duration_schedule(["a", "b"], 42, 0.25, 20, 100)
    assert b == duration_schedule(["a", "b"], 42, 0.25, 20, 100)
    assert b["arrivals"][0]["offset_ns"] > 0
    assert all(r["offset_ns"] < 120_000_000_000 for r in b["arrivals"])
    assert {r["phase"] for r in b["arrivals"]} == {"warmup", "measurement"}


def test_deadline_rejection_and_unknown_enqueue():
    q = CollectorQueue(contract(), arm="static", clock=lambda: 20)
    q.offer_at("e", 0, 10)
    q.step(lambda *_: (_ for _ in ()).throw(AssertionError("expired enqueue")))
    assert q.events[-1]["kind"] == "rejected"
    q = CollectorQueue(contract(), arm="static", clock=lambda: 0)
    q.offer_at("e", 0, 100)
    try:
        q.step(lambda *_: (_ for _ in ()).throw(RuntimeError("uncertain")))
    except RuntimeError:
        pass
    assert "e" in q.active and q.enqueue_state["e"] == "unknown"


def test_receipt_does_not_substitute_cleanup_for_answer():
    r = AnswerReceipt("e")
    event = dict(
        type="tool_execution_end",
        toolName="finish_task",
        toolCallId="call",
        result={"details": {"ok": True}},
    )
    r.accept(
        dict(
            kind="finish_result",
            episode_id="e",
            session_id="s",
            sequence=1,
            event=event,
        ),
        123,
    )
    parse = lambda rows: rows[-1]["result"]["details"] if rows else None
    assert r.resolve(parse, [event], "s")["t_final_answer_received_ns"] is None
    r.accept(
        dict(kind="finish_stream_end", episode_id="e", session_id="s", count=1), 999
    )
    assert r.resolve(parse, [event], "s")["t_final_answer_received_ns"] == 123


def test_nonfinite_arrival_parameters_fail_instead_of_hanging():
    import pytest

    for value in (float("nan"), float("inf"), 0, -1):
        with pytest.raises(ValueError):
            schedule(["a"], 1, value)
        with pytest.raises(ValueError):
            duration_schedule(["a"], 1, value, 0, 1)


def test_static_cohort_reuses_worker_and_retains_rejections(tmp_path):
    import time
    from types import SimpleNamespace
    from cohort_adapter import run_cohort_queue

    out = tmp_path / "cohort"
    out.mkdir()
    now = time.monotonic_ns()
    specs = [dict(case_id=i, episode_id=f"e{i}") for i in range(3)]
    plan = {
        "arrivals": [
            dict(ordinal=i, case_id=i, episode_id=f"e{i}", offset_ns=0)
            for i in range(3)
        ]
    }
    calls = []

    def worker(a, runtime, item, prepared, **kwargs):
        calls.append(item["case_id"])
        time.sleep(0.005)
        return dict(
            **item,
            exit_code=0,
            owner_settlement="fixture: trusted callback returned after retire",
        )

    rows = run_cohort_queue(
        a=SimpleNamespace(out=out),
        runtime=None,
        prepared=[specs],
        run_case=worker,
        collector_record=lambda *a, **k: None,
        plan=plan,
        contract=contract(),
        arm="static",
        epoch_ns=now,
        external_D_ns=10**9,
        hard_stop_ns=now + 2 * 10**9,
    )
    assert calls == [0, 1, 2] and len(rows) == 3
    import json

    events = json.loads((out / "arrival-events.json").read_text())
    offered = [r for r in events if r["kind"] == "episode_offered"]
    assert len(offered) == 3 and max(r["t_offered"] for r in offered) < min(
        r["ns"] for r in events if r["kind"] == "terminal"
    )
