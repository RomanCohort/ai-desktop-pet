"""飞书桥接器 - 桌宠与飞书的双向消息通道"""
import json
import queue
import threading
import time
from typing import Optional, Dict, Any, Callable

from ..utils.logger import get_logger

_logger = get_logger(__name__)


class FeishuBridge:
    """桌宠 ↔ 飞书 双向消息桥接。

    功能：
    - 接收飞书消息 → 转发给桌宠处理
    - 桌宠事件（提醒、告警、报时）→ 推送到飞书
    - 桌宠回复 → 发送到飞书原会话
    - 使用 lark-oapi SDK 的 WebSocket 长连接，无需公网IP
    - 自动重连机制
    - 心跳保活

    配置项 (settings.json -> feishu):
        enabled: bool - 是否启用
        appId: str - 飞书应用 ID
        appSecret: str - 飞书应用密钥
        allowFrom: list[str] - 允许的用户 open_id 列表
        groupPolicy: str - 群聊策略 ("mention" / "all")
        default_chat_id: str - 默认推送目标
        notify_events: bool - 是否推送桌宠事件到飞书
        notify_reminders: bool - 是否推送日程提醒
        notify_system_alerts: bool - 是否推送系统告警
        reconnect_interval_seconds: int - 重连间隔
        heartbeat_interval_seconds: int - 心跳间隔
    """

    def __init__(self, settings: dict, reply_queue: queue.Queue):
        self.settings = settings
        self.reply_queue = reply_queue
        self._client = None
        self._ws_client = None
        self._thread = None
        self._heartbeat_thread = None
        self._running = False
        self._connected = False
        self._last_feishu_msg_ts = 0.0
        self._lock = threading.Lock()
        self._last_chat_id: str = ""  # 最近消息来源的 chat_id
        self._last_sender_id: str = ""  # 最近消息发送者

    @property
    def config(self) -> dict:
        return self.settings.get("feishu", {})

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    @property
    def is_connected(self) -> bool:
        return self._connected and self._running

    def start(self):
        """启动飞书 WebSocket 监听。"""
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return

        try:
            import lark_oapi as lark
        except ImportError:
            self.reply_queue.put(("tip", "飞书集成需要安装 lark-oapi：pip install lark-oapi"))
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.reply_queue.put(("tip", "飞书桥接已启动"))

    def stop(self):
        """停止飞书连接。"""
        with self._lock:
            self._running = False
            self._connected = False
        if self._ws_client:
            try:
                # WsClient may not have close method, just let thread exit
                pass
            except Exception:
                pass
        self._thread = None
        self._heartbeat_thread = None
        self._client = None
        self._ws_client = None

    def send_to_feishu(self, chat_id: str, text: str, msg_type: str = "text") -> bool:
        """将消息发送到飞书指定会话。

        Args:
            chat_id: 目标会话 ID
            text: 消息内容
            msg_type: 消息类型 (text/post)

        Returns:
            是否发送成功
        """
        if not self._client or not chat_id:
            _logger.debug("飞书客户端未初始化或 chat_id 为空")
            return False
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            if msg_type == "text":
                content = json.dumps({"text": text})
            else:
                content = json.dumps({"text": text})

            req = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type(msg_type)
                    .content(content)
                    .build()) \
                .build()

            resp = self._client.im.v1.message.create(req)
            if not resp.success():
                _logger.error("飞书消息发送失败: %s", resp.msg)
                return False
            _logger.debug("飞书消息已发送到 %s", chat_id)
            return True
        except Exception as e:
            _logger.error("飞书消息发送失败: %s", e)
            return False

    def reply_last_chat(self, text: str) -> bool:
        """回复最近一条消息来源的会话。

        Args:
            text: 回复内容

        Returns:
            是否发送成功
        """
        if not self._last_chat_id:
            _logger.debug("没有可回复的飞书会话")
            return False
        return self.send_to_feishu(self._last_chat_id, text)

    def notify_event(self, event_type: str, text: str, chat_id: Optional[str] = None):
        """将桌宠事件推送到飞书（如果配置允许）。

        Args:
            event_type: 事件类型
            text: 事件内容
            chat_id: 目标会话（可选，默认使用配置的 default_chat_id）
        """
        if not self.enabled or not self._client:
            return

        cfg = self.config
        notify_map = {
            "reminder": cfg.get("notify_reminders", True),
            "system_alert": cfg.get("notify_system_alerts", False),
            "hourly_chime": cfg.get("notify_events", True),
            "random_event": cfg.get("notify_events", True),
            "meeting_summary": cfg.get("notify_events", True),
            "task_deadline": cfg.get("notify_events", True),
        }

        if not notify_map.get(event_type, False):
            return

        # 确定目标会话
        target_chat_id = chat_id or cfg.get("default_chat_id", "")
        if not target_chat_id:
            _logger.debug("飞书未配置 default_chat_id，跳过推送")
            return

        prefix_map = {
            "reminder": "⏰ 提醒",
            "system_alert": "⚠️ 系统告警",
            "hourly_chime": "🕐 报时",
            "random_event": "🎲 随机事件",
            "meeting_summary": "📝 会议摘要",
            "task_deadline": "📋 任务截止",
        }
        prefix = prefix_map.get(event_type, "📢")
        self.send_to_feishu(target_chat_id, f"{prefix} {text}")

    def _do_heartbeat(self):
        """心跳保活线程。"""
        while self._running:
            time.sleep(self.config.get("heartbeat_interval_seconds", 60))
            if not self._running:
                break
            if self._connected:
                _logger.debug("飞书心跳检查: connected=%s", self._connected)

    def _run_loop(self):
        """WebSocket 长连接主循环，带自动重连。"""
        try:
            import lark_oapi as lark
            from lark_oapi.ws import Client as WsClient
            from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        except ImportError as e:
            self.reply_queue.put(("tip", f"飞书 SDK 导入失败: {e}"))
            return

        cfg = self.config
        app_id = cfg.get("appId", "").strip()
        app_secret = cfg.get("appSecret", "").strip()
        if not app_id or not app_secret:
            self.reply_queue.put(("tip", "飞书 appId/appSecret 未配置"))
            return

        reconnect_interval = cfg.get("reconnect_interval_seconds", 30)

        # 主循环：连接 + 自动重连
        while self._running:
            try:
                # 创建飞书 API 客户端
                self._client = lark.Client.builder() \
                    .app_id(app_id) \
                    .app_secret(app_secret) \
                    .log_level(lark.LogLevel.WARNING) \
                    .build()

                # 创建消息处理器
                def on_message(ctx, event: P2ImMessageReceiveV1) -> None:
                    try:
                        if not event or not event.event:
                            return

                        msg_event = event.event
                        sender = msg_event.sender
                        if not sender:
                            return

                        # 权限检查
                        allow_from = cfg.get("allowFrom", [])
                        sender_open_id = sender.sender_id.open_id if sender.sender_id else ""
                        if allow_from and sender_open_id not in allow_from:
                            return

                        # 群聊策略检查
                        chat_type = msg_event.message.chat_type or ""
                        if chat_type == "group" and cfg.get("groupPolicy", "mention") == "mention":
                            mentions = msg_event.message.mentions or []
                            if not any(m.name == "OC桌宠" or m.key == "oc_pet" or m.id == sender_open_id for m in mentions):
                                return

                        # 解析消息内容
                        content = msg_event.message.content or "{}"
                        try:
                            content_dict = json.loads(content)
                            text = content_dict.get("text", "")
                        except (json.JSONDecodeError, TypeError):
                            text = str(content)

                        if not text.strip():
                            return

                        # 冷却：5秒内不重复处理
                        now = time.time()
                        with self._lock:
                            if now - self._last_feishu_msg_ts < 5:
                                return
                            self._last_feishu_msg_ts = now

                        # 存储会话信息用于回复
                        chat_id = msg_event.message.chat_id or ""
                        with self._lock:
                            self._last_chat_id = chat_id
                            self._last_sender_id = sender_open_id

                        # 转发给桌宠处理
                        self.reply_queue.put(("feishu_msg", {
                            "text": text,
                            "chat_id": chat_id,
                            "sender": sender_open_id,
                        }))

                    except Exception as e:
                        _logger.error("飞书消息处理失败: %s", e)

                # 创建事件处理器
                event_handler = lark.EventDispatcherHandler.builder("", "") \
                    .register_p2_im_message_receive_v1(on_message) \
                    .build()

                # 创建 WebSocket 客户端
                self._ws_client = WsClient(
                    app_id=app_id,
                    app_secret=app_secret,
                    log_level=lark.LogLevel.WARNING,
                    event_handler=event_handler,
                    auto_reconnect=True
                )

                self._connected = True
                self.reply_queue.put(("tip", "飞书 WebSocket 已连接"))
                _logger.info("飞书 WebSocket 连接成功")

                # 启动心跳线程
                if not self._heartbeat_thread or not self._heartbeat_thread.is_alive():
                    self._heartbeat_thread = threading.Thread(target=self._do_heartbeat, daemon=True)
                    self._heartbeat_thread.start()

                # 启动 WebSocket 客户端（阻塞）
                self._ws_client.start()

            except Exception as e:
                _logger.error("飞书连接异常: %s", e)
                self.reply_queue.put(("tip", f"飞书连接失败：{e}"))
                self._connected = False

            # 如果仍在运行，等待后重连
            if self._running:
                _logger.info("飞书将在 %d 秒后重连...", reconnect_interval)
                time.sleep(reconnect_interval)

        self._connected = False
        _logger.info("飞书桥接已停止")
