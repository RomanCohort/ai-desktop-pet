#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VTuber模式入口 - 让桌宠能作为轻量级VTuber工作

启动方式:
    python vtuber_main.py

配置文件: config_vtuber.yaml
"""
import asyncio
import sys
import os
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

from oc_desktop_pet.vtuber.bilibili_client import BilibiliLiveClient, Danmaku
from oc_desktop_pet.vtuber.danmaku_handler import DanmakuHandler
from oc_desktop_pet.vtuber.config import load_vtuber_config


class VtuberApp:
    """VTuber应用主类"""

    def __init__(self, config_path: str = "config_vtuber.yaml"):
        self.config_path = config_path
        self.config = None
        self.bilibili_client = None
        self.handler = None
        self.tts_engine = None
        self._running = False

    async def start(self):
        """启动VTuber模式"""
        # 加载配置
        self.config = load_vtuber_config(self.config_path)
        logger.info("VTuber模式启动: {}", self.config.bot_name)

        # 初始化弹幕处理器
        settings = {
            "api_key": self.config.api_key,
            "api_base": self.config.api_base,
            "model": self.config.model,
            "system_prompt": self.config.system_prompt,
        }
        self.handler = DanmakuHandler(settings)

        # 初始化TTS
        if self.config.tts.enable and self.config.tts.use_pyttsx3:
            self._init_tts()

        # 连接B站直播间
        if self.config.bilibili.enable:
            if not self.config.bilibili.room_id:
                logger.error("未配置直播间ID (room_id)")
                return

            self.bilibili_client = BilibiliLiveClient(
                room_id=self.config.bilibili.room_id,
                credentials={
                    "sessdata": self.config.bilibili.sessdata,
                    "bili_jct": self.config.bilibili.bili_jct,
                    "buvid3": self.config.bilibili.buvid3,
                },
            )
            self.bilibili_client.on_danmaku(self._on_danmaku)

            logger.info("正在连接B站直播间: {}", self.config.bilibili.room_id)
            await self.bilibili_client.start()
        else:
            logger.info("B站直播未启用，进入本地测试模式")
            await self._local_test_mode()

    def _init_tts(self):
        """初始化TTS引擎"""
        try:
            import pyttsx3
            self.tts_engine = pyttsx3.init()
            self.tts_engine.setProperty("rate", self.config.tts.rate)
            self.tts_engine.setProperty("volume", self.config.tts.volume)
            logger.info("TTS引擎初始化成功")
        except Exception as e:
            logger.warning("TTS初始化失败: {}", e)
            self.tts_engine = None

    def _on_danmaku(self, danmaku: Danmaku):
        """弹幕回调"""
        try:
            logger.info("[弹幕] {}: {}", danmaku.username, danmaku.content)

            # 处理弹幕
            reply = self.handler.handle(danmaku)
            if reply:
                logger.info("[回复] {}", reply)
                # TTS播报
                self._speak(reply)

        except Exception as e:
            logger.error("弹幕处理异常: {}", e)

    def _speak(self, text: str):
        """TTS语音输出"""
        if self.tts_engine and self.config.tts.enable:
            try:
                # 限制长度
                text = text[:200]
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()
            except Exception as e:
                logger.warning("TTS播报失败: {}", e)

    async def _local_test_mode(self):
        """本地测试模式（不需要连接直播）"""
        logger.info("进入本地测试模式，输入 quit 退出")

        loop = asyncio.get_event_loop()

        while True:
            try:
                # 从标准输入读取
                line = await loop.run_in_executor(None, input, "输入弹幕> ")
                line = line.strip()

                if line.lower() == "quit":
                    logger.info("退出测试模式")
                    break

                if not line:
                    continue

                # 模拟弹幕
                danmaku = Danmaku(
                    content=line,
                    username="测试用户",
                    uid=0,
                )

                reply = self.handler.handle(danmaku)
                if reply:
                    # 移除emoji避免Windows终端编码问题
                    safe_reply = self._remove_emoji(reply)
                    print(f"[回复] {safe_reply}")
                    self._speak(reply)

            except EOFError:
                break
            except KeyboardInterrupt:
                break

    def _remove_emoji(self, text: str) -> str:
        """移除emoji字符（解决Windows终端编码问题）"""
        import re
        emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"
            u"\U0001F300-\U0001F5FF"
            u"\U0001F680-\U0001F6FF"
            u"\U0001F1E0-\U0001F1FF"
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        return emoji_pattern.sub('', text)

    async def stop(self):
        """停止VTuber模式"""
        self._running = False
        if self.bilibili_client:
            await self.bilibili_client.stop()
        logger.info("VTuber模式已停止")


async def main():
    """主函数"""
    # 配置日志
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
    )

    app = VtuberApp()

    try:
        await app.start()
    except KeyboardInterrupt:
        logger.info("收到中断信号")
    finally:
        await app.stop()


if __name__ == "__main__":
    print("=" * 50)
    print("  iGEM VTuber 模式")
    print("  配置文件: config_vtuber.yaml")
    print("=" * 50)

    asyncio.run(main())
