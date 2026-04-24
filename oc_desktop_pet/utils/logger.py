# OC Desktop Pet - Central Logging Configuration
"""中心日志配置模块

提供统一的日志配置，所有模块通过 get_logger(__name__) 获取 logger。
日志同时输出到文件 (DEBUG+) 和控制台 (WARNING+)。
"""
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 日志目录
_LOG_DIR: Optional[Path] = None
_LOGGERS: dict = {}
_INITIALIZED: bool = False


def _get_log_dir() -> Path:
    """获取日志目录路径，如果不存在则创建。"""
    global _LOG_DIR
    if _LOG_DIR is None:
        # 日志目录位于项目根目录下的 logs/
        # 从当前文件向上查找包含 oc.py 或 settings.json 的目录
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "oc.py").exists() or (parent / "settings.json").exists():
                _LOG_DIR = parent / "logs"
                break
        else:
            # 回退到当前文件的同级目录
            _LOG_DIR = Path(__file__).parent.parent.parent / "logs"

        # 尝试创建目录
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            # 无法创建目录，日志将只输出到控制台
            _LOG_DIR = None

    return _LOG_DIR


def get_logger(name: str) -> logging.Logger:
    """获取配置好的 logger 实例。

    Args:
        name: logger 名称，通常使用 __name__

    Returns:
        配置了文件和控制台处理器的 logger

    日志级别：
        - 文件处理器: DEBUG 及以上
        - 控制台处理器: WARNING 及以上
    """
    global _INITIALIZED

    # 如果已经创建过该 logger，直接返回
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(name)

    # 避免重复添加 handler（logging.getLogger 会返回同一 logger）
    if logger.handlers:
        _LOGGERS[name] = logger
        return logger

    logger.setLevel(logging.DEBUG)

    # 日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 文件处理器
    log_dir = _get_log_dir()
    if log_dir:
        try:
            log_file = log_dir / f"oc_pet_{datetime.now().strftime('%Y%m%d')}.log"
            file_handler = logging.FileHandler(
                log_file,
                encoding="utf-8",
                mode="a"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except (OSError, PermissionError):
            # 文件创建失败，仅用控制台
            pass

    # 控制台处理器（仅 WARNING 及以上，避免干扰桌面宠物用户体验）
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    _LOGGERS[name] = logger
    return logger


def set_console_level(level: int) -> None:
    """动态调整控制台日志级别。

    Args:
        level: logging 级别常量 (如 logging.DEBUG, logging.INFO)
    """
    for logger in _LOGGERS.values():
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(level)


def flush_logs() -> None:
    """刷新所有日志 handler。"""
    for logger in _LOGGERS.values():
        for handler in logger.handlers:
            handler.flush()
