"""LLM 客户端：OpenAI 兼容接口（真实模型）+ MockLLM（无 key 跑通管线）。"""

from __future__ import annotations

import json
import os
import sys
import time
from abc import ABC, abstractmethod

from .env import KnowledgeBase


class LLMClient(ABC):
    @abstractmethod
    def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        ...

    def reset(self) -> None:
        """每任务开始时重置内部状态（如 MockLLM 的已读文档）。"""


class EmptyModelResponseError(RuntimeError):
    """接口成功返回但没有可用文本，通常属于可重试的瞬态响应。"""


class OpenAICompatClient(LLMClient):
    """OpenAI 兼容接口：默认对接 DeepSeek，也可通过环境变量切到 Qwen / OpenAI。

    环境变量：
    - DEEPSEEK_API_KEY 或 OPENAI_API_KEY：API key
    - OPENAI_BASE_URL：兼容接口地址，默认 https://api.deepseek.com
    - OPENAI_MODEL：模型名，默认 deepseek-v4-flash
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        max_output_tokens: int = 512,
        max_retries: int = 5,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 20.0,
        timeout: float = 60.0,
    ):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("缺少 openai 依赖，请先执行: python -m pip install openai") from e
        self.model = model or os.getenv("OPENAI_MODEL", "deepseek-v4-flash")
        self.max_output_tokens = max(64, int(max_output_tokens))
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self.retry_max_delay = max(self.retry_base_delay, float(retry_max_delay))
        self.timeout = max(1.0, float(timeout))
        self.retry_count = 0
        self.retry_events: list[dict] = []
        resolved_base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
        resolved_api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            # 关闭 SDK 的隐式重试，由本类统一记录并执行，保证实验可观测。
            max_retries=0,
            timeout=self.timeout,
        )

    def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=self.max_output_tokens,
                )
                content = resp.choices[0].message.content or ""
                if not content.strip():
                    raise EmptyModelResponseError("模型返回空内容")
                return content
            except Exception as error:
                if attempt >= self.max_retries:
                    # 保留 Agent 原有的格式错误兜底；网络/API 异常仍向上抛出。
                    if isinstance(error, EmptyModelResponseError):
                        return ""
                    raise
                if not _retryable_api_error(error):
                    raise
                delay = min(
                    self.retry_max_delay,
                    self.retry_base_delay * (2**attempt),
                )
                self.retry_count += 1
                event = {
                    "attempt": attempt + 1,
                    "delay_seconds": delay,
                    "error_type": type(error).__name__,
                    "status_code": getattr(error, "status_code", None),
                }
                self.retry_events.append(event)
                print(
                    "API 请求失败，准备重试 "
                    f"{attempt + 1}/{self.max_retries}：{type(error).__name__}，"
                    f"等待 {delay:g} 秒",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)

        raise RuntimeError("API 重试循环异常退出")

    def reset(self) -> None:
        self.retry_count = 0
        self.retry_events = []


def _retryable_api_error(error: Exception) -> bool:
    """仅重试瞬态连接、限流和服务端错误，不隐藏鉴权或参数错误。"""
    retryable_names = {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "EmptyModelResponseError",
    }
    if type(error).__name__ in retryable_names:
        return True
    status_code = getattr(error, "status_code", None)
    return status_code in {408, 409, 425, 429} or (
        isinstance(status_code, int) and 500 <= status_code < 600
    )


class MockLLM(LLMClient):
    """确定性 mock：无 API 也能跑通全流程，用于测试管线与指标脚本。

    行为：先 search，再把每个文档 read 一遍，最后用文档内容拼出答案。
    仅供阶段一/二调试，正式实验必须换 OpenAICompatClient。
    """

    def __init__(self, env: KnowledgeBase):
        self.env = env
        self._read: list[str] = []
        self._searched = False

    def reset(self) -> None:
        self._read = []
        self._searched = False

    def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        question = next(
            (m["content"] for m in messages if str(m.get("content", "")).startswith("当前任务")),
            "",
        )
        keyword = question.removeprefix("当前任务：").split("，")[0] or question[:8]

        if not self._searched:
            self._searched = True
            return json.dumps(
                {
                    "thought": "先检索相关资料。",
                    "action": {"name": "search", "args": {"keyword": keyword}},
                },
                ensure_ascii=False,
            )

        unread = [did for did in self.env.docs if did not in self._read]
        if unread:
            doc_id = unread[0]
            self._read.append(doc_id)
            return json.dumps(
                {
                    "thought": f"读取文档 {doc_id}。",
                    "action": {"name": "read", "args": {"doc_id": doc_id}},
                },
                ensure_ascii=False,
            )

        # 只能使用当前 prompt 中仍可见的工具证据，避免绕过上下文管理造成评测泄漏。
        visible_evidence = [
            str(message.get("content", ""))
            for message in messages
            if "read 结果：" in str(message.get("content", ""))
        ]
        content = "；".join(visible_evidence) or "当前上下文中没有可用证据"
        return json.dumps(
            {"thought": "信息收集完毕，给出总结。", "final_answer": f"总结：{content}"},
            ensure_ascii=False,
        )


class FaultInjectingLLM(LLMClient):
    """在指定模型调用处注入确定性故障，用于恢复机制的配对实验。"""

    def __init__(self, inner: LLMClient, config: dict | None = None):
        self.inner = inner
        self.config = dict(config or {})
        self.calls = 0
        self.last_reply = ""
        self.last_fault_type: str | None = None

    def reset(self) -> None:
        self.calls = 0
        self.last_reply = ""
        self.last_fault_type = None
        self.inner.reset()

    def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        self.calls += 1
        self.last_fault_type = None
        fault_call = int(self.config.get("call", -1))
        fault_type = str(self.config.get("type", ""))
        if self.calls == fault_call:
            self.last_fault_type = fault_type
            if fault_type == "parse_error":
                return str(self.config.get("payload", "故障注入：本轮输出无法解析"))
            if fault_type == "tool_error":
                return json.dumps(
                    {
                        "thought": "故障注入：调用不存在的工具",
                        "action": {"name": "missing_tool", "args": {}},
                    },
                    ensure_ascii=False,
                )
            if fault_type == "repeated_action" and self.last_reply:
                return self.last_reply

        reply = self.inner.complete(messages, temperature=temperature)
        self.last_reply = reply
        return reply
