"""技术文档中心 - 分类索引、语义搜索、自动监视"""
import hashlib
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..persistence.paths import DOC_HUB_PATH
from ..persistence.store import Store
from ..chat.api_client import APIClient


# ── 文档分类定义 ──

DOC_CATEGORIES = {
    "wetlab_protocols": {"label": "湿实验Protocol", "icon": "🧪"},
    "drylab_tools": {"label": "干实验工具", "icon": "💻"},
    "safety_rules": {"label": "安全规范", "icon": "⚠️"},
    "competition_rules": {"label": "比赛规则", "icon": "📋"},
    "wiki": {"label": "Wiki", "icon": "🌐"},
    "meeting_records": {"label": "会议记录", "icon": "📝"},
    "tutorials": {"label": "教程", "icon": "📖"},
    "other": {"label": "其他", "icon": "📁"},
}

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".py", ".fasta", ".fa", ".fastq", ".gb", ".gff", ".sam", ".bam", ".vcf"}


class DocHub:
    """iGEM团队技术文档中心。

    功能：
    - 分类索引文档（湿实验/干实验/安全/规则/Wiki/会议/教程）
    - 关键词 + LLM语义搜索
    - 监视文件夹自动索引新文件
    - LLM自动生成文档摘要
    - 按分类浏览
    """

    SUMMARIZE_DOC_PROMPT = """请用2-3句话简要概括以下文档的核心内容。
目标读者是iGEM队员，需要快速了解文档是否与当前任务相关。

文档标题：{title}
文档内容（前2000字）：
{content}

输出格式：一句话概括 + 关键词标签（用逗号分隔）"""

    def __init__(self, settings: dict, reply_queue=None):
        self.settings = settings
        self.reply_queue = reply_queue
        self.data: dict = {}
        self._load()

    def _load(self):
        self.data = Store.load_json(DOC_HUB_PATH, {
            "watch_folders": [],
            "documents": [],
            "last_scan_at": "",
        })

    def _save(self):
        Store.save_json(DOC_HUB_PATH, self.data)

    # ── 文档管理 ──

    def add_document(self, path: str, category: str = "other",
                     tags: list[str] = None, title: str = "") -> Optional[dict]:
        """添加一个文档到索引。"""
        if not os.path.isfile(path):
            return None

        # 检查是否已索引
        content_hash = self._hash_file(path)
        for doc in self.data["documents"]:
            if doc.get("path") == path:
                # 已存在，更新
                if doc.get("content_hash") != content_hash:
                    doc["content_hash"] = content_hash
                    doc["updated_at"] = datetime.now().isoformat()
                    doc["summary"] = None  # 标记需要重新摘要
                    self._save()
                return doc

        if not title:
            title = os.path.splitext(os.path.basename(path))[0]

        doc = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "path": path,
            "category": category,
            "tags": tags or [],
            "content_hash": content_hash,
            "summary": None,
            "size_bytes": os.path.getsize(path),
            "extension": os.path.splitext(path)[1].lower(),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self.data["documents"].append(doc)
        self._save()
        return doc

    def remove_document(self, doc_id: str) -> bool:
        """删除一个文档索引。"""
        before = len(self.data["documents"])
        self.data["documents"] = [d for d in self.data["documents"] if d.get("id") != doc_id]
        if len(self.data["documents"]) < before:
            self._save()
            return True
        return False

    def summarize_document(self, doc_id: str) -> Optional[str]:
        """用 LLM 生成文档摘要。"""
        doc = self._find_by_id(doc_id)
        if not doc:
            return None

        content = self._read_file_content(doc["path"], max_chars=2000)
        if not content.strip():
            return "文档内容为空或无法读取"

        try:
            client = APIClient(self.settings)
            prompt = self.SUMMARIZE_DOC_PROMPT.format(
                title=doc.get("title", ""),
                content=content,
            )
            messages = [
                {"role": "system", "content": "你是iGEM团队文档助手，擅长快速概括技术文档。"},
                {"role": "user", "content": prompt},
            ]
            summary = client.chat_completion(messages, temperature=0.3, max_tokens=200, timeout=20)

            doc["summary"] = summary.strip()
            # 尝试从摘要中提取标签
            if "关键词" in summary or "标签" in summary:
                tag_match = re.search(r'(?:关键词|标签)[：:]\s*(.+)', summary)
                if tag_match:
                    extracted = [t.strip() for t in tag_match.group(1).split(",") if t.strip()]
                    if extracted:
                        doc["tags"] = list(set(doc.get("tags", []) + extracted))[:10]
            doc["updated_at"] = datetime.now().isoformat()
            self._save()
            return summary.strip()
        except Exception as e:
            return f"摘要生成失败：{e}"

    # ── 搜索 ──

    def search(self, query: str, category: str = "", topk: int = 10) -> list[dict]:
        """搜索文档：关键词匹配 + 标签匹配 + 标题匹配。"""
        query_lower = query.lower()
        query_terms = set(re.findall(r'\w+', query_lower))
        scored = []

        for doc in self.data["documents"]:
            if category and doc.get("category") != category:
                continue

            score = 0
            # 标题匹配（权重最高）
            title_lower = doc.get("title", "").lower()
            if query_lower in title_lower:
                score += 15
            for term in query_terms:
                if term in title_lower:
                    score += 8

            # 标签匹配
            for tag in doc.get("tags", []):
                tag_lower = tag.lower()
                if query_lower in tag_lower:
                    score += 10
                for term in query_terms:
                    if term in tag_lower:
                        score += 5

            # 摘要匹配
            summary_lower = (doc.get("summary") or "").lower()
            if query_lower in summary_lower:
                score += 6
            for term in query_terms:
                if term in summary_lower:
                    score += 3

            # 文件名匹配
            filename_lower = os.path.basename(doc.get("path", "")).lower()
            if query_lower in filename_lower:
                score += 4

            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:topk]]

    def get_by_category(self, category: str) -> list[dict]:
        """按分类获取文档列表。"""
        return [d for d in self.data["documents"] if d.get("category") == category]

    def get_all_categories_with_count(self) -> list[dict]:
        """获取所有分类及其文档数量。"""
        counts = {}
        for doc in self.data["documents"]:
            cat = doc.get("category", "other")
            counts[cat] = counts.get(cat, 0) + 1

        result = []
        for cat_key, cat_info in DOC_CATEGORIES.items():
            result.append({
                "key": cat_key,
                "label": cat_info["label"],
                "icon": cat_info["icon"],
                "count": counts.get(cat_key, 0),
            })
        return result

    def preview_document(self, doc_id: str, max_chars: int = 500) -> Optional[dict]:
        """预览文档内容。"""
        doc = self._find_by_id(doc_id)
        if not doc:
            return None

        content = self._read_file_content(doc["path"], max_chars=max_chars)
        return {
            **doc,
            "preview": content,
        }

    # ── 监视文件夹 ──

    def add_watch_folder(self, folder_path: str) -> bool:
        """添加监视文件夹。"""
        folder_path = os.path.normpath(folder_path)
        for wf in self.data.get("watch_folders", []):
            if os.path.normpath(wf) == folder_path:
                return False  # 已存在
        self.data.setdefault("watch_folders", []).append(folder_path)
        self._save()
        return True

    def remove_watch_folder(self, folder_path: str) -> bool:
        """移除监视文件夹。"""
        folder_path = os.path.normpath(folder_path)
        before = len(self.data.get("watch_folders", []))
        self.data["watch_folders"] = [
            wf for wf in self.data.get("watch_folders", [])
            if os.path.normpath(wf) != folder_path
        ]
        if len(self.data["watch_folders"]) < before:
            self._save()
            return True
        return False

    def scan_watch_folders(self) -> dict:
        """扫描监视文件夹，索引新文件。返回扫描结果。"""
        stats = {"scanned": 0, "added": 0, "updated": 0, "errors": []}

        for folder in self.data.get("watch_folders", []):
            if not os.path.isdir(folder):
                stats["errors"].append(f"文件夹不存在: {folder}")
                continue

            for root, _dirs, files in os.walk(folder):
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in SUPPORTED_EXTENSIONS:
                        continue

                    fpath = os.path.join(root, fname)
                    stats["scanned"] += 1

                    # 检查是否已索引
                    existing = self._find_by_path(fpath)
                    if existing:
                        # 检查是否更新
                        new_hash = self._hash_file(fpath)
                        if existing.get("content_hash") != new_hash:
                            existing["content_hash"] = new_hash
                            existing["summary"] = None
                            existing["updated_at"] = datetime.now().isoformat()
                            stats["updated"] += 1
                    else:
                        # 自动推断分类
                        category = self._infer_category(fpath, folder)
                        result = self.add_document(fpath, category=category)
                        if result:
                            stats["added"] += 1

        self.data["last_scan_at"] = datetime.now().isoformat()
        self._save()
        return stats

    # ── 工具方法 ──

    @staticmethod
    def _infer_category(filepath: str, root_folder: str) -> str:
        """根据路径和文件名推断分类。"""
        rel_path = os.path.relpath(filepath, root_folder).lower()
        parts = rel_path.replace("\\", "/").split("/")

        # 按文件夹名推断
        folder_hints = {
            "wetlab": "wetlab_protocols", "protocol": "wetlab_protocols",
            "drylab": "drylab_tools", "tool": "drylab_tools", "scripts": "drylab_tools",
            "safety": "safety_rules", "safe": "safety_rules",
            "rules": "competition_rules", "igem_rules": "competition_rules",
            "wiki": "wiki",
            "meeting": "meeting_records", "meetings": "meeting_records",
            "tutorial": "tutorials", "guide": "tutorials", "learn": "tutorials",
        }

        for part in parts:
            for hint, cat in folder_hints.items():
                if hint in part:
                    return cat

        # 按扩展名推断
        ext = os.path.splitext(filepath)[1].lower()
        ext_hints = {
            ".fasta": "drylab_tools", ".fa": "drylab_tools",
            ".fastq": "drylab_tools", ".gb": "drylab_tools",
            ".gff": "drylab_tools", ".sam": "drylab_tools",
            ".bam": "drylab_tools", ".vcf": "drylab_tools",
            ".py": "drylab_tools",
        }
        if ext in ext_hints:
            return ext_hints[ext]

        return "other"

    @staticmethod
    def _hash_file(filepath: str) -> str:
        """计算文件内容的MD5哈希。"""
        h = hashlib.md5()
        try:
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
        except (OSError, PermissionError):
            return ""
        return h.hexdigest()

    @staticmethod
    def _read_file_content(filepath: str, max_chars: int = 2000) -> str:
        """读取文件内容（文本文件）。"""
        try:
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".pdf":
                return "[PDF文件，请使用专业阅读器打开]"
            if ext in (".docx", ".doc"):
                return "[Word文件，请使用Word打开]"

            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read(max_chars)
        except (OSError, PermissionError):
            return ""

    def _find_by_id(self, doc_id: str) -> Optional[dict]:
        for doc in self.data["documents"]:
            if doc.get("id") == doc_id:
                return doc
        return None

    def _find_by_path(self, filepath: str) -> Optional[dict]:
        normpath = os.path.normpath(filepath)
        for doc in self.data["documents"]:
            if os.path.normpath(doc.get("path", "")) == normpath:
                return doc
        return None

    # ── 命令解析 ──

    @staticmethod
    def parse_doc_command(text: str) -> tuple:
        """解析 /doc 命令。

        返回 (mode, payload):
            ("list", None) - 列出所有分类
            ("search", {"query": "..."}) - 搜索
            ("add", {"path": "...", "category": "..."}) - 添加文档
            ("watch", {"path": "..."}) - 添加监视文件夹
            ("scan", None) - 扫描监视文件夹
            (None, None) - 不是doc命令
        """
        raw = (text or "").strip()
        if not raw:
            return None, None
        lower = raw.lower()

        prefixes = ("/doc", "文档", "文档中心")
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
            parts = body.lstrip("添加add ").lstrip("：:").split(maxsplit=1)
            path = parts[0].strip() if parts else ""
            category = parts[1].strip() if len(parts) > 1 else "other"
            return "add", {"path": path, "category": category}
        if body.startswith("监视") or body.startswith("watch"):
            path = body.lstrip("监视watch ").lstrip("：:").strip()
            return "watch", {"path": path}
        if body in ("扫描", "scan"):
            return "scan", None

        # 默认当作搜索
        return "search", {"query": body}

    def format_doc_text(self, doc: dict) -> str:
        """格式化单条文档记录为可读文本。"""
        cat_info = DOC_CATEGORIES.get(doc.get("category", "other"), {"icon": "📁", "label": "其他"})
        lines = [f"{cat_info['icon']} {doc.get('title', '未命名')}"]
        lines.append(f"  分类：{cat_info['label']}")
        tags = doc.get("tags", [])
        if tags:
            lines.append(f"  标签：{', '.join(tags)}")
        if doc.get("summary"):
            lines.append(f"  摘要：{doc['summary'][:100]}")
        lines.append(f"  路径：{doc.get('path', '')}")
        return "\n".join(lines)
