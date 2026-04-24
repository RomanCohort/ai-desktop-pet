"""
VTuber模块 - 让桌宠能作为轻量级VTuber工作

包含:
- BilibiliLiveClient: B站直播客户端
- DanmakuHandler: 弹幕命令路由器
- Live2DRenderer: Live2D渲染封装(可选)
"""

from .bilibili_client import BilibiliLiveClient, Danmaku
from .danmaku_handler import DanmakuHandler
from .config import VTuberConfig, load_vtuber_config

__all__ = [
    "BilibiliLiveClient",
    "Danmaku",
    "DanmakuHandler",
    "VTuberConfig",
    "load_vtuber_config",
]
