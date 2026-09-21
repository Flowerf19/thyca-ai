"""Default constants shared by config entities and parsing."""
from __future__ import annotations

DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1"
DEFAULT_PROVIDER_API_KEY_ENV = "THYCA_TOKEN"
DEFAULT_PROVIDER_MODEL = "gpt-4o-mini"
REASONING_EFFORTS = ("low", "high", "max")
DEFAULT_PROVIDER_REASONING_EFFORT = "high"
DEFAULT_TIMELINE_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_LIMITS_LOOP_MAX = 200
DEFAULT_LIMITS_HOT_TAIL_KB = 4
DEFAULT_LIMITS_CONTEXT_TOKENS = 272_000
DEFAULT_LIMITS_CONTEXT_TOKENS_MAX = 2_000_000
DEFAULT_PROVIDER_ID = "default"
PROVIDER_APIS = ("openai_chat", "openai_responses")
DEFAULT_PROVIDER_API = "openai_chat"
