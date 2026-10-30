"""Доступ к LLM: все вызовы модели идут только через LLMClient."""

from specforge.core.contracts import ProfileName
from specforge.llm.client import LLMClient, LLMConfig, NullLLM
from specforge.llm.fake import Expect, FakeExpectationError, FakeLLM, FakeReply, FakeToolCall
from specforge.llm.profiles import DEFAULT_PROFILES, Profile, load_profiles
from specforge.llm.tool_parsing import tool_error_message
from specforge.llm.types import (
    ChatMessage,
    ChatResponse,
    LLMConfigError,
    LLMError,
    LLMOutputError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
    LLMUnavailableError,
    StructuredOutputError,
    ToolCall,
    ToolSpec,
)
from specforge.llm.usage import LLMCallRecord, UsageKey, UsageMeter, UsageTracker

__all__ = [
    "DEFAULT_PROFILES",
    "ChatMessage",
    "ChatResponse",
    "Expect",
    "FakeExpectationError",
    "FakeLLM",
    "FakeReply",
    "FakeToolCall",
    "LLMCallRecord",
    "LLMClient",
    "LLMConfig",
    "LLMConfigError",
    "LLMError",
    "LLMOutputError",
    "LLMRequest",
    "LLMResponse",
    "LLMTransientError",
    "LLMUnavailableError",
    "NullLLM",
    "Profile",
    "ProfileName",
    "StructuredOutputError",
    "ToolCall",
    "ToolSpec",
    "UsageKey",
    "UsageMeter",
    "UsageTracker",
    "load_profiles",
    "tool_error_message",
]
