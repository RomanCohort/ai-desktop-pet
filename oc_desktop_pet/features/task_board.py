"""团队任务看板 - 成员管理、任务追踪、截止提醒"""
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ..persistence.paths import TASK_BOARD_PATH
from ..persistence.store import Store


# ── 常量 ──

TASK_STATUSES = ["todo", "in_progress", "done", "blocked", "cancelled"]
PRIORITY_LEVELS = {"low": "低", "medium": "中", "high": "高", "urgent": "紧急"}


class TaskBoard:
    """iGEM团队任务看板。

    功能：
    - 团队成员管理（角色、技能、联系方式）
    - 任务创建/分配/更新
    - 看板视图（待办/进行中/已完成/卡住）
    - 截止日期追踪和提醒
    - 按角色/技能查找成员
    - 按关键词查找任务
    """

    def __init__(self, settings: dict, reply_queue=None):
        self.settings = settings
        self.reply_queue = reply_queue
        self.data: dict = {}
        self._load()

    def _load(self):
        self.data = Store.load_json(TASK_BOARD_PATH, {
            "members": [],
            "tasks": [],
        })

    def _save(self):
        Store.save_json(TASK_BOARD_PATH, self.data)

    # ── 成员管理 ──

    def add_member(self, name: str, role: str = "", skills: list[str] = None,
                   contact: str = "", member_id: str = "") -> Optional[dict]:
        """添加团队成员。"""
        # 检查重名
        for m in self.data["members"]:
            if m.get("name") == name:
                return None  # 已存在

        member = {
            "id": member_id or str(uuid.uuid4())[:8],
            "name": name.strip(),
            "role": role.strip(),
            "skills": skills or [],
            "contact": contact.strip(),
            "created_at": datetime.now().isoformat(),
        }
        self.data["members"].append(member)
        self._save()
        return member

    def update_member(self, member_id: str, updates: dict) -> Optional[dict]:
        """更新成员信息。"""
        member = self._find_member_by_id(member_id)
        if not member:
            return None
        for key in ("role", "skills", "contact", "name"):
            if key in updates:
                member[key] = updates[key]
        self._save()
        return member

    def remove_member(self, member_id: str) -> bool:
        """移除成员。"""
        before = len(self.data["members"])
        self.data["members"] = [m for m in self.data["members"] if m.get("id") != member_id]
        if len(self.data["members"]) < before:
            self._save()
            return True
        return False

    def find_by_role(self, role: str) -> list[dict]:
        """按角色查找成员。"""
        role_lower = role.lower()
        return [
            m for m in self.data["members"]
            if role_lower in m.get("role", "").lower()
        ]

    def find_by_skill(self, skill: str) -> list[dict]:
        """按技能查找成员。"""
        skill_lower = skill.lower()
        result = []
        for m in self.data["members"]:
            for s in m.get("skills", []):
                if skill_lower in s.lower():
                    result.append(m)
                    break
        return result

    def get_all_members(self) -> list[dict]:
        """获取所有成员。"""
        return list(self.data["members"])

    # ── 任务管理 ──

    def add_task(self, title: str, assignee_id: str = "", priority: str = "medium",
                 deadline: str = "", tags: list[str] = None,
                 description: str = "", task_id: str = "") -> Optional[dict]:
        """创建任务。"""
        task = {
            "id": task_id or str(uuid.uuid4())[:8],
            "title": title.strip(),
            "description": description.strip(),
            "assignee_id": assignee_id.strip(),
            "status": "todo",
            "priority": priority if priority in PRIORITY_LEVELS else "medium",
            "deadline": deadline.strip(),
            "tags": tags or [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "completed_at": None,
        }
        self.data["tasks"].append(task)
        self._save()
        return task

    def update_task(self, task_id: str, updates: dict) -> Optional[dict]:
        """更新任务。"""
        task = self._find_task_by_id(task_id)
        if not task:
            return None

        allowed_keys = {"title", "description", "assignee_id", "status", "priority", "deadline", "tags"}
        for key in allowed_keys:
            if key in updates:
                task[key] = updates[key]

        # 如果状态变为完成，记录完成时间
        if updates.get("status") == "done" and not task.get("completed_at"):
            task["completed_at"] = datetime.now().isoformat()

        task["updated_at"] = datetime.now().isoformat()
        self._save()
        return task

    def delete_task(self, task_id: str) -> bool:
        """删除任务。"""
        before = len(self.data["tasks"])
        self.data["tasks"] = [t for t in self.data["tasks"] if t.get("id") != task_id]
        if len(self.data["tasks"]) < before:
            self._save()
            return True
        return False

    def find_by_task(self, keyword: str) -> list[dict]:
        """按关键词搜索任务。"""
        keyword_lower = keyword.lower()
        result = []
        for t in self.data["tasks"]:
            if (keyword_lower in t.get("title", "").lower()
                    or keyword_lower in t.get("description", "").lower()
                    or keyword_lower in t.get("assignee_id", "").lower()):
                result.append(t)
                continue
            for tag in t.get("tags", []):
                if keyword_lower in tag.lower():
                    result.append(t)
                    break
        return result

    def get_tasks_by_status(self, status: str) -> list[dict]:
        """按状态获取任务。"""
        return [t for t in self.data["tasks"] if t.get("status") == status]

    def get_tasks_by_assignee(self, member_id: str) -> list[dict]:
        """获取某成员的所有任务。"""
        return [t for t in self.data["tasks"] if t.get("assignee_id") == member_id]

    def get_board_view(self) -> dict:
        """获取看板视图。"""
        board = {status: [] for status in TASK_STATUSES}
        for task in self.data["tasks"]:
            status = task.get("status", "todo")
            if status not in board:
                status = "todo"
            board[status].append(task)
        return board

    # ── 截止日期追踪 ──

    def check_deadlines(self, hours_before: int = 24) -> list[dict]:
        """检查即将到期的任务。返回需要提醒的任务列表。"""
        now = datetime.now()
        threshold = now + timedelta(hours=hours_before)
        upcoming = []

        for task in self.data["tasks"]:
            if task.get("status") in ("done", "cancelled"):
                continue
            deadline_str = task.get("deadline", "")
            if not deadline_str:
                continue

            try:
                deadline = datetime.fromisoformat(deadline_str)
            except (ValueError, TypeError):
                # 尝试简单日期格式
                try:
                    deadline = datetime.strptime(deadline_str, "%Y-%m-%d")
                except (ValueError, TypeError):
                    continue

            if now <= deadline <= threshold:
                # 计算剩余时间
                remaining = deadline - now
                hours_left = remaining.total_seconds() / 3600
                upcoming.append({
                    **task,
                    "hours_remaining": round(hours_left, 1),
                    "is_overdue": False,
                })
            elif deadline < now:
                upcoming.append({
                    **task,
                    "hours_remaining": 0,
                    "is_overdue": True,
                })

        return upcoming

    # ── 会议联动 ──

    def suggest_tasks_from_meeting(self, next_steps: list[str]) -> list[dict]:
        """从会议摘要的 next_steps 建议创建任务。返回建议列表（不自动创建）。"""
        suggestions = []
        for step in next_steps:
            if not step.strip():
                continue
            suggestions.append({
                "title": step.strip(),
                "status": "suggested",
                "priority": "medium",
                "source": "meeting",
            })
        return suggestions

    # ── 格式化 ──

    def format_task_text(self, task: dict) -> str:
        """格式化单条任务为可读文本。"""
        priority_label = PRIORITY_LEVELS.get(task.get("priority", "medium"), "中")
        status_map = {
            "todo": "📋 待办", "in_progress": "🔨 进行中",
            "done": "✅ 已完成", "blocked": "🚧 卡住",
            "cancelled": "❌ 已取消",
        }
        status_label = status_map.get(task.get("status", "todo"), "📋 待办")

        # 查找分配人
        assignee_name = "未分配"
        if task.get("assignee_id"):
            member = self._find_member_by_id(task["assignee_id"])
            if member:
                assignee_name = member.get("name", "未知")

        lines = [
            f"{status_label} {task.get('title', '未命名')}",
            f"  优先级：{priority_label} | 分配：{assignee_name}",
        ]
        if task.get("deadline"):
            lines.append(f"  截止：{task['deadline']}")
        if task.get("tags"):
            lines.append(f"  标签：{', '.join(task['tags'])}")
        if task.get("description"):
            lines.append(f"  说明：{task['description'][:100]}")
        return "\n".join(lines)

    def format_member_text(self, member: dict) -> str:
        """格式化单条成员信息。"""
        lines = [f"👤 {member.get('name', '未知')}"]
        if member.get("role"):
            lines.append(f"  角色：{member['role']}")
        if member.get("skills"):
            lines.append(f"  技能：{', '.join(member['skills'])}")
        if member.get("contact"):
            lines.append(f"  联系：{member['contact']}")
        return "\n".join(lines)

    # ── 内部查找 ──

    def _find_member_by_id(self, member_id: str) -> Optional[dict]:
        for m in self.data["members"]:
            if m.get("id") == member_id:
                return m
        return None

    def _find_task_by_id(self, task_id: str) -> Optional[dict]:
        for t in self.data["tasks"]:
            if t.get("id") == task_id:
                return t
        return None

    # ── 命令解析 ──

    @staticmethod
    def parse_task_command(text: str) -> tuple:
        """解析 /task 命令。

        返回 (mode, payload):
            ("board", None) - 查看看板
            ("add", {"title": "...", ...}) - 添加任务
            ("update", {"task_id": "...", "updates": {}}) - 更新任务
            ("find", {"keyword": "..."}) - 搜索任务
            (None, None) - 不是task命令
        """
        raw = (text or "").strip()
        if not raw:
            return None, None
        lower = raw.lower()

        prefixes = ("/task", "任务", "任务看板")
        matched = None
        for p in prefixes:
            if lower.startswith(p):
                matched = p
                break
        if not matched:
            return None, None

        body = raw[len(matched):].lstrip(" ：:")

        if not body or body in ("board", "看板", "列表"):
            return "board", None
        if body.startswith("添加") or body.startswith("add"):
            title = body.lstrip("添加add ").lstrip("：:").strip()
            return "add", {"title": title}
        if body.startswith("完成") or body.startswith("done"):
            task_id = body.lstrip("完成done ").lstrip("：:").strip()
            return "update", {"task_id": task_id, "updates": {"status": "done"}}
        if body.startswith("进度") or body.startswith("progress"):
            rest = body.lstrip("进度progress ").lstrip("：:").strip()
            parts = rest.split(maxsplit=1)
            task_id = parts[0] if parts else ""
            status = parts[1] if len(parts) > 1 else "in_progress"
            return "update", {"task_id": task_id, "updates": {"status": status}}

        # 默认当作搜索
        return "find", {"keyword": body}

    @staticmethod
    def parse_team_command(text: str) -> tuple:
        """解析 /team 命令。

        返回 (mode, payload):
            ("list", None) - 列出所有成员
            ("add", {"name": "...", ...}) - 添加成员
            ("find", {"keyword": "..."}) - 按角色/技能查找
            (None, None) - 不是team命令
        """
        raw = (text or "").strip()
        if not raw:
            return None, None
        lower = raw.lower()

        prefixes = ("/team", "团队", "成员")
        matched = None
        for p in prefixes:
            if lower.startswith(p):
                matched = p
                break
        if not matched:
            return None, None

        body = raw[len(matched):].lstrip(" ：:")

        if not body or body in ("list", "列表"):
            return "list", None
        if body.startswith("添加") or body.startswith("add"):
            # 格式: /team 添加 张三 湿实验 质粒构建,PCR
            parts = body.lstrip("添加add ").lstrip("：:").split()
            name = parts[0] if parts else ""
            role = parts[1] if len(parts) > 1 else ""
            skills = parts[2].split(",") if len(parts) > 2 else []
            return "add", {"name": name, "role": role, "skills": skills}

        # 默认当作查找
        return "find", {"keyword": body}
