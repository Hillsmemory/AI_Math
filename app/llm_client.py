"""
LLM 客户端封装
基于 OpenAI Python SDK，兼容 OpenAI / DeepSeek / Qwen 等 OpenAI 兼容协议
"""

import os
import threading
import time
from typing import Optional

# 进程内串行化所有 LLM 请求：部分账号（如 Kimi 免费档）并发上限为 1，
# 多标签页/重复点击会触发 429 rate_limit_reached_error
_API_LOCK = threading.Lock()

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # 演示模式下不需要


# 预置模型配置
# vision=True 表示该模型支持读取图片（多模态输入）
PRESET_MODELS = {
    # OpenAI 官方
    "gpt-4o": {"label": "GPT-4o", "base_url": None, "env_key": "OPENAI_API_KEY", "vision": True},
    "gpt-4o-mini": {"label": "GPT-4o Mini", "base_url": None, "env_key": "OPENAI_API_KEY", "vision": True},
    # DeepSeek
    "deepseek-chat": {
        "label": "DeepSeek Chat",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "vision": False,
    },
    # Qwen (DashScope OpenAI 兼容)
    "qwen-plus": {
        "label": "Qwen Plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "QWEN_API_KEY",
        "vision": False,
    },
    "qwen-max": {
        "label": "Qwen Max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "QWEN_API_KEY",
        "vision": False,
    },
    "qwen-vl-max": {
        "label": "Qwen VL Max（视觉）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "QWEN_API_KEY",
        "vision": True,
    },
    # Kimi（Moonshot 月之暗面，OpenAI 兼容协议）
    "moonshot-v1-8k": {
        "label": "Kimi 8K",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "vision": False,
    },
    "moonshot-v1-32k": {
        "label": "Kimi 32K",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "vision": False,
    },
    "moonshot-v1-128k": {
        "label": "Kimi 128K",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "vision": False,
    },
    # 2026 新代际模型（经账号实测可用且支持读图）
    # K2 系推理模型仅接受 temperature=1，其余取值会报 invalid_request_error
    "kimi-k2.6": {
        "label": "Kimi K2.6（视觉）",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "vision": True,
        "force_temperature": 1.0,
    },
    "kimi-k2.7-code": {
        "label": "Kimi K2.7 Code（视觉）",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "vision": True,
        "force_temperature": 1.0,
    },
}


def model_supports_vision(model: str) -> bool:
    """该预置模型是否支持读取图片；未知模型按不支持处理"""
    return bool(PRESET_MODELS.get(model, {}).get("vision", False))


class LLMClient:
    """LLM 客户端：基于 OpenAI 兼容协议"""

    def __init__(self, model: str, api_key: str, base_url: Optional[str] = None):
        if OpenAI is None:
            raise ImportError(
                "未安装 openai 包，请运行: pip install openai"
            )
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        # 显式超时：推理模型（如 Kimi K2）单次调用可能数分钟，
        # 无超时会在网络挂起时永久卡死，连带"停止"按钮也无法生效
        _kw = {"api_key": api_key, "timeout": 240.0, "max_retries": 0}
        if base_url:
            _kw["base_url"] = base_url
        self._client = OpenAI(**_kw)

    @classmethod
    def from_preset(cls, model: str, api_key: Optional[str] = None) -> "LLMClient":
        """用预置配置构造客户端"""
        if model not in PRESET_MODELS:
            raise ValueError(f"未知模型: {model}，可选: {list(PRESET_MODELS.keys())}")

        cfg = PRESET_MODELS[model]
        key = api_key or os.getenv(cfg["env_key"], "")
        if not key:
            raise ValueError(
                f"缺少 API Key：请在 .env 中设置 {cfg['env_key']}，或在界面填入"
            )
        return cls(model=model, api_key=key, base_url=cfg["base_url"])

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2,
             images: Optional[list[str]] = None) -> str:
        """调用 chat completions，返回纯文本响应。
        images: 可选的图片 data URL 列表（如 "data:image/png;base64,..."）。
        仅当模型支持视觉且传入 images 时，构造多模态 user 消息。
        """
        # 部分模型（如 Kimi K2 系推理模型）只接受固定 temperature，按预设强制覆盖
        forced = PRESET_MODELS.get(self.model, {}).get("force_temperature")
        if forced is not None:
            temperature = forced

        if images:
            user_content: object = [{"type": "text", "text": user_prompt}]
            for url in images:
                user_content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            user_content = user_prompt

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        last_err: Optional[Exception] = None
        with _API_LOCK:
            for attempt in range(4):
                try:
                    resp = self._client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature,
                    )
                    return resp.choices[0].message.content or ""
                except Exception as e:
                    msg = str(e)
                    # 限流/并发超限/超时：等待后自动重试
                    if ("429" in msg or "rate_limit" in msg or "concurrency" in msg
                            or "timed out" in msg.lower() or "timeout" in msg.lower()):
                        last_err = e
                        time.sleep(3 * (attempt + 1))
                        continue
                    raise
        raise last_err

    @staticmethod
    def is_available() -> bool:
        """openai 包是否可用"""
        return OpenAI is not None
