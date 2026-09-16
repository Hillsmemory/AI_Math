"""
LLM 客户端封装
基于 OpenAI Python SDK，兼容 OpenAI / DeepSeek / Qwen 等 OpenAI 兼容协议
"""

import os
from typing import Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # 演示模式下不需要


# 预置模型配置
PRESET_MODELS = {
    # OpenAI 官方
    "gpt-4o": {"label": "GPT-4o", "base_url": None, "env_key": "OPENAI_API_KEY"},
    "gpt-4o-mini": {"label": "GPT-4o Mini", "base_url": None, "env_key": "OPENAI_API_KEY"},
    # DeepSeek
    "deepseek-chat": {
        "label": "DeepSeek Chat",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
    },
    # Qwen (DashScope OpenAI 兼容)
    "qwen-plus": {
        "label": "Qwen Plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "QWEN_API_KEY",
    },
    "qwen-max": {
        "label": "Qwen Max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "QWEN_API_KEY",
    },
}


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
        self._client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

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

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        """调用 chat completions，返回纯文本响应"""
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def is_available() -> bool:
        """openai 包是否可用"""
        return OpenAI is not None
