"""会议进展追踪器 - 记录组会内容、AI摘要、查询检索"""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..persistence.paths import MEETINGS_PATH
from ..persistence.store import Store
from ..chat.api_client import APIClient


class MeetingTracker:
    """管理iGEM组会记录的追踪器。

    功能：
    - 记录组会原始笔记
    - AI自动生成结构化摘要（已完成/卡点/下一步/决定）
    - 按关键词/日期/参会人检索历史会议
    - 跨会议追踪任务进展
    - 推送摘要到飞书
    """

    SUMMARIZE_PROMPT = """你是一个iGEM团队会议纪要助手。请将以下组会原始笔记整理为结构化JSON。

要求：
- 严格按照JSON格式输出，不要加```标记
- completed: 已完成的事项列表
- blockers: 当前遇到的卡点/问题列表
- next_steps: 下一步行动列表
- key_decisions: 本次会议做出的关键决定列表
- topics: 本次会议涉及的主题标签（2-5个词）

原始笔记：
{raw_notes}

输出JSON："""

    def __init__(self, settings: dict, reply_queue=None):
        self.settings = settings
        self.reply_queue = reply_queue
        self.meetings: list[dict] = []
        self._load()

    def _load(self):
        self.meetings = Store.load_json(MEETINGS_PATH, [])

    def _save(self):
        Store.save_json(MEETINGS_PATH, self.meetings)

    def add_meeting(self, date: str, title: str, attendees: list[str],
                    raw_notes: str, meeting_id: str = "",
                    platform: str = "tencent_meeting") -> dict:
        """添加一条会议记录。"""
        now = datetime.now().isoformat()
        meeting = {
            "id": str(uuid.uuid4())[:8],
            "date": date,
            "time": datetime.now().strftime("%H:%M"),
            "title": title,
            "platform": platform,
            "meeting_id": meeting_id,
            "attendees": [a.strip() for a in attendees if a.strip()],
            "raw_notes": raw_notes.strip(),
            "summary": None,
            "topics": [],
            "created_at": now,
            "updated_at": now,
        }
        self.meetings.append(meeting)
        self._save()
        return meeting

    def summarize_meeting(self, meeting_id: str) -> Optional[dict]:
        """调用 LLM 生成会议结构化摘要。"""
        meeting = self._find_by_id(meeting_id)
        if not meeting:
            return None

        raw_notes = meeting.get("raw_notes", "")
        if not raw_notes.strip():
            return None

        try:
            client = APIClient(self.settings)
            prompt = self.SUMMARIZE_PROMPT.format(raw_notes=raw_notes)
            messages = [
                {"role": "system", "content": "你是iGEM团队助手，擅长整理会议纪要。只输出JSON，不要其他文字。"},
                {"role": "user", "content": prompt},
            ]
            result = client.chat_completion(messages, temperature=0.3, max_tokens=600, timeout=30)

            # 解析 JSON
            summary = self._parse_summary_json(result)
            if summary:
                meeting["summary"] = summary
                meeting["topics"] = summary.get("topics", [])
                meeting["updated_at"] = datetime.now().isoformat()
                self._save()
            return summary
        except Exception as e:
            return {"error": str(e)}

    def query_meetings(self, query: str, topk: int = 5) -> list[dict]:
        """按关键词/日期/参会人检索会议。"""
        if not self.meetings:
            return []

        query_lower = query.lower()
        scored = []

        for m in self.meetings:
            score = 0
            # 日期匹配
            if query_lower in m.get("date", ""):
                score += 10
            # 标题匹配
            if query_lower in m.get("title", "").lower():
                score += 8
            # 参会人匹配
            for a in m.get("attendees", []):
                if query_lower in a.lower():
                    score += 6
            # 原始笔记匹配
            if query_lower in m.get("raw_notes", "").lower():
                score += 5
            # 摘要匹配
            summary = m.get("summary") or {}
            for key in ("completed", "blockers", "next_steps", "key_decisions"):
                for item in summary.get(key, []):
                    if query_lower in item.lower():
                        score += 4
            # 主题匹配
            for t in m.get("topics", []):
                if query_lower in t.lower():
                    score += 3
            if score > 0:
                scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:topk]]

    def get_recent_meetings(self, n: int = 5) -> list[dict]:
        """返回最近N条会议记录。"""
        return self.meetings[-n:] if self.meetings else []

    def find_task_progress(self, keyword: str) -> list[dict]:
        """跨会议搜索特定任务/关键词的进展。"""
        results = []
        keyword_lower = keyword.lower()
        for m in reversed(self.meetings):
            summary = m.get("summary") or {}
            matched_items = []
            for key in ("completed", "blockers", "next_steps", "key_decisions"):
                for item in summary.get(key, []):
                    if keyword_lower in item.lower():
                        matched_items.append({"category": key, "content": item})
            if keyword_lower in m.get("raw_notes", "").lower():
                matched_items.append({"category": "raw_notes", "content": m["raw_notes"][:200]})
            if matched_items:
                results.append({
                    "date": m.get("date", ""),
                    "title": m.get("title", ""),
                    "matched": matched_items,
                })
        return results

    def delete_meeting(self, meeting_id: str) -> bool:
        """删除一条会议记录。"""
        before = len(self.meetings)
        self.meetings = [m for m in self.meetings if m.get("id") != meeting_id]
        if len(self.meetings) < before:
            self._save()
            return True
        return False

    def format_meeting_text(self, meeting: dict) -> str:
        """格式化单条会议记录为可读文本。"""
        lines = [f"📅 {meeting.get('title', '组会')} ({meeting.get('date', '')})"]
        attendees = meeting.get("attendees", [])
        if attendees:
            lines.append(f"👥 参会：{', '.join(attendees)}")
        summary = meeting.get("summary")
        if summary and not summary.get("error"):
            if summary.get("completed"):
                lines.append("✅ 已完成：" + "；".join(summary["completed"]))
            if summary.get("blockers"):
                lines.append("🚧 卡点：" + "；".join(summary["blockers"]))
            if summary.get("next_steps"):
                lines.append("➡️ 下一步：" + "；".join(summary["next_steps"]))
            if summary.get("key_decisions"):
                lines.append("🔑 决定：" + "；".join(summary["key_decisions"]))
        else:
            raw = meeting.get("raw_notes", "")
            if raw:
                lines.append(f"📝 笔记：{raw[:300]}")
        return "\n".join(lines)

    def _find_by_id(self, meeting_id: str) -> Optional[dict]:
        for m in self.meetings:
            if m.get("id") == meeting_id:
                return m
        return None

    @staticmethod
    def _parse_summary_json(text: str) -> Optional[dict]:
        """从 LLM 输出中解析 JSON 摘要。"""
        text = text.strip()
        # 尝试提取 JSON 块
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            return None
        try:
            data = json.loads(json_match.group())
            # 验证必要字段
            for key in ("completed", "blockers", "next_steps"):
                if key not in data:
                    data[key] = []
            if "key_decisions" not in data:
                data["key_decisions"] = []
            if "topics" not in data:
                data["topics"] = []
            # 确保列表类型
            for key in ("completed", "blockers", "next_steps", "key_decisions", "topics"):
                if not isinstance(data.get(key), list):
                    data[key] = []
            return data
        except json.JSONDecodeError:
            return None

    @staticmethod
    def parse_meeting_command(text: str) -> tuple:
        """解析 /meeting 或 /mt 命令。

        返回 (mode, payload):
            ("list", None) - 列出最近会议
            ("record", {"raw_notes": "..."}) - 记录新会议
            ("summarize", {"meeting_id": "..."}) - 生成摘要
            ("query", {"query": "..."}) - 查询
            ("progress", {"keyword": "..."}) - 追踪任务进展
            (None, None) - 不是会议命令
        """
        raw = (text or "").strip()
        if not raw:
            return None, None
        lower = raw.lower()

        # 匹配前缀
        prefixes = ("/meeting", "/mt", "组会", "会议")
        matched_prefix = None
        for p in prefixes:
            if lower.startswith(p):
                matched_prefix = p
                break
        if not matched_prefix:
            return None, None

        body = raw[len(matched_prefix):].lstrip(" ：:")

        # 子命令
        if not body or body in ("list", "列表", "历史"):
            return "list", None
        if body.startswith("记录") or body.startswith("record"):
            notes = body.lstrip("记录record ").lstrip("：:")
            return "record", {"raw_notes": notes}
        if body.startswith("总结") or body.startswith("summarize"):
            mid = body.lstrip("总结summarize ").lstrip("：:")
            return "summarize", {"meeting_id": mid.strip()}
        if body.startswith("进展") or body.startswith("progress"):
            keyword = body.lstrip("进展progress ").lstrip("：:")
            return "progress", {"keyword": keyword.strip()}
        # 默认当作查询
        if body:
            return "query", {"query": body}
        return "list", None
