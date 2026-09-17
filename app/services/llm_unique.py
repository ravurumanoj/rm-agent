from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage

from app.config import settings
from app.services.observability import llm_span, record_exception, record_llm_output
from app.services.unique_sdk_client import configure_unique_sdk
from app.utils.logger import logger

try:
    import unique_sdk  # type: ignore[reportMissingImports]
except Exception:
    unique_sdk = None


class UniqueAILLM:
    """Unique provider wrapper with explicit SDK contract validation.

    Primary path:
        unique_sdk.ChatCompletion.create(company_id=..., user_id=..., model=..., messages=...)

    Optional fallback path:
        langchain_openai.ChatOpenAI against UNIQUE_API_BASE_URL when unique-sdk is unavailable.
    """

    PROVIDER_NAME = "unique_ai"
    HANDLES_OWN_RETRY = False

    def __init__(self, model: Optional[str] = None, bound_tools: Optional[list] = None) -> None:
        self.model = model or settings.UNIQUE_MODEL_NAME
        self._bound_tools = list(bound_tools or [])
        self._openai_compatible_client = None

    def _resolve_identity(self, **kwargs) -> tuple[str, str]:
        company_id = str(
            kwargs.get("company_id")
            or settings.UNIQUE_COMPANY_ID
            or ""
        ).strip()
        user_id = str(
            kwargs.get("user_id")
            or settings.UNIQUE_USER_ID
            or ""
        ).strip()
        if not company_id or not user_id:
            raise RuntimeError(
                "Unique AI requires company/user identity. Set UNIQUE_COMPANY_ID and "
                "UNIQUE_USER_ID, or pass company_id/user_id per call."
            )
        return company_id, user_id

    def _validate_config(self) -> None:
        missing: list[str] = []
        if not self.model:
            missing.append("UNIQUE_MODEL_NAME")
        if not settings.UNIQUE_API_BASE_URL:
            missing.append("UNIQUE_API_BASE_URL")
        if not settings.UNIQUE_APP_ID:
            missing.append("UNIQUE_APP_ID")
        if not settings.UNIQUE_APP_KEY:
            missing.append("UNIQUE_APP_KEY")
        if missing:
            raise RuntimeError("Missing required Unique AI settings: " + ", ".join(missing))

    def _configure_unique_sdk(self) -> Any:
        return configure_unique_sdk(sdk_module=unique_sdk, settings_obj=settings)

    def _to_unique_messages(self, messages: list[BaseMessage]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for m in messages:
            role = getattr(m, "type", "user")
            if role == "human":
                role = "user"
            elif role == "ai":
                role = "assistant"
            elif role == "system":
                role = "system"
            else:
                role = "user"

            content = getattr(m, "content", "")
            if isinstance(content, list):
                content = "\n".join(str(item) for item in content)
            normalized.append({"role": role, "content": str(content or "")})
        return normalized

    def _extract_text(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, dict):
            choices = payload.get("choices", [])
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message", {})
                if isinstance(message, dict):
                    return str(message.get("content") or "").strip()
            return ""
        if hasattr(payload, "to_dict") and callable(payload.to_dict):
            try:
                return self._extract_text(payload.to_dict())
            except Exception:
                return ""
        choices = getattr(payload, "choices", None)
        if isinstance(choices, list) and choices:
            first = choices[0]
            message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
            if isinstance(message, dict):
                return str(message.get("content") or "").strip()
            if message is not None:
                return str(getattr(message, "content", "") or "").strip()
        return ""

    def _extract_tool_calls(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            if hasattr(payload, "to_dict") and callable(payload.to_dict):
                try:
                    payload = payload.to_dict()
                except Exception:
                    return []
            else:
                return []

        choices = payload.get("choices", [])
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return []
        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            return []
        raw_tool_calls = message.get("tool_calls") or message.get("toolCalls") or []
        if not isinstance(raw_tool_calls, list):
            return []
        normalized: list[dict[str, Any]] = []
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {})
            if not isinstance(fn, dict) or not fn.get("name"):
                continue
            normalized.append(
                {
                    "id": str(tc.get("id") or f"call_{fn['name']}"),
                    "type": "function",
                    "function": {
                        "name": str(fn.get("name")),
                        "arguments": str(fn.get("arguments") or "{}"),
                    },
                }
            )
        return normalized

    def _extract_usage(self, payload: Any) -> Optional[dict[str, int]]:
        if isinstance(payload, dict):
            usage = payload.get("usage")
            if isinstance(usage, dict):
                return {
                    "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
                    "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                }
        if hasattr(payload, "to_dict") and callable(payload.to_dict):
            try:
                return self._extract_usage(payload.to_dict())
            except Exception:
                return None
        usage = getattr(payload, "usage", None)
        if isinstance(usage, dict):
            return {
                "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
                "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        return None

    def _coerce_tool_defs(self, tools: Optional[list]) -> list[dict[str, Any]]:
        if not tools:
            return []
        result: list[dict[str, Any]] = []
        for item in tools:
            if isinstance(item, dict):
                if item.get("type") == "function" and isinstance(item.get("function"), dict):
                    result.append(item)
                    continue
                if "name" in item:
                    result.append(
                        {
                            "type": "function",
                            "function": {
                                "name": str(item.get("name")),
                                "description": str(item.get("description", "")),
                                "parameters": item.get("parameters", {"type": "object", "properties": {}}),
                            },
                        }
                    )
                    continue
            name = getattr(item, "name", None)
            if name:
                description = getattr(item, "description", "")
                args_schema = getattr(item, "args_schema", None)
                params: dict[str, Any] = {"type": "object", "properties": {}}
                if args_schema is not None and hasattr(args_schema, "model_json_schema"):
                    try:
                        params = args_schema.model_json_schema()
                    except Exception:
                        params = {"type": "object", "properties": {}}
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": str(name),
                            "description": str(description),
                            "parameters": params,
                        },
                    }
                )
        return result

    def _build_openai_fallback_client(self):
        if self._openai_compatible_client is not None:
            return self._openai_compatible_client
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[reportMissingImports]
        except Exception as exc:
            raise RuntimeError(
                "Unique AI fallback path requires langchain-openai (pip install -e .[openai])"
            ) from exc

        kwargs: dict[str, Any] = {
            "model": self.model,
            "api_key": settings.UNIQUE_APP_KEY,
            "base_url": settings.UNIQUE_API_BASE_URL,
        }
        client = ChatOpenAI(**kwargs)
        if self._bound_tools:
            client = client.bind_tools(self._bound_tools)
        self._openai_compatible_client = client
        return self._openai_compatible_client

    def _invoke_sync_unique_sdk(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        self._validate_config()
        company_id, user_id = self._resolve_identity(**kwargs)

        sdk = self._configure_unique_sdk()
        chat_completion = getattr(sdk, "ChatCompletion", None)
        create = getattr(chat_completion, "create", None) if chat_completion else None
        if create is None or not callable(create):
            raise RuntimeError("unique_sdk.ChatCompletion.create is unavailable in installed SDK")

        tool_defs = self._coerce_tool_defs(kwargs.get("tools") or self._bound_tools)
        tool_choice = kwargs.get("tool_choice")

        options: dict[str, Any] = {}
        if tool_defs:
            options["tools"] = tool_defs
        if tool_choice is not None:
            options["tool_choice"] = tool_choice
        if "temperature" in kwargs and kwargs["temperature"] is not None:
            options["temperature"] = kwargs["temperature"]

        payload: dict[str, Any] = {
            "company_id": company_id,
            "user_id": user_id,
            "model": self.model,
            "messages": self._to_unique_messages(messages),
        }
        if options:
            payload["options"] = options

        logger.debug(
            "UniqueAI invoke payload prepared",
            extra={
                "model": self.model,
                "message_count": len(payload["messages"]),
                "tool_count": len(tool_defs),
                "has_tool_choice": tool_choice is not None,
                "has_options": bool(options),
            },
        )

        result = create(**payload)
        text = self._extract_text(result)
        tool_calls = self._extract_tool_calls(result)
        usage = self._extract_usage(result)
        response_metadata = {"token_usage": usage} if usage else {}
        return AIMessage(
            content=text,
            additional_kwargs={"tool_calls": tool_calls} if tool_calls else {},
            response_metadata=response_metadata,
        )

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        with llm_span(
            "unique_ai.ainvoke",
            model=self.model,
            provider=self.PROVIDER_NAME,
            messages=messages,
            metadata={"bound_tool_count": len(self._bound_tools)},
        ) as span:
            try:
                result = await asyncio.to_thread(self._invoke_sync_unique_sdk, messages, **kwargs)
                record_llm_output(
                    span,
                    str(result.content or ""),
                    (result.response_metadata or {}).get("token_usage") if hasattr(result, "response_metadata") else None,
                )
                if span is not None:
                    span.set_attribute("llm.success", True)
                    span.set_attribute("llm.tool_call_count", len(getattr(result, "tool_calls", []) or []))
                return result
            except RuntimeError as exc:
                if "unique-sdk" not in str(exc):
                    if span is not None:
                        span.set_attribute("llm.success", False)
                    record_exception(span, exc)
                    raise
                logger.warning("Unique SDK path unavailable; using OpenAI-compatible fallback: %s", exc)
                if span is not None:
                    span.set_attribute("llm.fallback_provider", "openai_compatible")
                result = await self._build_openai_fallback_client().ainvoke(messages, **kwargs)
                record_llm_output(span, str(result.content or ""))
                if span is not None:
                    span.set_attribute("llm.success", True)
                return result
            except Exception as exc:
                if span is not None:
                    span.set_attribute("llm.success", False)
                record_exception(span, exc)
                raise

    async def astream(self, messages: list[BaseMessage], **kwargs) -> AsyncIterator[AIMessageChunk]:
        # unique-sdk path is synchronous; convert full response to one chunk.
        response = await self.ainvoke(messages, **kwargs)
        yield AIMessageChunk(content=response.content, additional_kwargs=response.additional_kwargs)

    def bind_tools(self, tools: list, **kwargs) -> "UniqueAILLM":
        return UniqueAILLM(model=self.model, bound_tools=list(tools))
