"""论文助手桥接器 - 连接桌宠和论文检索/问答工具"""
import json
import sys
import threading
from pathlib import Path

from ..persistence.paths import BASE_DIR
from ..utils.logger import get_logger

_logger = get_logger(__name__)


class PaperAssistantBridge:
    """论文搜索整理辅助工具的桥接器。"""

    def __init__(self, settings: dict):
        self.settings = settings
        self._retriever = None
        self._index_cache = None
        self._lock = threading.Lock()

    def _resolve_paths(self):
        cfg = self.settings.get("paper_tool", {})
        base_dir = str(cfg.get("base_dir", "")).strip()
        if not base_dir:
            base_dir = str(BASE_DIR / "论文搜索整理辅助工具" / "mad-professor-public-main" / "mad-professor-public-main")
        output_dir = str(cfg.get("output_dir", "")).strip()
        if not output_dir:
            output_dir = str(Path(base_dir) / "output")
        timeout = int(cfg.get("timeout_seconds", 90) or 90)
        use_llm = bool(cfg.get("use_llm", True))
        return base_dir, output_dir, timeout, use_llm

    def _load_index(self) -> list:
        base_dir, output_dir, _, _ = self._resolve_paths()
        index_path = Path(output_dir) / "papers_index.json"
        if not index_path.exists():
            return []
        try:
            with index_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._index_cache = data
                return data
        except json.JSONDecodeError as e:
            _logger.error("论文索引 JSON 解析失败: %s", e)
            return []
        except Exception as e:
            _logger.error("加载论文索引失败: %s", e)
        return []

    def list_papers(self) -> list[str]:
        rows = self._load_index()
        if not rows:
            return ["未找到论文索引，请先在论文工具里导入PDF生成索引。"]
        lines = []
        for item in rows[:80]:
            pid = str(item.get("id", "")).strip()
            title = str(item.get("translated_title") or item.get("title") or "").strip()
            if pid or title:
                lines.append(f"{pid} | {title}".strip())
        if not lines:
            return ["论文索引为空。"]
        return ["论文列表："] + lines

    def ask(self, query: str, paper_id: str = None) -> str:
        query = str(query or "").strip()
        if not query:
            return "请输入具体问题。"

        cfg = self.settings.get("paper_tool", {})
        default_paper = str(cfg.get("default_paper_id", "")).strip()
        rows = self._index_cache or self._load_index()
        if not rows:
            return "未找到论文索引，请先在论文工具中导入论文并生成索引。"

        target_id = str(paper_id or default_paper).strip()
        if not target_id:
            if len(rows) == 1:
                target_id = str(rows[0].get("id", "")).strip()
            else:
                return "请先指定论文ID，例如：/paper id=xxxxx 你的问题，或先用 /paper list 查看。"

        if not self._ensure_retriever(rows):
            return "论文检索器初始化失败，请检查依赖是否安装。"

        base_dir, output_dir, timeout, use_llm = self._resolve_paths()
        try:
            context, _scroll = self._retriever.retrieve_with_context(query=query, paper_id=target_id, top_k=5)
        except Exception as e:
            return f"检索失败：{e}"

        if not context:
            return "未检索到相关段落，请换个关键词试试。"

        if not use_llm:
            return context

        try:
            if base_dir not in sys.path:
                sys.path.insert(0, base_dir)
            from AI_professor_chat import AIProfessorChat

            chat = AIProfessorChat()
            chat.retriever = self._retriever
            rag_tree = self._retriever.load_rag_tree(target_id)
            if rag_tree:
                chat.set_paper_context(target_id, rag_tree)

            answer_parts = []
            for sentence, _emotion, _scroll in chat.process_query_stream(query=query):
                answer_parts.append(sentence)
            answer = "".join(answer_parts).strip()
            return answer or context
        except Exception as e:
            return f"{context}\n\n(论文助手LLM调用失败：{e})"

    def _ensure_retriever(self, rows) -> bool:
        with self._lock:
            base_dir, output_dir, _timeout, _use_llm = self._resolve_paths()
            if self._retriever and getattr(self._retriever, "base_path", None) == str(output_dir):
                return True
            try:
                if base_dir not in sys.path:
                    sys.path.insert(0, base_dir)
                from rag_retriever import RagRetriever
                retriever = RagRetriever(base_path=None)
                retriever.base_path = str(output_dir)
                retriever.paper_vector_paths = {}
                for item in rows:
                    pid = str(item.get("id", "")).strip()
                    vpath = item.get("paths", {}).get("rag_vector_store")
                    if pid and vpath:
                        retriever.paper_vector_paths[pid] = str(Path(output_dir) / vpath)
                self._retriever = retriever
                return True
            except ImportError as e:
                _logger.error("论文检索器导入失败: %s", e)
                return False
            except Exception as e:
                _logger.error("论文检索器初始化失败: %s", e)

    @staticmethod
    def parse_paper_command(text: str) -> tuple:
        """解析 /paper 指令，返回 (mode, payload) 或 (None, None)。"""
        raw = (text or "").strip()
        if not raw:
            return None, None
        lower = raw.lower()
        if lower.startswith("/paper") or raw.startswith("论文"):
            body = raw
            for prefix in ("/paper", "论文", "论文:", "论文："):
                if body.startswith(prefix):
                    body = body[len(prefix):]
                    break
            body = body.strip()
            if not body or body in ("list", "列表"):
                return "list", None
            if body.startswith("id="):
                parts = body.split(None, 1)
                pid = parts[0][3:].strip()
                q = parts[1].strip() if len(parts) > 1 else ""
                return "query", {"paper_id": pid, "query": q}
            if "::" in body:
                pid, q = body.split("::", 1)
                return "query", {"paper_id": pid.strip(), "query": q.strip()}
            return "query", {"paper_id": None, "query": body}
        return None, None
