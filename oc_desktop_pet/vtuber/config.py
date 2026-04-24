"""VTuber配置模型"""
import os
from pathlib import Path
from typing import Optional

import yaml


class BilibiliConfig:
    """B站直播配置"""
    def __init__(self, data: dict):
        self.enable = data.get("enable", False)
        self.room_id = data.get("room_id", 0)
        creds = data.get("credentials", {})
        self.sessdata = creds.get("sessdata", "")
        self.bili_jct = creds.get("bili_jct", "")
        self.buvid3 = creds.get("buvid3", "")


class TTSConfig:
    """TTS配置"""
    def __init__(self, data: dict):
        self.enable = data.get("enable", True)
        self.use_pyttsx3 = data.get("use_pyttsx3", True)
        self.rate = data.get("rate", 190)
        self.volume = data.get("volume", 0.9)


class VTuberConfig:
    """VTuber模式配置"""
    def __init__(self, data: dict):
        self.bot_name = data.get("bot_name", "iGEM助手")
        self.bilibili = BilibiliConfig(data.get("bilibili", {}))
        self.tts = TTSConfig(data.get("tts", {}))
        self.settings = data.get("settings", {})

    @property
    def api_key(self) -> str:
        return self.settings.get("api_key", "")

    @property
    def api_base(self) -> str:
        return self.settings.get("api_base", "https://api.deepseek.com")

    @property
    def model(self) -> str:
        return self.settings.get("model", "deepseek-chat")

    @property
    def system_prompt(self) -> str:
        return self.settings.get("system_prompt", "你是iGEM团队的AI助手。")


def load_vtuber_config(config_path: str) -> VTuberConfig:
    """加载VTuber配置文件"""
    path = Path(config_path)
    if not path.exists():
        # 返回默认配置
        return VTuberConfig({})

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return VTuberConfig(data.get("vtuber", data))
