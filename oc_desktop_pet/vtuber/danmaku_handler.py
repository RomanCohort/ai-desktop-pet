"""弹幕命令路由器 - 处理弹幕命令并生成回复"""
from typing import Optional

from loguru import logger

from ..features.meeting_tracker import MeetingTracker
from ..features.task_board import TaskBoard
from ..features.bio_workflow import BioWorkflowGuide
from ..features.doc_hub import DocHub
from ..chat.api_client import APIClient
from .bilibili_client import Danmaku


class DanmakuHandler:
    """弹幕命令路由器

    处理弹幕命令，支持：
    - /meeting - 会议管理
    - /task - 任务看板
    - /team - 团队成员
    - /flow - 生信工作流
    - /doc - 文档中心
    - 普通对话
    """

    def __init__(self, settings: dict):
        self.settings = settings
        self.api_client = APIClient(settings)

        # 初始化功能模块
        self.meeting = MeetingTracker(settings)
        self.task_board = TaskBoard(settings)
        self.bio_workflow = BioWorkflowGuide(settings)
        self.doc_hub = DocHub(settings)

        # 设置LLM预测函数（如果模块支持）
        if hasattr(self.meeting, 'set_llm_predict'):
            self.meeting.set_llm_predict(self._llm_predict)
        if hasattr(self.bio_workflow, 'set_llm_predict'):
            self.bio_workflow.set_llm_predict(self._llm_predict)
        if hasattr(self.doc_hub, 'set_llm_predict'):
            self.doc_hub.set_llm_predict(self._llm_predict)

        # 活跃工作流会话 (用户ID -> 会话)
        self._active_workflows: dict[str, dict] = {}

        # 命令冷却（防止刷屏）
        self._last_reply_time: dict[str, float] = {}
        self._cooldown_seconds = 3.0

    def _llm_predict(self, prompt: str, system: str = "") -> str:
        """LLM预测函数"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.api_client.chat_completion(messages, temperature=0.5, max_tokens=500)

    def handle(self, danmaku: Danmaku) -> Optional[str]:
        """处理弹幕，返回回复文本

        Args:
            danmaku: 弹幕数据

        Returns:
            回复文本，如果不需要回复则返回None
        """
        import time

        content = danmaku.content.strip()
        user_key = str(danmaku.uid)

        # 检查冷却
        now = time.time()
        last_time = self._last_reply_time.get(user_key, 0)
        if now - last_time < self._cooldown_seconds:
            return None

        logger.debug("处理弹幕: {} ({})", content, danmaku.username)

        # 1. 检查是否有活跃的工作流会话
        if user_key in self._active_workflows:
            return self._handle_workflow_continue(danmaku)

        # 2. 命令路由
        reply = None

        # 会议命令
        if any(content.startswith(p) for p in ("/meeting", "/mt", "组会", "会议")):
            reply = self._handle_meeting(content)

        # 任务命令
        elif any(content.startswith(p) for p in ("/task", "任务")):
            reply = self._handle_task(content)

        # 团队命令
        elif any(content.startswith(p) for p in ("/team", "团队", "成员")):
            reply = self._handle_team(content)

        # 生信工作流
        elif any(content.startswith(p) for p in ("/flow", "工作流")):
            reply = self._handle_flow(content, user_key)

        # 文档中心
        elif any(content.startswith(p) for p in ("/doc", "文档")):
            reply = self._handle_doc(content)

        # 自然语言匹配生信工作流
        elif self.bio_workflow.match_workflow(content):
            reply = self._handle_flow(content, user_key)

        # 普通对话
        else:
            reply = self._chat(content, danmaku.username)

        if reply:
            self._last_reply_time[user_key] = now

        return reply

    def _handle_meeting(self, content: str) -> str:
        """处理会议命令"""
        mode, payload = MeetingTracker.parse_meeting_command(content)

        if mode == "list":
            meetings = self.meeting.get_recent_meetings(3)
            if not meetings:
                return "暂无会议记录"
            return "\n".join([
                f"📅 {m.get('title', '')} ({m.get('date', '')})"
                for m in meetings
            ])

        elif mode == "record":
            notes = payload.get("raw_notes", "")
            if not notes:
                return "请提供会议内容"
            meeting = self.meeting.add_meeting(
                date="", title="直播记录", attendees=[],
                raw_notes=notes,
            )
            return f"已记录会议 ID: {meeting['id']}"

        elif mode == "query":
            results = self.meeting.query_meetings(payload.get("query", ""))
            if not results:
                return "未找到相关会议"
            return f"找到 {len(results)} 条相关会议"

        elif mode == "progress":
            results = self.meeting.find_task_progress(payload.get("keyword", ""))
            if not results:
                return "未找到相关进展"
            return f"找到 {len(results)} 条进展记录"

        return "会议命令: list/记录/查询/进展"

    def _handle_task(self, content: str) -> str:
        """处理任务命令"""
        mode, payload = TaskBoard.parse_task_command(content)

        if mode == "board":
            board = self.task_board.get_board_view()
            todo_count = len(board.get("todo", []))
            progress_count = len(board.get("in_progress", []))
            done_count = len(board.get("done", []))
            return f"📋 待办:{todo_count} 🔨 进行中:{progress_count} ✅ 完成:{done_count}"

        elif mode == "add":
            task = self.task_board.add_task(title=payload.get("title", ""))
            return f"已创建任务 ID: {task['id']}"

        elif mode == "update":
            result = self.task_board.update_task(
                payload.get("task_id", ""),
                payload.get("updates", {})
            )
            return f"任务已更新" if result else "任务未找到"

        elif mode == "find":
            tasks = self.task_board.find_by_task(payload.get("keyword", ""))
            return f"找到 {len(tasks)} 个相关任务"

        return "任务命令: 看板/添加/完成/搜索"

    def _handle_team(self, content: str) -> str:
        """处理团队命令"""
        mode, payload = TaskBoard.parse_team_command(content)

        if mode == "list":
            members = self.task_board.get_all_members()
            return f"团队共 {len(members)} 人"

        elif mode == "add":
            member = self.task_board.add_member(
                name=payload.get("name", ""),
                role=payload.get("role", ""),
                skills=payload.get("skills", []),
            )
            return f"已添加成员: {member['name']}" if member else "成员已存在"

        elif mode == "find":
            members = self.task_board.find_by_role(payload.get("keyword", ""))
            members += self.task_board.find_by_skill(payload.get("keyword", ""))
            return f"找到 {len(members)} 位成员"

        return "团队命令: 列表/添加/查找"

    def _handle_flow(self, content: str, user_key: str) -> str:
        """处理生信工作流"""
        mode, payload = BioWorkflowGuide.parse_flow_command(content)

        if mode == "list":
            workflows = self.bio_workflow.list_workflows()
            return "可用: " + ", ".join([w["type"] for w in workflows])

        elif mode == "start":
            wf_type = payload.get("workflow_type", "")
            info = self.bio_workflow.start_session(wf_type)
            if info:
                return f"开始 {info['display_name']}，步骤 {info['step']}/{info['total_steps']}"
            return "未知工作流类型"

        elif mode == "cancel":
            if user_key in self._active_workflows:
                del self._active_workflows[user_key]
            return "已取消工作流"

        return "工作流命令: list/fastqc/blast/primer/建树"

    def _handle_workflow_continue(self, danmaku: Danmaku) -> str:
        """继续活跃的工作流"""
        user_key = str(danmaku.uid)
        session = self._active_workflows.get(user_key)
        if not session:
            return None

        # 推进工作流
        result = self.bio_workflow.advance_session(
            session.get("session_id", ""),
            danmaku.content
        )

        if result:
            if result.get("input_type") == "execute":
                # 执行工作流
                exec_result = self.bio_workflow.execute_workflow(result["session_id"])
                if exec_result and exec_result.get("success"):
                    del self._active_workflows[user_key]
                    return f"执行完成！结果: {exec_result['result'][:200]}"
                return "执行失败"

            return f"步骤 {result['step']}/{result['total_steps']}: {result['prompt']}"

        del self._active_workflows[user_key]
        return "工作流已结束"

    def _handle_doc(self, content: str) -> str:
        """处理文档命令"""
        mode, payload = DocHub.parse_doc_command(content)

        if mode == "list":
            categories = self.doc_hub.get_all_categories_with_count()
            total = sum(c["count"] for c in categories)
            return f"文档中心共 {total} 篇文档"

        elif mode == "search":
            results = self.doc_hub.search(payload.get("query", ""))
            return f"找到 {len(results)} 篇相关文档"

        elif mode == "scan":
            stats = self.doc_hub.scan_watch_folders()
            return f"扫描完成: 新增{stats['added']}篇"

        return "文档命令: 列表/搜索/扫描"

    def _chat(self, content: str, username: str) -> str:
        """普通对话"""
        try:
            system_prompt = self.settings.get(
                "system_prompt",
                "你是iGEM团队的AI助手，正在B站直播。回复简洁有趣。"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ]

            reply = self.api_client.chat_completion(
                messages,
                temperature=0.8,
                max_tokens=150,
            )

            return reply

        except Exception as e:
            logger.error("对话异常: {}", e)
            return "网络有点卡，我稍后再回复你~"
