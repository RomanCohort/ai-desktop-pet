"""长期记忆系统 - 向量化存储和检索对话记忆"""
import json
import math
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

from ..utils.logger import get_logger

_logger = get_logger(__name__)

try:
    import numpy as np
except ImportError:
    np = None
except Exception as e:
    _logger.warning("numpy 导入异常: %s", e)
    np = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None
except Exception as e:
    _logger.warning("sentence_transformers 导入异常: %s", e)
    SentenceTransformer = None

try:
    import faiss
    _HAS_FAISS = True
except ImportError:
    _HAS_FAISS = False
except Exception as e:
    _logger.warning("faiss 导入异常: %s", e)
    _HAS_FAISS = False


class MemoryStore:
    """长期记忆管理器，支持真正的嵌入模型检索（优先）或回退到字符哈希。"""

    # 不存入记忆的自动标记
    AUTO_MARKERS = (
        "[屏幕吐槽]", "[音频吐槽]", "[音频吐槽汇总]", "[听歌]",
        "[实时听写]", "[实时点评]", "[最终]",
    )

    # 噪声过滤词
    NOISE_TOKENS = ("测试", "问过", "重复提问", "长期记忆", "屏幕角落", "后空翻")

    def __init__(self, memory_path: Path, settings: dict):
        self.memory_path = memory_path
        self.settings = settings
        self.db: list[dict] = []
        self._embedding_model = None
        self._faiss_index = None
        self._embeddings = None
        self._dim = None
        self._lock = threading.Lock()

    def load(self) -> None:
        """从文件加载记忆数据库。"""
        if self.memory_path.exists():
            try:
                with self.memory_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    with self._lock:
                        self.db = data
                else:
                    with self._lock:
                        self.db = []
            except json.JSONDecodeError as e:
                _logger.warning("记忆文件 JSON 解析失败 %s: %s", self.memory_path, e)
                with self._lock:
                    self.db = []
            except Exception as e:
                _logger.error("读取记忆文件失败 %s: %s", self.memory_path, e)
                with self._lock:
                    self.db = []
        self._prune_noise()

    def save(self) -> None:
        """原子写入保存记忆到文件。"""
        try:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                suffix=".tmp", prefix="memory_", dir=str(self.memory_path.parent)
            )
            try:
                with self._lock:
                    data_copy = list(self.db)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data_copy, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, str(self.memory_path))
                _logger.debug("已保存记忆文件 %s (%d 条)", self.memory_path, len(data_copy))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            _logger.error("保存记忆文件失败 %s: %s", self.memory_path, e)

    def remember(self, role: str, content: str) -> None:
        """将对话存入记忆（自动过滤噪声和自动标记内容）。"""
        text = str(content or "").strip()
        if not text:
            return
        if any(marker in text for marker in self.AUTO_MARKERS):
            return

        item = {
            "role": role,
            "content": text[:600],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "vector": self._to_vector(text),  # 兼容旧格式
            "tags": self._extract_topic_tags(text),
            "kind": "dialogue",
        }
        with self._lock:
            self.db.append(item)
            self.db = self.db[-400:]
        self.save()

    def recall(self, query: str, topk: int = 3) -> list[dict]:
        """检索与查询最相关的记忆。优先使用嵌入模型，回退到字符哈希。"""
        with self._lock:
            db_snapshot = list(self.db)

        if not db_snapshot:
            return []

        # 尝试使用真正的嵌入模型检索
        if self._try_init_embedding_model():
            return self._recall_by_embedding(query, topk, db_snapshot)

        # 回退：字符哈希向量检索
        return self._recall_by_hash(query, topk, db_snapshot)

    def _recall_by_embedding(self, query: str, topk: int, db_snapshot: list[dict]) -> list[dict]:
        """使用 sentence-transformers 嵌入模型检索。"""
        try:
            qemb = self._embedding_model.encode([query], convert_to_numpy=True, show_progress_bar=False)
            if _HAS_FAISS and self._faiss_index is not None:
                D, I = self._faiss_index.search(qemb.astype("float32"), topk)
                results = []
                for idx, dist in zip(I.tolist()[0], D.tolist()[0]):
                    if 0 <= idx < len(db_snapshot):
                        results.append(db_snapshot[idx])
                return results
            elif np is not None and self._embeddings is not None:
                dists = np.linalg.norm(self._embeddings - qemb[0], axis=1)
                idxs = np.argsort(dists)[:topk]
                return [db_snapshot[i] for i in idxs if i < len(db_snapshot)]
        except Exception as e:
            _logger.debug("嵌入检索失败，回退到哈希检索: %s", e)
        return self._recall_by_hash(query, topk, db_snapshot)

    def _recall_by_hash(self, query: str, topk: int, db_snapshot: list[dict]) -> list[dict]:
        """使用字符哈希向量检索（兼容旧数据）。"""
        query_text = str(query or "").strip().lower()
        qv = self._to_vector(query_text)
        scored = []
        for item in db_snapshot:
            content = str(item.get("content", "")).lower()
            if any(token in content for token in self.NOISE_TOKENS):
                continue
            sim = self._cosine(qv, item.get("vector", []))
            if sim > 0.28:
                scored.append((sim, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        unique = []
        seen = set()
        for _sim, item in scored:
            key = re.sub(r"\s+", "", str(item.get("content", "")).lower())[:120]
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= topk:
                break
        return unique

    def _try_init_embedding_model(self) -> bool:
        """尝试初始化嵌入模型，成功返回 True。"""
        if self._embedding_model is not None:
            return True
        if SentenceTransformer is None:
            return False
        try:
            self._embedding_model = SentenceTransformer("paraphrase-MiniLM-L3-v2")
            self._rebuild_embedding_index()
            return True
        except Exception as e:
            _logger.warning("嵌入模型初始化失败: %s", e)
            self._embedding_model = None
            return False

    def _rebuild_embedding_index(self) -> None:
        """用嵌入模型重建向量索引。"""
        if self._embedding_model is None or np is None or not self.db:
            return
        try:
            texts = [item.get("content", "") for item in self.db if item.get("content")]
            if not texts:
                return
            self._embeddings = self._embedding_model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
            self._dim = self._embeddings.shape[1]
            if _HAS_FAISS:
                self._faiss_index = faiss.IndexFlatL2(self._dim)
                self._faiss_index.add(self._embeddings.astype("float32"))
        except Exception as e:
            _logger.error("重建嵌入索引失败: %s", e)
            self._embeddings = None
            self._faiss_index = None

    def _prune_noise(self) -> None:
        """过滤记忆中的噪声条目。"""
        if not isinstance(self.db, list) or not self.db:
            return
        cleaned = []
        for item in self.db:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).lower()
            if not content.strip():
                continue
            if any(token in content for token in self.NOISE_TOKENS):
                continue
            cleaned.append(item)
        if cleaned:
            self.db = cleaned[-400:]

    # ── 兼容旧向量格式 ──
    @staticmethod
    def _to_vector(text: str, dim: int = 64) -> list[float]:
        """字符哈希伪向量（兼容旧数据，新检索优先用嵌入模型）。"""
        vec = [0.0] * dim
        normalized = re.sub(r"\s+", "", text.lower())
        if not normalized:
            return vec
        for i, ch in enumerate(normalized):
            idx = (ord(ch) + i * 13) % dim
            vec[idx] += 1.0
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / n for x in vec]

    @staticmethod
    def _cosine(a: list, b: list) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        return float(sum(x * y for x, y in zip(a, b)))

    @staticmethod
    def _extract_topic_tags(text: str) -> list[str]:
        """从文本中提取话题标签。"""
        content = str(text or "").lower()
        if not content:
            return []
        keyword_map = {
            "代码": ["代码", "python", "bug", "报错", "函数", "脚本", "debug", "api", "程序"],
            "学习": ["学习", "复习", "作业", "考试", "课程", "背书", "题"],
            "工作": ["工作", "项目", "会议", "汇报", "进度", "交付"],
            "游戏": ["游戏", "开黑", "副本", "上分", "steam", "lol", "原神"],
            "生活": ["吃饭", "睡觉", "出门", "家务", "计划", "日常"],
            "情绪": ["开心", "难过", "焦虑", "压力", "烦", "生气", "崩溃"],
            "健康": ["休息", "喝水", "运动", "眼睛", "肩颈", "头疼", "疲劳"],
            "创作": ["写作", "画图", "剪辑", "音乐", "灵感", "创意"],
        }
        tags = []
        for tag, words in keyword_map.items():
            if any(w in content for w in words):
                tags.append(tag)
        return tags[:3]

    def build_layered_memory_block(self, query: str, topk: int = 4) -> str:
        """构建分层记忆注入块（事实/偏好/关系/近期）。"""
        recalls = self.recall(query, topk=max(2, topk * 2))
        if not recalls:
            return ""

        cfg = self.settings.get("conversation_engine", {})
        recent_days = int(cfg.get("memory_recent_days", 14))
        cutoff = datetime.now() - timedelta(days=recent_days)

        pref_words = ("喜欢", "讨厌", "偏好", "习惯", "想要", "不想")
        relation_words = ("我们", "你和我", "陪", "约定", "纪念", "关系")

        fact_like = []
        preference_like = []
        relation_like = []
        recent_like = []

        for item in recalls:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            ts = str(item.get("time", "")).strip()
            is_recent = False
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                is_recent = dt >= cutoff
            except (ValueError, TypeError) as e:
                _logger.debug("日期解析失败 '%s': %s", ts, e)
                is_recent = True

            if any(w in content for w in pref_words):
                preference_like.append(content)
            elif any(w in content for w in relation_words):
                relation_like.append(content)
            elif is_recent:
                recent_like.append(content)
            else:
                fact_like.append(content)

        blocks = []
        if preference_like:
            blocks.append("用户偏好记忆:\n" + "\n".join(f"- {c}" for c in preference_like[:3]))
        if relation_like:
            blocks.append("关系记忆:\n" + "\n".join(f"- {c}" for c in relation_like[:2]))
        if fact_like:
            blocks.append("事实记忆:\n" + "\n".join(f"- {c}" for c in fact_like[:3]))
        if recent_like:
            blocks.append("近期记忆:\n" + "\n".join(f"- {c}" for c in recent_like[:3]))

        if not blocks:
            return ""
        return "\n\n[长期记忆检索 - 自动注入]\n" + "\n".join(blocks)
