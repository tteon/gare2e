"""Prepared information ablation seam, not a reproduced scheduler or tuned baseline.
The existing Admission owns safety/commit/enqueue. This module selects soft holds only.
All pressures are dimensionless ratios produced by a separately frozen calibration.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class State:
    snapshot_id: str
    source_ns: int
    received_ns: int
    cpu_pressure: float
    gpu_pressure: float
    history_pressure: float


@dataclass(frozen=True)
class Hint:
    hint_id: str
    source_episode: str
    target_episode: str
    source_ns: int
    received_ns: int
    available_ns: int
    not_ready: bool
    independent: bool
    increment_pressure: float
    recipe: str  # syntax or db
    db_available: bool = True  # explicit missing-DB fallback provenance


ARMS = {
    "static",
    "state_history",
    "state_prefix",
    "state_graph",
    "cpu_mask",
    "gpu_mask",
    "state_blind",
    "late_hint",
}


def finite(x):
    return type(x) in (int, float) and math.isfinite(x) and x >= 0


def soft_choice(*, arm, now_ns, target, state, hint, started, contract):
    if arm not in ARMS:
        raise ValueError("unknown_arm")
    if arm == "static":
        return False, {"reason": "static", "state_id": None, "hint_id": None}
    if (
        state is None
        or not state.snapshot_id
        or not 0 <= state.source_ns <= state.received_ns <= now_ns
        or now_ns - state.source_ns > contract["state_ttl_ns"]
        or not all(
            finite(x)
            for x in [state.cpu_pressure, state.gpu_pressure, state.history_pressure]
        )
    ):
        return False, {
            "reason": "missing_or_stale_state_generic",
            "state_id": None,
            "hint_id": None,
        }
    cpu = 0 if arm in ("cpu_mask", "state_blind") else state.cpu_pressure
    gpu = 0 if arm in ("gpu_mask", "state_blind") else state.gpu_pressure
    pressure = max(cpu, gpu, state.history_pressure)
    hid = None
    valid = (
        arm != "state_history"
        and hint is not None
        and bool(hint.hint_id)
        and hint.target_episode == target
        and hint.source_episode != target
        and hint.source_episode in started
        and hint.not_ready
        and hint.independent
        and 0 <= hint.source_ns <= hint.received_ns <= hint.available_ns <= now_ns
        and now_ns - hint.source_ns <= contract["hint_ttl_ns"]
        and type(hint.increment_pressure) in (int, float)
        and math.isfinite(hint.increment_pressure)
        and (
            (arm == "state_prefix" and hint.recipe == "syntax")
            or (
                arm != "state_prefix"
                and (
                    hint.recipe == "db"
                    or (hint.recipe == "syntax" and hint.db_available is False)
                )
            )
        )
    )
    # All ready/queued/active work belongs exclusively in state. Only not-ready demand adds here.
    if valid:
        pressure = max(0, pressure + hint.increment_pressure)
        hid = hint.hint_id
    return pressure > contract["pressure_threshold"], {
        "reason": "pressure"
        if pressure > contract["pressure_threshold"]
        else "below_threshold",
        "state_id": state.snapshot_id,
        "hint_id": hid,
        "pressure": pressure,
    }


# A narrow adapter preserves original hard cap, deadlines, hold anchor, aging and commits.
def adapter_class(base):
    class StatefulAdmission(base):
        def __init__(self, contract, *, arm, clock):
            super().__init__(contract, clock)
            self.arm = arm
            self.state = None
            self.hints = {}

        def observe(self, state):
            with self.lock:
                if (
                    state.received_ns > self.clock()
                    or state.source_ns > state.received_ns
                ):
                    raise ValueError("future_state")
                self.state = state
                self.emit(
                    "state_received",
                    snapshot_id=state.snapshot_id,
                    source_ns=state.source_ns,
                    received_ns=state.received_ns,
                )

        def receive_hint(self, hint):
            with self.lock:
                if (
                    hint.received_ns > self.clock()
                    or hint.source_ns > hint.received_ns
                    or hint.available_ns < hint.received_ns
                ):
                    raise ValueError("future_or_invalid_hint")
                self.hints[hint.target_episode] = hint
                self.emit(
                    "hint_received",
                    hint_id=hint.hint_id,
                    source=hint.source_episode,
                    target=hint.target_episode,
                    source_ns=hint.source_ns,
                    received_ns=hint.received_ns,
                    available_ns=hint.available_ns,
                )

        def _choice(self, p, now, policy):
            safety = super()._choice(p, now, "generic")
            if safety[0] != "grant":
                return safety
            until = min(
                p.scheduled_ns + self.c["forecast_hold_ns"],
                p.scheduled_ns + self.c["aging_ns"],
                p.deadline_ns,
            )
            if now >= until:
                return safety
            hold, info = soft_choice(
                arm=self.arm,
                now_ns=now,
                target=p.episode_id,
                state=self.state,
                hint=self.hints.get(p.episode_id),
                started=self.started,
                contract=self.c,
            )
            self.emit("soft_choice", target=p.episode_id, arm=self.arm, **info)
            return (
                ("hold", "bounded_" + info["reason"], info["hint_id"])
                if hold
                else safety
            )

    return StatefulAdmission
