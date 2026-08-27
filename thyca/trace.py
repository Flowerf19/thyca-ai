"""Trace aggregation from session JSONL. No new DB — messages + meta are truth."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from thyca.protocol import Message
from thyca.sessions import Session


@dataclass
class TurnSummary:
    session_id: str
    turn_index: int
    title: str
    started_at: str  # ISO of first message
    ended_at: str
    model: str | None
    status: str  # completed | failed | loop_limit
    rounds: int
    requests: int
    prompt_tokens: int | None
    cached_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    # raw slice for detail endpoint
    messages: list[Message]


    def to_payload(self) -> dict:
        """Public JSON shape for /api/traces* — the single source of the field list."""
        return {
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "title": self.title,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "model": self.model,
            "status": self.status,
            "rounds": self.rounds,
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
        }


def _turn_status(slice_msgs: list[Message]) -> str:
    # naming meta-messages are not turn outcomes — skip them, keep old semantics
    last = next(
        (m for m in reversed(slice_msgs) if (m.meta or {}).get("kind") != "naming"),
        None,
    )
    if last is None or last.role != "assistant":
        return "failed"
    if last.content == "loop limit reached":
        return "loop_limit"
    meta = last.meta or {}
    if meta.get("status") == "loop_limit":
        return "loop_limit"
    if meta.get("finish_reason") == "error":
        return "failed"
    return "completed"


def _sum_tokens(turn_msgs: list[Message]) -> tuple[int | None, int | None, int | None, int | None, float | None, int | None, str | None]:
    prompt: int | None = None
    cached: int | None = None
    completion: int | None = None
    total: int | None = None
    cost: float | None = None
    latency: int | None = None
    model: str | None = None
    rounds = 0
    for msg in turn_msgs:
        if msg.role != "assistant":
            continue
        meta = msg.meta or {}
        usage = meta.get("usage")
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            ct = usage.get("cached_tokens")
            cot = usage.get("completion_tokens")
            tt = usage.get("total_tokens")
            if isinstance(pt, int):
                prompt = (prompt or 0) + pt
            if isinstance(ct, int):
                cached = (cached or 0) + ct
            if isinstance(cot, int):
                completion = (completion or 0) + cot
            if isinstance(tt, int):
                total = (total or 0) + tt
            # if no prompt but total present, keep as is; already summed
        c = meta.get("cost_usd")
        if isinstance(c, (int, float)):
            cost = (cost or 0.0) + float(c)
        lat = meta.get("latency_ms")
        if isinstance(lat, int) and lat >= 0:
            latency = (latency or 0) + lat
        m = meta.get("model")
        if isinstance(m, str) and m.strip():
            model = m.strip()  # last wins
        rounds += 1
    # Normalize zero-means-none: if no token found, keep None
    if prompt == 0 and all((m.meta or {}).get("usage") is None for m in turn_msgs if m.role == "assistant"):
        # actually if we summed zero because of missing usage but we initialized 0, we need to check
        # we used (prompt or 0) pattern — if first addition was 0 we set to 0, but we need to know if any usage existed
        has_any = any(isinstance((m.meta or {}).get("usage"), dict) for m in turn_msgs if m.role == "assistant")
        if not has_any:
            prompt = None
            cached = None
            completion = None
            total = None
    # cached defaults to 0 when prompt exists but cached missing? keep 0
    if prompt is not None and cached is None:
        cached = 0
    if cost == 0.0:
        # if no cost found, keep None unless at least one assistant had cost
        has_cost = any(isinstance((m.meta or {}).get("cost_usd"), (int, float)) for m in turn_msgs if m.role == "assistant")
        if not has_cost:
            cost = None
    if latency == 0:
        has_lat = any(isinstance((m.meta or {}).get("latency_ms"), int) for m in turn_msgs if m.role == "assistant")
        if not has_lat:
            latency = None
    # if total missing but prompt+completion present, derive
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return prompt, cached, completion, total, cost, latency, model


def turns_from_session(session: Session) -> list[TurnSummary]:
    # ignore system compaction markers for grouping
    filtered = [m for m in session.messages if m.role != "system"]
    slices: list[list[Message]] = []
    cur: list[Message] | None = None
    for msg in filtered:
        if msg.role == "user":
            if cur is not None:
                slices.append(cur)
            cur = [msg]
        else:
            if cur is None:
                continue
            cur.append(msg)
    if cur is not None:
        slices.append(cur)
    out: list[TurnSummary] = []
    for idx, sl in enumerate(slices):
        prompt, cached, completion, total, cost, latency, model = _sum_tokens(sl)
        status = _turn_status(sl)
        rounds = sum(1 for m in sl if m.role == "assistant")
        requests = rounds
        started = sl[0].ts if sl else ""
        ended = sl[-1].ts if sl else ""
        out.append(
            TurnSummary(
                session_id=session.id,
                turn_index=idx,
                title=session.title or session.id,
                started_at=started,
                ended_at=ended,
                model=model,
                status=status,
                rounds=rounds,
                requests=requests,
                prompt_tokens=prompt,
                cached_tokens=cached,
                completion_tokens=completion,
                total_tokens=total,
                cost_usd=cost,
                latency_ms=latency,
                messages=list(sl),
            )
        )
    return out


def _percentile(sorted_vals: list[int], p: float) -> int | None:
    if not sorted_vals:
        return None
    # nearest-rank method
    k = int((len(sorted_vals) - 1) * p + 0.5)
    k = max(0, min(k, len(sorted_vals) - 1))
    return sorted_vals[k]


def aggregate(turns: list[TurnSummary]) -> dict:
    totals = {
        "requests": sum(t.requests for t in turns),
        "prompt_tokens": sum(t.prompt_tokens or 0 for t in turns),
        "cached_tokens": sum(t.cached_tokens or 0 for t in turns),
        "completion_tokens": sum(t.completion_tokens or 0 for t in turns),
        "total_tokens": sum(t.total_tokens or 0 for t in turns),
        "cost_usd": round(sum(t.cost_usd or 0 for t in turns), 6) if any(t.cost_usd is not None for t in turns) else None,
        "latency_ms_p50": None,
        "latency_ms_p90": None,
    }
    # fix total_tokens when some turns missing total but have prompt+completion
    # already summed as 0 for missing — need to keep 0 if no tokens at all
    has_any_token = any(t.total_tokens is not None or t.prompt_tokens is not None for t in turns)
    if not has_any_token:
        totals["prompt_tokens"] = 0
        totals["cached_tokens"] = 0
        totals["completion_tokens"] = 0
        totals["total_tokens"] = 0
    latencies = sorted([t.latency_ms for t in turns if isinstance(t.latency_ms, int)])
    totals["latency_ms_p50"] = _percentile(latencies, 0.5)
    totals["latency_ms_p90"] = _percentile(latencies, 0.9)
    # by_model
    by: dict[str, list[TurnSummary]] = defaultdict(list)
    for t in turns:
        key = t.model or "unknown"
        by[key].append(t)
    by_model = []
    for model, group in sorted(by.items()):
        lat = sorted([x.latency_ms for x in group if isinstance(x.latency_ms, int)])
        entry = {
            "model": model,
            "requests": sum(x.requests for x in group),
            "prompt_tokens": sum(x.prompt_tokens or 0 for x in group),
            "cached_tokens": sum(x.cached_tokens or 0 for x in group),
            "completion_tokens": sum(x.completion_tokens or 0 for x in group),
            "total_tokens": sum(x.total_tokens or 0 for x in group),
            "cost_usd": round(sum(x.cost_usd or 0 for x in group), 6) if any(x.cost_usd is not None for x in group) else None,
            "latency_ms_p50": _percentile(lat, 0.5),
        }
        by_model.append(entry)
    # by_day
    by_day_map: dict[str, list[TurnSummary]] = defaultdict(list)
    for t in turns:
        day = ""
        try:
            # started_at is ISO UTC YYYY-MM-DDTHH:MM:SSZ
            day = t.started_at.split("T")[0] if "T" in t.started_at else t.started_at[:10]
        except Exception:
            day = ""
        if day:
            by_day_map[day].append(t)
    by_day = []
    for day, group in sorted(by_day_map.items()):
        by_day.append(
            {
                "day": day,
                "requests": sum(x.requests for x in group),
                "cost_usd": round(sum(x.cost_usd or 0 for x in group), 6) if any(x.cost_usd is not None for x in group) else None,
            }
        )
    models = sorted(by.keys())
    by_status_map: dict[str, list[TurnSummary]] = defaultdict(list)
    for t in turns:
        by_status_map[t.status].append(t)
    by_status = [
        {"status": status, "requests": sum(x.requests for x in group)}
        for status, group in sorted(by_status_map.items())
    ]
    by_hour_map: dict[str, list[TurnSummary]] = defaultdict(list)
    for t in turns:
        hour = ""
        try:
            hour = t.started_at.split("T")[1][:2] if "T" in t.started_at else ""
        except Exception:
            hour = ""
        if hour:
            by_hour_map[hour].append(t)
    by_hour = [
        {
            "hour": hour,
            "requests": sum(x.requests for x in group),
            "cost_usd": round(sum(x.cost_usd or 0 for x in group), 6)
            if any(x.cost_usd is not None for x in group)
            else None,
        }
        for hour, group in sorted(by_hour_map.items())
    ]
    return {
        "totals": totals,
        "by_model": by_model,
        "by_day": by_day,
        "by_hour": by_hour,
        "by_status": by_status,
        "models": models,
    }
