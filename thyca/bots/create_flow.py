"""Create-bot chat flow (BE-5): questionnaire → Agent Card.

Thyca builds the card from the user's answers (role / main job /
data touched / command permission) plus Settings defaults. Pure state
machine — no LLM inside, so it is fully unit-testable; the chat layer
drives it one question per turn.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from thyca.bots.card import BotCard, BotMemoryPolicy, BotModel, with_baseline_tools

STEPS = ("role", "main_job", "data_touched", "bash", "confirm")

QUESTIONS = {
    "role": "Bot mới sẽ đảm nhận vai trò gì? (ví dụ: trợ lý lịch, pesquisador, coder)",
    "main_job": "Việc chính của bot là gì? Mô tả một công việc cụ thể nó sẽ làm.",
    "data_touched": "Bot được chạm vào dữ liệu nào? (ví dụ: chỉ lịch, toàn bộ memory, thư mục dự án)",
    "bash": "Bot được chạy lệnh ở mức nào? Trả lời: ask (hỏi trước), allow (tự chạy), deny (cấm).",
    "confirm": "Tạo bot với thông tin trên nhé? Trả lời: ok / sửa <mục>.",
}

BASH_CHOICES = ("ask", "allow", "deny")


@dataclass
class CreateFlowState:
    step: str = "role"
    answers: dict = field(default_factory=dict)
    done: bool = False
    bot_id: str | None = None


def start_flow() -> CreateFlowState:
    return CreateFlowState()


def current_question(state: CreateFlowState) -> str:
    return QUESTIONS[state.step]


def answer_flow(state: CreateFlowState, text: str) -> tuple[CreateFlowState, str]:
    """Feed one user reply; returns the (possibly new) state + next prompt.

    The machine never skips: a bad bash choice re-asks; ``confirm`` accepts
    ``ok`` (done) or ``sửa <mục>`` (jump back to that step).
    """
    text = (text or "").strip()
    if state.done:
        return state, "Luồng tạo bot đã xong."
    if state.step == "bash":
        choice = text.lower()
        if choice not in BASH_CHOICES:
            return state, "Chọn một trong: ask / allow / deny."
        state.answers["bash"] = choice
    elif state.step == "confirm":
        lowered = text.lower()
        if lowered in ("ok", "đồng ý", "dong y", "yes"):
            state.done = True
            return state, "Đã chốt. Thyca sẽ tạo bot."
        for step in STEPS:
            if lowered.startswith(f"sửa {step}") or lowered.startswith(f"sua {step}"):
                state.step = step
                return state, QUESTIONS[step]
        return state, "Trả lời 'ok' để tạo, hoặc 'sửa <mục>' (role/main_job/data_touched/bash)."
    else:
        if not text:
            return state, QUESTIONS[state.step]
        state.answers[state.step] = text
    index = STEPS.index(state.step)
    if index + 1 < len(STEPS):
        state.step = STEPS[index + 1]
        if state.step == "confirm":
            return state, summarize(state) + "\n" + QUESTIONS["confirm"]
        return state, QUESTIONS[state.step]
    state.done = True
    return state, "Đã chốt. Thyca sẽ tạo bot."


def summarize(state: CreateFlowState) -> str:
    answers = state.answers
    return (
        "Tóm tắt bot mới:\n"
        f"- Vai trò: {answers.get('role', '?')}\n"
        f"- Việc chính: {answers.get('main_job', '?')}\n"
        f"- Dữ liệu: {answers.get('data_touched', '?')}\n"
        f"- Chạy lệnh: {answers.get('bash', 'ask')}"
    )


def build_card(
    state: CreateFlowState,
    bot_id: str,
    *,
    model: str = "default",
    tint: str = "accent",
    tools: tuple[str, ...] | list[str] = (),
) -> BotCard:
    """Card from accepted answers + Settings defaults (model/tint)."""
    if not state.done:
        raise ValueError("flow is not confirmed yet")
    answers = state.answers
    role = str(answers.get("role", "Trợ lý")).strip()
    job = str(answers.get("main_job", "")).strip()
    data = str(answers.get("data_touched", "")).strip()
    bash = str(answers.get("bash", "ask")).strip()
    if bash not in BASH_CHOICES:
        raise ValueError("invalid bash choice")
    persona = (
        f"# {role}\n\nViệc chính: {job}\nDữ liệu được chạm: {data}\n\n"
        "Bạn là một bot trong đội của Thyca. Làm đúng việc của mình; "
        "muốn thêm quyền thì gửi yêu cầu cho Thyca, không tự sửa card."
    )
    return BotCard(
        bot_id=bot_id,
        name=bot_id,
        role=role,
        description=job[:1024] or role,
        tint=tint,
        model=BotModel(id=model),
        tools=with_baseline_tools(tools),
        bash=bash,
        memory=BotMemoryPolicy(read=True, write=True),
        persona=persona,
    )
