"""Default constants shared by config entities and parsing."""
from __future__ import annotations

DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1"
DEFAULT_PROVIDER_API_KEY_ENV = "THYCA_TOKEN"
DEFAULT_PROVIDER_MODEL = "gpt-4o-mini"
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_PROVIDER_REASONING_EFFORT = "high"
DEFAULT_TIMELINE_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_LIMITS_LOOP_MAX = 200
DEFAULT_LIMITS_SOFT_TIMEOUT_S = 60
DEFAULT_LIMITS_HOT_TAIL_KB = 4
DEFAULT_LIMITS_CONTEXT_TOKENS = 272_000
DEFAULT_LIMITS_CONTEXT_TOKENS_MAX = 2_000_000
DEFAULT_MODEL_MAX_TOKENS = 16_384
LIMIT_RANGES: dict[str, tuple[int, int]] = {
    "loopMax": (1, 200),
    "hotTailKB": (1, 64),
    "contextTokens": (1000, DEFAULT_LIMITS_CONTEXT_TOKENS_MAX),
    "softTimeoutS": (1, 300),
    "maxTokens": (1, DEFAULT_LIMITS_CONTEXT_TOKENS_MAX),
}
DEFAULT_PROVIDER_ID = "default"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
GOOGLE_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
PROVIDER_APIS = (
    "openai_chat",
    "openai_responses",
    "anthropic_messages",
    "google_gemini",
)
DEFAULT_PROVIDER_API = "openai_chat"
