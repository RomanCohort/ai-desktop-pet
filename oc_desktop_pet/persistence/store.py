"""JSON 持久化存储 - 统一的读写和脏标记管理"""
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..utils.logger import get_logger

_logger = get_logger(__name__)


class Store:
    """带脏标记的 JSON 存储管理器，避免频繁写盘。

    线程安全说明：
        - save_json() / load_json() 使用原子文件操作，线程安全
        - mark_dirty() / persist_if_dirty() / force_persist() 非线程安全，
          当前仅从主线程调用
    """

    def __init__(self):
        self._dirty: set[str] = set()
        self._last_persist_ts: float = 0.0
        self._persist_interval: float = 30.0  # 最少间隔30秒

    # --- 基础读写 ---
    @staticmethod
    def load_json(path: Path, default_value: Any) -> Any:
        if not path.exists():
            return default_value
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except json.JSONDecodeError as e:
            _logger.warning("JSON 解析失败 %s: %s", path, e)
            return default_value
        except Exception as e:
            _logger.error("读取文件失败 %s: %s", path, e)
            return default_value

    @staticmethod
    def save_json(path: Path, data: Any) -> None:
        """原子写入 JSON 文件。

        使用 tempfile + os.replace 实现原子写入，避免写入中断导致文件损坏。
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                suffix=".tmp", prefix="oc_", dir=str(path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, str(path))
                _logger.debug("已保存 %s", path)
            except Exception:
                # 写入失败，清理临时文件
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            _logger.error("保存文件失败 %s: %s", path, e)

    # --- 脏标记 ---
    def mark_dirty(self, *paths: str) -> None:
        for p in paths:
            self._dirty.add(str(p))

    def is_dirty(self, path: str) -> bool:
        return str(path) in self._dirty

    def persist_if_dirty(self, path_map: dict[str, tuple[Path, Any]]) -> None:
        """检查脏标记，对脏数据执行写盘并清除标记。

        path_map: {key_str: (Path, data)}
        """
        import time
        now = time.time()
        if now - self._last_persist_ts < self._persist_interval:
            return
        persisted = False
        for key in list(self._dirty):
            if key in path_map:
                path, data = path_map[key]
                self.save_json(path, data)
                persisted = True
        self._dirty.clear()
        if persisted:
            self._last_persist_ts = now

    def force_persist(self, path_map: dict[str, tuple[Path, Any]]) -> None:
        """立即写盘所有脏数据（退出时调用）。"""
        for key in list(self._dirty):
            if key in path_map:
                path, data = path_map[key]
                self.save_json(path, data)
        self._dirty.clear()
        import time
        self._last_persist_ts = time.time()


def deep_copy_dict(value: Any) -> Any:
    """深度复制字典，避免引用问题。"""
    return json.loads(json.dumps(value, ensure_ascii=False))
