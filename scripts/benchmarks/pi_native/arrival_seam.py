"""One collector queue seam. Existing worker/loader is injected without replacing it."""

from pathlib import Path
import importlib.util, sys, time, random, hashlib, json, os, math

P = Path(__file__).resolve().parent
R = P.parents[3]


def module(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


base = module("retained_admission", P / "retained_admission.py")
soft = module("retained_soft", P / "retained_state_policy.py")


def schedule(case_ids, seed, rate_per_second):
    if (
        not math.isfinite(rate_per_second)
        or rate_per_second <= 0
        or len(set(case_ids)) != len(case_ids)
    ):
        raise ValueError("invalid_schedule")
    rng = random.Random(seed)
    at = 0
    rows = []
    for i, case in enumerate(case_ids):
        if i:
            at += max(1, round(rng.expovariate(rate_per_second) * 1e9))
        rows.append(dict(ordinal=i, case_id=case, offset_ns=at))
    return dict(seed=seed, rate_per_second=rate_per_second, arrivals=rows)


def digest(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class CollectorQueue(soft.adapter_class(base.Admission)):
    def __init__(
        self,
        contract,
        *,
        arm,
        clock=time.monotonic_ns,
        run_id="cpu",
        block_id="cpu-block",
    ):
        if arm not in contract["arms"]:
            raise ValueError("unregistered_arm")
        super().__init__(contract, arm=arm, clock=clock)
        self.times = {}
        self.run_id = run_id
        self.block_id = block_id
        self.recheck = {}
        self.hold_started = {}
        self.hold_total = {}

    def emit(self, kind, **kw):
        return super().emit(
            kind,
            run_id=self.run_id,
            block_id=self.block_id,
            pid=os.getpid(),
            clock="collector.monotonic_ns",
            **kw,
        )

    def offer_at(self, episode, scheduled_ns, external_D_ns):
        with self.lock:
            now = self.clock()
            if now < scheduled_ns or external_D_ns <= 0:
                raise ValueError("early_offer_or_invalid_D")
            if episode in self.times:
                raise ValueError("duplicate_offer")
            self.times[episode] = {
                "t_scheduled": scheduled_ns,
                "t_offered": now,
                "t_pending_enqueued": None,
                "t_first_eligible": None,
                "t_decision": None,
                "t_commit": None,
                "t_final_answer_received": None,
            }
            self.emit(
                "episode_offered",
                episode_id=episode,
                t_scheduled=scheduled_ns,
                t_offered=now,
            )
            p = base.Pending(episode, scheduled_ns, scheduled_ns + external_D_ns)
            super().offer(p)
            # Atomic queue insertion and timestamp under the same collector lock.
            self.times[episode]["t_pending_enqueued"] = self.clock()
            self.emit("pending_enqueued", episode_id=episode, **self.times[episode])

    def _choice(self, p, now, policy):
        safety = base.Admission._choice(self, p, now, "generic")
        if self.times[p.episode_id]["t_decision"] is None:
            self.times[p.episode_id]["t_decision"] = now
        if safety[0] != "grant":
            self._end_hold(p.episode_id, now)
            return safety
        timing = self.times[p.episode_id]
        if timing["t_first_eligible"] is None:
            timing["t_first_eligible"] = now
            self.emit("first_eligible", episode_id=p.episode_id, t_first_eligible=now)
        anchor = timing["t_first_eligible"]
        until = min(
            anchor + self.c["forecast_hold_ns"],
            p.scheduled_ns + self.c["aging_ns"],
            p.deadline_ns,
        )
        if now >= until:
            self._end_hold(p.episode_id, now)
            return safety
        if now < self.recheck.get(p.episode_id, 0):
            return "hold", "nonblocking_recheck", None
        hint = self.hints.get(p.episode_id)
        if hint is not None and (hint.db_available is not True or hint.recipe != "db"):
            hint = None
        choices = {
            arm: soft.soft_choice(
                arm=arm,
                now_ns=now,
                target=p.episode_id,
                state=self.state,
                hint=hint,
                started=self.started,
                contract=self.c,
            )
            for arm in ["state_history", "state_graph"]
        }
        hold, detail = soft.soft_choice(
            arm=self.arm,
            now_ns=now,
            target=p.episode_id,
            state=self.state,
            hint=hint,
            started=self.started,
            contract=self.c,
        )
        self.emit(
            "legal_choice",
            episode_id=p.episode_id,
            alternatives=["grant", "hold"],
            t_decision=now,
            t_first_eligible=anchor,
            shadow=choices,
            **detail,
        )
        if hold:
            self.hold_started.setdefault(p.episode_id, now)
            self.recheck[p.episode_id] = min(until, now + self.c["recheck_ns"])
            return (
                "hold",
                "first_eligible_bounded_" + detail["reason"],
                detail["hint_id"],
            )
        self._end_hold(p.episode_id, now)
        return safety

    def _end_hold(self, episode, now):
        begin = self.hold_started.pop(episode, None)
        if begin is not None:
            self.hold_total[episode] = self.hold_total.get(episode, 0) + now - begin
            self.emit(
                "voluntary_hold_end",
                episode_id=episode,
                hold_ns=now - begin,
                total_hold_ns=self.hold_total[episode],
            )

    def step(self, enqueue, policy="generic"):
        def linked(p, commit):
            self.times[p.episode_id]["t_commit"] = commit["ns"]
            # The original external schedule is preserved into native acceptance.
            self.emit(
                "episode_commit",
                episode_id=p.episode_id,
                commit_seq=commit["seq"],
                **self.times[p.episode_id],
            )
            return enqueue(p, commit)

        return super().step(linked, policy)

    def final_answer(self, episode, *, request_id, answer_digest):
        with self.lock:
            if self.request_ids.get(episode) != request_id or not answer_digest:
                raise ValueError("answer_identity")
            t = self.clock()
            if self.times[episode]["t_final_answer_received"] is not None:
                raise ValueError("duplicate_final_answer")
            self.times[episode]["t_final_answer_received"] = t
            self.emit(
                "final_answer_received",
                episode_id=episode,
                request_id=request_id,
                answer_digest=answer_digest,
                t_final_answer_received=t,
                e2e_ns=t - self.times[episode]["t_scheduled"],
            )


def drive(
    plan,
    epoch_ns,
    queue,
    enqueue,
    poll_completed,
    *,
    external_D_ns,
    hard_stop_ns,
    sleep=time.sleep,
    decision_step=None,
):
    """One epoch per run is fixed before release; policy cannot reset/slow its offers.
    poll_completed retires only verified owned work. Unknown settlement raises/stops.
    """
    i = 0
    rows = plan["arrivals"]
    while i < len(rows) or queue.pending or queue.active:
        now = queue.clock()
        if now >= hard_stop_ns:
            raise TimeoutError("campaign_limit_keep_all_offered")
        while i < len(rows) and epoch_ns + rows[i]["offset_ns"] <= now:
            row = rows[i]
            ep = row.get("episode_id", f"{queue.run_id}-i{row['ordinal']:02d}")
            queue.offer_at(ep, epoch_ns + row["offset_ns"], external_D_ns)
            i += 1
        poll_completed()
        if queue.pending:
            (decision_step or (lambda: queue.step(enqueue)))()
        sleep(0.001)
    return queue.events


def duration_schedule(case_ids, seed, rate_per_second, warmup_s, measurement_s):
    if (
        not all(math.isfinite(x) for x in (rate_per_second, warmup_s, measurement_s))
        or rate_per_second <= 0
        or not case_ids
        or warmup_s < 0
        or measurement_s <= 0
    ):
        raise ValueError("invalid_duration")
    rng = random.Random(seed)
    at = 0
    rows = []
    end = round((warmup_s + measurement_s) * 1e9)
    # First arrival is sampled, unlike the retained fixed20 pilot's forced zero arrival.
    while True:
        at += max(1, round(rng.expovariate(rate_per_second) * 1e9))
        if at >= end:
            break
        i = len(rows)
        rows.append(
            dict(
                ordinal=i,
                case_id=case_ids[i % len(case_ids)],
                offset_ns=at,
                phase="warmup" if at < round(warmup_s * 1e9) else "measurement",
            )
        )
    return dict(
        seed=seed,
        rate_per_second=rate_per_second,
        warmup_s=warmup_s,
        measurement_s=measurement_s,
        arrivals=rows,
        mode="duration",
        question_scope="cycled development bank; repeated questions NOT independent samples",
    )
