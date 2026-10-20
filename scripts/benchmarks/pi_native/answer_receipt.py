"""Collector-clock answer receipt. Original scorer's latest-finish rule stays authoritative."""


class AnswerReceipt:
    def __init__(self, episode):
        self.episode = episode
        self.session = None
        self.rows = []
        self.end = None
        self.errors = []

    def accept(self, source, received_ns):
        if source.get("kind") not in ("finish_result", "finish_stream_end"):
            return
        if source.get("episode_id") != self.episode or not isinstance(
            source.get("session_id"), str
        ):
            self.errors.append("identity")
            return
        if self.session is None:
            self.session = source["session_id"]
        if self.session != source["session_id"]:
            self.errors.append("session_drift")
            return
        if source["kind"] == "finish_stream_end":
            if self.end is not None:
                self.errors.append("duplicate_end")
            self.end = source
            return
        if self.end is not None:
            self.errors.append("result_after_end")
        e = source.get("event", {})
        if (
            source.get("sequence") != len(self.rows) + 1
            or e.get("type") != "tool_execution_end"
            or e.get("toolName") != "finish_task"
        ):
            self.errors.append("sequence_or_kind")
            return
        self.rows.append(dict(event=e, received_ns=received_ns, source=source))

    def resolve(self, parse_finish, journal_events, session):
        details = parse_finish([r["event"] for r in self.rows])
        original = parse_finish(journal_events)
        valid = (
            not self.errors
            and self.end is not None
            and self.end.get("count") == len(self.rows)
            and self.session == session
            and details == original
        )
        return dict(
            t_final_answer_received_ns=self.rows[-1]["received_ns"]
            if valid and details and self.rows
            else None,
            answer_receipt_complete=valid,
            answer_receipt_errors=self.errors,
            finish_frames=len(self.rows),
            answer_receipt_scope="collector complete-line receipt; latest finish selected by unchanged parser; independent of exit/QA/cleanup",
            selected_tool_call_id=self.rows[-1]["event"].get("toolCallId")
            if valid and details and self.rows
            else None,
        )
