"""API 客户端 - 封装 LLM API 调用，含重试和流式支持"""
import json
import re

import requests

from ..persistence.secure_config import SecureConfig
from ..utils.logger import get_logger

_logger = get_logger(__name__)


class APIClient:
    """统一的 LLM API 客户端，支持 OpenAI 兼容格式。"""

    def __init__(self, settings: dict):
        self.settings = settings
        self._max_retries = 3
        self._secure_config = SecureConfig(settings)

    @property
    def api_key(self) -> str:
        """从安全来源获取 API Key（环境变量 > 密钥环 > settings.json）。"""
        return self._secure_config.get_api_key()

    @property
    def api_base(self) -> str:
        return self.settings.get("api_base", "https://api.deepseek.com").rstrip("/")

    @property
    def model(self) -> str:
        return self.settings.get("model", "deepseek-chat")

    def _build_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def validate_key(self) -> bool:
        return self.api_key.startswith("sk-")

    def chat_completion(self, messages, temperature=0.8, max_tokens=320, timeout=60) -> str:
        """同步调用 chat completion API，带自动重试。"""
        if not self.validate_key():
            raise RuntimeError("API Key 未设置")

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = None
        for attempt in range(self._max_retries):
            try:
                resp = requests.post(
                    f"{self.api_base}/chat/completions",
                    headers=self._build_headers(),
                    json=payload,
                    timeout=timeout,
                )
                if resp.status_code == 429:
                    # 限流，等待后重试
                    import time
                    wait = min(2 ** attempt, 8)
                    _logger.warning("API 限流 429，等待 %ds 后重试 (第%d次)", wait, attempt + 1)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    detail = ""
                    try:
                        detail = (resp.text or "")[:180]
                    except Exception as e:
                        _logger.debug("提取错误详情失败: %s", e)
                        detail = ""
                    raise RuntimeError(f"API失败: {resp.status_code} {detail}".strip())

                answer = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                return answer or "嗯嗯，我在听~"

            except requests.exceptions.Timeout:
                _logger.warning("API 请求超时 (第%d次重试)", attempt + 1)
                last_error = RuntimeError("请求超时（网络慢或服务繁忙）")
                continue
            except requests.exceptions.ConnectionError:
                _logger.warning("API 连接失败 (第%d次重试)", attempt + 1)
                last_error = RuntimeError("网络连接失败")
                continue
            except RuntimeError:
                raise
            except Exception as e:
                last_error = e
                continue

        raise last_error or RuntimeError("API调用失败")

    def chat_stream(self, messages, temperature=0.8, max_tokens=320, timeout=60):
        """流式调用 chat completion API，逐 token 返回。"""
        if not self.validate_key():
            raise RuntimeError("API Key 未设置")

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        with requests.post(
            f"{self.api_base}/chat/completions",
            headers=self._build_headers(),
            json=payload,
            timeout=timeout,
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                detail = ""
                try:
                    detail = (resp.text or "")[:180]
                except Exception:
                    detail = ""
                raise RuntimeError(f"API失败: {resp.status_code} {detail}".strip())

            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8", errors="ignore")
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError as e:
                        _logger.debug("流式响应 JSON 解析失败: %s", e)
                        continue
                    except (IndexError, KeyError) as e:
                        _logger.debug("流式响应字段缺失: %s", e)

    @staticmethod
    def build_fallback_reply(user_text: str, reason: str = "") -> str:
        """构建 API 失败时的本地兜底回复。"""
        text = (user_text or "").strip()
        brief = text[:24] + ("…" if len(text) > 24 else "")
        candidates = [
            f"我这边网络有点卡，不过我有在认真听：{brief}",
            "我先陪着你，等网络恢复我们继续聊。",
            "刚刚没连上模型服务，要不要先讲讲你现在在做什么？",
        ]
        if reason and ("API Key" in reason or "401" in reason):
            candidates[0] = "我现在还连不上模型服务，先在本地陪你聊天。"
        return "\n".join(candidates[:2])

    @staticmethod
    def format_error_reason(reason: str) -> str:
        """格式化错误信息为用户友好的提示。"""
        msg = str(reason or "").strip()
        if not msg:
            return "未知错误"
        msg = re.sub(r"\s+", " ", msg)
        if "Read timed out" in msg or "timeout" in msg.lower():
            return "请求超时（网络慢或服务繁忙）"
        if "API Key" in msg or "401" in msg:
            return "鉴权失败（请检查 API Key）"
        if "API失败:" in msg:
            return msg[:120]
        if "网络连接失败" in msg:
            return "网络连接失败"
        return msg[:120]
