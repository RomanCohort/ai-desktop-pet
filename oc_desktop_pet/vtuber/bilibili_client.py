"""B站直播客户端 - 弹幕监听"""
import asyncio
import json
import zlib
import struct
from dataclasses import dataclass
from typing import Callable, Optional
from loguru import logger

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    logger.warning("websockets未安装，B站弹幕功能不可用。pip install websockets")


@dataclass
class Danmaku:
    """弹幕数据"""
    content: str
    username: str
    uid: int
    platform: str = "bilibili"


class BilibiliLiveClient:
    """B站直播间弹幕客户端"""

    # B站直播WebSocket地址
    WS_URL = "wss://broadcastlv.chat.bilibili.com/sub"

    def __init__(self, room_id: int, credentials: dict = None):
        """
        初始化B站直播客户端

        Args:
            room_id: 直播间ID（必须是真实房间号，不是短号）
            credentials: 登录凭证 (sessdata, bili_jct, buvid3)
        """
        self.room_id = room_id
        self.credentials = credentials or {}
        self._ws = None
        self._running = False
        self._danmaku_handlers: list[Callable[[Danmaku], None]] = []
        self._gift_handlers: list[Callable] = []
        self._heartbeat_task = None

    def on_danmaku(self, handler: Callable[[Danmaku], None]):
        """注册弹幕处理器"""
        self._danmaku_handlers.append(handler)

    def on_gift(self, handler: Callable):
        """注册礼物处理器"""
        self._gift_handlers.append(handler)

    async def connect(self) -> bool:
        """连接到直播间"""
        if not HAS_WEBSOCKETS:
            logger.error("websockets库未安装")
            return False

        try:
            self._ws = await websockets.connect(
                self.WS_URL,
                ping_interval=30,
                ping_timeout=10,
            )

            # 发送进房认证包
            auth_packet = self._build_auth_packet()
            await self._ws.send(auth_packet)

            # 接收认证响应
            response = await asyncio.wait_for(self._ws.recv(), timeout=5)
            if self._parse_response(response):
                logger.info("成功连接B站直播间: {}", self.room_id)
                self._running = True
                return True
            else:
                logger.error("B站直播间认证失败")
                return False

        except Exception as e:
            logger.error("连接B站直播间失败: {}", e)
            return False

    async def start(self):
        """启动弹幕监听循环"""
        if not await self.connect():
            return

        # 启动心跳任务
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            while self._running:
                try:
                    message = await asyncio.wait_for(self._ws.recv(), timeout=35)
                    self._handle_message(message)
                except asyncio.TimeoutError:
                    # 超时，检查连接
                    if self._ws.closed:
                        logger.warning("WebSocket连接已关闭，尝试重连...")
                        if not await self.connect():
                            await asyncio.sleep(5)
        except Exception as e:
            logger.error("弹幕监听异常: {}", e)
        finally:
            self._running = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()

    async def stop(self):
        """停止监听"""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

    async def _heartbeat_loop(self):
        """心跳循环"""
        while self._running:
            try:
                await asyncio.sleep(30)
                if self._ws and not self._ws.closed:
                    heartbeat = self._build_heartbeat_packet()
                    await self._ws.send(heartbeat)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("心跳异常: {}", e)

    def _build_auth_packet(self) -> bytes:
        """构建认证包"""
        body = json.dumps({
            "uid": 0,  # 未登录为0
            "roomid": self.room_id,
            "protover": 2,
            "platform": "web",
            "type": 2,
            "key": "",
        }).encode("utf-8")

        header = struct.pack(
            ">IHHII",
            16 + len(body),  # 包长度
            16,  # 头部长度
            1,   # 协议版本
            7,   # 操作码(认证)
            1,   # 序列号
        )

        return header + body

    def _build_heartbeat_packet(self) -> bytes:
        """构建心跳包"""
        header = struct.pack(">IHHII", 16, 16, 1, 2, 1)
        return header

    def _parse_response(self, data: bytes) -> bool:
        """解析响应包"""
        try:
            if len(data) < 16:
                return False

            _, _, _, op, _ = struct.unpack(">IHHII", data[:16])
            return op == 8  # 认证成功响应
        except:
            return False

    def _handle_message(self, data: bytes):
        """处理消息"""
        try:
            # 解析包头部
            if len(data) < 16:
                return

            total_len, header_len, ver, op, _ = struct.unpack(">IHHII", data[:16])

            # 处理多个包
            offset = 0
            while offset + 16 <= len(data):
                total_len, header_len, ver, op, _ = struct.unpack(
                    ">IHHII", data[offset:offset+16]
                )

                body = data[offset + header_len:offset + total_len]

                if op == 5:  # 弹幕消息
                    self._handle_danmaku(body, ver)
                elif op == 3:  # 人气值
                    pass

                offset += total_len
                if offset >= len(data):
                    break

        except Exception as e:
            logger.debug("消息解析异常: {}", e)

    def _handle_danmaku(self, body: bytes, version: int):
        """处理弹幕"""
        try:
            # 解压
            if version == 2:
                body = zlib.decompress(body)

            msg = json.loads(body.decode("utf-8"))

            cmd = msg.get("cmd", "")
            if cmd == "DANMU_MSG":
                info = msg.get("info", [])
                if len(info) >= 2:
                    content = info[1]
                    uid = info[2][0] if len(info) > 2 and len(info[2]) > 0 else 0
                    username = info[2][1] if len(info) > 2 and len(info[2]) > 1 else "匿名"

                    danmaku = Danmaku(
                        content=content,
                        username=username,
                        uid=uid,
                    )

                    for handler in self._danmaku_handlers:
                        try:
                            handler(danmaku)
                        except Exception as e:
                            logger.warning("弹幕处理器异常: {}", e)

            elif cmd == "SEND_GIFT":
                # 礼物处理
                for handler in self._gift_handlers:
                    try:
                        handler(msg)
                    except Exception as e:
                        logger.warning("礼物处理器异常: {}", e)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug("弹幕处理异常: {}", e)

    async def send_danmaku(self, text: str) -> bool:
        """发送弹幕（需要登录凭证和主播权限）"""
        # 这个功能需要更复杂的API调用，这里仅作占位
        logger.warning("发送弹幕功能需要主播权限，暂未实现")
        return False
