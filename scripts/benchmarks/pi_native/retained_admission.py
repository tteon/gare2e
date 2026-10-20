"""Bounded external episode admission; backend enqueue is injected, not replaced."""

from dataclasses import dataclass, asdict
from collections import deque
from threading import RLock
import time, math


@dataclass(frozen=True)
class Pending:
    episode_id: str
    scheduled_ns: int
    deadline_ns: int


@dataclass(frozen=True)
class Forecast:
    forecast_id: str
    source_episode: str
    target_episode: str
    received_ns: int
    producer_ready: bool
    next_tokens: float
    source_independent: bool


class Admission:
    def __init__(self, contract, clock=time.monotonic_ns):
        self.c = contract
        self.clock = clock
        self.pending = deque()
        self.active = set()
        self.forecasts = {}
        self.events = []
        self.lock = RLock()
        self.seen = set()
        self.reservations = {}
        self.started = set()
        self.enqueue_state = {}
        self.request_ids = {}

    def emit(self, kind, **kw):
        r = dict(seq=len(self.events) + 1, ns=self.clock(), kind=kind, **kw)
        self.events.append(r)
        return r

    def offer(self, p):
        with self.lock:
            if p.episode_id in self.seen or p.scheduled_ns > p.deadline_ns:
                raise ValueError("duplicate_or_invalid_offer")
            self.seen.add(p.episode_id)
            self.pending.append(p)
            self.emit("offered", **asdict(p))

    def receive(self, f):
        with self.lock:
            now = self.clock()
            if f.received_ns > now:
                raise ValueError("future_receipt")
            self.forecasts[f.target_episode] = f
            self.emit("forecast_received", **asdict(f))

    def _choice(self, p, now, policy):
        if now >= p.deadline_ns:
            return "reject", "hard_deadline", None
        if now < p.scheduled_ns:
            return "hold", "scheduled_not_arrived", None
        if len(self.active) >= self.c["capacity"]:
            return "hold", "hard_capacity", None
        f = self.forecasts.get(p.episode_id)
        valid = (
            policy != "generic"
            and f is not None
            and f.source_episode in self.started
            and f.source_episode != p.episode_id
            and f.source_independent
            and not f.producer_ready
            and 0 <= now - f.received_ns <= self.c["forecast_ttl_ns"]
            and isinstance(f.next_tokens, (float, int))
            and math.isfinite(f.next_tokens)
            and f.next_tokens >= 0
        )
        until = min(
            p.scheduled_ns + self.c["forecast_hold_ns"],
            p.scheduled_ns + self.c["aging_ns"],
            p.deadline_ns,
        )
        if valid and f.next_tokens > self.c["demand_threshold_tokens"] and now < until:
            return "hold", "forecast_pressure", f.forecast_id
        return (
            "grant",
            "generic_fallback_or_hold_bound",
            f.forecast_id if valid else None,
        )

    def step(self, enqueue, policy="generic"):
        if policy not in ["generic", "prefix", "prefix_db"]:
            raise ValueError("policy")
        with self.lock:
            if not self.pending:
                return None
            p = self.pending[0]
            now = self.clock()
            action, reason, fid = self._choice(p, now, policy)
            decision = self.emit(
                "decision",
                target=p.episode_id,
                action=action,
                reason=reason,
                forecast_id=fid,
                active=sorted(self.active),
            )
            if action == "hold":
                return decision
            self.pending.popleft()
            if action == "reject":
                return self.emit(
                    "rejected", target=p.episode_id, decision_seq=decision["seq"]
                )
            # Serialized local commit before crossing the backend seam.
            self.active.add(p.episode_id)
            commit = self.emit(
                "commit",
                target=p.episode_id,
                decision_seq=decision["seq"],
                forecast_id=fid,
            )
            self.reservations[p.episode_id] = commit["seq"]
            self.enqueue_state[p.episode_id] = "inflight"
        try:
            receipt = enqueue(p, commit)
            if (
                not isinstance(receipt, dict)
                or receipt.get("episode_id") != p.episode_id
                or not receipt.get("request_id")
            ):
                raise ValueError("enqueue_identity")
            with self.lock:
                if self.reservations.get(p.episode_id) != commit["seq"]:
                    raise ValueError("reservation_lost")
                self.enqueue_state[p.episode_id] = "accepted"
                self.request_ids[p.episode_id] = receipt["request_id"]
                self.emit(
                    "enqueue_accepted",
                    target=p.episode_id,
                    commit_seq=commit["seq"],
                    request_id=receipt["request_id"],
                    backend_scope=receipt.get("scope", "unknown"),
                )
            return receipt
        except Exception as exc:
            with self.lock:
                # Cannot assume a throwing enqueue has not started work. Keep reservation
                # until trusted callback confirms no active work; fail closed per target.
                self.enqueue_state[p.episode_id] = "unknown"
                self.emit(
                    "enqueue_unknown",
                    target=p.episode_id,
                    commit_seq=commit["seq"],
                    error_type=type(exc).__name__,
                )
            raise

    def mark_started(self, episode, request_id):
        with self.lock:
            if (
                episode not in self.reservations
                or episode not in self.active
                or self.enqueue_state.get(episode) != "accepted"
                or self.request_ids.get(episode) != request_id
            ):
                raise ValueError("unaccepted_start")
            self.started.add(episode)
            self.emit("backend_started", target=episode, request_id=request_id)

    def terminal(self, episode, *, no_active_work, evidence_id):
        with self.lock:
            if no_active_work is not True or not evidence_id:
                raise ValueError("unverified_terminal")
            if episode not in self.reservations:
                raise ValueError("unowned_terminal")
            if self.enqueue_state.get(episode) == "inflight":
                raise ValueError("enqueue_still_inflight")
            self.enqueue_state[episode] = "terminal"
            self.active.discard(episode)
            self.started.discard(episode)
            self.emit(
                "terminal",
                target=episode,
                commit_seq=self.reservations.pop(episode),
                evidence_id=evidence_id,
            )
