"""Nanobot 桥接器 - 连接桌宠和 Nanobot 生信/爬数引擎"""
import asyncio
import json
import queue
import sys
import threading

from ..persistence.paths import BASE_DIR
from ..utils.logger import get_logger

_logger = get_logger(__name__)


class NanobotBridge:
    """通过子进程与 Nanobot 引擎交互的桥接器。"""

    def __init__(self, settings: dict, reply_queue: queue.Queue):
        self.settings = settings
        self.reply_queue = reply_queue
        self._loop = None
        self._thread = None
        self._agent = None
        self._ready = threading.Event()
        self._error = None
        self._lock = threading.Lock()

    def _resolve_paths(self) -> dict:
        cfg = self.settings.get("nanobot", {})
        return {
            "config_path": str(cfg.get("config_path", "")).strip(),
            "workspace": str(cfg.get("workspace", "")).strip(),
            "model": str(cfg.get("model", "")).strip(),
            "timeout": int(cfg.get("timeout_seconds", 60) or 60),
            "channel": str(cfg.get("channel", "desktop_pet") or "desktop_pet"),
            "chat_id": str(cfg.get("chat_id", "oc") or "oc"),
        }

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._error = None
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            if self._loop:
                try:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                except Exception as e:
                    _logger.debug("停止事件循环时出错: %s", e)
            self._loop = None
            self._agent = None

    def ensure_ready(self, timeout=20):
        if self._thread is None or not self._thread.is_alive():
            self.start()
        if not self._ready.wait(timeout=timeout):
            raise RuntimeError("Nanobot 启动超时")
        if self._error:
            raise RuntimeError(self._error)

    def ask(self, text: str) -> str:
        cfg = self._resolve_paths()
        self.ensure_ready(timeout=min(20, cfg["timeout"]))
        if not self._loop or not self._agent:
            raise RuntimeError("Nanobot 未就绪")
        future = asyncio.run_coroutine_threadsafe(
            self._agent.process_direct(
                text,
                session_key=f"{cfg['channel']}:{cfg['chat_id']}",
                channel=cfg["channel"],
                chat_id=cfg["chat_id"],
            ),
            self._loop,
        )
        resp = future.result(timeout=max(10, cfg["timeout"]))
        if resp and getattr(resp, "content", None):
            return resp.content
        return ""

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._bootstrap())
        try:
            self._loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception as e:
                _logger.debug("清理异步任务时出错: %s", e)
            self._loop.close()

    async def _bootstrap(self):
        try:
            cfg = self._resolve_paths()
            nanobot_root = BASE_DIR / "nanobot-0.1.4.post6" / "nanobot-0.1.4.post6"
            if nanobot_root.exists() and str(nanobot_root) not in sys.path:
                sys.path.insert(0, str(nanobot_root))

            from nanobot.agent.loop import AgentLoop
            from nanobot.bus.queue import MessageBus
            from nanobot.cli.commands import _load_runtime_config, _make_provider
            from nanobot.session.manager import SessionManager

            config = _load_runtime_config(cfg["config_path"] or None, cfg["workspace"] or None)
            if cfg["model"]:
                config.agents.defaults.model = cfg["model"]

            bus = MessageBus()
            provider = _make_provider(config)
            session_manager = SessionManager(config.workspace_path)

            self._agent = AgentLoop(
                bus=bus,
                provider=provider,
                workspace=config.workspace_path,
                model=config.agents.defaults.model,
                max_iterations=config.agents.defaults.max_tool_iterations,
                context_window_tokens=config.agents.defaults.context_window_tokens,
                web_search_config=config.tools.web.search,
                web_proxy=config.tools.web.proxy or None,
                exec_config=config.tools.exec,
                bio_lab_config=config.tools.bio_lab,
                cron_service=None,
                restrict_to_workspace=config.tools.restrict_to_workspace,
                session_manager=session_manager,
                mcp_servers=config.tools.mcp_servers,
                channels_config=config.channels,
                timezone=config.agents.defaults.timezone,
            )
            self._ready.set()
        except Exception as e:
            self._error = str(e)
            self._ready.set()
            self.reply_queue.put(("tip", f"Nanobot 初始化失败：{e}"))

    @staticmethod
    def build_nanobot_directive(settings: dict, mode: str = "auto") -> str:
        """构建 Nanobot 工作指令。"""
        cfg = settings.get("nanobot", {})
        if not isinstance(cfg, dict):
            return ""
        pieces = [
            "[Nanobot 工作说明 - 仅用于本次请求]",
            "优先通过工具解决可执行问题，输出清晰可复现步骤。",
            "面向不熟悉生物信息学的医学生，先给结论，再用通俗解释补充关键概念。",
            "不提供临床诊疗建议或具体用药方案；仅用于学习与信息整理。",
            "涉及医学或生物信息学事实时，需在末尾提供可核查的来源链接（论文/数据库/指南）。",
        ]

        bio_enabled = bool(cfg.get("bio_lab_enabled", True))
        web_enabled = bool(cfg.get("web_enabled", True))

        if mode == "bio" and bio_enabled:
            pieces.append("本次请求是生物信息学任务，务必优先使用 bio_platform/bio_data/bio_ml 工具。")
            pieces.append(
                "输出格式建议：\n- 结论（1-3 句）\n- 通俗解释（关键概念）\n"
                "- 可操作步骤（如有）\n- 参考来源：\n  1) <标题> - <URL>\n  2) <标题> - <URL>"
            )
        elif mode == "crawl" and web_enabled:
            pieces.append("本次请求是固定数据库/站点抓取，务必使用 web_search/web_fetch。")
        else:
            if bio_enabled:
                pieces.append(
                    "需要生物信息学分析时，优先使用 bio_platform/bio_data/bio_ml 工具；"
                    "如需本地命令行，仅调用 allowlist 工具。"
                )
            if web_enabled:
                pieces.append("需要爬取或检索数据时，使用 web_search/web_fetch 工具。")

        sources = [str(x).strip() for x in cfg.get("fixed_sources", []) if str(x).strip()]
        policy = str(cfg.get("source_policy", "fixed_only")).strip().lower()
        if sources and policy != "off":
            pieces.append("数据源限制：" + ", ".join(sources))
            if policy == "fixed_only":
                pieces.append("只允许这些来源；如需新增来源，先询问用户。")
            elif policy == "prefer_fixed":
                pieces.append("优先这些来源；若不足以回答，再征询用户是否扩展来源。")
        elif policy == "fixed_only":
            pieces.append("数据源尚未配置，若需要爬数据请先询问用户提供数据库或站点。")

        return "\n".join(pieces).strip()

    @staticmethod
    def parse_nanobot_mode(text: str) -> tuple[str, str]:
        """解析 Nanobot 指令模式。"""
        raw = (text or "").strip()
        if not raw:
            return "auto", raw
        lower = raw.lower()
        prefixes = {
            "/bio": "bio", "bio:": "bio", "生信:": "bio", "生信": "bio",
            "/crawl": "crawl", "/db": "crawl", "crawl:": "crawl",
            "爬数:": "crawl", "数据库:": "crawl",
        }
        for key, mode in prefixes.items():
            if lower.startswith(key):
                return mode, raw[len(key):].lstrip(" ：:")
        return "auto", raw
