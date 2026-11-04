from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class AIError(RuntimeError):
    """AI 调用失败；kind 用于区分超时、HTTP 错误与响应异常。"""

    def __init__(self, message: str, *, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(slots=True)
class ChatResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ChatClient:
    """OpenAI 兼容的 chat/completions 客户端（DeepSeek 默认）。"""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise AIError(f"请求超时: {exc}", kind="timeout") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"请求异常: {exc}", kind="http") from exc

        if response.status_code != 200:
            raise AIError(
                f"HTTP {response.status_code}: {response.text[:200]}", kind="http"
            )
        try:
            body = response.json()
            choices = body["choices"]
            text = str(choices[0]["message"]["content"] or "").strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIError(f"响应格式异常: {response.text[:200]}", kind="bad_response") from exc

        usage = body.get("usage") or {}
        return ChatResult(
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
