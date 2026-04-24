"""路径常量定义 - 集中管理所有文件路径"""
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    MEIPASS_DIR = Path(getattr(sys, "_MEIPASS", str(BASE_DIR)))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent  # oc_desktop_pet/..
    MEIPASS_DIR = BASE_DIR

INTERNAL_DIR = BASE_DIR / "_internal"
SETTINGS_PATH = BASE_DIR / "settings.json"
HISTORY_PATH = BASE_DIR / "history.json"
STATE_PATH = BASE_DIR / "state.json"
ITEMS_PATH = BASE_DIR / "items.json"
REMINDERS_PATH = BASE_DIR / "reminders.json"
README_PATH = BASE_DIR / "readme.md"
NOTES_PATH = BASE_DIR / "notes.txt"
MEMORY_PATH = BASE_DIR / "memory.json"
MEETINGS_PATH = BASE_DIR / "meetings.json"
BIO_WORKFLOWS_PATH = BASE_DIR / "bio_workflows_state.json"
DOC_HUB_PATH = BASE_DIR / "doc_hub_index.json"
TASK_BOARD_PATH = BASE_DIR / "task_board.json"
