# Context budget guidance templates

Nguồn hướng dẫn cho agent về checkpoint/compact (TURN-CURATE).
`agent/context_budget.py` chọn template theo state và điền placeholder
`{action}`, `{urgency}`, `{lookup}`. Không sửa nội dung tùy tiện — test
giữ nguyên các chuỗi khóa ("kind='checkpoint'", "call compact"...).

[recorded]
A checkpoint is recorded in memory for this segment; update it only when the goal, decisions or next step change.

[no-write]
Memory write tools are unavailable for this bot; do not attempt an in-turn checkpoint. The system summary fallback will preserve context at compaction.

[pressure]
{action} {urgency} before large work: {lookup}call memory_remember with kind='checkpoint' and task/new_task to record only the durable delta (goal, decisions, open work, next step, references). Do not dump the transcript, secrets, or DM content. After recording, call compact to summarize the session and free context.

[roomy]
You may proactively checkpoint at a task boundary: call memory_remember with kind='checkpoint' and task/new_task, reusing ids and references instead of copying memory text. After checkpointing a finished task, you may call compact to free context.