import ctypes
import ctypes.wintypes
import asyncio
import importlib
import json
import math
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tempfile
import wave
import tkinter as tk
import webbrowser
import io
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk, filedialog, simpledialog
import tkinter.scrolledtext as scrolledtext

import requests
from PIL import Image, ImageGrab, ImageTk, ImageDraw
import hashlib
import pathlib

try:
    import psutil
except Exception:
    psutil = None

pyttsx3 = None
try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    import soundcard as sc
except Exception:
    sc = None

try:
    from faster_whisper import WhisperModel
except Exception:
    WhisperModel = None

# ── 模块化导入 ──
from oc_desktop_pet.persistence.paths import (
    BASE_DIR, MEIPASS_DIR, INTERNAL_DIR,
    SETTINGS_PATH, HISTORY_PATH, STATE_PATH,
    ITEMS_PATH, REMINDERS_PATH, README_PATH,
    NOTES_PATH, MEMORY_PATH,
)
from oc_desktop_pet.persistence.defaults import (
    AFFINITY_MIN, AFFINITY_MAX, AFFINITY_THRESHOLDS,
    AFFINITY_LEVEL_LABELS, HIDDEN_DIALOGUES,
    DEFAULT_SETTINGS, DEFAULT_STATE, DEFAULT_ITEMS,
)
from oc_desktop_pet.persistence.store import Store, deep_copy_dict
from oc_desktop_pet.emotions.affinity import AffinityManager
from oc_desktop_pet.emotions.mood import MoodManager
from oc_desktop_pet.emotions.economy import EconomyManager
from oc_desktop_pet.chat.api_client import APIClient
from oc_desktop_pet.chat.memory import MemoryStore
from oc_desktop_pet.chat.prompt_builder import PromptBuilder
from oc_desktop_pet.chat.nanobot_bridge import NanobotBridge
from oc_desktop_pet.features.paper_assistant import PaperAssistantBridge
from oc_desktop_pet.animation.sprite_loader import SpriteLoader
from oc_desktop_pet.perception.feishu_bridge import FeishuBridge
from oc_desktop_pet.features.meeting_tracker import MeetingTracker
from oc_desktop_pet.features.bio_workflow import BioWorkflowGuide
from oc_desktop_pet.features.doc_hub import DocHub
from oc_desktop_pet.features.task_board import TaskBoard



# ── 默认值已迁移到 oc_desktop_pet.persistence.defaults ──
# 以下保留为引用别名，确保向后兼容
DEFAULT_SETTINGS = DEFAULT_SETTINGS
DEFAULT_STATE = DEFAULT_STATE
DEFAULT_ITEMS = DEFAULT_ITEMS


# ── NanobotBridge 和 PaperAssistantBridge 已迁移到模块 ──
# from oc_desktop_pet.chat.nanobot_bridge import NanobotBridge
# from oc_desktop_pet.features.paper_assistant import PaperAssistantBridge


class DesktopPet:
    def __init__(self):
        global affinity
        # ── 持久化存储管理器（脏标记+降频写盘） ──
        self.store = Store()

        self.settings = Store.load_json(SETTINGS_PATH, DEFAULT_SETTINGS)
        self._sanitize_tts_config()
        self._sanitize_auto_event_config()
        self._sanitize_proactive_config()
        self._sanitize_conversation_engine_config()
        self._sanitize_nanobot_config()
        self._sanitize_paper_tool_config()
        self.history = Store.load_json(HISTORY_PATH, [])
        self.state = Store.load_json(STATE_PATH, DEFAULT_STATE)
        self.items = Store.load_json(ITEMS_PATH, DEFAULT_ITEMS)
        self.reminders = Store.load_json(REMINDERS_PATH, [])

        # ── 模块化组件（需在 _normalize_mood_state 等方法之前初始化） ──
        self.affinity_mgr = AffinityManager(self.state)
        self.mood_mgr = MoodManager(self.state)
        self.economy_mgr = EconomyManager(self.state, self.items)
        self.api_client = APIClient(self.settings)

        self._normalize_mood_state()
        # 尝试将明文 API Key 迁移到密钥环
        from oc_desktop_pet.persistence.secure_config import SecureConfig
        self.secure_config = SecureConfig(self.settings)
        self.secure_config.migrate_from_settings()
        self.memory_store = MemoryStore(MEMORY_PATH, self.settings)
        self.memory_store.load()
        self.memory_db = self.memory_store.db  # 兼容旧代码
        self.prompt_builder = PromptBuilder(self.settings, self.state, self.memory_store)

        self._ensure_animation_slots()
        Store.save_json(ITEMS_PATH, self.items)
        Store.save_json(REMINDERS_PATH, self.reminders)
        self.memory_store.save()

        self.root = tk.Tk()
        self.root.title("OC Desktop Pet")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry("420x560+100+100")
        self.root.configure(bg="#00ff00")
        self.root.wm_attributes("-transparentcolor", "#00ff00")
        self.pet_scale = self._get_pet_scale()

        ico_path = BASE_DIR / "oc.ico"
        if ico_path.exists():
            try:
                self.root.iconbitmap(default=str(ico_path))
            except Exception:
                pass

        self.drag_start = (0, 0)
        self.reply_queue = queue.Queue()
        self.busy = False
        self.sleep_mode = False
        self.manual_awake_until_ts = 0.0
        self.game_mode = bool(self.state.get("quiet_mode", False))
        self.last_input_time = time.time()
        self.last_clipboard = ""
        self.pending_clipboard_text = ""
        self.clip_prompt_visible = False
        self.clipboard_event_queue = queue.Queue()
        self.clipboard_stop = threading.Event()
        self.focus_running = False
        self.focus_end_ts = 0.0
        self.focus_total_minutes = 0
        self.focus_last_warn = 0.0
        self.last_media_signature = ""
        self.last_media_comment_ts = 0.0
        self.media_backend_unavailable = set()
        self.last_hover_affinity_ts = 0.0
        self.last_click_affinity_ts = 0.0
        self.system_overheat_mode = False
        self.system_face_mode = ""
        self.memory_low_notified = False
        self.last_screen_roast_time = 0.0
        self.last_screen_comment_time = 0.0
        self.last_screen_fingerprint = ""
        self.screen_roast_warned = False
        self.screen_comment_warned = False
        self.last_audio_roast_time = 0.0
        self.last_audio_fingerprint = ""
        self.audio_roast_warned = False
        self.audio_roast_working = False
        self.asr_model = None
        self.auto_event_last_emit = {}
        self.last_auto_event_emit_ts = 0.0
        self.session_start_ts = time.time()
        self.proactive_stage = "warmup"
        self.proactive_last_emit_ts = 0.0
        self.recent_hook_keys = []
        self.recent_topic_hints = []
        self.conversation_metrics = {
            "request_count": 0,
            "fallback_count": 0,
            "topic_hits": 0,
            "hook_uses": 0,
            "memory_hits": 0,
            "auto_events_posted": 0,
            "last_topic_hint": "",
            "last_hook": "",
        }

        self.tts_queue = queue.Queue()
        self.tts_stop = False
        self.tts_engine = None
        self.tts_thread_started = False
        self.multimodal_idx = None
        self.last_highlight_path = None
        self.highlight_photo = None
        self._hotkey_handle = None

        self.current_sentences = []
        self.sentence_index = 0
        self.char_index = 0
        self.typing_after_id = None
        self.wait_next_after_id = None
        self.skip_cooldown_until = 0.0
        self.is_typing = False
        self.mouth_anim_after_id = None
        self.mouth_open = False
        self.anim_frame_index = {"idle": 0, "talk": 0, "blink": 0}
        self.idle_blink_after_id = None
        self.blink_recover_after_id = None
        self.is_blinking = False
        self.fall_after_id = None
        self.last_response_text = ""

        self.message_box_visible = False
        self.fade_after_id = None
        self.chat_window = None
        self.chat_log_text = None
        self.chat_input_var = tk.StringVar()
        self.chat_send_btn = None

        affinity = int(self.state.get("affinity", 0))
        affinity = max(AFFINITY_MIN, min(AFFINITY_MAX, affinity))
        self.state["affinity"] = affinity
        self.state.setdefault("affinity_unlocked", [])

        self._build_ui()
        self._refresh_affinity_ui()
        self._refresh_status_display()
        self._bind_events()
        self._init_tts()
        self._start_clipboard_listener_thread()
        self._schedule_next_blink()
        self.root.after(120, self._poll_reply_queue)
        self.root.after(250, self._poll_clipboard_events)
        self.root.after(1000, self._heartbeat)
        self.root.after(3000, self._monitor_system_status)
        self.root.after(60000, self._monitor_network)
        self.root.after(5000, self._monitor_media_playback)
        self.root.after(8000, self._monitor_screen_roast)
        self.root.after(12000, self._monitor_screen_comment)
        self.root.after(9000, self._monitor_audio_roast)
        # register global hotkey for screen comment if enabled
        self._register_hotkey()
        self.nanobot = NanobotBridge(self.settings, self.reply_queue)
        self.paper_tool = PaperAssistantBridge(self.settings)
        self.feishu = FeishuBridge(self.settings, self.reply_queue)
        self.feishu.start()

        # ── iGEM 助手模块 ──
        self.meeting_tracker = MeetingTracker(self.settings, self.reply_queue)
        self.bio_workflow = BioWorkflowGuide(self.settings, self.reply_queue)
        self.doc_hub = DocHub(self.settings, self.reply_queue)
        self.task_board = TaskBoard(self.settings, self.reply_queue)
        self._active_workflow_session_id = None  # 当前活跃的生信工作流会话
        self._igem_last_doc_scan = ""  # 上次文档扫描时间
        self._pending_feishu_reply = ""  # 待回复的飞书会话ID

    def _load_json(self, path: Path, default_value):
        if not path.exists():
            return deep_copy_dict(default_value)
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return deep_copy_dict(default_value)

    def _save_json(self, path: Path, data):
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_pet_scale(self):
        ui_cfg = self.settings.setdefault("ui", {"pet_scale": 1.0})
        raw = ui_cfg.get("pet_scale", 1.0)
        try:
            val = float(raw)
        except Exception:
            val = 1.0
        val = max(0.6, min(1.8, val))
        ui_cfg["pet_scale"] = round(val, 2)
        return ui_cfg["pet_scale"]

    def _ensure_animation_slots(self):
        slots = self.settings.setdefault("animation_slots", {})

        # 兼容旧配置：未配置动画槽位时自动用 images 做回退并写入占位槽位
        defaults = {
            "normal": {
                "idle": [self.settings.get("images", {}).get("normal_closed", "normal1.png")],
                "talk": [self.settings.get("images", {}).get("normal_open", "normal2.png")],
                "blink": [self.settings.get("images", {}).get("blink", "blink1.png")],
            },
            "excited": {
                "idle": [self.settings.get("images", {}).get("happy_closed", self.settings.get("images", {}).get("edge", "edge1.png"))],
                "talk": [self.settings.get("images", {}).get("happy_open", self.settings.get("images", {}).get("edge", "edge1.png"))],
                "blink": [self.settings.get("images", {}).get("happy_blink", self.settings.get("images", {}).get("edge", "edge1.png"))],
            },
            "happy": {
                "idle": [self.settings.get("images", {}).get("happy_closed", self.settings.get("images", {}).get("edge", "edge1.png"))],
                "talk": [self.settings.get("images", {}).get("happy_open", self.settings.get("images", {}).get("edge", "edge1.png"))],
                "blink": [self.settings.get("images", {}).get("happy_blink", self.settings.get("images", {}).get("edge", "edge1.png"))],
            },
            "sad": {
                "idle": [self.settings.get("images", {}).get("sad_closed", self.settings.get("images", {}).get("sweat", "sweat1.png"))],
                "talk": [self.settings.get("images", {}).get("sad_open", self.settings.get("images", {}).get("sweat", "sweat1.png"))],
                "blink": [self.settings.get("images", {}).get("sad_blink", self.settings.get("images", {}).get("faint", "faint1.png"))],
            },
            "angry": {
                "idle": [self.settings.get("images", {}).get("faint", "faint1.png")],
                "talk": [self.settings.get("images", {}).get("sweat", "sweat1.png")],
                "blink": [self.settings.get("images", {}).get("faint", "faint1.png")],
            },
        }

        updated = False
        for mood_key, mood_defaults in defaults.items():
            mood_cfg = slots.setdefault(mood_key, {})
            for phase_key, phase_defaults in mood_defaults.items():
                val = mood_cfg.get(phase_key)
                if not isinstance(val, list) or not val:
                    mood_cfg[phase_key] = list(phase_defaults)
                    updated = True

        if "emotion_value" not in self.state:
            self.state["emotion_value"] = int(self.state.get("mood_score", 0))
            updated = True

        if updated:
            self._save_json(SETTINGS_PATH, self.settings)
            self._save_json(STATE_PATH, self.state)

    def _sanitize_nanobot_config(self):
        cfg = self.settings.setdefault("nanobot", {})
        if not isinstance(cfg, dict):
            cfg = {}
            self.settings["nanobot"] = cfg
        cfg.setdefault("enabled", False)
        cfg.setdefault("config_path", "")
        cfg.setdefault("workspace", "")
        cfg.setdefault("model", "")
        cfg.setdefault("timeout_seconds", 60)
        cfg.setdefault("channel", "desktop_pet")
        cfg.setdefault("chat_id", "oc")
        cfg.setdefault("bio_lab_enabled", True)
        cfg.setdefault("web_enabled", True)
        cfg.setdefault("source_policy", "fixed_only")
        cfg.setdefault("fixed_sources", [])
        cfg["timeout_seconds"] = int(max(10, min(300, int(cfg.get("timeout_seconds", 60)))))
        if cfg.get("source_policy") not in ("fixed_only", "prefer_fixed", "off"):
            cfg["source_policy"] = "fixed_only"
        if not isinstance(cfg.get("fixed_sources"), list):
            cfg["fixed_sources"] = []
        self._save_json(SETTINGS_PATH, self.settings)

    def _sanitize_paper_tool_config(self):
        cfg = self.settings.setdefault("paper_tool", {})
        if not isinstance(cfg, dict):
            cfg = {}
            self.settings["paper_tool"] = cfg
        cfg.setdefault("enabled", True)
        cfg.setdefault("base_dir", "")
        cfg.setdefault("output_dir", "")
        cfg.setdefault("default_paper_id", "")
        cfg.setdefault("use_llm", True)
        cfg.setdefault("timeout_seconds", 90)
        cfg["timeout_seconds"] = int(max(10, min(300, int(cfg.get("timeout_seconds", 90)))))
        self._save_json(SETTINGS_PATH, self.settings)

    def _build_ui(self):
        self.canvas = tk.Canvas(self.root, width=420, height=440, bg="#00ff00", highlightthickness=0, bd=0)
        self.canvas.pack(side="top", fill="both", expand=False)

        (
            self.closed_img,
            self.open_img,
            self.blink_img,
            self.sleep_img,
            self.edge_img,
            self.sweat_img,
            self.faint_img,
            self.happy_closed_img,
            self.happy_open_img,
            self.happy_blink_img,
            self.sad_closed_img,
            self.sad_open_img,
            self.sad_blink_img,
        ) = self._load_pet_images()
        self.pet_img_id = self.canvas.create_image(210, 250, image=self.closed_img)

        self.bubble_bg_id = self.canvas.create_rectangle(40, 40, 380, 160, fill="#fff8f2", outline="#e7d7c9", width=2, state="hidden")
        self.bubble_text_id = self.canvas.create_text(60, 60, text="", fill="#3b2f2f", anchor="nw", font=("Microsoft YaHei UI", 11), width=300, state="hidden")

        self.clip_hint_bg_id = self.canvas.create_oval(154, 146, 198, 190, fill="#fff8f2", outline="#e7d7c9", width=2, state="hidden")
        self.clip_hint_icon_id = self.canvas.create_text(176, 166, text="📋", fill="#3b2f2f", font=("Segoe UI Emoji", 14), state="hidden")
        self.clip_hint_text_id = self.canvas.create_text(176, 134, text="你复制了什么？", fill="#3b2f2f", font=("Microsoft YaHei UI", 9), state="hidden")

        input_wrap = tk.Frame(self.root, bg="#00ff00")
        input_wrap.pack(side="top", fill="x", padx=10, pady=(0, 4))

        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(input_wrap, textvariable=self.input_var)
        self.input_entry.pack(side="left", fill="x", expand=True)
        self.send_btn = ttk.Button(input_wrap, text="发送", command=self.send_user_message)
        self.send_btn.pack(side="left", padx=(8, 0))

        status_wrap = tk.Frame(self.root, bg="#00ff00")
        status_wrap.pack(side="bottom", fill="x", padx=10, pady=(0, 8))

        self.status_var = tk.StringVar()
        self.status_var.set("状态: Normal (0)")
        tk.Label(status_wrap, textvariable=self.status_var, bg="#00ff00", fg="#2c2c2c", font=("Microsoft YaHei UI", 9)).pack(side="left")

        self.affinity_var = tk.IntVar(value=int(self.state.get("affinity", 0)))
        self.affinity_bar = ttk.Progressbar(status_wrap, maximum=100, variable=self.affinity_var, length=120)
        self.affinity_bar.pack(side="left", padx=10)
        self.affinity_text_var = tk.StringVar(value=f"❤ {self.affinity_var.get()}/{AFFINITY_MAX}")
        tk.Label(status_wrap, textvariable=self.affinity_text_var, bg="#00ff00", fg="#2c2c2c", font=("Microsoft YaHei UI", 9)).pack(side="left")

        self.clip_eat_btn = ttk.Button(status_wrap, text="吃掉剪贴板", command=self._eat_clipboard)
        self.clip_eat_btn.pack(side="left")
        self.clip_eat_btn.pack_forget()

        self.coin_var = tk.StringVar(value=f"金币: {int(self.state.get('coins', 0))}")
        tk.Label(status_wrap, textvariable=self.coin_var, bg="#00ff00", fg="#2c2c2c", font=("Microsoft YaHei UI", 9)).pack(side="right")

    def _load_pet_images(self):
        img_cfg = self.settings.get("images", {})
        closed_path = self._resolve_asset_path(img_cfg.get("normal_closed", "normal1.png"))
        open_path = self._resolve_asset_path(img_cfg.get("normal_open", "normal2.png"))
        blink_path = self._resolve_asset_path(img_cfg.get("blink", "blink1.png"))
        sleep_path = self._resolve_asset_path(img_cfg.get("sleep", "sleep1.png"))
        edge_path = self._resolve_asset_path(img_cfg.get("edge", "edge1.png"))
        sweat_path = self._resolve_asset_path(img_cfg.get("sweat", "sweat1.png"))
        faint_path = self._resolve_asset_path(img_cfg.get("faint", "faint1.png"))
        happy_closed_path = self._resolve_asset_path(img_cfg.get("happy_closed", img_cfg.get("edge", "edge1.png")))
        happy_open_path = self._resolve_asset_path(img_cfg.get("happy_open", img_cfg.get("edge", "edge1.png")))
        happy_blink_path = self._resolve_asset_path(img_cfg.get("happy_blink", img_cfg.get("edge", "edge1.png")))
        sad_closed_path = self._resolve_asset_path(img_cfg.get("sad_closed", img_cfg.get("sweat", "sweat1.png")))
        sad_open_path = self._resolve_asset_path(img_cfg.get("sad_open", img_cfg.get("sweat", "sweat1.png")))
        sad_blink_path = self._resolve_asset_path(img_cfg.get("sad_blink", img_cfg.get("faint", "faint1.png")))

        self._log_asset_paths(
            {
                "normal_closed": closed_path,
                "normal_open": open_path,
                "blink": blink_path,
                "sleep": sleep_path,
                "edge": edge_path,
                "sweat": sweat_path,
                "faint": faint_path,
                "happy_closed": happy_closed_path,
                "happy_open": happy_open_path,
                "happy_blink": happy_blink_path,
                "sad_closed": sad_closed_path,
                "sad_open": sad_open_path,
                "sad_blink": sad_blink_path,
            }
        )

        if not closed_path.exists():
            auto_img = self._find_first_image()
            if auto_img:
                closed_path = auto_img

        closed = self._safe_open_image(closed_path)
        opened = self._safe_open_image(open_path) if open_path.exists() else closed.copy()
        blinked = self._safe_open_image(blink_path) if blink_path.exists() else closed.copy()
        slept = self._safe_open_image(sleep_path) if sleep_path.exists() else closed.copy()
        edged = self._safe_open_image(edge_path) if edge_path.exists() else closed.copy()
        sweat = self._safe_open_image(sweat_path) if sweat_path.exists() else closed.copy()
        faint = self._safe_open_image(faint_path) if faint_path.exists() else closed.copy()
        happy_closed = self._safe_open_image(happy_closed_path) if happy_closed_path.exists() else edged.copy()
        happy_open = self._safe_open_image(happy_open_path) if happy_open_path.exists() else happy_closed.copy()
        happy_blink = self._safe_open_image(happy_blink_path) if happy_blink_path.exists() else happy_closed.copy()
        sad_closed = self._safe_open_image(sad_closed_path) if sad_closed_path.exists() else sweat.copy()
        sad_open = self._safe_open_image(sad_open_path) if sad_open_path.exists() else sad_closed.copy()
        sad_blink = self._safe_open_image(sad_blink_path) if sad_blink_path.exists() else faint.copy()

        max_side = int(300 * float(self.pet_scale))
        max_side = max(120, min(540, max_side))
        for img in (closed, opened, blinked, slept, edged, sweat, faint, happy_closed, happy_open, happy_blink, sad_closed, sad_open, sad_blink):
            img.thumbnail((max_side, max_side))

        result = (
            ImageTk.PhotoImage(closed),
            ImageTk.PhotoImage(opened),
            ImageTk.PhotoImage(blinked),
            ImageTk.PhotoImage(slept),
            ImageTk.PhotoImage(edged),
            ImageTk.PhotoImage(sweat),
            ImageTk.PhotoImage(faint),
            ImageTk.PhotoImage(happy_closed),
            ImageTk.PhotoImage(happy_open),
            ImageTk.PhotoImage(happy_blink),
            ImageTk.PhotoImage(sad_closed),
            ImageTk.PhotoImage(sad_open),
            ImageTk.PhotoImage(sad_blink),
        )

        # 动画槽位：每个情绪支持 idle/talk/blink 多帧，后期可直接在 settings.json 填充更多文件名
        self.animation_library = self._build_animation_library(max_side)
        return result

    def _build_animation_library(self, max_side):
        lib = {}
        slots = self.settings.get("animation_slots", {})

        fallback = {
            "normal": {
                "idle": [self.settings.get("images", {}).get("normal_closed", "normal1.png")],
                "talk": [self.settings.get("images", {}).get("normal_open", "normal2.png")],
                "blink": [self.settings.get("images", {}).get("blink", "blink1.png")],
            },
            "excited": {
                "idle": [self.settings.get("images", {}).get("happy_closed", self.settings.get("images", {}).get("edge", "edge1.png"))],
                "talk": [self.settings.get("images", {}).get("happy_open", self.settings.get("images", {}).get("edge", "edge1.png"))],
                "blink": [self.settings.get("images", {}).get("happy_blink", self.settings.get("images", {}).get("edge", "edge1.png"))],
            },
            "happy": {
                "idle": [self.settings.get("images", {}).get("happy_closed", self.settings.get("images", {}).get("edge", "edge1.png"))],
                "talk": [self.settings.get("images", {}).get("happy_open", self.settings.get("images", {}).get("edge", "edge1.png"))],
                "blink": [self.settings.get("images", {}).get("happy_blink", self.settings.get("images", {}).get("edge", "edge1.png"))],
            },
            "sad": {
                "idle": [self.settings.get("images", {}).get("sad_closed", self.settings.get("images", {}).get("sweat", "sweat1.png"))],
                "talk": [self.settings.get("images", {}).get("sad_open", self.settings.get("images", {}).get("sweat", "sweat1.png"))],
                "blink": [self.settings.get("images", {}).get("sad_blink", self.settings.get("images", {}).get("faint", "faint1.png"))],
            },
            "angry": {
                "idle": [self.settings.get("images", {}).get("faint", "faint1.png")],
                "talk": [self.settings.get("images", {}).get("sweat", "sweat1.png")],
                "blink": [self.settings.get("images", {}).get("faint", "faint1.png")],
            },
        }

        def load_frames(paths):
            frames = []
            for name in paths:
                if not name:
                    continue
                p = self._resolve_asset_path(str(name))
                if not p.exists():
                    continue
                img = self._safe_open_image(p)
                img.thumbnail((max_side, max_side))
                frames.append(ImageTk.PhotoImage(img))
            return frames

        for mood in ("normal", "excited", "happy", "sad", "angry"):
            mood_cfg = slots.get(mood, {}) if isinstance(slots, dict) else {}
            lib[mood] = {}
            for phase in ("idle", "talk", "blink"):
                configured = mood_cfg.get(phase, [])
                if not isinstance(configured, list) or not configured:
                    configured = fallback[mood][phase]
                frames = load_frames(configured)
                if not frames:
                    frames = load_frames(fallback[mood][phase])
                lib[mood][phase] = frames
        return lib

    def _current_emotion_key(self):
        mood = self.state.get("mood", "Normal")
        if mood == "Excited":
            return "excited"
        if mood == "Happy":
            return "happy"
        if mood == "Sad":
            return "sad"
        if mood == "Angry":
            return "angry"
        return "normal"

    def _pick_animation_frame(self, phase):
        mood_key = self._current_emotion_key()
        frames = []
        if isinstance(getattr(self, "animation_library", None), dict):
            frames = self.animation_library.get(mood_key, {}).get(phase, [])
        if not frames:
            closed, opened, blinked = self._get_expression_images()
            fallback_map = {"idle": [closed], "talk": [opened], "blink": [blinked]}
            frames = fallback_map.get(phase, [closed])
        idx = self.anim_frame_index.get(phase, 0)
        frame = frames[idx % len(frames)]
        self.anim_frame_index[phase] = (idx + 1) % max(1, len(frames))
        return frame

    def _get_expression_images(self):
        mood = self.state.get("mood", "Normal")
        if mood == "Excited":
            return self.happy_closed_img, self.happy_open_img, self.happy_blink_img
        if mood == "Happy":
            return self.happy_closed_img, self.happy_open_img, self.happy_blink_img
        if mood == "Angry":
            return self.faint_img, self.sweat_img, self.faint_img
        if mood == "Sad":
            return self.sad_closed_img, self.sad_open_img, self.sad_blink_img
        return self.closed_img, self.open_img, self.blink_img

    def _safe_open_image(self, path: Path):
        if path.exists():
            return Image.open(path).convert("RGBA")
        return Image.new("RGBA", (280, 280), (255, 255, 255, 0))

    def _log_asset_paths(self, mapping: dict):
        try:
            log_path = BASE_DIR / "asset_debug.log"
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n[asset_check %s]\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                f.write("BASE_DIR=%s\n" % BASE_DIR)
                f.write("MEIPASS_DIR=%s\n" % MEIPASS_DIR)
                f.write("INTERNAL_DIR=%s\n" % INTERNAL_DIR)
                for key, p in mapping.items():
                    f.write("%s=%s exists=%s\n" % (key, p, p.exists()))
        except Exception:
            pass

    def _resolve_asset_path(self, name: str) -> Path:
        name = str(name)
        candidates = [BASE_DIR, MEIPASS_DIR, INTERNAL_DIR]
        for base in candidates:
            p = base / name
            if p.exists():
                return p
        return candidates[0] / name

    def _find_first_image(self):
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        for base in (BASE_DIR, MEIPASS_DIR, INTERNAL_DIR):
            if not base.exists():
                continue
            for p in sorted(base.iterdir()):
                if p.is_file() and p.suffix.lower() in exts and p.name.lower() not in {"normal1.png", "normal2.png", "blink1.png"}:
                    return p
        return None

    def _bind_events(self):
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_release)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.canvas.bind("<Button-1>", self._on_dialogue_click, add="+")
        self.canvas.bind("<Button-1>", self._on_pet_click, add="+")
        self.canvas.bind("<Motion>", self._on_pet_hover, add="+")
        self.input_entry.bind("<Return>", lambda _e: self.send_user_message())

    def _show_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="打开对话窗口", command=self.open_chat_window)
        menu.add_command(label="历史记录", command=self.open_history_window)
        menu.add_command(label="帮助/说明", command=self.open_readme_window)
        menu.add_command(label="修改人设Prompt", command=self.open_prompt_editor)
        menu.add_command(label="御主档案", command=self.open_profile_editor)
        menu.add_command(label="整点报时测试", command=self._hourly_chime)
        menu.add_command(label="专注时钟", command=self.open_focus_window)
        menu.add_command(label="背包", command=self.open_bag_window)
        menu.add_command(label="日程提醒", command=self.open_reminder_window)
        menu.add_command(label="随机事件", command=self.trigger_random_event)
        menu.add_command(label="观察日记", command=self.open_journal_window)
        menu.add_command(label="安静模式开关", command=self.toggle_quiet_mode)
        menu.add_command(label="听歌感想开关", command=self.toggle_media_commentary)
        menu.add_command(label="屏幕吐槽开关", command=self.toggle_screen_roast)
        menu.add_command(label="屏幕解析开关", command=self.toggle_screen_comment)
        menu.add_command(label="屏幕解析设置", command=self.open_screen_comment_settings)
        menu.add_command(label="系统总控面板", command=self.open_system_control_panel)
        menu.add_command(label="对话引擎设置", command=self.open_conversation_engine_settings)
        menu.add_command(label="Nanobot 引擎开关", command=self.toggle_nanobot)
        menu.add_command(label="飞书桥接开关", command=self.toggle_feishu)
        menu.add_command(label="飞书设置", command=self.open_feishu_settings)
        menu.add_command(label="Nanobot 工具设置", command=self.open_nanobot_settings)
        menu.add_command(label="生信任务示例", command=self.prefill_bio_prompt)
        menu.add_command(label="数据库抓取示例", command=self.prefill_crawl_prompt)
        menu.add_command(label="多模态工具", command=self.open_multimodal_window)
        menu.add_command(label="查看最近高亮截图", command=self.open_latest_highlight)
        menu.add_command(label="音频吐槽开关", command=self.toggle_audio_roast)
        menu.add_command(label="语音朗读开关", command=self.toggle_tts)
        menu.add_command(label="语音设置", command=self.open_tts_settings)
        menu.add_command(label="宠物大小", command=self.open_size_settings)
        menu.add_command(label="管家服务", command=self.open_launcher_window)
        menu.add_command(label="猜拳/掷骰子", command=self.open_game_window)
        menu.add_command(label="记一笔", command=self.open_notes_window)
        menu.add_command(label="整理文件", command=self.open_file_sort_window)
        menu.add_command(label="商城", command=self.open_shop_window)
        menu.add_command(label="重置记忆", command=self.reset_memory_data)
        menu.add_command(label="清空长期记忆", command=self.clear_longterm_memory)

        bookmark_menu = tk.Menu(menu, tearoff=0)
        for name, url in self.settings.get("bookmarks", {}).items():
            bookmark_menu.add_command(label=name, command=lambda u=url: self._open_url(u))
        menu.add_cascade(label="传送门", menu=bookmark_menu)

        menu.add_separator()
        # Knowledge Base submenu
        kb_menu = tk.Menu(menu, tearoff=0)
        kb_menu.add_command(label="加入知识库", command=self._kb_add_files)
        kb_menu.add_command(label="摘要文件", command=self._kb_summarize_file)
        kb_menu.add_command(label="语义检索 (KB)", command=self._kb_semsearch)
        menu.add_cascade(label="知识库", menu=kb_menu)

        # iGEM 助手子菜单
        igem_menu = tk.Menu(menu, tearoff=0)
        igem_menu.add_command(label="记录组会", command=self._open_meeting_record_window)
        igem_menu.add_command(label="查看会议记录", command=self._open_meeting_list_window)
        igem_menu.add_command(label="生信工作流", command=self._open_workflow_window)
        igem_menu.add_command(label="文档中心", command=self._open_doc_hub_window)
        igem_menu.add_command(label="任务看板", command=self._open_task_board_window)
        igem_menu.add_command(label="团队成员", command=self._open_team_window)
        igem_menu.add_command(label="iGEM助手设置", command=self._open_igem_settings_window)
        menu.add_cascade(label="iGEM助手", menu=igem_menu)

        menu.add_command(label="重新加载图片", command=self.reload_images)
        menu.add_command(label="退出", command=self.on_exit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_text_window(self, title, text):
        w = tk.Toplevel(self.root)
        w.title(title)
        w.geometry("640x480")
        txt = scrolledtext.ScrolledText(w, wrap='word')
        txt.pack(expand=True, fill='both')
        txt.insert('1.0', text)
        txt.config(state='disabled')

    def _kb_add_files(self):
        paths = filedialog.askopenfilenames(title="选择要加入知识库的文件")
        if not paths:
            return
        cmd = [sys.executable, str(BASE_DIR / 'kb' / 'kb_cli.py'), 'add'] + list(paths)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                messagebox.showinfo("加入知识库", proc.stdout or "已完成")
            else:
                messagebox.showerror("错误", proc.stderr or "发生错误")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _kb_summarize_file(self):
        path = filedialog.askopenfilename(title="选择要摘要的文件")
        if not path:
            return
        cmd = [sys.executable, str(BASE_DIR / 'kb' / 'kb_cli.py'), 'summarize-file', path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            out = proc.stdout or (proc.stderr or '')
            self._show_text_window("摘要 - " + os.path.basename(path), out)
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _kb_semsearch(self):
        q = simpledialog.askstring("语义检索", "输入语义检索查询")
        if not q:
            return
        cmd = [sys.executable, str(BASE_DIR / 'kb' / 'kb_cli.py'), 'semsearch', '--q', q, '--k', '5']
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            out = proc.stdout or (proc.stderr or '')
            self._show_text_window("语义检索结果", out)
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def reset_memory_data(self):
        ok = messagebox.askyesno("重置记忆", "确定清空历史记录和长期记忆吗？该操作不可撤销。")
        if not ok:
            return
        self.history = []
        self.memory_db = []
        self._save_json(HISTORY_PATH, self.history)
        self._save_json(MEMORY_PATH, self.memory_db)
        if self.chat_log_text and self.chat_window and self.chat_window.winfo_exists():
            self.chat_log_text.config(state="normal")
            self.chat_log_text.delete("1.0", "end")
            self.chat_log_text.config(state="disabled")
        self.show_lines(["记忆已重置：历史记录与长期记忆已清空。"])

    def clear_longterm_memory(self):
        ok = messagebox.askyesno("清空长期记忆", "确定只清空长期记忆（memory.json）吗？")
        if not ok:
            return
        self.memory_db = []
        self._save_json(MEMORY_PATH, self.memory_db)
        self.show_lines(["长期记忆已清空，历史对话未受影响。"])

    def _open_url(self, url):
        webbrowser.open(url)
        self.show_lines([f"正在打开：{url}"])

    def reload_images(self):
        (
            self.closed_img,
            self.open_img,
            self.blink_img,
            self.sleep_img,
            self.edge_img,
            self.sweat_img,
            self.faint_img,
            self.happy_closed_img,
            self.happy_open_img,
            self.happy_blink_img,
            self.sad_closed_img,
            self.sad_open_img,
            self.sad_blink_img,
        ) = self._load_pet_images()
        self._apply_idle_image()
        self.show_lines(["图片已重新加载。"])

    def _compose_system_prompt(self):
        """委托给 PromptBuilder，生成结构化系统提示词。"""
        return self.prompt_builder.compose_system_prompt()

    def _mood_from_emotion(self, emotion):
        """委托给 MoodManager。"""
        return MoodManager._mood_from_emotion(emotion)

    def _normalize_mood_state(self):
        """委托给 MoodManager。"""
        self.mood_mgr.normalize()

    def _refresh_status_display(self):
        mood = self.state.get("mood", "Normal")
        score = int(self.state.get("emotion_value", self.state.get("mood_score", 0)))
        sign = "+" if score > 0 else ""
        suffix = " | 安静模式" if self.game_mode else ""
        self.status_var.set(f"状态: {mood} | 情绪值 {sign}{score}{suffix}")
        if hasattr(self, "pet_img_id") and (not self.is_typing) and (not self.is_blinking):
            self._apply_idle_image()

    def toggle_quiet_mode(self):
        self.game_mode = not self.game_mode
        self.state["quiet_mode"] = self.game_mode
        self._save_json(STATE_PATH, self.state)

        x, y = self.root.winfo_x(), self.root.winfo_y()
        if self.game_mode:
            self.root.geometry(f"300x430+{x}+{y}")
            self.show_lines(["已开启安静模式（手动）。"])
        else:
            self.root.geometry(f"420x560+{x}+{y}")
            self.show_lines(["已关闭安静模式（手动）。"])
        self._refresh_status_display()

    def toggle_media_commentary(self):
        media_cfg = self.settings.setdefault("media", {
            "enabled": True,
            "poll_seconds": 4,
            "comment_cooldown_seconds": 20,
        })
        media_cfg["enabled"] = not bool(media_cfg.get("enabled", True))
        self._save_json(SETTINGS_PATH, self.settings)
        if media_cfg["enabled"]:
            self.show_lines(["听歌感想已开启。"])
        else:
            self.show_lines(["听歌感想已关闭。"])

    def _adjust_mood(self, delta, reason=""):
        """委托给 MoodManager。"""
        try:
            delta = int(delta)
        except Exception:
            delta = 0
        if delta == 0:
            return
        self.mood_mgr.adjust(delta, reason)
        self._refresh_status_display()

    def _sentiment_delta(self, text, strength=1.0):
        if not text:
            return 0
        content = str(text).lower()
        positive_words = [
            "开心", "高兴", "喜欢", "赞", "棒", "太好了", "幸福", "谢谢", "爱", "顺利", "厉害", "可爱",
            "happy", "good", "great", "awesome", "love", "thanks", "nice",
        ]
        negative_words = [
            "难过", "烦", "生气", "糟", "讨厌", "崩溃", "痛苦", "失败", "卡", "累", "无语", "伤心",
            "sad", "bad", "angry", "hate", "terrible", "tired", "lag", "stuck",
        ]
        pos = sum(content.count(word) for word in positive_words)
        neg = sum(content.count(word) for word in negative_words)
        raw = pos - neg
        if raw == 0:
            return 0
        delta = int(round(raw * float(strength)))
        if delta == 0:
            delta = 1 if raw > 0 else -1
        return max(-6, min(6, delta))

    def _get_generation_params(self):
        mood = self.state.get("mood", "Normal")
        if mood == "Excited":
            return {"temperature": 0.95, "max_tokens": 460}
        if mood == "Happy":
            return {"temperature": 0.9, "max_tokens": 420}
        if mood == "Angry":
            return {"temperature": 0.45, "max_tokens": 200}
        if mood == "Sad":
            return {"temperature": 0.55, "max_tokens": 220}
        return {"temperature": 0.8, "max_tokens": 320}

    def _push_history(self, role, content):
        self.history.append({"role": role, "content": content, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        self._save_json(HISTORY_PATH, self.history)
        self._append_chat_log(role, content)
        if role in ("user", "assistant") and content.strip():
            self._remember_text(role, content)

    def _append_chat_log(self, role, content):
        if not self.chat_log_text or not self.chat_window or not self.chat_window.winfo_exists():
            return
        role_name = "你" if role == "user" else "OC"
        self.chat_log_text.config(state="normal")
        self.chat_log_text.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {role_name}: {content}\n")
        self.chat_log_text.see("end")
        self.chat_log_text.config(state="disabled")

    def _set_send_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.send_btn.config(state=state)
        if self.chat_send_btn and self.chat_window and self.chat_window.winfo_exists():
            self.chat_send_btn.config(state=state)

    def open_chat_window(self):
        if self.chat_window and self.chat_window.winfo_exists():
            self.chat_window.lift()
            self.chat_window.focus_force()
            return

        self.chat_window = tk.Toplevel(self.root)
        self.chat_window.title("和 OC 对话")
        self.chat_window.geometry("520x440")
        self.chat_window.attributes("-topmost", True)

        self.chat_log_text = tk.Text(self.chat_window, wrap="word", font=("Microsoft YaHei UI", 10), state="disabled")
        self.chat_log_text.pack(fill="both", expand=True, padx=8, pady=8)

        bottom = ttk.Frame(self.chat_window)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        chat_entry = ttk.Entry(bottom, textvariable=self.chat_input_var)
        chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_send_btn = ttk.Button(bottom, text="发送", command=lambda: self.send_user_message(self.chat_input_var.get().strip()))
        self.chat_send_btn.pack(side="left", padx=(8, 0))
        chat_entry.bind("<Return>", lambda _e: self.send_user_message(self.chat_input_var.get().strip()))

        for item in self.history[-120:]:
            self._append_chat_log(item.get("role", "assistant"), item.get("content", ""))

        if self.busy:
            self._set_send_enabled(False)

        chat_entry.focus_force()

    def _to_vector(self, text, dim=64):
        vec = [0.0] * dim
        normalized = re.sub(r"\s+", "", text.lower())
        if not normalized:
            return vec
        for i, ch in enumerate(normalized):
            idx = (ord(ch) + i * 13) % dim
            vec[idx] += 1.0
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / n for x in vec]

    def _cosine(self, a, b):
        if not a or not b or len(a) != len(b):
            return 0.0
        return float(sum(x * y for x, y in zip(a, b)))

    def _remember_text(self, role, content):
        """委托给 MemoryStore，自动过滤噪声和自动标记。"""
        self.memory_store.remember(role, content)
        self.memory_db = self.memory_store.db  # 同步引用

    def _prune_memory_noise(self):
        """委托给 MemoryStore。"""
        self.memory_store._prune_noise()
        self.memory_db = self.memory_store.db

    def _recall_memory(self, query, topk=3):
        """委托给 MemoryStore，支持嵌入模型检索。"""
        return self.memory_store.recall(query, topk=topk)

    def send_user_message(self, direct_text=None):
        self.last_input_time = time.time()
        text = (direct_text or "").strip() if direct_text is not None else self.input_var.get().strip()
        if not text:
            return
        if self.sleep_mode:
            if self._is_wake_request(text):
                self._wake_from_sleep(30)
            else:
                self.show_lines(["呼噜呼噜...Zzz...（夜间睡眠中）"])
                return
        if self.busy:
            self.show_lines(["我还在说话中，稍等一下~"])
            return

        self.input_var.set("")
        if direct_text is not None:
            self.chat_input_var.set("")
        self.busy = True
        self._set_send_enabled(False)
        self.show_lines(["收到，我想想怎么回你…"])
        self._adjust_mood(self._sentiment_delta(text), reason="user_chat")
        self._increase_affinity(1, reason="聊天")
        self._push_history("user", text)

        # ── iGEM 助手命令路由 ──
        igem_handled = self._parse_igem_command(text)
        if igem_handled:
            return

        # ── 活跃生信工作流会话路由 ──
        if self._active_workflow_session_id:
            threading.Thread(target=self._handle_workflow_input, args=(text,), daemon=True).start()
            return

        paper_mode, paper_payload = self._parse_paper_command(text)
        if paper_mode:
            threading.Thread(target=self._request_paper_reply, args=(paper_mode, paper_payload), daemon=True).start()
            return

        if self._use_nanobot():
            mode, clean_text = self._parse_nanobot_mode(text)
            threading.Thread(target=self._request_nanobot_reply, args=(clean_text, mode), daemon=True).start()
        else:
            threading.Thread(target=self._request_ai_reply, args=(text,), daemon=True).start()

    def _is_wake_request(self, text):
        content = str(text or "").lower()
        wake_keywords = (
            "叫醒", "起床", "醒醒", "别睡", "不许睡", "wake", "wake up", "wakeup",
        )
        return any(keyword in content for keyword in wake_keywords)

    def _wake_from_sleep(self, minutes=30):
        try:
            minutes = max(1, int(minutes))
        except Exception:
            minutes = 30
        self.manual_awake_until_ts = time.time() + minutes * 60
        self.sleep_mode = False
        self.show_lines([f"唔…被你叫醒啦，我先陪你{minutes}分钟。"])

    def _chat_completion(self, messages, temperature=0.8, max_tokens=320, timeout=60):
        """委托给 APIClient，支持自动重试。"""
        return self.api_client.chat_completion(
            messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout
        )

    def _build_fallback_reply(self, user_text, reason=""):
        return APIClient.build_fallback_reply(user_text, reason)

    def _format_error_reason(self, reason):
        return APIClient.format_error_reason(reason)

    def _request_ai_reply(self, user_text, use_stream=True):
        try:
            self.conversation_metrics["request_count"] = int(self.conversation_metrics.get("request_count", 0)) + 1
            recent = []
            for item in self.history[-20:]:
                if item.get("role") in ("user", "assistant"):
                    recent.append({"role": item["role"], "content": item.get("content", "")})

            context_addon = self.prompt_builder.build_full_context(user_text) if self.prompt_builder else ""

            gen = self._get_generation_params()
            messages = [
                {"role": "system", "content": self._compose_system_prompt()},
                *recent,
                {"role": "user", "content": user_text + context_addon},
            ]

            # 尝试流式响应
            if use_stream:
                try:
                    answer = self._request_ai_reply_stream(messages, gen)
                except Exception:
                    # 流式失败时回退到非流式
                    answer = self._chat_completion(
                        messages,
                        temperature=gen["temperature"],
                        max_tokens=gen["max_tokens"],
                        timeout=60,
                    )
            else:
                answer = self._chat_completion(
                    messages,
                    temperature=gen["temperature"],
                    max_tokens=gen["max_tokens"],
                    timeout=60,
                )

            self._push_history("assistant", answer)
            self.reply_queue.put(("ok", answer))
            self._adjust_mood(self._sentiment_delta(answer, strength=0.6), reason="assistant_chat")
        except Exception as e:
            self.conversation_metrics["fallback_count"] = int(self.conversation_metrics.get("fallback_count", 0)) + 1
            reason_text = self._format_error_reason(str(e))
            fallback = self._build_fallback_reply(user_text, reason_text)
            self.reply_queue.put(("tip", f"请求失败原因：{reason_text}"))
            self._push_history("assistant", f"[fallback] {fallback}")
            self.reply_queue.put(("ok", fallback))

    def _request_ai_reply_stream(self, messages, gen_params):
        """流式请求 AI 回复，逐 token 推送到 UI。"""
        collected = []
        buffer = ""
        sentence_enders = set("。！？!?\n")

        for token in self.api_client.chat_stream(
            messages,
            temperature=gen_params["temperature"],
            max_tokens=gen_params["max_tokens"],
            timeout=60,
        ):
            collected.append(token)
            buffer += token

            # 遇到句末标点就推送一句
            if buffer and buffer[-1] in sentence_enders:
                sentence = buffer.strip()
                if sentence:
                    self.reply_queue.put(("stream", sentence))
                buffer = ""

        # 推送剩余内容
        remaining = buffer.strip()
        if remaining:
            self.reply_queue.put(("stream", remaining))

        return "".join(collected).strip() or "嗯嗯，我在听~"

    def _request_nanobot_reply(self, user_text, mode="auto"):
        try:
            self.nanobot.ensure_ready(timeout=12)
            prompt = self._wrap_nanobot_prompt(user_text, mode=mode)
            answer = self.nanobot.ask(prompt)
            answer = answer.strip() or "嗯嗯，我在听~"
            self._push_history("assistant", answer)
            self.reply_queue.put(("ok", answer))
            self._adjust_mood(self._sentiment_delta(answer, strength=0.6), reason="assistant_chat")
        except Exception as e:
            reason_text = self._format_error_reason(str(e))
            fallback = self._build_fallback_reply(user_text, reason_text)
            self.reply_queue.put(("tip", f"Nanobot 请求失败：{reason_text}"))
            self._push_history("assistant", f"[fallback] {fallback}")
            self.reply_queue.put(("ok", fallback))

    def _request_paper_reply(self, mode, payload):
        try:
            if not bool(self.settings.get("paper_tool", {}).get("enabled", True)):
                self.reply_queue.put(("tip", "论文助手已关闭，请在设置中启用。"))
                return

            if mode == "list":
                lines = self.paper_tool.list_papers()
                self._push_history("assistant", "\n".join(lines))
                self.reply_queue.put(("ok", "\n".join(lines)))
                return

            if isinstance(payload, dict):
                query = payload.get("query", "")
                paper_id = payload.get("paper_id")
            else:
                query = str(payload or "")
                paper_id = None

            answer = self.paper_tool.ask(query, paper_id=paper_id)
            answer = answer.strip() or "未返回内容。"
            self._push_history("assistant", answer)
            self.reply_queue.put(("ok", answer))
            self._adjust_mood(self._sentiment_delta(answer, strength=0.6), reason="assistant_chat")
        except Exception as e:
            reason_text = self._format_error_reason(str(e))
            self.reply_queue.put(("tip", f"论文助手调用失败：{reason_text}"))

    def _wrap_nanobot_prompt(self, user_text, mode="auto"):
        directive = self._build_nanobot_directive(mode=mode)
        if not directive:
            return user_text
        return f"{directive}\n\n用户请求：{user_text}"

    def _build_nanobot_directive(self, mode="auto"):
        cfg = self.settings.get("nanobot", {})
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
            pieces.append("输出格式建议：\n- 结论（1-3 句）\n- 通俗解释（关键概念）\n- 可操作步骤（如有）\n- 参考来源：\n  1) <标题> - <URL>\n  2) <标题> - <URL>")
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

    def _prefill_input(self, text):
        self.open_chat_window()
        self.chat_input_var.set(text)

    def prefill_bio_prompt(self):
        self._prefill_input("/bio 用 fastqc 检查这个测序文件的质量：<file_path>")

    def prefill_crawl_prompt(self):
        self._prefill_input("/db 从 <database_or_url> 抓取 <keyword> 相关条目，并给出下载链接")

    def _parse_nanobot_mode(self, text: str):
        raw = (text or "").strip()
        if not raw:
            return "auto", raw
        lower = raw.lower()
        prefixes = {
            "/bio": "bio",
            "bio:": "bio",
            "生信:": "bio",
            "生信": "bio",
            "/crawl": "crawl",
            "/db": "crawl",
            "crawl:": "crawl",
            "爬数:": "crawl",
            "数据库:": "crawl",
        }
        for key, mode in prefixes.items():
            if lower.startswith(key):
                return mode, raw[len(key):].lstrip(" ：:")
        return "auto", raw

    def _parse_paper_command(self, text: str):
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

    # ── iGEM 助手命令路由 ──

    def _parse_igem_command(self, text: str) -> bool:
        """解析并路由 iGEM 助手相关命令。返回是否已处理。"""
        igem_cfg = self.settings.get("igem_assistant", {})
        if not igem_cfg.get("enabled", True):
            return False

        # /meeting /mt 命令
        mt_mode, mt_payload = MeetingTracker.parse_meeting_command(text)
        if mt_mode:
            threading.Thread(target=self._handle_meeting_command, args=(mt_mode, mt_payload), daemon=True).start()
            return True

        # /flow 命令
        flow_mode, flow_payload = BioWorkflowGuide.parse_flow_command(text)
        if flow_mode:
            threading.Thread(target=self._handle_flow_command, args=(flow_mode, flow_payload), daemon=True).start()
            return True

        # /doc 命令
        doc_mode, doc_payload = DocHub.parse_doc_command(text)
        if doc_mode:
            threading.Thread(target=self._handle_doc_command, args=(doc_mode, doc_payload), daemon=True).start()
            return True

        # /task 命令
        task_mode, task_payload = TaskBoard.parse_task_command(text)
        if task_mode:
            threading.Thread(target=self._handle_task_command, args=(task_mode, task_payload), daemon=True).start()
            return True

        # /team 命令
        team_mode, team_payload = TaskBoard.parse_team_command(text)
        if team_mode:
            threading.Thread(target=self._handle_team_command, args=(team_mode, team_payload), daemon=True).start()
            return True

        # 自然语言匹配生信工作流
        wf_type = self.bio_workflow.match_workflow(text)
        if wf_type:
            threading.Thread(target=self._start_workflow_by_type, args=(wf_type,), daemon=True).start()
            return True

        return False

    def _handle_meeting_command(self, mode, payload):
        """处理会议命令。"""
        try:
            if mode == "list":
                meetings = self.meeting_tracker.get_recent_meetings(5)
                if not meetings:
                    self.reply_queue.put(("ok", "还没有会议记录哦。可以用 /meeting 记录 [内容] 来添加。"))
                else:
                    lines = []
                    for m in meetings:
                        lines.append(self.meeting_tracker.format_meeting_text(m))
                        lines.append("")
                    self.reply_queue.put(("ok", "\n".join(lines).strip()))
            elif mode == "record":
                # 打开记录组会窗口
                self.root.after(0, lambda: self._open_meeting_record_window(payload.get("raw_notes", "")))
            elif mode == "summarize":
                mid = payload.get("meeting_id", "")
                if mid:
                    summary = self.meeting_tracker.summarize_meeting(mid)
                    if summary and not summary.get("error"):
                        meeting = self.meeting_tracker._find_by_id(mid)
                        text = self.meeting_tracker.format_meeting_text(meeting) if meeting else str(summary)
                        self.reply_queue.put(("ok", f"会议摘要已生成：\n{text}"))
                        # 推送飞书
                        if igem_cfg.get("meeting_auto_push_feishu") and self.feishu:
                            self.feishu.notify_event("meeting_summary", text)
                    else:
                        self.reply_queue.put(("ok", "摘要生成失败，请确认会议ID是否正确。"))
                else:
                    # 没有指定ID，总结最近一条
                    meetings = self.meeting_tracker.get_recent_meetings(1)
                    if meetings:
                        summary = self.meeting_tracker.summarize_meeting(meetings[0]["id"])
                        if summary and not summary.get("error"):
                            text = self.meeting_tracker.format_meeting_text(meetings[0])
                            self.reply_queue.put(("ok", f"最近会议摘要：\n{text}"))
                    else:
                        self.reply_queue.put(("ok", "没有会议可以总结。"))
            elif mode == "query":
                query = payload.get("query", "")
                results = self.meeting_tracker.query_meetings(query)
                if not results:
                    self.reply_queue.put(("ok", f"没有找到与「{query}」相关的会议记录。"))
                else:
                    lines = [self.meeting_tracker.format_meeting_text(m) for m in results[:3]]
                    self.reply_queue.put(("ok", "\n\n---\n\n".join(lines)))
            elif mode == "progress":
                keyword = payload.get("keyword", "")
                results = self.meeting_tracker.find_task_progress(keyword)
                if not results:
                    self.reply_queue.put(("ok", f"没有找到「{keyword}」的进展记录。"))
                else:
                    lines = []
                    for r in results:
                        items_text = "；".join(f"[{i['category']}] {i['content'][:50]}" for i in r["matched"][:5])
                        lines.append(f"[{r['date']}] {r['title']}：{items_text}")
                    self.reply_queue.put(("ok", f"「{keyword}」的进展追踪：\n" + "\n".join(lines)))
        except Exception as e:
            self.reply_queue.put(("ok", f"会议命令执行出错：{e}"))
        finally:
            self.busy = False
            self._set_send_enabled(True)

    def _handle_flow_command(self, mode, payload):
        """处理生信工作流命令。"""
        try:
            if mode == "list":
                workflows = self.bio_workflow.list_workflows()
                lines = [f"{w['display_name']}（触发词：{', '.join(w['trigger_keywords'][:3])}）" for w in workflows]
                self.reply_queue.put(("ok", "可用生信工作流：\n" + "\n".join(lines)))
            elif mode == "cancel":
                if self._active_workflow_session_id:
                    self.bio_workflow.cancel_session(self._active_workflow_session_id)
                    self._active_workflow_session_id = None
                    self.reply_queue.put(("ok", "已取消当前工作流。"))
                else:
                    self.reply_queue.put(("ok", "当前没有活跃的工作流。"))
            elif mode == "start":
                wf_type = payload.get("workflow_type", "")
                self._start_workflow_by_type(wf_type)
        except Exception as e:
            self.reply_queue.put(("ok", f"工作流命令执行出错：{e}"))
            self._active_workflow_session_id = None
            self.busy = False
            self._set_send_enabled(True)

    def _start_workflow_by_type(self, wf_type: str):
        """启动指定类型的工作流。"""
        try:
            result = self.bio_workflow.start_session(wf_type)
            if result:
                self._active_workflow_session_id = result["session_id"]
                choices_str = ""
                if result.get("choices"):
                    choices_str = f"（选项：{', '.join(result['choices'])}）"
                self.reply_queue.put(("ok",
                    f"已启动工作流：{result['display_name']}\n"
                    f"步骤 {result['step']}/{result['total_steps']}：{result['prompt']}{choices_str}\n"
                    f"（输入 /flow cancel 可随时取消）"))
            else:
                self.reply_queue.put(("ok", f"未找到工作流「{wf_type}」，输入 /flow list 查看可用工作流。"))
                self.busy = False
                self._set_send_enabled(True)
        except Exception as e:
            self.reply_queue.put(("ok", f"启动工作流出错：{e}"))
            self.busy = False
            self._set_send_enabled(True)

    def _handle_workflow_input(self, text: str):
        """处理活跃工作流会话中的用户输入。"""
        try:
            session_id = self._active_workflow_session_id
            if not session_id:
                self.busy = False
                self._set_send_enabled(True)
                return

            result = self.bio_workflow.advance_session(session_id, text)
            if not result:
                self._active_workflow_session_id = None
                self.reply_queue.put(("ok", "工作流会话已失效。"))
                self.busy = False
                self._set_send_enabled(True)
                return

            if result.get("input_type") == "execute":
                # 收集完毕，执行
                exec_result = self.bio_workflow.execute_workflow(session_id)
                if exec_result and exec_result.get("success"):
                    # 用 LLM 解读结果
                    explanation = self.bio_workflow.explain_result(session_id)
                    reply = f"执行完成！\n结果：{exec_result.get('result', '')[:500]}"
                    if explanation:
                        reply += f"\n\n--- AI解读 ---\n{explanation}"
                    self.reply_queue.put(("ok", reply))
                else:
                    err = exec_result.get("error", "") if exec_result else "未知错误"
                    self.reply_queue.put(("ok", f"执行失败：{err}"))
                self._active_workflow_session_id = None
            else:
                # 继续引导下一步
                choices_str = ""
                if result.get("choices"):
                    choices_str = f"（选项：{', '.join(result['choices'])}）"
                self.reply_queue.put(("ok",
                    f"[{result['display_name']}] 步骤 {result['step']}/{result['total_steps']}："
                    f"{result['prompt']}{choices_str}"))
        except Exception as e:
            self.reply_queue.put(("ok", f"工作流执行出错：{e}"))
            self._active_workflow_session_id = None
        finally:
            if not self._active_workflow_session_id:
                self.busy = False
                self._set_send_enabled(True)

    def _handle_doc_command(self, mode, payload):
        """处理文档中心命令。"""
        try:
            if mode == "list":
                cats = self.doc_hub.get_all_categories_with_count()
                lines = [f"{c['icon']} {c['label']}：{c['count']}篇" for c in cats if c['count'] > 0]
                if not lines:
                    self.reply_queue.put(("ok", "文档中心还没有文档。可以用 /doc 添加 [路径] 来添加，或设置监视文件夹自动索引。"))
                else:
                    self.reply_queue.put(("ok", "文档分类：\n" + "\n".join(lines)))
            elif mode == "search":
                query = payload.get("query", "")
                results = self.doc_hub.search(query)
                if not results:
                    self.reply_queue.put(("ok", f"没有找到与「{query}」相关的文档。"))
                else:
                    lines = [self.doc_hub.format_doc_text(d) for d in results[:5]]
                    self.reply_queue.put(("ok", "\n".join(lines)))
            elif mode == "add":
                path = payload.get("path", "")
                category = payload.get("category", "other")
                if path and os.path.isfile(path):
                    doc = self.doc_hub.add_document(path, category)
                    if doc:
                        self.reply_queue.put(("ok", f"已添加文档：{doc['title']}（分类：{category}）"))
                    else:
                        self.reply_queue.put(("ok", "添加文档失败。"))
                else:
                    self.reply_queue.put(("ok", "请提供有效的文件路径。用法：/doc 添加 [路径] [分类]"))
            elif mode == "watch":
                path = payload.get("path", "")
                if path and os.path.isdir(path):
                    if self.doc_hub.add_watch_folder(path):
                        self.reply_queue.put(("ok", f"已添加监视文件夹：{path}"))
                    else:
                        self.reply_queue.put(("ok", "该文件夹已在监视列表中。"))
                else:
                    self.reply_queue.put(("ok", "请提供有效的文件夹路径。用法：/doc 监视 [路径]"))
            elif mode == "scan":
                stats = self.doc_hub.scan_watch_folders()
                self.reply_queue.put(("ok",
                    f"扫描完成：扫描 {stats['scanned']} 个文件，新增 {stats['added']} 篇，更新 {stats['updated']} 篇。"))
        except Exception as e:
            self.reply_queue.put(("ok", f"文档命令执行出错：{e}"))
        finally:
            self.busy = False
            self._set_send_enabled(True)

    def _handle_task_command(self, mode, payload):
        """处理任务命令。"""
        try:
            if mode == "board":
                board = self.task_board.get_board_view()
                lines = []
                status_labels = {"todo": "📋 待办", "in_progress": "🔨 进行中", "done": "✅ 已完成", "blocked": "🚧 卡住"}
                for status, label in status_labels.items():
                    tasks = board.get(status, [])
                    if tasks:
                        lines.append(f"\n{label}（{len(tasks)}）")
                        for t in tasks[:8]:
                            assignee = ""
                            if t.get("assignee_id"):
                                m = self.task_board._find_member_by_id(t["assignee_id"])
                                if m:
                                    assignee = f" → {m['name']}"
                            deadline_str = f" 截止:{t['deadline']}" if t.get("deadline") else ""
                            lines.append(f"  · {t['title']}{assignee}{deadline_str}")
                if not lines:
                    self.reply_queue.put(("ok", "任务看板是空的。用 /task 添加 [任务名] 来创建任务。"))
                else:
                    self.reply_queue.put(("ok", "任务看板：" + "\n".join(lines)))
            elif mode == "add":
                title = payload.get("title", "")
                if title:
                    task = self.task_board.add_task(title)
                    self.reply_queue.put(("ok", f"已创建任务：{title}（ID: {task['id']}）"))
                else:
                    self.reply_queue.put(("ok", "请输入任务名称。用法：/task 添加 [任务名]"))
            elif mode == "update":
                task_id = payload.get("task_id", "")
                updates = payload.get("updates", {})
                if task_id:
                    result = self.task_board.update_task(task_id, updates)
                    if result:
                        self.reply_queue.put(("ok", f"任务已更新：{result['title']} → {updates.get('status', '已修改')}"))
                    else:
                        self.reply_queue.put(("ok", f"未找到任务 {task_id}。"))
                else:
                    self.reply_queue.put(("ok", "请指定任务ID。用法：/task 完成 [ID]"))
            elif mode == "find":
                keyword = payload.get("keyword", "")
                results = self.task_board.find_by_task(keyword)
                if not results:
                    self.reply_queue.put(("ok", f"没有找到与「{keyword}」相关的任务。"))
                else:
                    lines = [self.task_board.format_task_text(t) for t in results[:5]]
                    self.reply_queue.put(("ok", "\n\n".join(lines)))
        except Exception as e:
            self.reply_queue.put(("ok", f"任务命令执行出错：{e}"))
        finally:
            self.busy = False
            self._set_send_enabled(True)

    def _handle_team_command(self, mode, payload):
        """处理团队命令。"""
        try:
            if mode == "list":
                members = self.task_board.get_all_members()
                if not members:
                    self.reply_queue.put(("ok", "团队还没有成员。用 /team 添加 [名字] [角色] 来添加。"))
                else:
                    lines = [self.task_board.format_member_text(m) for m in members]
                    self.reply_queue.put(("ok", "团队成员：\n" + "\n\n".join(lines)))
            elif mode == "add":
                name = payload.get("name", "")
                if name:
                    member = self.task_board.add_member(
                        name, payload.get("role", ""), payload.get("skills", []),
                        payload.get("contact", ""),
                    )
                    if member:
                        self.reply_queue.put(("ok", f"已添加成员：{name}（角色：{payload.get('role', '未指定')}）"))
                    else:
                        self.reply_queue.put(("ok", f"成员「{name}」已存在。"))
                else:
                    self.reply_queue.put(("ok", "请输入成员名称。用法：/team 添加 [名字] [角色] [技能]"))
            elif mode == "find":
                keyword = payload.get("keyword", "")
                # 先按角色找，再按技能找
                by_role = self.task_board.find_by_role(keyword)
                by_skill = self.task_board.find_by_skill(keyword)
                all_found = []
                seen_ids = set()
                for m in by_role + by_skill:
                    if m["id"] not in seen_ids:
                        seen_ids.add(m["id"])
                        all_found.append(m)
                if not all_found:
                    self.reply_queue.put(("ok", f"没有找到角色或技能匹配「{keyword}」的成员。"))
                else:
                    lines = [self.task_board.format_member_text(m) for m in all_found]
                    self.reply_queue.put(("ok", "\n\n".join(lines)))
        except Exception as e:
            self.reply_queue.put(("ok", f"团队命令执行出错：{e}"))
        finally:
            self.busy = False
            self._set_send_enabled(True)

    def _request_screen_roast(self, screen_text):
        try:
            prompt = (
                "请根据我正在看的屏幕内容，给一句轻吐槽。"
                "要求：中文、俏皮但不刻薄、不要攻击性、不超过28字。\n\n"
                f"屏幕内容：{screen_text}"
            )
            messages = [
                {"role": "system", "content": self._compose_system_prompt() + "\n你会做简短、友好的屏幕吐槽。"},
                {"role": "user", "content": prompt},
            ]
            gen = self._get_generation_params()
            answer = self._chat_completion(
                messages,
                temperature=min(0.95, gen["temperature"] + 0.05),
                max_tokens=min(120, gen["max_tokens"]),
                timeout=30,
            )
            answer = re.sub(r"\s+", " ", answer).strip()
            if len(answer) > 48:
                answer = answer[:48] + "…"
            self._push_history("assistant", f"[屏幕吐槽] {answer}")
            self._post_auto_event("screen_roast", answer, priority=6, cooldown_seconds=90, speak=True)
        except Exception as e:
            if not self.screen_roast_warned:
                self.screen_roast_warned = True
                self.reply_queue.put(("tip", f"屏幕吐槽暂不可用：{e}"))

    def _request_screen_comment(self, screen_text):
        try:
            # 如果配置了云视觉服务且启用，则先发送截图获取视觉描述
            try:
                vcfg = self.settings.get("vision", {})
            except Exception:
                vcfg = {}

            vision_text = ""
            img = None
            try:
                img = ImageGrab.grab()
            except Exception:
                img = None

            if vcfg.get("enabled", False) and img is not None:
                try:
                    resp = self._call_vision_api(img, vcfg)
                    if resp:
                        vision_text = resp
                except Exception:
                    vision_text = ""

            # detect interactive regions via local OCR if enabled
            regions = []
            try:
                scfg = self.settings.get("screen_comment", {})
                if scfg.get("highlight", False) and img is not None:
                    regions = self._detect_interactive_regions(img, scfg)
            except Exception:
                regions = []

            combined_screen_text = screen_text
            if vision_text:
                # vision_text may be dict with regions
                if isinstance(vision_text, dict):
                    vtext = vision_text.get("text", "")
                    regions_v = vision_text.get("regions", [])
                    combined_screen_text = f"视觉检测结果：{vtext}\n屏幕OCR：{screen_text}"
                    # convert regions_v into region entries
                    for rv in regions_v[:8]:
                        try:
                            l = rv.get("left")
                            t = rv.get("top")
                            w = rv.get("width")
                            h = rv.get("height")
                            lab = rv.get("label", "")
                            if None not in (l, t, w, h):
                                region_summary = f"[{int(l)},{int(t)},{int(w)},{int(h)}] {lab}"
                                combined_screen_text += "\n" + region_summary
                                regions.append({"text": lab, "left": int(l), "top": int(t), "width": int(w), "height": int(h), "conf": float(rv.get("score",0.0))})
                        except Exception:
                            continue
                else:
                    combined_screen_text = f"视觉检测结果：{vision_text}\n屏幕OCR：{screen_text}"
            # append region summary for assistant prompt
            region_summary = ""
            if regions:
                lines = []
                for r in regions:
                    lines.append(f"[{r.get('left')},{r.get('top')},{r.get('width')},{r.get('height')}] {r.get('text')}")
                region_summary = "\n可交互区域建议：\n" + "\n".join(lines[:8])
                combined_screen_text = combined_screen_text + region_summary

            prompt = (
                "请扮演一个负责理解和分析屏幕内容的助手，给出详细分析与可执行建议。"
                "要求：中文，多段说明，包括（1）对屏幕要点的简要概述（不要逐字复述原始文本）；（2）可能有问题或异常的地方；（3）实用建议或下一步操作（例如点击、复制、检查哪个区域）；（4）一行简短的 TL;DR，总结重点。"
                "禁止复述：不要逐字复述或复制屏幕OCR/视觉检测的原始文本，也不要把屏幕内容完整粘贴到回复中。若确需引用，仅摘录不超过20个字符并用引号标注。"
                "不要包含侮辱或敏感语言。请尽量标注明显的可交互区域或按钮文本。\n\n"
                f"屏幕内容：{combined_screen_text}"
            )
            messages = [
                {"role": "system", "content": self._compose_system_prompt() + "\n你会做详细、建设性的屏幕分析和建议。"},
                {"role": "user", "content": prompt},
            ]
            gen = self._get_generation_params()
            answer = self._chat_completion(
                messages,
                temperature=min(0.9, gen.get("temperature", 0.7) + 0.05),
                max_tokens=min(800, int(gen.get("max_tokens", 400)) * 2),
                timeout=60,
            )
            answer = re.sub(r"\s+", " ", answer).strip()
            # if regions were detected and highlighting is enabled, save highlighted screenshot and notify user
            try:
                scfg = self.settings.get("screen_comment", {})
                if scfg.get("highlight", False) and img is not None and regions:
                    saved = self._save_highlight_image(img.copy(), regions)
                    if saved:
                        answer = answer + f"\n\n（已保存高亮截图：{saved}）"
                        # also push a tip so user sees path
                        self.reply_queue.put(("tip", f"已保存屏幕高亮截图：{saved}"))
            except Exception:
                pass

            self._push_history("assistant", f"[屏幕解析] {answer}")
            self._post_auto_event("screen_comment", answer, priority=7, cooldown_seconds=120, speak=True)
        except Exception as e:
            if not self.screen_comment_warned:
                self.screen_comment_warned = True
                self.reply_queue.put(("tip", f"屏幕解析暂不可用：{e}"))

    def _poll_reply_queue(self):
        try:
            while True:
                try:
                    status, payload = self.reply_queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    if status in ("ok", "auto"):
                        self.last_response_text = payload
                        self.show_lines(self._split_sentences(payload))
                        speak_source = "assistant" if status == "ok" else "auto"
                        self._speak_async(payload, source=speak_source)
                        # 如果是飞书消息的回复，发送到飞书
                        if self._pending_feishu_reply and self.feishu:
                            self.feishu.send_to_feishu(self._pending_feishu_reply, payload)
                            self._pending_feishu_reply = ""
                    elif status == "auto_event":
                        event_payload = payload if isinstance(payload, dict) else {}
                        text = str(event_payload.get("text", "")).strip()
                        if text:
                            self.last_response_text = text
                            self.show_lines(self._split_sentences(text))
                            if bool(event_payload.get("speak", True)):
                                self._speak_async(text, source="auto")
                    elif status == "stream":
                        self.last_response_text = payload
                        self.show_lines([payload])
                    elif status == "tip":
                        self.show_lines([payload])
                    elif status == "music_ok":
                        payload_dict = payload if isinstance(payload, dict) else {}
                        track_text = payload_dict.get("track", "")
                        comment = payload_dict.get("comment", "")
                        merged = f"[听歌] {track_text}｜{comment}".strip("｜")
                        if merged:
                            self._push_history("assistant", merged)
                        if (not self.busy) and (not self.sleep_mode) and (not self.game_mode):
                            lines = []
                            if track_text:
                                lines.append(f"♪ 正在播放：{track_text}")
                            if comment:
                                lines.append(comment)
                            if lines:
                                self.last_response_text = "\n".join(lines)
                                self.show_lines(lines)
                                self._speak_async(comment, source="auto")
                    elif status == "music_err":
                        pass
                    elif status == "feishu_msg":
                        # 飞书消息：将消息作为用户输入处理
                        msg_data = payload if isinstance(payload, dict) else {}
                        feishu_text = str(msg_data.get("text", "")).strip()
                        feishu_chat_id = msg_data.get("chat_id", "")
                        if feishu_text:
                            self.show_lines([f"[飞书] {feishu_text[:30]}"])
                            # 设置飞书回复标记
                            self._pending_feishu_reply = feishu_chat_id
                            self.send_user_message(direct_text=feishu_text)
                    else:
                        self.show_lines([f"请求失败：{payload}"])
                        self.busy = False
                        self._set_send_enabled(True)
                except Exception as e:
                    self.busy = False
                    self._set_send_enabled(True)
                    self.show_lines([f"消息处理异常：{e}"])
        finally:
            if self.root.winfo_exists():
                self.root.after(120, self._poll_reply_queue)

    def _split_sentences(self, text):
        text = text.replace("\r", "").strip()
        parts = re.split(r"(?<=[。！？!?\n])", text)
        out = [p.strip() for p in parts if p.strip() and p.strip() != "\n"]
        return out or [text]

    def show_lines(self, lines):
        self._cancel_dialogue_jobs()
        self.current_sentences = lines
        self.sentence_index = 0
        self.char_index = 0
        self.skip_cooldown_until = 0
        self.is_typing = True
        self._show_bubble()
        self._start_mouth_anim()
        self._type_current_sentence()

    def _show_bubble(self):
        self.message_box_visible = True
        self.canvas.itemconfig(self.bubble_bg_id, state="normal", fill="#fff8f2", outline="#e7d7c9")
        self.canvas.itemconfig(self.bubble_text_id, state="normal", fill="#3b2f2f", text="")

    def _hide_bubble(self):
        self.message_box_visible = False
        self.canvas.itemconfig(self.bubble_bg_id, state="hidden")
        self.canvas.itemconfig(self.bubble_text_id, state="hidden", text="")

    def _type_current_sentence(self):
        if self.sentence_index >= len(self.current_sentences):
            self._finish_dialogue_flow()
            return
        sentence = self.current_sentences[self.sentence_index]
        if self.char_index > len(sentence):
            self.is_typing = False
            self.skip_cooldown_until = time.time() + 0.5
            hold = 2000 if self.sentence_index < len(self.current_sentences) - 1 else 3000
            self.wait_next_after_id = self.root.after(hold, self._next_sentence_or_end)
            return
        self.canvas.itemconfig(self.bubble_text_id, text=sentence[: self.char_index])
        self.char_index += 1
        self.typing_after_id = self.root.after(28, self._type_current_sentence)

    def _next_sentence_or_end(self):
        self.sentence_index += 1
        self.char_index = 0
        self.is_typing = True
        if self.sentence_index >= len(self.current_sentences):
            self._finish_dialogue_flow()
            return
        self._type_current_sentence()

    def _finish_dialogue_flow(self):
        self.is_typing = False
        self._stop_mouth_anim()
        self.fade_after_id = self.root.after(100, lambda: self._fade_bubble(1.0))
        self.busy = False
        self._set_send_enabled(True)

    def _fade_bubble(self, alpha):
        if alpha <= 0:
            self._hide_bubble()
            return
        def blend(c1, c2, a):
            return int(c1 * a + c2 * (1 - a))
        bg_from = (255, 248, 242)
        bg_to = (0, 255, 0)
        tx_from = (59, 47, 47)
        tx_to = (0, 255, 0)
        bg = tuple(blend(bg_from[i], bg_to[i], alpha) for i in range(3))
        tx = tuple(blend(tx_from[i], tx_to[i], alpha) for i in range(3))
        self.canvas.itemconfig(self.bubble_bg_id, fill=f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}", outline=f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}")
        self.canvas.itemconfig(self.bubble_text_id, fill=f"#{tx[0]:02x}{tx[1]:02x}{tx[2]:02x}")
        self.fade_after_id = self.root.after(45, lambda: self._fade_bubble(alpha - 0.08))

    def _start_mouth_anim(self):
        self._cancel_blink_now()
        self._stop_mouth_anim()
        self.mouth_open = False
        self._mouth_tick()

    def _mouth_tick(self):
        if not self.is_typing:
            self._apply_idle_image()
            return
        # talk 槽位按帧轮播，配置多个文件即可做逐帧说话动画
        frame = self._pick_animation_frame("talk")
        self.canvas.itemconfig(self.pet_img_id, image=frame)
        self.mouth_anim_after_id = self.root.after(170, self._mouth_tick)

    def _stop_mouth_anim(self):
        if self.mouth_anim_after_id:
            self.root.after_cancel(self.mouth_anim_after_id)
            self.mouth_anim_after_id = None
        self._apply_idle_image()
        self._schedule_next_blink()

    def _apply_idle_image(self):
        if self.system_overheat_mode:
            if self.system_face_mode == "faint":
                self.canvas.itemconfig(self.pet_img_id, image=self.faint_img)
            else:
                self.canvas.itemconfig(self.pet_img_id, image=self.sweat_img)
            return
        if self.sleep_mode:
            self.canvas.itemconfig(self.pet_img_id, image=self.sleep_img)
        else:
            frame = self._pick_animation_frame("idle")
            self.canvas.itemconfig(self.pet_img_id, image=frame)

    def _schedule_next_blink(self):
        if self.idle_blink_after_id:
            self.root.after_cancel(self.idle_blink_after_id)
            self.idle_blink_after_id = None
        self.idle_blink_after_id = self.root.after(3800 + int((time.time() * 1000) % 2600), self._do_blink)

    def _do_blink(self):
        self.idle_blink_after_id = None
        if self.is_typing or self.sleep_mode:
            self._schedule_next_blink()
            return
        self.is_blinking = True
        frame = self._pick_animation_frame("blink")
        self.canvas.itemconfig(self.pet_img_id, image=frame)
        self.blink_recover_after_id = self.root.after(170, self._recover_from_blink)

    def _recover_from_blink(self):
        self.blink_recover_after_id = None
        self.is_blinking = False
        self._apply_idle_image()
        self._schedule_next_blink()

    def _cancel_blink_now(self):
        if self.idle_blink_after_id:
            self.root.after_cancel(self.idle_blink_after_id)
            self.idle_blink_after_id = None
        if self.blink_recover_after_id:
            self.root.after_cancel(self.blink_recover_after_id)
            self.blink_recover_after_id = None

    def _cancel_dialogue_jobs(self):
        for attr in ("typing_after_id", "wait_next_after_id", "fade_after_id"):
            aid = getattr(self, attr)
            if aid:
                self.root.after_cancel(aid)
                setattr(self, attr, None)

    def _on_dialogue_click(self, _event):
        self.last_input_time = time.time()

        if self.clip_prompt_visible and self._point_in_item(self.clip_hint_bg_id, _event.x, _event.y):
            self._on_clip_hint_click()
            return

        self.state["click_count"] = int(self.state.get("click_count", 0)) + 1
        if self.state["click_count"] in (30, 60):
            self.show_lines(["你一直在戳我欸……不过有点开心。"])

        if self.is_typing:
            sentence = self.current_sentences[self.sentence_index]
            self.char_index = len(sentence) + 1
            self.canvas.itemconfig(self.bubble_text_id, text=sentence)
            return
        if time.time() < self.skip_cooldown_until:
            return
        if self.wait_next_after_id:
            self.root.after_cancel(self.wait_next_after_id)
            self.wait_next_after_id = None
        if self.sentence_index < len(self.current_sentences) - 1:
            self._next_sentence_or_end()

    def _on_drag_start(self, event):
        self.last_input_time = time.time()
        self.drag_start = (event.x_root, event.y_root)
        if self.fall_after_id:
            self.root.after_cancel(self.fall_after_id)
            self.fall_after_id = None

    def _on_drag_move(self, event):
        dx = event.x_root - self.drag_start[0]
        dy = event.y_root - self.drag_start[1]
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")
        self.drag_start = (event.x_root, event.y_root)

    def _on_drag_release(self, _event):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = self.root.winfo_width()
        wh = self.root.winfo_height()
        x = self.root.winfo_x()
        y = self.root.winfo_y()

        if x < 50:
            self.root.geometry(f"+0+{max(0, y)}")
            self.canvas.itemconfig(self.pet_img_id, image=self.edge_img)
            return
        if x + ww > sw - 50:
            self.root.geometry(f"+{sw - ww}+{max(0, y)}")
            self.canvas.itemconfig(self.pet_img_id, image=self.edge_img)
            return

        target_y = sh - wh - 40

        def fall_step():
            cur_y = self.root.winfo_y()
            if cur_y >= target_y:
                self.root.geometry(f"+{self.root.winfo_x()}+{target_y}")
                self._apply_idle_image()
                return
            self.root.geometry(f"+{self.root.winfo_x()}+{cur_y + 18}")
            self.fall_after_id = self.root.after(16, fall_step)

        fall_step()

    def _is_pointer_on_pet(self, event):
        bbox = self.canvas.bbox(self.pet_img_id)
        if not bbox:
            return False
        x1, y1, x2, y2 = bbox
        return x1 <= event.x <= x2 and y1 <= event.y <= y2

    def _on_pet_hover(self, event):
        if not self._is_pointer_on_pet(event):
            return
        now_ts = time.time()
        if now_ts - self.last_hover_affinity_ts < 2.0:
            return
        self.last_hover_affinity_ts = now_ts
        self._increase_affinity(1, reason="抚摸")

    def _on_pet_click(self, event):
        if not self._is_pointer_on_pet(event):
            return
        now_ts = time.time()
        if now_ts - self.last_click_affinity_ts < 0.8:
            return
        self.last_click_affinity_ts = now_ts
        self._increase_affinity(2, reason="抚摸")

    def _current_affinity_level(self):
        value = int(self.state.get("affinity", 0))
        level = 0
        for threshold in AFFINITY_THRESHOLDS:
            if value >= threshold:
                level = threshold
        return level

    def _refresh_affinity_ui(self):
        value = int(self.state.get("affinity", 0))
        value = max(AFFINITY_MIN, min(AFFINITY_MAX, value))
        self.affinity_var.set(value)
        level = self._current_affinity_level()
        level_name = AFFINITY_LEVEL_LABELS.get(level, "陌生")
        self.affinity_text_var.set(f"❤ {value}/{AFFINITY_MAX} {level_name}")

    def _check_affinity_unlocks(self, old_value, new_value):
        unlocked = set(int(x) for x in self.state.get("affinity_unlocked", []) if isinstance(x, int) or str(x).isdigit())
        newly = []
        for threshold in AFFINITY_THRESHOLDS:
            if threshold <= 0:
                continue
            if old_value < threshold <= new_value and threshold not in unlocked:
                unlocked.add(threshold)
                newly.append(threshold)
        if not newly:
            return
        self.state["affinity_unlocked"] = sorted(unlocked)
        messages = []
        for threshold in newly:
            level_name = AFFINITY_LEVEL_LABELS.get(threshold, "新阶段")
            hidden = random.choice(HIDDEN_DIALOGUES.get(threshold, ["隐藏对话已解锁。"]))
            messages.append(f"好感达到 {threshold}，已解锁【{level_name}】隐藏对话。")
            messages.append(hidden)
        self.show_lines(messages)

    def _increase_affinity(self, val, reason=""):
        global affinity
        old_value = self.affinity_mgr.value
        self.affinity_mgr.increase(val, reason)
        affinity = self.affinity_mgr.value
        self._refresh_affinity_ui()
        self._check_affinity_unlocks(old_value, affinity)
        if val > 0:
            self._adjust_mood(min(3, val), reason="affinity_up")
        elif val < 0:
            self._adjust_mood(max(-3, val), reason="affinity_down")

    def _add_coins(self, val):
        self.economy_mgr.add_coins(val)
        self.coin_var.set(f"金币: {self.state['coins']}")

    def _set_mood(self, mood):
        mood = str(mood).title()
        mapping = {"Excited": 70, "Happy": 45, "Normal": 0, "Sad": -45, "Angry": -70}
        if mood not in mapping:
            mood = "Normal"
        self.state["mood"] = mood
        self.state["emotion_value"] = mapping[mood]
        self.state["mood_score"] = mapping[mood]
        self._refresh_status_display()

    def open_history_window(self):
        win = tk.Toplevel(self.root)
        win.title("历史记录")
        win.geometry("760x500")

        outer = ttk.Frame(win)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def refresh_rows():
            for child in inner.winfo_children():
                child.destroy()
            for idx, item in enumerate(self.history):
                row = ttk.Frame(inner)
                row.pack(fill="x", padx=8, pady=4)
                text = f"[{item.get('time', '')}] {item.get('role', '')}: {item.get('content', '').replace(chr(10), ' ')[:95]}"
                ttk.Label(row, text=text).pack(side="left", fill="x", expand=True)
                ttk.Button(row, text="删除", command=lambda i=idx: delete_one(i)).pack(side="right")

        def delete_one(index):
            if 0 <= index < len(self.history):
                self.history.pop(index)
                self._save_json(HISTORY_PATH, self.history)
                refresh_rows()

        refresh_rows()

    def open_readme_window(self):
        win = tk.Toplevel(self.root)
        win.title("帮助 / 说明")
        win.geometry("760x500")
        text = tk.Text(win, wrap="word", font=("Microsoft YaHei UI", 10))
        text.pack(fill="both", expand=True)
        content = README_PATH.read_text(encoding="utf-8", errors="ignore") if README_PATH.exists() else "未找到 readme.md"
        text.insert("1.0", content)
        text.config(state="disabled")

    def open_prompt_editor(self):
        win = tk.Toplevel(self.root)
        win.title("修改人设 Prompt")
        win.geometry("720x460")
        txt = tk.Text(win, wrap="word", font=("Microsoft YaHei UI", 10))
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("1.0", self.settings.get("system_prompt", ""))

        def save_prompt():
            self.settings["system_prompt"] = txt.get("1.0", "end").strip()
            self._save_json(SETTINGS_PATH, self.settings)
            self.history = self.history[-4:]
            self._save_json(HISTORY_PATH, self.history)
            self.show_lines(["人设已更新并重置短期上下文。"])
            win.destroy()

        ttk.Button(win, text="保存", command=save_prompt).pack(pady=(0, 8))

    def open_profile_editor(self):
        win = tk.Toplevel(self.root)
        win.title("御主档案")
        win.geometry("420x300")
        fields = [("nickname", "昵称"), ("birthday", "生日"), ("oc_call", "对OC称呼"), ("relationship", "关系设定")]
        entries = {}
        profile = self.settings.get("profile", {})

        for i, (key, label) in enumerate(fields):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=10, pady=8)
            var = tk.StringVar(value=profile.get(key, ""))
            ttk.Entry(win, textvariable=var, width=34).grid(row=i, column=1, padx=10, pady=8)
            entries[key] = var

        def save_profile():
            self.settings["profile"] = {k: v.get().strip() for k, v in entries.items()}
            self._save_json(SETTINGS_PATH, self.settings)
            self.show_lines(["御主档案已保存。"])
            win.destroy()

        ttk.Button(win, text="保存", command=save_profile).grid(row=len(fields), column=0, columnspan=2, pady=14)

    def _hourly_chime(self):
        now = datetime.now()
        slot = "深夜" if now.hour < 6 else "清晨" if now.hour < 9 else "白天" if now.hour < 18 else "晚上"
        chime_text = f"现在是{now.hour:02d}:00，{slot}时间到啦。"
        self.show_lines([chime_text])
        self.canvas.itemconfig(self.pet_img_id, image=self.open_img)
        try:
            import winsound
            winsound.Beep(880, 180)
        except Exception:
            pass
        # 推送到飞书
        self.feishu.notify_event("hourly_chime", chime_text)

    def open_focus_window(self):
        win = tk.Toplevel(self.root)
        win.title("专注时钟")
        win.geometry("360x380")

        mins_var = tk.IntVar(value=25)
        ttk.Label(win, text="专注分钟").pack(pady=(12, 4))
        ttk.Entry(win, textvariable=mins_var).pack()
        info_var = tk.StringVar(value="未开始")
        ttk.Label(win, textvariable=info_var).pack(pady=8)

        now_var = tk.StringVar(value="当前时间 --:--:--")
        ttk.Label(win, textvariable=now_var).pack()

        timer_var = tk.StringVar(value="倒计时 --:--")
        ttk.Label(win, textvariable=timer_var, font=("Microsoft YaHei UI", 18, "bold")).pack(pady=(4, 8))

        clock_canvas = tk.Canvas(win, width=180, height=180, bg="#f5f2ec", highlightthickness=0)
        clock_canvas.pack(pady=(0, 8))
        clock_canvas.create_oval(14, 14, 166, 166, outline="#6d5a4c", width=3)
        for i in range(12):
            angle = math.radians(i * 30 - 90)
            x1 = 90 + math.cos(angle) * 64
            y1 = 90 + math.sin(angle) * 64
            x2 = 90 + math.cos(angle) * 73
            y2 = 90 + math.sin(angle) * 73
            clock_canvas.create_line(x1, y1, x2, y2, fill="#6d5a4c", width=2)
        clock_canvas.create_oval(86, 86, 94, 94, fill="#6d5a4c", outline="")

        paused_state = {"paused": False, "remain": 0}
        update_after_id = {"id": None}

        def _draw_analog_clock(now_dt):
            clock_canvas.delete("hands")

            hour = now_dt.hour % 12 + now_dt.minute / 60.0
            minute = now_dt.minute + now_dt.second / 60.0
            second = now_dt.second

            hour_angle = math.radians(hour * 30 - 90)
            minute_angle = math.radians(minute * 6 - 90)
            second_angle = math.radians(second * 6 - 90)

            hx = 90 + math.cos(hour_angle) * 38
            hy = 90 + math.sin(hour_angle) * 38
            mx = 90 + math.cos(minute_angle) * 56
            my = 90 + math.sin(minute_angle) * 56
            sx = 90 + math.cos(second_angle) * 64
            sy = 90 + math.sin(second_angle) * 64

            clock_canvas.create_line(90, 90, hx, hy, fill="#3b2f2f", width=4, tags="hands")
            clock_canvas.create_line(90, 90, mx, my, fill="#5e4a3d", width=3, tags="hands")
            clock_canvas.create_line(90, 90, sx, sy, fill="#b34b4b", width=2, tags="hands")

        def _tick_focus_clock():
            now_dt = datetime.now()
            now_var.set(f"当前时间 {now_dt.strftime('%H:%M:%S')}")
            _draw_analog_clock(now_dt)

            if paused_state["paused"]:
                remain = max(0, int(paused_state["remain"]))
                timer_var.set(f"倒计时 {remain // 60:02d}:{remain % 60:02d} (暂停)")
            elif self.focus_running:
                remain = max(0, int(self.focus_end_ts - time.time()))
                timer_var.set(f"倒计时 {remain // 60:02d}:{remain % 60:02d}")
            else:
                timer_var.set("倒计时 --:--")

            update_after_id["id"] = win.after(1000, _tick_focus_clock)

        def start_focus():
            mins = max(1, mins_var.get())
            self.focus_running = True
            self.focus_total_minutes = mins
            self.focus_end_ts = time.time() + mins * 60
            self.focus_last_warn = 0.0
            paused_state["paused"] = False
            paused_state["remain"] = 0
            info_var.set(f"进行中: {mins} 分钟")
            self.show_lines(["专注开始，我会监督你。"])

        def pause_focus():
            if not self.focus_running:
                return
            paused_state["remain"] = max(0, int(self.focus_end_ts - time.time()))
            paused_state["paused"] = True
            self.focus_running = False
            info_var.set("已暂停")
            self.show_lines(["专注已暂停，休息一下也可以。"])

        def resume_focus():
            if not paused_state["paused"]:
                return
            remain = max(1, int(paused_state["remain"]))
            self.focus_running = True
            self.focus_end_ts = time.time() + remain
            self.focus_last_warn = 0.0
            paused_state["paused"] = False
            info_var.set("继续专注中")
            self.show_lines(["继续专注，我们接着冲。"])

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=6)
        ttk.Button(btn_row, text="开始", command=start_focus).pack(side="left", padx=4)
        ttk.Button(btn_row, text="暂停", command=pause_focus).pack(side="left", padx=4)
        ttk.Button(btn_row, text="继续", command=resume_focus).pack(side="left", padx=4)

        def on_close():
            after_id = update_after_id.get("id")
            if after_id:
                try:
                    win.after_cancel(after_id)
                except Exception:
                    pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        _tick_focus_clock()

    def _get_active_window_info(self):
        title, process_name = self._get_foreground_window_info_raw()
        return title.lower(), process_name.lower()

    def _get_foreground_window_info_raw(self):
        if os.name != "nt":
            return "", ""
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value

        process_name = ""
        if psutil:
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                process_name = psutil.Process(pid.value).name()
            except Exception:
                process_name = ""
        return title, process_name

    def _normalize_track_text(self, text):
        return re.sub(r"\s+", " ", str(text or "")).strip()

    def _parse_track_from_window_title(self, title, process_name):
        t = self._normalize_track_text(title)
        p = self._normalize_track_text(process_name).lower()
        if not t:
            return None

        media_proc = {
            "chrome.exe", "msedge.exe", "firefox.exe", "qqmusic.exe", "cloudmusic.exe", "spotify.exe"
        }
        if p and p not in media_proc:
            return None

        noise_tokens = {
            "youtube", "youtube music", "google chrome", "microsoft edge", "mozilla firefox",
            "bilibili", "哔哩哔哩", "qq音乐", "网易云音乐", "spotify"
        }

        separators = [" - ", " • ", " · ", " — ", " – "]
        parts = None
        for sep in separators:
            if sep in t:
                pieces = [x.strip() for x in t.split(sep) if x.strip()]
                if len(pieces) >= 2:
                    parts = pieces
                    break
        if not parts:
            return None

        while len(parts) > 2 and parts[-1].strip().lower() in noise_tokens:
            parts.pop()

        if len(parts) < 2:
            return None

        song = parts[0]
        artist = parts[1]
        if len(song) > 100 or len(artist) > 100:
            return None
        if song.lower() in noise_tokens or artist.lower() in noise_tokens:
            return None
        return {"title": song, "artist": artist, "source": "browser-title"}

    def _get_track_from_windows_media_manager(self):
        if os.name != "nt":
            return None

        backends = [
            ("winsdk", "winsdk.windows.media.control"),
            ("winrt", "winrt.windows.media.control"),
        ]

        for backend_name, module_name in backends:
            if backend_name in self.media_backend_unavailable:
                continue
            try:
                media_mod = importlib.import_module(module_name)
                manager_cls = getattr(media_mod, "GlobalSystemMediaTransportControlsSessionManager")

                async def _read_media():
                    mgr = await manager_cls.request_async()
                    session = mgr.get_current_session()
                    if not session:
                        return None
                    playback = session.get_playback_info()
                    status_name = str(getattr(playback, "playback_status", ""))
                    if "PLAYING" not in status_name.upper() and "4" not in status_name:
                        return None
                    props = await session.try_get_media_properties_async()
                    title = self._normalize_track_text(getattr(props, "title", ""))
                    artist = self._normalize_track_text(getattr(props, "artist", ""))
                    if not title:
                        return None
                    return {
                        "title": title,
                        "artist": artist or "未知歌手",
                        "source": "windows-media-manager",
                    }

                return asyncio.run(_read_media())
            except Exception:
                self.media_backend_unavailable.add(backend_name)
        return None

    def _get_current_media_track(self):
        track = self._get_track_from_windows_media_manager()
        if track:
            return track
        title, process_name = self._get_foreground_window_info_raw()
        return self._parse_track_from_window_title(title, process_name)

    def _request_music_comment(self, title, artist, source):
        try:
            prompt = (
                f"我们正在一起听歌，当前歌曲是《{title}》- {artist}（来源: {source}）。"
                "请你以OC口吻给出1-2句听歌感想（20-60字），温柔、俏皮，不要列表，不要复述提示词。"
            )
            messages = [
                {"role": "system", "content": self._compose_system_prompt()},
                {"role": "user", "content": prompt},
            ]
            answer = self._chat_completion(messages, temperature=0.9, timeout=45)
            self.reply_queue.put(("music_ok", {"track": f"{title} - {artist}", "comment": answer}))
        except Exception as e:
            self.reply_queue.put(("music_err", str(e)))

    def _monitor_focus(self):
        if not self.focus_running:
            return
        if time.time() >= self.focus_end_ts:
            self.focus_running = False
            minutes = max(1, int(self.focus_total_minutes or 1))
            reward = minutes * int(self.settings.get("focus", {}).get("reward_per_min", 2))
            self._add_coins(reward)
            self.show_lines([f"专注完成！奖励金币 +{reward}"])
            return

        title, process = self._get_active_window_info()
        keywords = [k.lower() for k in self.settings.get("focus", {}).get("safe_keywords", [])]
        okay = any(k in title or k in process for k in keywords)
        if not okay and time.time() - self.focus_last_warn > 8:
            self.focus_last_warn = time.time()
            self._shake_window()
            self.show_lines(["检测到可能在摸鱼，回来专注哦。"])

    def _shake_window(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        for dx in (12, -12, 8, -8, 0):
            self.root.geometry(f"+{x + dx}+{y}")
            self.root.update_idletasks()
            time.sleep(0.02)

    def _point_in_item(self, item_id, x, y):
        try:
            bbox = self.canvas.bbox(item_id)
            if not bbox:
                return False
            x1, y1, x2, y2 = bbox
            return x1 <= x <= x2 and y1 <= y <= y2
        except Exception:
            return False

    def _start_clipboard_listener_thread(self):
        if os.name != "nt":
            return
        threading.Thread(target=self._clipboard_listener_loop, daemon=True).start()

    def _clipboard_listener_loop(self):
        seq_func = getattr(ctypes.windll.user32, "GetClipboardSequenceNumber", None)
        if not seq_func:
            return

        try:
            last_seq = int(seq_func())
        except Exception:
            last_seq = 0

        while not self.clipboard_stop.is_set():
            try:
                seq = int(seq_func())
            except Exception:
                seq = last_seq

            if seq != last_seq:
                last_seq = seq
                content = self._read_clipboard_text_win().strip()
                if content and content != self.last_clipboard:
                    self.last_clipboard = content
                    self.clipboard_event_queue.put(("new_clip", content))
            time.sleep(0.6)

    def _read_clipboard_text_win(self):
        if os.name != "nt":
            return ""

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13

        for _ in range(3):
            if not user32.OpenClipboard(None):
                time.sleep(0.03)
                continue
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr) or ""
                finally:
                    kernel32.GlobalUnlock(handle)
            except Exception:
                return ""
            finally:
                user32.CloseClipboard()
        return ""

    def _poll_clipboard_events(self):
        try:
            while True:
                event_type, payload = self.clipboard_event_queue.get_nowait()
                if event_type == "new_clip":
                    self.pending_clipboard_text = payload
                    self._show_clip_prompt()
        except queue.Empty:
            pass
        self.root.after(250, self._poll_clipboard_events)

    def _show_clip_prompt(self):
        self.clip_prompt_visible = True
        self.canvas.itemconfig(self.clip_hint_bg_id, state="normal")
        self.canvas.itemconfig(self.clip_hint_icon_id, state="normal")
        self.canvas.itemconfig(self.clip_hint_text_id, state="normal")

    def _hide_clip_prompt(self):
        self.clip_prompt_visible = False
        self.canvas.itemconfig(self.clip_hint_bg_id, state="hidden")
        self.canvas.itemconfig(self.clip_hint_icon_id, state="hidden")
        self.canvas.itemconfig(self.clip_hint_text_id, state="hidden")

    def _on_clip_hint_click(self):
        content = (self.pending_clipboard_text or self.last_clipboard or "").strip()
        self._hide_clip_prompt()
        if not content:
            self.show_lines(["我探了一下，剪贴板现在是空的。"])
            self.clip_eat_btn.pack_forget()
            return

        preview = content.replace("\n", " ").strip()
        if len(preview) > 36:
            preview = preview[:36] + "..."

        roast = self._build_clipboard_roast(content)
        self.show_lines([f"你刚刚复制了：{preview}", roast])
        self.clip_eat_btn.pack(side="left")

    def _build_clipboard_roast(self, content):
        text = content.lower()
        length = len(content)

        if any(k in text for k in ("http://", "https://", "www.")):
            return "又在囤链接啦？收藏夹快爆仓咯。"
        if any(k in text for k in ("def ", "class ", "import ", "{", "}")):
            return "代码味很重，我闻到了调试的气息。"
        if any(k in text for k in ("密码", "password", "token", "apikey", "api_key")):
            return "这串看起来有点敏感，记得别随手乱贴哦。"
        if length <= 6:
            return "这么短！像是临时口令。"
        if length >= 120:
            return "这段也太长了吧，我都快嚼不动了。"
        return "内容中规中矩，已帮你默默围观。"

    def _eat_clipboard(self):
        try:
            self.root.clipboard_clear()
            self.root.update_idletasks()
        except Exception:
            self.show_lines(["吃掉失败，剪贴板暂时不让我碰。"])
            return

        self.pending_clipboard_text = ""
        self.last_clipboard = ""
        self.clip_eat_btn.pack_forget()
        self._increase_affinity(2)
        self.show_lines(["啊呜——吃掉了！剪贴板清空，好感 +2。"])

    def open_bag_window(self):
        win = tk.Toplevel(self.root)
        win.title("背包")
        win.geometry("460x360")

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        def refresh():
            for c in frame.winfo_children():
                c.destroy()
            for i, item in enumerate(self.items):
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=4)
                ttk.Label(row, text=f"{item['name']} x{item['qty']}").pack(side="left")
                ttk.Button(row, text="喂食", command=lambda idx=i: feed(idx)).pack(side="right")

        def feed(index):
            if self.items[index]["qty"] <= 0:
                self.show_lines(["这个已经吃完啦。"])
                return
            self.items[index]["qty"] -= 1
            self._save_json(ITEMS_PATH, self.items)
            self._increase_affinity(int(self.items[index].get("affinity", 1)), reason="喂食")
            self._start_eat_animation()
            self.show_lines([self.items[index].get("reply", "好吃！")])
            refresh()

        refresh()

    def _start_eat_animation(self):
        self.canvas.itemconfig(self.pet_img_id, image=self.open_img)
        self.root.after(120, lambda: self.canvas.itemconfig(self.pet_img_id, image=self.closed_img))
        self.root.after(240, lambda: self.canvas.itemconfig(self.pet_img_id, image=self.open_img))
        self.root.after(360, self._apply_idle_image)

    def open_reminder_window(self):
        win = tk.Toplevel(self.root)
        win.title("日程提醒")
        win.geometry("520x360")

        listbox = tk.Listbox(win)
        listbox.pack(fill="both", expand=True, padx=8, pady=8)

        row = ttk.Frame(win)
        row.pack(fill="x", padx=8)
        time_var = tk.StringVar(value="09:00")
        text_var = tk.StringVar(value="喝水")
        ttk.Entry(row, textvariable=time_var, width=10).pack(side="left")
        ttk.Entry(row, textvariable=text_var).pack(side="left", fill="x", expand=True, padx=6)

        def refresh():
            listbox.delete(0, "end")
            for r in self.reminders:
                listbox.insert("end", f"{r.get('time')}  提醒: {r.get('text')}")

        def add_one():
            self.reminders.append({"time": time_var.get().strip(), "text": text_var.get().strip(), "pre_minutes": 5, "last_date": ""})
            self._save_json(REMINDERS_PATH, self.reminders)
            refresh()

        def del_one():
            idx = listbox.curselection()
            if not idx:
                return
            self.reminders.pop(idx[0])
            self._save_json(REMINDERS_PATH, self.reminders)
            refresh()

        ttk.Button(row, text="添加", command=add_one).pack(side="left")
        ttk.Button(row, text="删除", command=del_one).pack(side="left", padx=6)
        refresh()

    def trigger_random_event(self):
        win = tk.Toplevel(self.root)
        win.title("随机事件")
        win.geometry("460x230")

        events = [
            ("路上捡到硬币，要不要上交？", ("上交", 2, 3), ("自己留着", 5, -1)),
            ("朋友请你喝奶茶，你会？", ("感谢并回礼", -2, 4), ("白嫖一杯", 1, -2)),
        ]
        text, op1, op2 = random.choice(events)
        ttk.Label(win, text=text, wraplength=420).pack(pady=20)

        def choose(opt):
            self._add_coins(opt[1])
            self._increase_affinity(opt[2])
            event_delta = opt[2] * 2 + (1 if opt[1] > 0 else -1 if opt[1] < 0 else 0)
            self._adjust_mood(event_delta, reason="random_event")
            self.show_lines([f"你选择了{opt[0]}，金币{opt[1]:+d}，好感{opt[2]:+d}。"])
            win.destroy()

        ttk.Button(win, text=op1[0], command=lambda: choose(op1)).pack(pady=4)
        ttk.Button(win, text=op2[0], command=lambda: choose(op2)).pack(pady=4)

    def open_journal_window(self):
        win = tk.Toplevel(self.root)
        win.title("观察日记")
        win.geometry("700x520")
        txt = tk.Text(win, wrap="word", font=("Microsoft YaHei UI", 10))
        txt.pack(fill="both", expand=True, padx=8, pady=8)

        def collect_recent_3days():
            cutoff = datetime.now() - timedelta(days=3)
            rows = []
            for item in self.history:
                t = item.get("time", "")
                try:
                    dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if dt >= cutoff:
                    rows.append(item)
            return rows

        def local_journal(rows):
            user_count = sum(1 for x in rows if x.get("role") == "user")
            ai_count = sum(1 for x in rows if x.get("role") == "assistant")
            mood = self.state.get("mood", "Normal")
            affinity = self.state.get("affinity", 0)
            coins = self.state.get("coins", 0)
            snippets = [x.get("content", "")[:28] for x in rows[-6:]]
            lines = [
                f"日期：{datetime.now().strftime('%Y-%m-%d')}",
                "",
                "今天的观察日记：",
                f"最近三天，我们一共聊了 {user_count} 句你说的话和 {ai_count} 句我说的话。",
                f"我现在的心情是 {mood}，和你的好感度是 {affinity}，你身上的金币是 {coins}。",
                "我记得最近这些片段：",
            ]
            for s in snippets:
                if s:
                    lines.append(f"- {s}")
            lines.append("\n无论你忙不忙，我都会继续在桌面等你。")
            return "\n".join(lines)

        def generate():
            rows = collect_recent_3days()
            if not rows:
                txt.delete("1.0", "end")
                txt.insert("1.0", "最近三天没有足够交互记录，先多聊几句再来生成吧。")
                return
            try:
                api_key = self.settings.get("api_key", "").strip()
                if not api_key.startswith("sk-"):
                    raise RuntimeError("no api")
                api_base = self.settings.get("api_base", "https://api.deepseek.com").rstrip("/")
                model = self.settings.get("model", "deepseek-chat")
                merged = "\n".join([f"[{x.get('time','')}] {x.get('role','')}: {x.get('content','')[:100]}" for x in rows[-80:]])
                prompt = (
                    "请你以OC第一人称写一篇300-500字观察日记，语气温柔。"
                    "请基于以下最近三天的交互日志，不要编造过多事实：\n" + merged
                )
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": self._compose_system_prompt()},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": self._get_generation_params()["temperature"],
                    "max_tokens": self._get_generation_params()["max_tokens"],
                }
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                resp = requests.post(f"{api_base}/chat/completions", headers=headers, json=payload, timeout=60)
                if resp.status_code != 200:
                    raise RuntimeError("api failed")
                diary = resp.json()["choices"][0]["message"]["content"].strip()
            except Exception:
                diary = local_journal(rows)

            txt.delete("1.0", "end")
            txt.insert("1.0", diary)
            self.state["last_journal_day"] = datetime.now().strftime("%Y-%m-%d")
            self._save_json(STATE_PATH, self.state)

        btn = ttk.Frame(win)
        btn.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btn, text="生成日记", command=generate).pack(side="left")
        ttk.Button(btn, text="保存到 notes.txt", command=lambda: self._append_note_from_text(txt)).pack(side="left", padx=8)
        generate()

    def _sanitize_tts_config(self):
        cfg = self.settings.setdefault("tts", {})
        defaults = {
            "enabled": True,
            "rate": 190,
            "volume": 0.9,
            "voice_name": "",
            "queue_mode": "replace",
            "emotion_adapt": True,
            "speak_auto_events": True,
            "max_chars": 360,
            "split_sentences": True,
            "auto_mute_in_quiet_mode": True,
            "test_text": "语音设置已应用，听得到吗？",
        }
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        cfg["rate"] = int(max(120, min(260, int(cfg.get("rate", 190)))))
        cfg["volume"] = float(max(0.0, min(1.0, float(cfg.get("volume", 0.9)))))
        cfg["queue_mode"] = str(cfg.get("queue_mode", "replace")).strip().lower()
        if cfg["queue_mode"] not in ("replace", "append"):
            cfg["queue_mode"] = "replace"
        cfg["max_chars"] = int(max(80, min(1000, int(cfg.get("max_chars", 360)))))

    def _sanitize_auto_event_config(self):
        cfg = self.settings.setdefault("auto_events", {})
        defaults = {
            "enabled": True,
            "global_min_interval_seconds": 6,
            "allow_in_quiet_mode": False,
            "allow_in_sleep_mode": False,
        }
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        cfg["global_min_interval_seconds"] = int(max(1, min(120, int(cfg.get("global_min_interval_seconds", 6)))))

    def _sanitize_proactive_config(self):
        cfg = self.settings.setdefault("proactive", {})
        defaults = {
            "enabled": True,
            "warmup_seconds": 90,
            "warmup_interval_seconds": 180,
            "steady_interval_seconds": 480,
            "hype_interval_seconds": 300,
            "cooldown_interval_seconds": 720,
        }
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        for key in (
            "warmup_seconds",
            "warmup_interval_seconds",
            "steady_interval_seconds",
            "hype_interval_seconds",
            "cooldown_interval_seconds",
        ):
            cfg[key] = int(max(30, min(3600, int(cfg.get(key, defaults[key])))))

    def _sanitize_conversation_engine_config(self):
        cfg = self.settings.setdefault("conversation_engine", {})
        defaults = {
            "enabled": True,
            "hook_cooldown_seconds": 180,
            "hook_history_size": 20,
            "memory_recent_days": 14,
            "topic_hints": ["学习", "工作", "代码", "生活", "游戏", "创作", "情绪", "健康"],
            "hook_pool": [
                "先定一个最小可完成步骤，我们从那里起步。",
                "如果你愿意，我可以把这件事拆成三步并陪你做完。",
                "先别急，我在，你只要告诉我现在最卡的点。",
                "我们按‘先完成再完美’的节奏走，会更轻松。",
            ],
        }
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        cfg["hook_cooldown_seconds"] = int(max(30, min(3600, int(cfg.get("hook_cooldown_seconds", 180)))))
        cfg["hook_history_size"] = int(max(5, min(200, int(cfg.get("hook_history_size", 20)))))
        cfg["memory_recent_days"] = int(max(1, min(90, int(cfg.get("memory_recent_days", 14)))))
        if not isinstance(cfg.get("topic_hints"), list) or not cfg.get("topic_hints"):
            cfg["topic_hints"] = list(defaults["topic_hints"])
        if not isinstance(cfg.get("hook_pool"), list) or not cfg.get("hook_pool"):
            cfg["hook_pool"] = list(defaults["hook_pool"])

    def _extract_topic_tags(self, text):
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

    def _build_topic_hint(self, user_text):
        cfg = self.settings.get("conversation_engine", {})
        tags = self._extract_topic_tags(user_text)
        if not tags:
            hint_pool = [str(x) for x in cfg.get("topic_hints", []) if str(x).strip()]
            if hint_pool:
                tags = [random.choice(hint_pool)]
        if not tags:
            return ""
        for t in tags:
            self.recent_topic_hints.append(t)
        self.recent_topic_hints = self.recent_topic_hints[-12:]
        hint = "当前优先话题: " + " / ".join(tags)
        self.conversation_metrics["topic_hits"] = int(self.conversation_metrics.get("topic_hits", 0)) + len(tags)
        self.conversation_metrics["last_topic_hint"] = hint
        return hint

    def _pick_non_repeating_hook(self):
        cfg = self.settings.get("conversation_engine", {})
        hooks = [str(x).strip() for x in cfg.get("hook_pool", []) if str(x).strip()]
        if not hooks:
            return "", ""
        available = []
        for h in hooks:
            key = re.sub(r"\W+", "", h.lower())[:80]
            last = float(self.auto_event_last_emit.get("hook:" + key, 0.0))
            if (time.time() - last) >= int(cfg.get("hook_cooldown_seconds", 180)):
                available.append((h, key))
        if not available:
            h = random.choice(hooks)
            key = re.sub(r"\W+", "", h.lower())[:80]
            return h, key
        return random.choice(available)

    def _maybe_build_hook_directive(self):
        hook, key = self._pick_non_repeating_hook()
        if not hook:
            return ""
        full_key = "hook:" + key
        self.auto_event_last_emit[full_key] = time.time()
        self.recent_hook_keys.append(full_key)
        limit = int(self.settings.get("conversation_engine", {}).get("hook_history_size", 20))
        if len(self.recent_hook_keys) > limit:
            self.recent_hook_keys = self.recent_hook_keys[-limit:]
        self.conversation_metrics["hook_uses"] = int(self.conversation_metrics.get("hook_uses", 0)) + 1
        self.conversation_metrics["last_hook"] = hook
        return "风格提示: 可自然融入一句，不要逐字照抄 -> " + hook

    def _build_layered_memory_block(self, query, topk=4):
        recalls = self._recall_memory(query, topk=max(2, topk * 2))
        if not recalls:
            return ""
        self.conversation_metrics["memory_hits"] = int(self.conversation_metrics.get("memory_hits", 0)) + len(recalls)

        cfg = self.settings.get("conversation_engine", {})
        recent_days = int(cfg.get("memory_recent_days", 14))
        cutoff = datetime.now() - timedelta(days=recent_days)

        fact_like = []
        preference_like = []
        relation_like = []
        recent_like = []

        pref_words = ("喜欢", "讨厌", "偏好", "习惯", "想要", "不想")
        relation_words = ("我们", "你和我", "陪", "约定", "纪念", "关系")

        for item in recalls:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            ts = str(item.get("time", "")).strip()
            role = str(item.get("role", ""))
            is_recent = False
            try:
                if ts:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                    is_recent = dt >= cutoff
            except Exception:
                is_recent = False

            line = f"[{ts}] {role}: {content}"
            lower = content.lower()
            if any(w in lower for w in pref_words):
                preference_like.append(line)
            elif any(w in lower for w in relation_words):
                relation_like.append(line)
            elif is_recent:
                recent_like.append(line)
            else:
                fact_like.append(line)

        chunks = []
        if fact_like:
            chunks.append("事实记忆:\n" + "\n".join(fact_like[:2]))
        if preference_like:
            chunks.append("偏好记忆:\n" + "\n".join(preference_like[:2]))
        if relation_like:
            chunks.append("关系记忆:\n" + "\n".join(relation_like[:2]))
        if recent_like:
            chunks.append("近期记忆:\n" + "\n".join(recent_like[:2]))

        if not chunks:
            return ""
        return "\n\n分层长期记忆参考:\n" + "\n\n".join(chunks[:4])

    def _post_auto_event(self, event_type, text, priority=5, cooldown_seconds=30, dedupe_key=None, speak=True):
        self._sanitize_auto_event_config()
        cfg = self.settings.get("auto_events", {})
        if not bool(cfg.get("enabled", True)):
            return False
        if (not bool(cfg.get("allow_in_quiet_mode", False))) and self.game_mode and priority < 9:
            return False
        if (not bool(cfg.get("allow_in_sleep_mode", False))) and self.sleep_mode and priority < 10:
            return False
        if self.busy and priority < 9:
            return False

        now_ts = time.time()
        min_gap = int(cfg.get("global_min_interval_seconds", 6))
        if (now_ts - float(self.last_auto_event_emit_ts or 0.0)) < min_gap and priority < 8:
            return False

        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False
        if dedupe_key is None:
            dedupe_key = f"{event_type}:{re.sub(r'\W+', '', normalized.lower())[:80]}"
        last_ts = float(self.auto_event_last_emit.get(dedupe_key, 0.0))
        if (now_ts - last_ts) < max(1, int(cooldown_seconds)):
            return False

        self.auto_event_last_emit[dedupe_key] = now_ts
        self.last_auto_event_emit_ts = now_ts
        self.conversation_metrics["auto_events_posted"] = int(self.conversation_metrics.get("auto_events_posted", 0)) + 1
        self.reply_queue.put(("auto_event", {
            "text": normalized,
            "source": "auto",
            "event_type": str(event_type),
            "priority": int(priority),
            "speak": bool(speak),
        }))
        return True

    def _update_proactive_stage(self):
        emotion = int(self.state.get("emotion_value", self.state.get("mood_score", 0)))
        idle_sec = time.time() - float(self.last_input_time or 0.0)
        since_start = time.time() - float(self.session_start_ts or time.time())
        cfg = self.settings.get("proactive", {})

        if idle_sec > 25 * 60:
            stage = "cooldown"
        elif emotion >= 45 and idle_sec < 180:
            stage = "hype"
        elif since_start < int(cfg.get("warmup_seconds", 90)):
            stage = "warmup"
        else:
            stage = "steady"

        self.proactive_stage = stage
        return stage

    def _maybe_emit_proactive_event(self):
        self._sanitize_proactive_config()
        cfg = self.settings.get("proactive", {})
        if not bool(cfg.get("enabled", True)):
            return
        if self.busy or self.message_box_visible or self.sleep_mode or self.game_mode:
            return

        stage = self._update_proactive_stage()
        interval_map = {
            "warmup": int(cfg.get("warmup_interval_seconds", 180)),
            "steady": int(cfg.get("steady_interval_seconds", 480)),
            "hype": int(cfg.get("hype_interval_seconds", 300)),
            "cooldown": int(cfg.get("cooldown_interval_seconds", 720)),
        }
        interval = max(30, interval_map.get(stage, 480))
        now_ts = time.time()
        if (now_ts - float(self.proactive_last_emit_ts or 0.0)) < interval:
            return

        mood = str(self.state.get("mood", "Normal"))
        pools = {
            "warmup": [
                "今天想让我怎么陪你？",
                "我已经上线待命啦。",
                "要不要先定一个小目标？",
            ],
            "steady": [
                "进度卡住就喊我，我给你拆步骤。",
                "记得每 30 分钟活动一下肩颈。",
                "我在这儿，你专心做事就好。",
            ],
            "hype": [
                "状态不错，趁手感继续冲一段。",
                "这波节奏很好，别断档。",
                "要不要顺手把下一个小任务也拿下？",
            ],
            "cooldown": [
                "你忙了很久啦，喝口水休息一下。",
                "现在适合收束一下，别硬撑。",
                "要不要先暂停两分钟，让大脑透口气？",
            ],
        }
        mood_prefix = {
            "Excited": "[兴奋] ",
            "Happy": "[开心] ",
            "Normal": "",
            "Sad": "[低落] ",
            "Angry": "[烦躁] ",
        }.get(mood, "")

        line = random.choice(pools.get(stage, pools["steady"]))
        hint_pool = [str(x) for x in self.settings.get("conversation_engine", {}).get("topic_hints", []) if str(x).strip()]
        if hint_pool and random.random() < 0.35:
            line = f"要不要切到「{random.choice(hint_pool)}」这个话题？"
        posted = self._post_auto_event(
            event_type=f"proactive:{stage}",
            text=mood_prefix + line,
            priority=4,
            cooldown_seconds=interval,
            dedupe_key=f"proactive:{stage}:{line}",
            speak=True,
        )
        if posted:
            self.proactive_last_emit_ts = now_ts

    def _list_tts_voices(self):
        global pyttsx3
        if pyttsx3 is None:
            try:
                pyttsx3 = importlib.import_module("pyttsx3")
            except Exception:
                return []
        engine = self.tts_engine
        temp_engine = None
        if engine is None:
            try:
                temp_engine = pyttsx3.init()
                engine = temp_engine
            except Exception:
                return []
        result = []
        try:
            for v in engine.getProperty("voices"):
                name = str(getattr(v, "name", "") or "").strip()
                vid = str(getattr(v, "id", "") or "").strip()
                if name or vid:
                    result.append({"name": name or vid, "id": vid})
        except Exception:
            result = []
        finally:
            try:
                if temp_engine is not None:
                    temp_engine.stop()
            except Exception:
                pass
        return result

    def _clear_tts_queue(self):
        try:
            while True:
                self.tts_queue.get_nowait()
        except queue.Empty:
            pass
        if self.tts_engine:
            try:
                self.tts_engine.stop()
            except Exception:
                pass

    def _build_tts_segments(self, text, max_chars=360, split_sentences=True):
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return []
        normalized = normalized[:max_chars]
        if not split_sentences:
            return [normalized]
        parts = re.split(r"(?<=[。！？!?；;,.，])", normalized)
        segments = []
        for p in parts:
            s = p.strip()
            if not s:
                continue
            if len(s) <= 120:
                segments.append(s)
            else:
                for i in range(0, len(s), 120):
                    segments.append(s[i:i + 120])
        return segments or [normalized]

    def _init_tts(self):
        global pyttsx3
        self._sanitize_tts_config()
        cfg = self.settings.get("tts", {})
        if not cfg.get("enabled", False):
            return
        if pyttsx3 is None:
            try:
                pyttsx3 = importlib.import_module("pyttsx3")
            except Exception:
                pyttsx3 = None
                return
        try:
            if self.tts_engine is None:
                self.tts_engine = pyttsx3.init()
            self._apply_tts_properties()
            if not self.tts_thread_started:
                self.tts_thread_started = True
                threading.Thread(target=self._tts_loop, daemon=True).start()
        except Exception:
            self.tts_engine = None

    def _apply_tts_properties(self):
        if not self.tts_engine:
            return
        self._sanitize_tts_config()
        cfg = self.settings.get("tts", {})
        self.tts_engine.setProperty("rate", int(cfg.get("rate", 190)))
        self.tts_engine.setProperty("volume", float(cfg.get("volume", 0.9)))
        name = str(cfg.get("voice_name", "")).strip().lower()
        if name:
            for v in self.tts_engine.getProperty("voices"):
                if name in (v.name or "").lower():
                    self.tts_engine.setProperty("voice", v.id)
                    break

    def _tts_loop(self):
        while not self.tts_stop:
            try:
                item = self.tts_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break
            if not self.tts_engine:
                continue
            try:
                text = ""
                mood = "Normal"
                if isinstance(item, dict):
                    text = str(item.get("text", "")).strip()
                    mood = str(item.get("mood", "Normal") or "Normal")
                else:
                    text = str(item).strip()
                if not text:
                    continue

                cfg = self.settings.get("tts", {})
                base_rate = int(cfg.get("rate", 190))
                if bool(cfg.get("emotion_adapt", True)):
                    rate_delta = {
                        "Excited": 22,
                        "Happy": 12,
                        "Normal": 0,
                        "Sad": -14,
                        "Angry": -18,
                    }.get(mood, 0)
                    self.tts_engine.setProperty("rate", max(120, min(260, base_rate + rate_delta)))
                else:
                    self.tts_engine.setProperty("rate", base_rate)

                self.tts_engine.say(text)
                self.tts_engine.runAndWait()
            except Exception:
                pass

    def _speak_async(self, text, source="assistant", force=False):
        self._sanitize_tts_config()
        cfg = self.settings.get("tts", {})
        if not cfg.get("enabled", False):
            return
        if (not force) and self.game_mode and bool(cfg.get("auto_mute_in_quiet_mode", True)):
            return
        if (not force) and (source != "assistant") and (not bool(cfg.get("speak_auto_events", True))):
            return
        if not self.tts_engine:
            self._init_tts()
        if not self.tts_engine:
            return
        segments = self._build_tts_segments(
            text,
            max_chars=int(cfg.get("max_chars", 360)),
            split_sentences=bool(cfg.get("split_sentences", True)),
        )
        if not segments:
            return
        if str(cfg.get("queue_mode", "replace")).lower() == "replace":
            self._clear_tts_queue()
        mood = str(self.state.get("mood", "Normal") or "Normal")
        for seg in segments:
            self.tts_queue.put({"text": seg, "mood": mood, "source": source})

    def toggle_tts(self):
        self._sanitize_tts_config()
        cfg = self.settings.setdefault("tts", {"enabled": False, "rate": 190, "volume": 0.9, "voice_name": ""})
        cfg["enabled"] = not bool(cfg.get("enabled", False))
        self._save_json(SETTINGS_PATH, self.settings)

        if cfg["enabled"]:
            self._init_tts()
            self.show_lines(["语音朗读已开启。"])
            self._speak_async("语音朗读已开启", source="system", force=True)
        else:
            self._clear_tts_queue()
            self.show_lines(["语音朗读已关闭。"])

    def toggle_screen_roast(self):
        cfg = self.settings.setdefault("screen_roast", {
            "enabled": False,
            "interval_seconds": 25,
            "cooldown_seconds": 90,
            "min_chars": 12,
            "max_chars": 160,
            "ocr_lang": "chi_sim+eng",
            "tesseract_cmd": ""
        })
        cfg["enabled"] = not bool(cfg.get("enabled", False))
        self._save_json(SETTINGS_PATH, self.settings)
        self.screen_roast_warned = False
        self.show_lines(["屏幕吐槽已开启。" if cfg["enabled"] else "屏幕吐槽已关闭。"])

    def toggle_screen_comment(self):
        cfg = self.settings.setdefault("screen_comment", {
            "enabled": False,
            "interval_seconds": 60,
            "cooldown_seconds": 180,
            "min_chars": 20,
            "max_chars": 800,
            "ocr_lang": "chi_sim+eng",
            "tesseract_cmd": "",
            "detailed": True
        })
        cfg["enabled"] = not bool(cfg.get("enabled", False))
        self._save_json(SETTINGS_PATH, self.settings)
        self.screen_comment_warned = False
        self.show_lines(["屏幕解析评论已开启。" if cfg["enabled"] else "屏幕解析评论已关闭。"])

    def toggle_audio_roast(self):
        cfg = self.settings.setdefault("audio_roast", {
            "enabled": False,
            "interval_seconds": 8,
            "sample_seconds": 5,
            "cooldown_seconds": 8,
            "min_chars": 4,
            "max_chars": 140,
            "realtime": True,
            "language": "zh",
            "model_size": "tiny"
        })
        cfg["enabled"] = not bool(cfg.get("enabled", False))
        self._save_json(SETTINGS_PATH, self.settings)
        self.audio_roast_warned = False
        self.show_lines(["音频吐槽已开启。" if cfg["enabled"] else "音频吐槽已关闭。"])

    def open_tts_settings(self):
        win = tk.Toplevel(self.root)
        win.title("语音设置")
        win.geometry("560x460")

        self._sanitize_tts_config()
        cfg = self.settings.setdefault("tts", {"enabled": False, "rate": 190, "volume": 0.9, "voice_name": ""})
        enabled_var = tk.BooleanVar(value=bool(cfg.get("enabled", False)))
        rate_var = tk.IntVar(value=int(cfg.get("rate", 190)))
        volume_var = tk.IntVar(value=int(float(cfg.get("volume", 0.9)) * 100))
        voice_var = tk.StringVar(value=str(cfg.get("voice_name", "")))
        queue_mode_var = tk.StringVar(value=str(cfg.get("queue_mode", "replace")))
        emotion_adapt_var = tk.BooleanVar(value=bool(cfg.get("emotion_adapt", True)))
        auto_event_var = tk.BooleanVar(value=bool(cfg.get("speak_auto_events", True)))
        split_sentences_var = tk.BooleanVar(value=bool(cfg.get("split_sentences", True)))
        quiet_mute_var = tk.BooleanVar(value=bool(cfg.get("auto_mute_in_quiet_mode", True)))
        max_chars_var = tk.IntVar(value=int(cfg.get("max_chars", 360)))
        test_text_var = tk.StringVar(value=str(cfg.get("test_text", "语音设置已应用，听得到吗？")))

        voices = self._list_tts_voices()
        voice_names = [v.get("name", "") for v in voices if v.get("name")]

        row1 = ttk.Frame(win)
        row1.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Checkbutton(row1, text="启用语音朗读", variable=enabled_var).pack(side="left")

        row2 = ttk.Frame(win)
        row2.pack(fill="x", padx=12, pady=6)
        ttk.Label(row2, text="语速").pack(side="left")
        ttk.Scale(row2, from_=120, to=260, orient="horizontal", command=lambda v: rate_var.set(int(float(v))), length=240).pack(side="left", padx=8)
        ttk.Label(row2, textvariable=rate_var, width=4).pack(side="left")

        row3 = ttk.Frame(win)
        row3.pack(fill="x", padx=12, pady=6)
        ttk.Label(row3, text="音量").pack(side="left")
        ttk.Scale(row3, from_=0, to=100, orient="horizontal", command=lambda v: volume_var.set(int(float(v))), length=240).pack(side="left", padx=8)
        ttk.Label(row3, textvariable=volume_var, width=4).pack(side="left")

        row4 = ttk.Frame(win)
        row4.pack(fill="x", padx=12, pady=6)
        ttk.Label(row4, text="音色").pack(side="left")
        if voice_names:
            combo = ttk.Combobox(row4, textvariable=voice_var, values=voice_names, state="normal")
            combo.pack(side="left", fill="x", expand=True, padx=8)
        else:
            ttk.Entry(row4, textvariable=voice_var).pack(side="left", fill="x", expand=True, padx=8)

        row5 = ttk.Frame(win)
        row5.pack(fill="x", padx=12, pady=6)
        ttk.Label(row5, text="队列模式").pack(side="left")
        ttk.Radiobutton(row5, text="打断旧语音", value="replace", variable=queue_mode_var).pack(side="left", padx=6)
        ttk.Radiobutton(row5, text="排队播放", value="append", variable=queue_mode_var).pack(side="left", padx=6)

        row6 = ttk.Frame(win)
        row6.pack(fill="x", padx=12, pady=4)
        ttk.Checkbutton(row6, text="情绪自适应语速", variable=emotion_adapt_var).pack(side="left")
        ttk.Checkbutton(row6, text="朗读自动事件", variable=auto_event_var).pack(side="left", padx=12)

        row7 = ttk.Frame(win)
        row7.pack(fill="x", padx=12, pady=4)
        ttk.Checkbutton(row7, text="按句分段朗读", variable=split_sentences_var).pack(side="left")
        ttk.Checkbutton(row7, text="安静模式自动静音", variable=quiet_mute_var).pack(side="left", padx=12)

        row8 = ttk.Frame(win)
        row8.pack(fill="x", padx=12, pady=6)
        ttk.Label(row8, text="单次最大字符").pack(side="left")
        ttk.Entry(row8, textvariable=max_chars_var, width=8).pack(side="left", padx=8)

        row9 = ttk.Frame(win)
        row9.pack(fill="x", padx=12, pady=6)
        ttk.Label(row9, text="试听文本").pack(side="left")
        ttk.Entry(row9, textvariable=test_text_var).pack(side="left", fill="x", expand=True, padx=8)

        # 初始化滑条位置
        for child in row2.winfo_children():
            if isinstance(child, ttk.Scale):
                child.set(rate_var.get())
        for child in row3.winfo_children():
            if isinstance(child, ttk.Scale):
                child.set(volume_var.get())

        def apply_settings(speak_demo=False):
            cfg["enabled"] = bool(enabled_var.get())
            cfg["rate"] = int(rate_var.get())
            cfg["volume"] = max(0.0, min(1.0, float(volume_var.get()) / 100.0))
            cfg["voice_name"] = voice_var.get().strip()
            cfg["queue_mode"] = queue_mode_var.get().strip().lower()
            cfg["emotion_adapt"] = bool(emotion_adapt_var.get())
            cfg["speak_auto_events"] = bool(auto_event_var.get())
            cfg["split_sentences"] = bool(split_sentences_var.get())
            cfg["auto_mute_in_quiet_mode"] = bool(quiet_mute_var.get())
            cfg["max_chars"] = max(80, min(1000, int(max_chars_var.get())))
            cfg["test_text"] = test_text_var.get().strip() or "语音设置已应用，听得到吗？"
            self._sanitize_tts_config()
            self._save_json(SETTINGS_PATH, self.settings)

            if cfg["enabled"]:
                self._init_tts()
                self._apply_tts_properties()
                if speak_demo:
                    self._speak_async(cfg.get("test_text", "语音设置已应用，听得到吗？"), source="system", force=True)
            else:
                self._clear_tts_queue()

        btn = ttk.Frame(win)
        btn.pack(fill="x", padx=12, pady=12)
        ttk.Button(btn, text="试听", command=lambda: apply_settings(speak_demo=True)).pack(side="left")
        ttk.Button(btn, text="保存", command=lambda: (apply_settings(False), self.show_lines(["语音设置已保存。"]), win.destroy())).pack(side="right")

    def open_size_settings(self):
        win = tk.Toplevel(self.root)
        win.title("宠物大小")
        win.geometry("360x170")

        scale_pct = tk.IntVar(value=int(round(float(self.pet_scale) * 100)))

        row = ttk.Frame(win)
        row.pack(fill="x", padx=14, pady=(18, 8))
        ttk.Label(row, text="缩放比例").pack(side="left")
        ttk.Scale(row, from_=60, to=180, orient="horizontal", command=lambda v: scale_pct.set(int(float(v))), length=220).pack(side="left", padx=8)
        ttk.Label(row, textvariable=scale_pct, width=5).pack(side="left")

        for child in row.winfo_children():
            if isinstance(child, ttk.Scale):
                child.set(scale_pct.get())

        ttk.Label(win, text="范围 60% ~ 180%", foreground="#666").pack(anchor="w", padx=14)

        def save_size():
            self.settings.setdefault("ui", {})["pet_scale"] = max(0.6, min(1.8, float(scale_pct.get()) / 100.0))
            self.pet_scale = self._get_pet_scale()
            self._save_json(SETTINGS_PATH, self.settings)
            self.reload_images()
            self.show_lines([f"宠物大小已调整为 {int(round(self.pet_scale * 100))}%"])
            win.destroy()

        btn = ttk.Frame(win)
        btn.pack(fill="x", padx=14, pady=12)
        ttk.Button(btn, text="保存", command=save_size).pack(side="right")

    def open_screen_comment_settings(self):
        win = tk.Toplevel(self.root)
        win.title("屏幕解析设置")
        win.geometry("480x280")

        cfg = self.settings.setdefault("screen_comment", {
            "enabled": False,
            "interval_seconds": 60,
            "cooldown_seconds": 180,
            "min_chars": 20,
            "max_chars": 800,
            "ocr_lang": "chi_sim+eng",
            "tesseract_cmd": "",
            "detailed": True,
            "highlight": True
        })

        enabled_var = tk.BooleanVar(value=bool(cfg.get("enabled", False)))
        detailed_var = tk.BooleanVar(value=bool(cfg.get("detailed", True)))
        highlight_var = tk.BooleanVar(value=bool(cfg.get("highlight", True)))
        hotkey_enabled_var = tk.BooleanVar(value=bool(cfg.get("hotkey_enabled", False)))
        hotkey_var = tk.StringVar(value=str(cfg.get("hotkey", "ctrl+alt+c")))
        interval_var = tk.IntVar(value=int(cfg.get("interval_seconds", 60)))
        cooldown_var = tk.IntVar(value=int(cfg.get("cooldown_seconds", 180)))
        minchars_var = tk.IntVar(value=int(cfg.get("min_chars", 20)))

        row1 = ttk.Frame(win)
        row1.pack(fill="x", padx=12, pady=(12,6))
        ttk.Checkbutton(row1, text="启用屏幕解析", variable=enabled_var).pack(side="left")
        ttk.Checkbutton(row1, text="详细模式（多段分析）", variable=detailed_var).pack(side="left", padx=12)

        row2 = ttk.Frame(win)
        row2.pack(fill="x", padx=12, pady=6)
        ttk.Checkbutton(row2, text="启用高亮并保存截图", variable=highlight_var).pack(side="left")

        row3 = ttk.Frame(win)
        row3.pack(fill="x", padx=12, pady=6)
        ttk.Label(row3, text="检测间隔(秒)").pack(side="left")
        ttk.Entry(row3, textvariable=interval_var, width=6).pack(side="left", padx=6)
        ttk.Label(row3, text="冷却(秒)").pack(side="left", padx=8)
        ttk.Entry(row3, textvariable=cooldown_var, width=6).pack(side="left", padx=6)

        row4 = ttk.Frame(win)
        row4.pack(fill="x", padx=12, pady=6)
        ttk.Label(row4, text="最少字符数触发").pack(side="left")
        ttk.Entry(row4, textvariable=minchars_var, width=6).pack(side="left", padx=6)

        row5 = ttk.Frame(win)
        row5.pack(fill="x", padx=12, pady=6)
        ttk.Checkbutton(row5, text="启用全局热键切换（Ctrl+Alt+C）", variable=hotkey_enabled_var).pack(side="left")
        ttk.Label(row5, text="热键（可配置）").pack(side="left", padx=8)
        ttk.Entry(row5, textvariable=hotkey_var, width=18).pack(side="left")

        def apply_settings():
            cfg["enabled"] = bool(enabled_var.get())
            cfg["detailed"] = bool(detailed_var.get())
            cfg["highlight"] = bool(highlight_var.get())
            cfg["hotkey_enabled"] = bool(hotkey_enabled_var.get())
            cfg["hotkey"] = str(hotkey_var.get()).strip()
            cfg["interval_seconds"] = max(10, int(interval_var.get()))
            cfg["cooldown_seconds"] = max(10, int(cooldown_var.get()))
            cfg["min_chars"] = max(4, int(minchars_var.get()))
            self._save_json(SETTINGS_PATH, self.settings)
            self.show_lines(["屏幕解析设置已保存。"])
            # re-register hotkey according to new settings
            try:
                self._unregister_hotkey()
            except Exception:
                pass
            try:
                self._register_hotkey()
            except Exception:
                pass

        btn = ttk.Frame(win)
        btn.pack(fill="x", padx=12, pady=12)
        ttk.Button(btn, text="保存", command=lambda: (apply_settings(), win.destroy())).pack(side="right")

    def _conversation_engine_stats_text(self):
        m = self.conversation_metrics if isinstance(self.conversation_metrics, dict) else {}
        lines = [
            f"本轮请求: {int(m.get('request_count', 0))}",
            f"回退次数: {int(m.get('fallback_count', 0))}",
            f"话题命中: {int(m.get('topic_hits', 0))}",
            f"梗使用次数: {int(m.get('hook_uses', 0))}",
            f"记忆命中条数: {int(m.get('memory_hits', 0))}",
            f"自动事件发送: {int(m.get('auto_events_posted', 0))}",
            f"最近话题提示: {str(m.get('last_topic_hint', ''))}",
            f"最近风格句: {str(m.get('last_hook', ''))}",
            f"当前主动阶段: {self.proactive_stage}",
        ]
        return "\n".join(lines)

    def open_system_control_panel(self):
        win = tk.Toplevel(self.root)
        win.title("系统总控面板")
        win.geometry("700x520")

        self._sanitize_tts_config()
        self._sanitize_auto_event_config()
        self._sanitize_proactive_config()
        self._sanitize_conversation_engine_config()

        tts_cfg = self.settings.setdefault("tts", {})
        auto_cfg = self.settings.setdefault("auto_events", {})
        pro_cfg = self.settings.setdefault("proactive", {})
        conv_cfg = self.settings.setdefault("conversation_engine", {})

        tts_enabled_var = tk.BooleanVar(value=bool(tts_cfg.get("enabled", True)))
        auto_enabled_var = tk.BooleanVar(value=bool(auto_cfg.get("enabled", True)))
        pro_enabled_var = tk.BooleanVar(value=bool(pro_cfg.get("enabled", True)))
        conv_enabled_var = tk.BooleanVar(value=bool(conv_cfg.get("enabled", True)))

        tts_queue_var = tk.StringVar(value=str(tts_cfg.get("queue_mode", "replace")))
        tts_emotion_var = tk.BooleanVar(value=bool(tts_cfg.get("emotion_adapt", True)))
        auto_min_gap_var = tk.IntVar(value=int(auto_cfg.get("global_min_interval_seconds", 6)))
        pro_steady_var = tk.IntVar(value=int(pro_cfg.get("steady_interval_seconds", 480)))
        conv_mem_days_var = tk.IntVar(value=int(conv_cfg.get("memory_recent_days", 14)))

        title = ttk.Label(win, text="统一开关与关键参数", font=("Microsoft YaHei UI", 11, "bold"))
        title.pack(anchor="w", padx=12, pady=(12, 8))

        grid = ttk.Frame(win)
        grid.pack(fill="x", padx=12)

        ttk.Checkbutton(grid, text="TTS 语音", variable=tts_enabled_var).grid(row=0, column=0, sticky="w", pady=6)
        ttk.Label(grid, text="队列").grid(row=0, column=1, sticky="e")
        ttk.Combobox(grid, textvariable=tts_queue_var, values=["replace", "append"], width=10, state="readonly").grid(row=0, column=2, padx=6, sticky="w")
        ttk.Checkbutton(grid, text="情绪语速", variable=tts_emotion_var).grid(row=0, column=3, sticky="w", padx=8)

        ttk.Checkbutton(grid, text="自动事件分发", variable=auto_enabled_var).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Label(grid, text="全局最小间隔(s)").grid(row=1, column=1, sticky="e")
        ttk.Entry(grid, textvariable=auto_min_gap_var, width=10).grid(row=1, column=2, padx=6, sticky="w")

        ttk.Checkbutton(grid, text="主动互动状态机", variable=pro_enabled_var).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Label(grid, text="平稳阶段间隔(s)").grid(row=2, column=1, sticky="e")
        ttk.Entry(grid, textvariable=pro_steady_var, width=10).grid(row=2, column=2, padx=6, sticky="w")

        ttk.Checkbutton(grid, text="对话导演引擎", variable=conv_enabled_var).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Label(grid, text="分层记忆天数").grid(row=3, column=1, sticky="e")
        ttk.Entry(grid, textvariable=conv_mem_days_var, width=10).grid(row=3, column=2, padx=6, sticky="w")

        for c in range(4):
            grid.grid_columnconfigure(c, weight=1 if c == 0 else 0)

        ttk.Label(win, text="实时状态").pack(anchor="w", padx=12, pady=(10, 2))
        status_text = tk.Text(win, height=12)
        status_text.pack(fill="both", expand=True, padx=12)

        def render_status():
            if not win.winfo_exists():
                return
            lines = [
                f"当前心情: {self.state.get('mood', 'Normal')} / 情绪值: {self.state.get('emotion_value', self.state.get('mood_score', 0))}",
                f"自动事件最近触发: {int(time.time() - float(self.last_auto_event_emit_ts or 0.0))} 秒前",
                f"主动阶段: {self.proactive_stage}",
                "",
                "--- 对话引擎命中统计 ---",
                self._conversation_engine_stats_text(),
            ]
            status_text.config(state="normal")
            status_text.delete("1.0", "end")
            status_text.insert("1.0", "\n".join(lines))
            status_text.config(state="disabled")
            win.after(1200, render_status)

        def save_all():
            tts_cfg["enabled"] = bool(tts_enabled_var.get())
            tts_cfg["queue_mode"] = str(tts_queue_var.get()).strip().lower() or "replace"
            tts_cfg["emotion_adapt"] = bool(tts_emotion_var.get())

            auto_cfg["enabled"] = bool(auto_enabled_var.get())
            auto_cfg["global_min_interval_seconds"] = max(1, min(120, int(auto_min_gap_var.get())))

            pro_cfg["enabled"] = bool(pro_enabled_var.get())
            pro_cfg["steady_interval_seconds"] = max(30, min(3600, int(pro_steady_var.get())))

            conv_cfg["enabled"] = bool(conv_enabled_var.get())
            conv_cfg["memory_recent_days"] = max(1, min(90, int(conv_mem_days_var.get())))

            self._sanitize_tts_config()
            self._sanitize_auto_event_config()
            self._sanitize_proactive_config()
            self._sanitize_conversation_engine_config()
            self._save_json(SETTINGS_PATH, self.settings)

            if tts_cfg.get("enabled", False):
                self._init_tts()
                self._apply_tts_properties()
            else:
                self._clear_tts_queue()

            self.show_lines(["系统总控参数已保存。"])

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=10)
        ttk.Button(btns, text="打开语音设置", command=self.open_tts_settings).pack(side="left")
        ttk.Button(btns, text="打开对话引擎设置", command=self.open_conversation_engine_settings).pack(side="left", padx=8)
        ttk.Button(btns, text="保存全部", command=save_all).pack(side="right")

        render_status()

    def open_conversation_engine_settings(self):
        win = tk.Toplevel(self.root)
        win.title("对话引擎设置")
        win.geometry("760x620")

        self._sanitize_conversation_engine_config()
        cfg = self.settings.setdefault("conversation_engine", {})

        enabled_var = tk.BooleanVar(value=bool(cfg.get("enabled", True)))
        hook_cd_var = tk.IntVar(value=int(cfg.get("hook_cooldown_seconds", 180)))
        hook_hist_var = tk.IntVar(value=int(cfg.get("hook_history_size", 20)))
        recent_days_var = tk.IntVar(value=int(cfg.get("memory_recent_days", 14)))

        top = ttk.Frame(win)
        top.pack(fill="x", padx=12, pady=(12, 8))
        ttk.Checkbutton(top, text="启用对话导演引擎", variable=enabled_var).pack(side="left")

        row1 = ttk.Frame(win)
        row1.pack(fill="x", padx=12, pady=4)
        ttk.Label(row1, text="梗冷却(秒)").pack(side="left")
        ttk.Entry(row1, textvariable=hook_cd_var, width=8).pack(side="left", padx=8)
        ttk.Label(row1, text="梗历史窗口").pack(side="left", padx=(18, 0))
        ttk.Entry(row1, textvariable=hook_hist_var, width=8).pack(side="left", padx=8)
        ttk.Label(row1, text="近期记忆天数").pack(side="left", padx=(18, 0))
        ttk.Entry(row1, textvariable=recent_days_var, width=8).pack(side="left", padx=8)

        ttk.Label(win, text="话题提示池（每行一个）").pack(anchor="w", padx=12, pady=(8, 2))
        topic_text = tk.Text(win, height=6)
        topic_text.pack(fill="x", padx=12)
        topic_text.insert("1.0", "\n".join([str(x) for x in cfg.get("topic_hints", [])]))

        ttk.Label(win, text="风格句池 Hook（每行一个）").pack(anchor="w", padx=12, pady=(8, 2))
        hook_text = tk.Text(win, height=8)
        hook_text.pack(fill="x", padx=12)
        hook_text.insert("1.0", "\n".join([str(x) for x in cfg.get("hook_pool", [])]))

        ttk.Label(win, text="实时命中统计").pack(anchor="w", padx=12, pady=(10, 2))
        stats = tk.Text(win, height=10)
        stats.pack(fill="both", expand=True, padx=12)
        stats.insert("1.0", self._conversation_engine_stats_text())
        stats.config(state="disabled")

        def refresh_stats():
            if not win.winfo_exists():
                return
            stats.config(state="normal")
            stats.delete("1.0", "end")
            stats.insert("1.0", self._conversation_engine_stats_text())
            stats.config(state="disabled")
            win.after(1500, refresh_stats)

        def parse_lines(text_widget):
            lines = []
            for raw in text_widget.get("1.0", "end").splitlines():
                s = raw.strip()
                if s:
                    lines.append(s)
            return lines

        def save_settings():
            cfg["enabled"] = bool(enabled_var.get())
            cfg["hook_cooldown_seconds"] = max(30, min(3600, int(hook_cd_var.get())))
            cfg["hook_history_size"] = max(5, min(200, int(hook_hist_var.get())))
            cfg["memory_recent_days"] = max(1, min(90, int(recent_days_var.get())))
            topics = parse_lines(topic_text)
            hooks = parse_lines(hook_text)
            if topics:
                cfg["topic_hints"] = topics
            if hooks:
                cfg["hook_pool"] = hooks
            self._sanitize_conversation_engine_config()
            self._save_json(SETTINGS_PATH, self.settings)
            self.show_lines(["对话引擎设置已保存。"])

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=10)
        ttk.Button(btns, text="刷新统计", command=refresh_stats).pack(side="left")
        ttk.Button(btns, text="保存", command=save_settings).pack(side="right")
        ttk.Button(btns, text="关闭", command=win.destroy).pack(side="right", padx=8)

        refresh_stats()

    def open_nanobot_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Nanobot 工具设置")
        win.geometry("720x560")

        self._sanitize_nanobot_config()
        cfg = self.settings.setdefault("nanobot", {})

        enabled_var = tk.BooleanVar(value=bool(cfg.get("enabled", False)))
        bio_var = tk.BooleanVar(value=bool(cfg.get("bio_lab_enabled", True)))
        web_var = tk.BooleanVar(value=bool(cfg.get("web_enabled", True)))
        policy_var = tk.StringVar(value=str(cfg.get("source_policy", "fixed_only")))
        timeout_var = tk.IntVar(value=int(cfg.get("timeout_seconds", 60)))
        config_path_var = tk.StringVar(value=str(cfg.get("config_path", "")))
        workspace_var = tk.StringVar(value=str(cfg.get("workspace", "")))
        model_var = tk.StringVar(value=str(cfg.get("model", "")))

        top = ttk.Frame(win)
        top.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Checkbutton(top, text="启用 Nanobot 引擎", variable=enabled_var).pack(side="left")
        ttk.Checkbutton(top, text="生信工具", variable=bio_var).pack(side="left", padx=12)
        ttk.Checkbutton(top, text="爬数/检索", variable=web_var).pack(side="left")

        row1 = ttk.Frame(win)
        row1.pack(fill="x", padx=12, pady=6)
        ttk.Label(row1, text="配置路径").pack(side="left")
        ttk.Entry(row1, textvariable=config_path_var).pack(side="left", fill="x", expand=True, padx=8)

        row2 = ttk.Frame(win)
        row2.pack(fill="x", padx=12, pady=6)
        ttk.Label(row2, text="工作目录").pack(side="left")
        ttk.Entry(row2, textvariable=workspace_var).pack(side="left", fill="x", expand=True, padx=8)

        row3 = ttk.Frame(win)
        row3.pack(fill="x", padx=12, pady=6)
        ttk.Label(row3, text="模型").pack(side="left")
        ttk.Entry(row3, textvariable=model_var).pack(side="left", fill="x", expand=True, padx=8)

        row4 = ttk.Frame(win)
        row4.pack(fill="x", padx=12, pady=6)
        ttk.Label(row4, text="超时(s)").pack(side="left")
        ttk.Entry(row4, textvariable=timeout_var, width=8).pack(side="left", padx=8)
        ttk.Label(row4, text="数据源策略").pack(side="left", padx=(12, 0))
        ttk.Combobox(
            row4,
            textvariable=policy_var,
            values=["fixed_only", "prefer_fixed", "off"],
            width=12,
            state="readonly",
        ).pack(side="left", padx=8)

        ttk.Label(win, text="固定数据库/站点（每行一个，可写域名或完整URL）").pack(anchor="w", padx=12, pady=(8, 2))
        source_text = tk.Text(win, height=8)
        source_text.pack(fill="x", padx=12)
        source_text.insert("1.0", "\n".join([str(x) for x in cfg.get("fixed_sources", [])]))

        ttk.Label(win, text="策略说明：fixed_only=仅允许固定来源；prefer_fixed=优先固定来源；off=不限制").pack(anchor="w", padx=12, pady=(6, 2))

        def parse_sources():
            items = []
            for raw in source_text.get("1.0", "end").splitlines():
                s = raw.strip()
                if s:
                    items.append(s)
            return items

        def save_settings():
            cfg["enabled"] = bool(enabled_var.get())
            cfg["bio_lab_enabled"] = bool(bio_var.get())
            cfg["web_enabled"] = bool(web_var.get())
            cfg["source_policy"] = str(policy_var.get()).strip() or "fixed_only"
            cfg["timeout_seconds"] = int(timeout_var.get())
            cfg["config_path"] = str(config_path_var.get()).strip()
            cfg["workspace"] = str(workspace_var.get()).strip()
            cfg["model"] = str(model_var.get()).strip()
            cfg["fixed_sources"] = parse_sources()
            self._sanitize_nanobot_config()
            self._save_json(SETTINGS_PATH, self.settings)
            self.show_lines(["Nanobot 设置已保存。"])

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=10)
        ttk.Button(btns, text="保存", command=save_settings).pack(side="right")
        ttk.Button(btns, text="关闭", command=win.destroy).pack(side="right", padx=8)

    def open_multimodal_window(self):
        try:
            from multimodal import EmbeddingIndex
        except Exception:
            EmbeddingIndex = None

        win = tk.Toplevel(self.root)
        win.title("多模态工具（原型）")
        win.geometry("520x360")

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        status_label = ttk.Label(frame, text="多模态模块加载：%s" % ("可用" if EmbeddingIndex else "不可用"))
        status_label.pack(anchor="w")

        # simple controls: create index, add text docs, search
        docs_text = tk.Text(frame, height=6)
        docs_text.pack(fill="x", pady=6)
        docs_text.insert("1.0", "在此输入每行一条文本，用于构建示例索引。")

        def build_index():
            if EmbeddingIndex is None:
                self.show_lines(["未安装 sentence-transformers，无法构建索引。请见 requirements.txt。"])
                return
            texts = [l.strip() for l in docs_text.get("1.0","end").splitlines() if l.strip()]
            if not texts:
                self.show_lines(["请先输入样本文本。"])
                return
            idx = EmbeddingIndex(persist_path=str(BASE_DIR / "multimodal" / "index"))
            idx.add_docs(texts, metadatas=[{"text":t} for t in texts])
            self.multimodal_idx = idx
            status_label.config(text="索引已构建，文档数：%d" % len(idx.ids))

        btn_build = ttk.Button(frame, text="构建索引", command=build_index)
        btn_build.pack(anchor="e", pady=6)

        search_row = ttk.Frame(frame)
        search_row.pack(fill="x", pady=6)
        qvar = tk.StringVar()
        ttk.Entry(search_row, textvariable=qvar).pack(side="left", fill="x", expand=True, padx=6)
        def do_search():
            q = qvar.get().strip()
            if not q or not getattr(self, 'multimodal_idx', None):
                self.show_lines(["请先构建索引并输入查询。"])
                return
            results = self.multimodal_idx.search([q], topk=5)[0]
            lines = [f"{r['metadata'].get('text','')} (score={r['score']:.4f})" for r in results]
            self.show_lines(lines)

        ttk.Button(search_row, text="搜索", command=do_search).pack(side="right")

        ttk.Label(frame, text="提示：若临时不可用，请运行 `pip install -r requirements.txt` 安装依赖。").pack(anchor="w", pady=(8,0))
        ttk.Button(frame, text="关闭", command=win.destroy).pack(anchor="e", pady=8)
        
    def open_highlight_preview(self, path):
        try:
            if not path:
                return
            img = Image.open(path)
            win = tk.Toplevel(self.root)
            win.title(f"预览：{path}")
            # resize to fit
            w, h = img.size
            maxw, maxh = 800, 600
            scale = min(1.0, maxw / w, maxh / h)
            if scale < 1.0:
                img = img.resize((int(w*scale), int(h*scale)))
            photo = ImageTk.PhotoImage(img)
            lbl = tk.Label(win, image=photo)
            lbl.image = photo
            lbl.pack()
        except Exception as e:
            self.show_lines([f"无法预览图片：{e}"])

    def open_latest_highlight(self):
        try:
            screenshots_dir = BASE_DIR / "screenshots"
            if not screenshots_dir.exists():
                self.show_lines(["暂无高亮截图。"])
                return
            files = list(screenshots_dir.glob("screen_highlight_*.png"))
            if not files:
                self.show_lines(["暂无高亮截图。"])
                return
            latest = max(files, key=lambda p: p.stat().st_mtime)
            self.open_highlight_preview(str(latest))
        except Exception as e:
            self.show_lines([f"无法打开高亮截图：{e}"])

    def _update_highlight_thumbnail(self, path):
        try:
            img = Image.open(path)
            # create thumbnail
            img.thumbnail((100, 80))
            photo = ImageTk.PhotoImage(img)
            self.highlight_photo = photo
            # create or update on canvas at top-right
            try:
                if hasattr(self, 'highlight_img_id') and self.highlight_img_id:
                    self.canvas.itemconfig(self.highlight_img_id, image=photo)
                else:
                    self.highlight_img_id = self.canvas.create_image(360, 60, image=photo, anchor='ne')
                    # bind click to open preview
                    self.canvas.tag_bind(self.highlight_img_id, '<Button-1>', lambda e: self.open_highlight_preview(path))
            except Exception:
                # fallback: show tip
                self.show_lines([f"已保存高亮截图：{path}"])
        except Exception:
            pass
        

    def _append_note_from_text(self, text_widget):
        content = text_widget.get("1.0", "end").strip()
        if not content:
            return
        with NOTES_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n[观察日记 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n{content}\n")
        self.show_lines(["日记已追加到 notes.txt"])

    def open_launcher_window(self):
        win = tk.Toplevel(self.root)
        win.title("管家服务")
        win.geometry("520x340")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        launchers = self.settings.get("launchers", {})

        def refresh():
            for c in frame.winfo_children():
                c.destroy()
            for name, cmd in launchers.items():
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=4)
                ttk.Label(row, text=f"{name}: {cmd}").pack(side="left", fill="x", expand=True)
                ttk.Button(row, text="启动", command=lambda c=cmd, n=name: start_one(c, n)).pack(side="right")

        def start_one(command, name):
            try:
                os.startfile(command) if os.path.exists(command) else subprocess.Popen(command, shell=True)
                self.show_lines([f"正在为您启动 {name} ..."])
            except Exception as e:
                self.show_lines([f"启动失败: {e}"])

        ttk.Button(win, text="刷新", command=refresh).pack()
        refresh()

    def open_game_window(self):
        win = tk.Toplevel(self.root)
        win.title("小游戏")
        win.geometry("420x280")
        out_var = tk.StringVar(value="选择一个游戏")
        ttk.Label(win, textvariable=out_var, wraplength=380).pack(pady=10)

        def rps(user):
            role = random.choice(["石头", "剪刀", "布"])
            if user == role:
                result = "平局"
            elif (user, role) in (("石头", "剪刀"), ("剪刀", "布"), ("布", "石头")):
                result = "你赢啦"
                self._add_coins(1)
            else:
                result = "我赢啦"
            out_var.set(f"你: {user} / 我: {role} => {result}")

        def dice():
            me = random.randint(1, 100)
            you = random.randint(1, 100)
            if you > me:
                self._add_coins(1)
                out_var.set(f"你掷到 {you}，我掷到 {me}，你赢！金币+1")
            else:
                out_var.set(f"你掷到 {you}，我掷到 {me}，我赢~")

        btn = ttk.Frame(win)
        btn.pack(pady=8)
        ttk.Button(btn, text="石头", command=lambda: rps("石头")).pack(side="left", padx=4)
        ttk.Button(btn, text="剪刀", command=lambda: rps("剪刀")).pack(side="left", padx=4)
        ttk.Button(btn, text="布", command=lambda: rps("布")).pack(side="left", padx=4)
        ttk.Button(win, text="掷骰子(1-100)", command=dice).pack(pady=8)

    def open_file_sort_window(self):
        win = tk.Toplevel(self.root)
        win.title("本地文件整理")
        win.geometry("520x250")
        src = tk.StringVar(value=str(BASE_DIR))
        dst = tk.StringVar(value=str(BASE_DIR / "整理输出"))

        ttk.Label(win, text="杂乱目录").pack(anchor="w", padx=8, pady=(8, 0))
        ttk.Entry(win, textvariable=src).pack(fill="x", padx=8)
        ttk.Label(win, text="目标目录").pack(anchor="w", padx=8, pady=(8, 0))
        ttk.Entry(win, textvariable=dst).pack(fill="x", padx=8)

        def run_sort():
            s = Path(src.get().strip())
            d = Path(dst.get().strip())
            if not s.exists():
                self.show_lines(["源目录不存在。"])
                return
            d.mkdir(parents=True, exist_ok=True)
            rules = {
                "图片": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
                "文档": {".txt", ".md", ".doc", ".docx", ".pdf"},
                "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz"},
                "其他": set(),
            }
            moved = 0
            for p in s.iterdir():
                if not p.is_file():
                    continue
                cat = "其他"
                for k, exts in rules.items():
                    if p.suffix.lower() in exts:
                        cat = k
                        break
                target = d / cat
                target.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(target / p.name))
                moved += 1
            self.show_lines([f"桌面整理完毕，已移动 {moved} 个文件。"])

        ttk.Button(win, text="一键整理", command=run_sort).pack(pady=12)

    def open_shop_window(self):
        win = tk.Toplevel(self.root)
        win.title("商城")
        win.geometry("460x360")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        def refresh():
            for c in frame.winfo_children():
                c.destroy()
            for idx, item in enumerate(self.items):
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=4)
                ttk.Label(row, text=f"{item['name']} 价格:{item['price']} 当前:{item['qty']}").pack(side="left")
                ttk.Button(row, text="购买", command=lambda i=idx: buy(i)).pack(side="right")

        def buy(i):
            price = int(self.items[i].get("price", 1))
            if int(self.state.get("coins", 0)) < price:
                self.show_lines(["金币不够啦。"])
                return
            self.state["coins"] -= price
            self.items[i]["qty"] += 1
            self._save_json(ITEMS_PATH, self.items)
            self._add_coins(0)
            self.show_lines([f"购买成功：{self.items[i]['name']}"])
            refresh()

        refresh()

    def open_notes_window(self):
        win = tk.Toplevel(self.root)
        win.title("随手记")
        win.geometry("520x320")
        text = tk.Text(win, height=10)
        text.pack(fill="both", expand=True, padx=8, pady=8)

        def save_note():
            line = text.get("1.0", "end").strip()
            if not line:
                return
            with NOTES_PATH.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
            self.show_lines(["已记下。"])
            win.destroy()

        ttk.Button(win, text="保存", command=save_note).pack(pady=(0, 8))

    # ── iGEM 助手 UI 窗口 ──

    def _open_meeting_record_window(self, preset_notes=""):
        """打开记录组会窗口。"""
        win = tk.Toplevel(self.root)
        win.title("记录组会")
        win.geometry("560x480")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 标题
        row0 = ttk.Frame(frame)
        row0.pack(fill="x", pady=4)
        ttk.Label(row0, text="会议标题：").pack(side="left")
        title_var = tk.StringVar(value="iGEM组会")
        ttk.Entry(row0, textvariable=title_var, width=30).pack(side="left", fill="x", expand=True)

        # 日期
        row1 = ttk.Frame(frame)
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="日期：").pack(side="left")
        date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(row1, textvariable=date_var, width=15).pack(side="left")

        # 参会人
        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="参会人（逗号分隔）：").pack(side="left")
        attendees_var = tk.StringVar()
        ttk.Entry(row2, textvariable=attendees_var, width=40).pack(side="left", fill="x", expand=True)

        # 笔记
        ttk.Label(frame, text="组会笔记：").pack(anchor="w", pady=(8, 2))
        notes_text = tk.Text(frame, height=12)
        notes_text.pack(fill="both", expand=True)
        if preset_notes:
            notes_text.insert("1.0", preset_notes)

        # 按钮
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(8, 0))

        def save_meeting():
            title = title_var.get().strip()
            date = date_var.get().strip()
            attendees = [a.strip() for a in attendees_var.get().split(",") if a.strip()]
            raw_notes = notes_text.get("1.0", "end").strip()
            if not raw_notes:
                messagebox.showwarning("提示", "请输入组会笔记。")
                return
            meeting = self.meeting_tracker.add_meeting(date, title, attendees, raw_notes)
            # 自动生成摘要
            summary = self.meeting_tracker.summarize_meeting(meeting["id"])
            result_text = self.meeting_tracker.format_meeting_text(meeting)
            # 推送飞书
            igem_cfg = self.settings.get("igem_assistant", {})
            if igem_cfg.get("meeting_auto_push_feishu") and self.feishu:
                self.feishu.notify_event("meeting_summary", result_text)
            # 建议创建任务
            if summary and summary.get("next_steps"):
                suggestions = self.task_board.suggest_tasks_from_meeting(summary["next_steps"])
                if suggestions:
                    result_text += f"\n\n建议创建的任务（{len(suggestions)}条）："
                    for s in suggestions[:3]:
                        result_text += f"\n  · {s['title']}"
            self._show_text_window("组会记录已保存", result_text)
            win.destroy()

        ttk.Button(btn_row, text="保存并AI摘要", command=save_meeting).pack(side="left")
        ttk.Button(btn_row, text="取消", command=win.destroy).pack(side="right")

    def _open_meeting_list_window(self):
        """打开会议记录列表窗口。"""
        win = tk.Toplevel(self.root)
        win.title("会议记录")
        win.geometry("600x500")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        # 搜索栏
        search_row = ttk.Frame(frame)
        search_row.pack(fill="x", pady=(0, 8))
        search_var = tk.StringVar()
        ttk.Entry(search_row, textvariable=search_var, width=30).pack(side="left", fill="x", expand=True)
        ttk.Button(search_row, text="搜索", command=lambda: refresh(search_var.get())).pack(side="left", padx=4)
        ttk.Button(search_row, text="显示全部", command=lambda: refresh("")).pack(side="left")

        # 列表
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True)
        text_widget = scrolledtext.ScrolledText(list_frame, wrap='word', state='disabled')
        text_widget.pack(fill="both", expand=True)

        def refresh(query=""):
            meetings = self.meeting_tracker.query_meetings(query) if query else self.meeting_tracker.get_recent_meetings(20)
            text_widget.config(state='normal')
            text_widget.delete('1.0', 'end')
            if not meetings:
                text_widget.insert('1.0', '暂无会议记录。')
            else:
                for m in meetings:
                    text_widget.insert('end', self.meeting_tracker.format_meeting_text(m))
                    text_widget.insert('end', '\n' + '─' * 40 + '\n')
            text_widget.config(state='disabled')

        refresh()

    def _open_workflow_window(self):
        """打开生信工作流向导窗口。"""
        win = tk.Toplevel(self.root)
        win.title("生信工作流")
        win.geometry("560x480")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(frame, text="选择工作流：", font=("", 11, "bold")).pack(anchor="w")

        workflows = self.bio_workflow.list_workflows()
        for wf in workflows:
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=3)
            btn = ttk.Button(
                row, text=wf["display_name"],
                command=lambda t=wf["type"]: self._start_workflow_ui(t, win),
            )
            btn.pack(side="left")
            ttk.Label(row, text=f"  触发词：{', '.join(wf['trigger_keywords'][:3])}",
                      foreground="gray").pack(side="left")

        ttk.Separator(frame).pack(fill="x", pady=8)

        # 活跃会话信息
        session_frame = ttk.LabelFrame(frame, text="当前工作流")
        session_frame.pack(fill="both", expand=True)

        session_text = scrolledtext.ScrolledText(session_frame, wrap='word', height=8, state='disabled')
        session_text.pack(fill="both", expand=True)

        def refresh_session():
            session_text.config(state='normal')
            session_text.delete('1.0', 'end')
            if self._active_workflow_session_id:
                info = self.bio_workflow.get_session_info(self._active_workflow_session_id)
                if info:
                    session_text.insert('1.0',
                        f"工作流：{info.get('display_name', '')}\n"
                        f"步骤：{info.get('step', '?')}/{info.get('total_steps', '?')}\n"
                        f"状态：{info.get('status', '')}\n"
                        f"参数：{json.dumps(info.get('params', {}), ensure_ascii=False)}\n"
                        f"\n请在聊天窗口中继续输入参数。")
                else:
                    session_text.insert('1.0', '当前没有活跃的工作流会话。')
            else:
                session_text.insert('1.0', '当前没有活跃的工作流会话。\n选择上方工作流开始。')
            session_text.config(state='disabled')

        btn_row = ttk.Frame(session_frame)
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="刷新", command=refresh_session).pack(side="left")
        ttk.Button(btn_row, text="取消工作流", command=lambda: (
            self.bio_workflow.cancel_session(self._active_workflow_session_id) if self._active_workflow_session_id else None,
            setattr(self, '_active_workflow_session_id', None),
            refresh_session(),
        )).pack(side="left", padx=4)

        refresh_session()

    def _start_workflow_ui(self, wf_type, parent_win=None):
        """从UI启动工作流。"""
        result = self.bio_workflow.start_session(wf_type)
        if result:
            self._active_workflow_session_id = result["session_id"]
            choices_str = ""
            if result.get("choices"):
                choices_str = f"（选项：{', '.join(result['choices'])}）"
            self.show_lines([
                f"已启动工作流：{result['display_name']}",
                f"步骤 {result['step']}/{result['total_steps']}：{result['prompt']}{choices_str}",
                "请在聊天窗口中继续输入。",
            ])
        else:
            self.show_lines([f"未找到工作流「{wf_type}」。"])

    def _open_doc_hub_window(self):
        """打开文档中心窗口。"""
        win = tk.Toplevel(self.root)
        win.title("文档中心")
        win.geometry("640x500")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        # 分类标签
        cat_frame = ttk.Frame(frame)
        cat_frame.pack(fill="x", pady=(0, 8))

        cats = self.doc_hub.get_all_categories_with_count()
        for cat in cats:
            if cat["count"] > 0:
                ttk.Button(
                    cat_frame,
                    text=f"{cat['icon']} {cat['label']}({cat['count']})",
                    command=lambda k=cat["key"]: show_category(k),
                ).pack(side="left", padx=2)

        ttk.Button(cat_frame, text="全部", command=lambda: show_category("")).pack(side="left", padx=2)

        # 搜索
        search_row = ttk.Frame(frame)
        search_row.pack(fill="x", pady=(0, 8))
        search_var = tk.StringVar()
        ttk.Entry(search_row, textvariable=search_var, width=30).pack(side="left", fill="x", expand=True)
        ttk.Button(search_row, text="搜索", command=lambda: do_search(search_var.get())).pack(side="left", padx=4)

        # 文档列表
        doc_text = scrolledtext.ScrolledText(frame, wrap='word', state='disabled')
        doc_text.pack(fill="both", expand=True)

        # 操作按钮
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="添加文档", command=add_doc).pack(side="left")
        ttk.Button(btn_row, text="设置监视文件夹", command=setup_watch).pack(side="left", padx=4)
        ttk.Button(btn_row, text="扫描", command=scan_folders).pack(side="left", padx=4)

        def show_category(category):
            docs = self.doc_hub.get_by_category(category) if category else self.doc_hub.data.get("documents", [])
            refresh_docs(docs)

        def do_search(query):
            results = self.doc_hub.search(query)
            refresh_docs(results)

        def refresh_docs(docs):
            doc_text.config(state='normal')
            doc_text.delete('1.0', 'end')
            if not docs:
                doc_text.insert('1.0', '暂无文档。')
            else:
                for d in docs:
                    doc_text.insert('end', self.doc_hub.format_doc_text(d))
                    doc_text.insert('end', '\n' + '─' * 40 + '\n')
            doc_text.config(state='disabled')

        def add_doc():
            path = filedialog.askopenfilename(title="选择文档")
            if path:
                # 简单分类选择
                cat_names = {k: v["label"] for k, v in {
                    "wetlab_protocols": {"label": "湿实验Protocol"},
                    "drylab_tools": {"label": "干实验工具"},
                    "safety_rules": {"label": "安全规范"},
                    "competition_rules": {"label": "比赛规则"},
                    "wiki": {"label": "Wiki"},
                    "meeting_records": {"label": "会议记录"},
                    "tutorials": {"label": "教程"},
                    "other": {"label": "其他"},
                }.items()}
                self.doc_hub.add_document(path)
                self.show_lines([f"已添加文档：{os.path.basename(path)}"])
                show_category("")

        def setup_watch():
            folder = filedialog.askdirectory(title="选择监视文件夹")
            if folder:
                self.doc_hub.add_watch_folder(folder)
                self.show_lines([f"已添加监视文件夹：{folder}"])

        def scan_folders():
            stats = self.doc_hub.scan_watch_folders()
            self.show_lines([f"扫描完成：新增 {stats['added']} 篇，更新 {stats['updated']} 篇。"])
            show_category("")

        show_category("")

    def _open_task_board_window(self):
        """打开任务看板窗口。"""
        win = tk.Toplevel(self.root)
        win.title("任务看板")
        win.geometry("700x500")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        # 添加任务
        add_row = ttk.Frame(frame)
        add_row.pack(fill="x", pady=(0, 8))
        task_var = tk.StringVar()
        ttk.Entry(add_row, textvariable=task_var, width=30).pack(side="left", fill="x", expand=True)
        ttk.Button(add_row, text="添加任务", command=lambda: add_task()).pack(side="left", padx=4)

        # 看板视图
        board_frame = ttk.Frame(frame)
        board_frame.pack(fill="both", expand=True)

        board = self.task_board.get_board_view()
        status_cols = {
            "todo": ("📋 待办", "#e8e8e8"),
            "in_progress": ("🔨 进行中", "#fff3cd"),
            "done": ("✅ 已完成", "#d4edda"),
            "blocked": ("🚧 卡住", "#f8d7da"),
        }

        for col_idx, (status, (label, color)) in enumerate(status_cols.items()):
            col = ttk.LabelFrame(board_frame, text=f"{label} ({len(board.get(status, []))})")
            col.grid(row=0, column=col_idx, sticky="nsew", padx=4, pady=4)
            board_frame.columnconfigure(col_idx, weight=1)
            board_frame.rowconfigure(0, weight=1)

            tasks = board.get(status, [])
            for t in tasks[:10]:
                task_row = ttk.Frame(col)
                task_row.pack(fill="x", padx=4, pady=2)
                ttk.Label(task_row, text=f"· {t['title']}", wraplength=140).pack(anchor="w")
                # 完成按钮
                if status != "done":
                    ttk.Button(task_row, text="✓", width=3,
                               command=lambda tid=t["id"]: complete_task(tid)).pack(side="right")

        def add_task():
            title = task_var.get().strip()
            if title:
                self.task_board.add_task(title)
                task_var.set("")
                self.show_lines([f"已创建任务：{title}"])
                win.destroy()
                self._open_task_board_window()

        def complete_task(task_id):
            self.task_board.update_task(task_id, {"status": "done"})
            self.show_lines(["任务已完成！"])
            win.destroy()
            self._open_task_board_window()

    def _open_team_window(self):
        """打开团队成员窗口。"""
        win = tk.Toplevel(self.root)
        win.title("团队成员")
        win.geometry("560x460")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 添加成员
        add_frame = ttk.LabelFrame(frame, text="添加成员")
        add_frame.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(add_frame)
        row1.pack(fill="x", pady=2)
        name_var = tk.StringVar()
        role_var = tk.StringVar()
        skills_var = tk.StringVar()
        ttk.Label(row1, text="姓名：").pack(side="left")
        ttk.Entry(row1, textvariable=name_var, width=10).pack(side="left")
        ttk.Label(row1, text="角色：").pack(side="left", padx=(8, 0))
        ttk.Entry(row1, textvariable=role_var, width=10).pack(side="left")
        ttk.Label(row1, text="技能：").pack(side="left", padx=(8, 0))
        ttk.Entry(row1, textvariable=skills_var, width=15).pack(side="left")

        ttk.Button(add_frame, text="添加", command=lambda: add_member()).pack(pady=4)

        # 成员列表
        member_text = scrolledtext.ScrolledText(frame, wrap='word', state='disabled')
        member_text.pack(fill="both", expand=True)

        def refresh():
            member_text.config(state='normal')
            member_text.delete('1.0', 'end')
            members = self.task_board.get_all_members()
            if not members:
                member_text.insert('1.0', '暂无团队成员。')
            else:
                for m in members:
                    member_text.insert('end', self.task_board.format_member_text(m))
                    # 显示该成员的任务
                    tasks = self.task_board.get_tasks_by_assignee(m["id"])
                    active = [t for t in tasks if t.get("status") in ("todo", "in_progress")]
                    if active:
                        member_text.insert('end', f"  当前任务：{', '.join(t['title'][:20] for t in active[:3])}\n")
                    member_text.insert('end', '\n')
            member_text.config(state='disabled')

        def add_member():
            name = name_var.get().strip()
            if name:
                skills = [s.strip() for s in skills_var.get().split(",") if s.strip()]
                self.task_board.add_member(name, role_var.get().strip(), skills)
                name_var.set("")
                role_var.set("")
                skills_var.set("")
                self.show_lines([f"已添加成员：{name}"])
                refresh()

        refresh()

    def _open_igem_settings_window(self):
        """打开iGEM助手设置窗口。"""
        win = tk.Toplevel(self.root)
        win.title("iGEM助手设置")
        win.geometry("480x400")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        igem_cfg = self.settings.setdefault("igem_assistant", {})

        # 团队名称
        row0 = ttk.Frame(frame)
        row0.pack(fill="x", pady=4)
        ttk.Label(row0, text="团队名称：").pack(side="left")
        team_name_var = tk.StringVar(value=igem_cfg.get("team_name", ""))
        ttk.Entry(row0, textvariable=team_name_var, width=25).pack(side="left", fill="x", expand=True)

        # 开关选项
        vars_dict = {}
        toggle_items = [
            ("meeting_auto_push_feishu", "会议记录自动推送飞书"),
            ("doc_auto_index", "文档自动索引"),
            ("task_deadline_push_feishu", "任务截止推送飞书"),
        ]
        for key, label in toggle_items:
            var = tk.BooleanVar(value=bool(igem_cfg.get(key, True)))
            ttk.Checkbutton(frame, text=label, variable=var).pack(anchor="w", pady=2)
            vars_dict[key] = var

        # 截止提醒时间
        row_remind = ttk.Frame(frame)
        row_remind.pack(fill="x", pady=4)
        ttk.Label(row_remind, text="截止提醒提前（小时）：").pack(side="left")
        remind_var = tk.StringVar(value=str(igem_cfg.get("task_deadline_remind_hours_before", 24)))
        ttk.Entry(row_remind, textvariable=remind_var, width=8).pack(side="left")

        # Bio API
        row_bio = ttk.Frame(frame)
        row_bio.pack(fill="x", pady=4)
        ttk.Label(row_bio, text="生信API地址：").pack(side="left")
        bio_url_var = tk.StringVar(value=igem_cfg.get("bio_api_url", "http://127.0.0.1:18901"))
        ttk.Entry(row_bio, textvariable=bio_url_var, width=25).pack(side="left", fill="x", expand=True)

        def save_settings():
            igem_cfg["team_name"] = team_name_var.get().strip()
            for key, var in vars_dict.items():
                igem_cfg[key] = var.get()
            try:
                igem_cfg["task_deadline_remind_hours_before"] = int(remind_var.get())
            except ValueError:
                pass
            igem_cfg["bio_api_url"] = bio_url_var.get().strip()
            self._save_json(SETTINGS_PATH, self.settings)
            self.show_lines(["iGEM助手设置已保存。"])
            win.destroy()

        ttk.Button(frame, text="保存", command=save_settings).pack(pady=10)

    # ── iGEM 心跳集成 ──

    def _igem_heartbeat_tick(self, now):
        """iGEM助手心跳任务：文档自动索引、任务截止提醒。"""
        igem_cfg = self.settings.get("igem_assistant", {})
        if not igem_cfg.get("enabled", True):
            return

        # 每小时检查一次文档自动索引
        if igem_cfg.get("doc_auto_index", True):
            interval_min = igem_cfg.get("doc_index_interval_minutes", 60)
            doc_scan_key = now.strftime("%Y%m%d%H")
            if now.minute == 0 and now.second == 0 and self._igem_last_doc_scan != doc_scan_key:
                if self.doc_hub.data.get("watch_folders"):
                    self._igem_last_doc_scan = doc_scan_key
                    try:
                        stats = self.doc_hub.scan_watch_folders()
                        if stats["added"] > 0:
                            self.show_lines([f"文档中心：扫描发现 {stats['added']} 篇新文档。"])
                    except Exception:
                        pass

        # 任务截止提醒
        if now.minute == 0 and now.second == 0:
            try:
                hours_before = igem_cfg.get("task_deadline_remind_hours_before", 24)
                upcoming = self.task_board.check_deadlines(hours_before)
                if upcoming:
                    lines = ["任务截止提醒："]
                    for t in upcoming:
                        if t.get("is_overdue"):
                            lines.append(f"⚠ 已过期：{t['title']}")
                        else:
                            lines.append(f"⏰ {t['hours_remaining']}h 后截止：{t['title']}")
                    self.show_lines(lines)
                    # 推送飞书
                    if igem_cfg.get("task_deadline_push_feishu") and self.feishu:
                        self.feishu.notify_event("task_deadline", "\n".join(lines))
            except Exception:
                pass

    def _heartbeat(self):
        now = datetime.now()

        if now.minute == 0 and now.second == 0:
            hour_key = now.strftime("%Y%m%d%H")
            if self.state.get("last_chime") != hour_key:
                self.state["last_chime"] = hour_key
                self._hourly_chime()

        was_sleep = self.sleep_mode
        in_night_window = now.hour >= 23 or now.hour < 7
        manual_awake = time.time() < float(self.manual_awake_until_ts or 0.0)
        self.sleep_mode = in_night_window and (not manual_awake)
        if self.sleep_mode and not was_sleep:
            self.show_lines(["到休息时间啦，晚安~"])
        if (not self.sleep_mode) and was_sleep:
            if in_night_window:
                self.show_lines(["我醒着呢，要继续陪你吗？"])
            elif now.hour < 9:
                self.show_lines(["早安！新的一天开始咯。"])

        self._check_reminders(now)
        self._monitor_focus()
        self._idle_companion()
        self._maybe_emit_proactive_event()
        self._check_random_event(now)
        self._weather_tick()
        self._refresh_status_display()
        self._igem_heartbeat_tick(now)

        self._save_json(STATE_PATH, self.state)
        self._save_json(HISTORY_PATH, self.history)
        # ── 脏标记降频写盘 ──
        self.store.mark_dirty(str(STATE_PATH), str(HISTORY_PATH))
        self.store.persist_if_dirty({
            str(STATE_PATH): (STATE_PATH, self.state),
            str(HISTORY_PATH): (HISTORY_PATH, self.history),
        })
        self.root.after(1000, self._heartbeat)

    def _check_reminders(self, now):
        today = now.strftime("%Y-%m-%d")
        for reminder in self.reminders:
            t = reminder.get("time", "")
            try:
                hh, mm = [int(x) for x in t.split(":", 1)]
            except Exception:
                continue
            base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            pre = int(reminder.get("pre_minutes", 5))
            if reminder.get("last_date") == today:
                continue
            if abs((now - base).total_seconds()) < 1 or abs((now - (base - timedelta(minutes=pre))).total_seconds()) < 1:
                reminder["last_date"] = today
                self.root.attributes("-topmost", True)
                messagebox.showinfo("日程提醒", reminder.get("text", "到时间啦"))
                reminder_text = f"提醒：{reminder.get('text', '')}"
                self.show_lines([reminder_text])
                self._save_json(REMINDERS_PATH, self.reminders)
                # 推送到飞书
                self.feishu.notify_event("reminder", reminder_text)

    def _idle_companion(self):
        if self.game_mode:
            return
        if bool(self.settings.get("proactive", {}).get("enabled", True)):
            return
        idle_sec = time.time() - self.last_input_time
        if idle_sec < 20 * 60:
            return
        token = datetime.now().strftime("%Y%m%d%H%M")
        if self.state.get("last_idle_chat") == token:
            return
        self.state["last_idle_chat"] = token
        self.show_lines(["你还在忙吗？记得休息一下眼睛。"])

    def _check_random_event(self, now):
        freq = int(self.settings.get("random_event_minutes", 60))
        marker = f"{now.strftime('%Y%m%d')}-{now.hour}-{now.minute // max(1, freq)}"
        if now.minute % max(1, freq) == 0 and now.second == 0:
            if self.state.get("last_event") != marker:
                self.state["last_event"] = marker
                if not self.game_mode:
                    self.trigger_random_event()

    def _monitor_system_status(self):
        if psutil:
            cpu = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            cpu_hot = cpu > 85
            mem_low = (100 - vm.percent) <= 15

            if cpu_hot:
                if not self.system_overheat_mode:
                    self.system_overheat_mode = True
                    self.system_face_mode = random.choice(["faint", "sweat"])
                    self._apply_idle_image()
                    self.show_lines(["电脑好烫"])
                self._adjust_mood(-6, reason="system_cpu_hot")
            elif self.system_overheat_mode:
                self.system_overheat_mode = False
                self.system_face_mode = ""
                if not self.is_typing:
                    self._apply_idle_image()
                self._adjust_mood(2, reason="system_cpu_recover")

            if mem_low and not self.memory_low_notified and not cpu_hot:
                self.memory_low_notified = True
                self.show_lines(["脑容量不够了"])
                self._adjust_mood(-4, reason="system_mem_low")
            elif (not mem_low) and self.memory_low_notified:
                self.memory_low_notified = False
                self._adjust_mood(1, reason="system_mem_recover")
        self.root.after(5000, self._monitor_system_status)

    def _monitor_network(self):
        host = self.settings.get("network", {}).get("ping_host", "www.baidu.com")
        high = int(self.settings.get("network", {}).get("high_latency_ms", 250))
        latency = self._ping_ms(host)
        if latency is None:
            self.show_lines(["网络断开或信号很差。"])
            self._adjust_mood(-4, reason="network_offline")
        elif latency >= high * 1.5:
            self.show_lines([f"网络延迟很高：{latency}ms"])
            self._adjust_mood(-5, reason="network_very_high")
        elif latency >= high:
            self.show_lines([f"网络延迟有点高：{latency}ms"])
            self._adjust_mood(-3, reason="network_high")
        else:
            self._adjust_mood(1, reason="network_stable")
        self.root.after(60000, self._monitor_network)

    def _monitor_media_playback(self):
        cfg = self.settings.get("media", {})
        enabled = bool(cfg.get("enabled", True))
        poll_seconds = max(2, int(cfg.get("poll_seconds", 4)))
        cooldown = max(8, int(cfg.get("comment_cooldown_seconds", 20)))

        try:
            if enabled:
                current = self._get_current_media_track()
                if current:
                    title = self._normalize_track_text(current.get("title", ""))
                    artist = self._normalize_track_text(current.get("artist", ""))
                    source = self._normalize_track_text(current.get("source", "unknown"))
                    signature = f"{title}||{artist}"
                    if title and artist and signature != self.last_media_signature:
                        self.last_media_signature = signature
                        now_ts = time.time()
                        if now_ts - self.last_media_comment_ts >= cooldown:
                            self.last_media_comment_ts = now_ts
                            threading.Thread(target=self._request_music_comment, args=(title, artist, source), daemon=True).start()
        except Exception:
            pass

        self.root.after(poll_seconds * 1000, self._monitor_media_playback)

    def _read_screen_text(self, cfg):
        global pytesseract
        if pytesseract is None:
            try:
                pytesseract = importlib.import_module("pytesseract")
            except Exception:
                pytesseract = None
        text = ""
        tesseract_cmd = str(cfg.get("tesseract_cmd", "")).strip()
        if not tesseract_cmd:
            candidates = []
            pf = os.environ.get("ProgramFiles", "")
            pfx86 = os.environ.get("ProgramFiles(x86)", "")
            if pf:
                candidates.append(os.path.join(pf, "Tesseract-OCR", "tesseract.exe"))
            if pfx86:
                candidates.append(os.path.join(pfx86, "Tesseract-OCR", "tesseract.exe"))
            for p in candidates:
                if p and os.path.exists(p):
                    tesseract_cmd = p
                    break
        if pytesseract and tesseract_cmd:
            try:
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            except Exception:
                pass

        if pytesseract:
            try:
                img = ImageGrab.grab().convert("L")
                text = pytesseract.image_to_string(
                    img,
                    lang=str(cfg.get("ocr_lang", "chi_sim+eng")),
                    config="--psm 6",
                )
            except Exception:
                text = ""

        if not text:
            title, _proc = self._get_active_window_info()
            text = title or ""

        text = re.sub(r"\s+", " ", text).strip()
        max_chars = max(40, int(cfg.get("max_chars", 160)))
        return text[:max_chars]

    def _monitor_screen_roast(self):
        cfg = self.settings.get("screen_roast", {})
        interval = max(10, int(cfg.get("interval_seconds", 25)))
        try:
            if not cfg.get("enabled", False):
                return
            if self.sleep_mode or self.game_mode or self.busy or self.message_box_visible:
                return

            now_ts = time.time()
            cooldown = max(interval, int(cfg.get("cooldown_seconds", 90)))
            if now_ts - self.last_screen_roast_time < cooldown:
                return

            text = self._read_screen_text(cfg)
            min_chars = max(6, int(cfg.get("min_chars", 12)))
            if len(text) < min_chars:
                return

            fingerprint = re.sub(r"\W+", "", text.lower())[:80]
            if fingerprint and fingerprint == self.last_screen_fingerprint:
                return

            self.last_screen_roast_time = now_ts
            if fingerprint:
                self.last_screen_fingerprint = fingerprint
            threading.Thread(target=self._request_screen_roast, args=(text,), daemon=True).start()
            # 如果启用了屏幕详细解析，则并行请求详细评论（频率由 screen_comment 控制）
            try:
                scfg = self.settings.get("screen_comment", {})
                if scfg.get("enabled", False):
                    # 让专门的监控函数决定节流与触发，这里仅记录时间
                    pass
            except Exception:
                pass
        finally:
            self.root.after(interval * 1000, self._monitor_screen_roast)

    def _monitor_screen_comment(self):
        cfg = self.settings.get("screen_comment", {})
        interval = max(15, int(cfg.get("interval_seconds", 60)))
        try:
            if not cfg.get("enabled", False):
                return
            if self.sleep_mode or self.game_mode or self.busy or self.message_box_visible:
                return

            now_ts = time.time()
            cooldown = max(interval, int(cfg.get("cooldown_seconds", 180)))
            if now_ts - self.last_screen_comment_time < cooldown:
                return

            text = self._read_screen_text(cfg)
            min_chars = max(10, int(cfg.get("min_chars", 20)))
            if len(text) < min_chars:
                return

            fingerprint = re.sub(r"\W+", "", text.lower())[:400]
            if fingerprint and fingerprint == self.last_screen_fingerprint:
                return

            self.last_screen_comment_time = now_ts
            if fingerprint:
                self.last_screen_fingerprint = fingerprint
            threading.Thread(target=self._request_screen_comment, args=(text,), daemon=True).start()
        finally:
            self.root.after(interval * 1000, self._monitor_screen_comment)

    def _register_hotkey(self):
        try:
            cfg = self.settings.get("screen_comment", {})
            if not cfg.get("hotkey_enabled", False):
                return
            hk = str(cfg.get("hotkey", "ctrl+alt+c")).strip()
            # try keyboard module for global hotkey
            try:
                import keyboard
                # store handle
                self._hotkey_handle = keyboard.add_hotkey(hk, lambda: threading.Thread(target=self.toggle_screen_comment, daemon=True).start())
                return
            except Exception:
                self._hotkey_handle = None
            # fallback: bind to root (only when app has focus)
            try:
                seq = '<Control-Alt-Key-c>'
                self.root.bind_all(seq, lambda e: threading.Thread(target=self.toggle_screen_comment, daemon=True).start())
            except Exception:
                pass
        except Exception:
            pass

    def _unregister_hotkey(self):
        try:
            if self._hotkey_handle:
                try:
                    import keyboard
                    keyboard.remove_hotkey(self._hotkey_handle)
                except Exception:
                    pass
                self._hotkey_handle = None
        except Exception:
            pass

    def _detect_interactive_regions(self, pil_image, cfg):
        """Return list of dicts: {text, left, top, width, height} for likely interactive regions using pytesseract."""
        regions = []
        try:
            if pytesseract is None:
                return regions
            try:
                tesseract_cmd = str(cfg.get("tesseract_cmd", "")).strip()
                if tesseract_cmd:
                    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            except Exception:
                pass

            # use image_to_data (TSV) and parse
            try:
                data = pytesseract.image_to_data(pil_image, lang=str(cfg.get("ocr_lang", "chi_sim+eng")), config="--psm 6")
            except Exception:
                return regions

            lines = data.splitlines()
            if len(lines) <= 1:
                return regions
            headers = lines[0].split("\t")
            for row in lines[1:]:
                parts = row.split("\t")
                if len(parts) < 12:
                    continue
                try:
                    left = int(parts[6]); top = int(parts[7]); width = int(parts[8]); height = int(parts[9])
                    conf = parts[10]
                    text = "\t".join(parts[11:]).strip()
                except Exception:
                    continue
                if not text:
                    continue
                try:
                    conf_val = int(float(conf))
                except Exception:
                    conf_val = 0

                keywords = ["确定","取消","关闭","保存","提交","登录","注册","更多","下一步","播放","下载"]
                score = 0
                if any(k in text for k in keywords):
                    score += 5
                if len(text) >= 2:
                    score += 2
                if conf_val >= 50:
                    score += 2
                if score >= 5:
                    regions.append({"text": text, "left": left, "top": top, "width": width, "height": height, "conf": conf_val})
            # sort by area desc and return top 8
            regions = sorted(regions, key=lambda r: r.get("width",0)*r.get("height",0), reverse=True)[:8]
            return regions
        except Exception:
            return regions

    def _save_highlight_image(self, pil_image, regions):
        try:
            if not regions:
                return None
            draw = ImageDraw.Draw(pil_image)
            for r in regions:
                x0 = r.get("left",0); y0 = r.get("top",0); w = r.get("width",0); h = r.get("height",0)
                x1 = x0 + w; y1 = y0 + h
                draw.rectangle([x0,y0,x1,y1], outline="red", width=2)
                txt = r.get("text","")[:20]
                draw.text((x0, max(y0-14,0)), txt, fill="red")
            screenshots_dir = BASE_DIR / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            fname = screenshots_dir / f"screen_highlight_{int(time.time())}.png"
            pil_image.save(str(fname))
            # update last highlight and thumbnail on main UI
            try:
                self.last_highlight_path = str(fname)
                self._update_highlight_thumbnail(self.last_highlight_path)
            except Exception:
                pass
            return str(fname)
        except Exception:
            return None

    def _call_vision_api(self, pil_image, cfg):
        """Send a PIL image to optional cloud vision endpoint defined in settings['vision'].
        Expected cfg keys: enabled, provider, endpoint, api_key. Returns a short textual description or None.
        """
        try:
            endpoint = str(cfg.get("endpoint", "")).strip()
            api_key = str(cfg.get("api_key", "")).strip()
            if not endpoint:
                return None

            # compute cache key based on endpoint + image bytes
            try:
                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                img_bytes = buf.getvalue()
            except Exception:
                img_bytes = None

            key_source = (endpoint + (api_key or "") ).encode("utf-8")
            if img_bytes:
                key_source += hashlib.sha256(img_bytes).digest()
            cache_key = hashlib.sha256(key_source).hexdigest()

            # prepare cache path and load cache
            cache_path = BASE_DIR / str(self.settings.get("vision_cache_path", "vision_cache.json"))
            try:
                if cache_path.exists():
                    with cache_path.open("r", encoding="utf-8") as cf:
                        cache = json.load(cf)
                else:
                    cache = {}
            except Exception:
                cache = {}

            if cache_key in cache:
                return cache[cache_key]

            files = {"image": ("screenshot.png", io.BytesIO(img_bytes), "image/png")} if img_bytes else {}
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            try:
                r = requests.post(endpoint, files=files, headers=headers, timeout=20)
                if r.status_code == 200:
                    j = r.json()
                    # If structured detections exist, extract them
                    regions = []
                    # common keys for detections
                    for key in ("detections", "predictions", "objects", "results"):
                        if key in j and isinstance(j[key], (list, tuple)):
                            for item in j[key]:
                                try:
                                    # try multiple possible field names
                                    label = item.get("label") or item.get("class") or item.get("name") or item.get("text") or ""
                                    score = item.get("score") or item.get("confidence") or item.get("prob") or 0.0
                                    box = item.get("box") or item.get("bbox") or item.get("bounding_box") or None
                                    if box and isinstance(box, (list, tuple)) and len(box) >= 4:
                                        left, top, width, height = box[0], box[1], box[2], box[3]
                                    elif box and isinstance(box, dict):
                                        left = box.get("left") or box.get("x") or 0
                                        top = box.get("top") or box.get("y") or 0
                                        width = box.get("width") or box.get("w") or 0
                                        height = box.get("height") or box.get("h") or 0
                                    else:
                                        left = top = width = height = None
                                    regions.append({"label": label, "score": float(score or 0.0), "left": left, "top": top, "width": width, "height": height})
                                except Exception:
                                    continue
                            break

                    # Compose textual summary
                    if regions:
                        out = {"text": j.get("description") or j.get("caption") or str(j)[:800], "regions": regions}
                    else:
                        # try common fields
                        for k in ("description", "text", "result", "caption", "labels"):
                            if k in j and j[k]:
                                if isinstance(j[k], (list, tuple)):
                                    out = "; ".join(map(str, j[k]))
                                else:
                                    out = str(j[k])
                                break
                        else:
                            out = json.dumps(j, ensure_ascii=False)[:800]

                    # save cache (stringify if dict)
                    try:
                        cache[cache_key] = out
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        with cache_path.open("w", encoding="utf-8") as cf:
                            json.dump(cache, cf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                    return out
            except Exception:
                return None
        except Exception:
            return None

    def _ensure_asr_model(self, cfg):
        global WhisperModel
        if WhisperModel is None:
            try:
                mod = importlib.import_module("faster_whisper")
                WhisperModel = getattr(mod, "WhisperModel", None)
            except Exception:
                WhisperModel = None
        if WhisperModel is None:
            raise RuntimeError("未安装 faster-whisper")
        if self.asr_model is not None:
            return self.asr_model
        size = str(cfg.get("model_size", "tiny")).strip() or "tiny"
        self.asr_model = WhisperModel(size, device="cpu", compute_type="int8")
        return self.asr_model

    def _capture_system_audio_text(self, cfg):
        global sc
        if sc is None:
            try:
                sc = importlib.import_module("soundcard")
            except Exception:
                sc = None
        if sc is None:
            raise RuntimeError("未安装 soundcard")

        model = self._ensure_asr_model(cfg)
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("无法获取默认扬声器")

        sample_seconds = max(3, min(12, int(cfg.get("sample_seconds", 6))))
        samplerate = 16000
        frames = sample_seconds * samplerate
        audio = None
        with speaker.recorder(samplerate=samplerate, channels=1) as rec:
            audio = rec.record(numframes=frames)
        if audio is None:
            return ""

        if hasattr(audio, "flatten"):
            audio = audio.flatten()
        if hasattr(audio, "size") and int(getattr(audio, "size", 0)) <= 0:
            return ""

        wav_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav_path = tf.name
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(samplerate)
                pcm_bytes = b""
                if hasattr(audio, "clip") and hasattr(audio, "astype"):
                    pcm = (audio.clip(-1.0, 1.0) * 32767).astype("int16")
                    pcm_bytes = pcm.tobytes()
                if not pcm_bytes:
                    return ""
                wf.writeframes(pcm_bytes)

            lang = str(cfg.get("language", "zh")).strip() or "zh"
            segments, _info = model.transcribe(
                wav_path,
                language=lang,
                vad_filter=True,
                beam_size=1,
            )
            parts = []
            for seg in segments:
                txt = re.sub(r"\s+", " ", str(getattr(seg, "text", ""))).strip()
                if txt:
                    parts.append(txt)
            merged = re.sub(r"\s+", " ", " ".join(parts)).strip()
            return parts, merged
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

    def _request_audio_roast(self, transcript, emit=True):
        try:
            prompt = (
                "请根据我刚听到的内容，给一句轻吐槽。"
                "要求：中文、俏皮但不刻薄、不要攻击性、不超过30字。\n\n"
                f"听到内容：{transcript}"
            )
            messages = [
                {"role": "system", "content": self._compose_system_prompt() + "\n你会做简短、友好的音频吐槽。"},
                {"role": "user", "content": prompt},
            ]
            gen = self._get_generation_params()
            answer = self._chat_completion(
                messages,
                temperature=min(0.95, gen["temperature"] + 0.05),
                max_tokens=min(140, gen["max_tokens"]),
                timeout=30,
            )
            answer = re.sub(r"\s+", " ", answer).strip()
            if len(answer) > 48:
                answer = answer[:48] + "…"
            if emit:
                self._push_history("assistant", f"[音频吐槽] {answer}")
                self._post_auto_event("audio_roast", answer, priority=7, cooldown_seconds=80, speak=True)
            return answer
        except Exception as e:
            if not self.audio_roast_warned:
                self.audio_roast_warned = True
                self.reply_queue.put(("tip", f"音频吐槽暂不可用：{e}"))
            return ""

    def _audio_roast_worker(self, cfg):
        try:
            segments, text = self._capture_system_audio_text(cfg)
            min_chars = max(4, int(cfg.get("min_chars", 8)))
            if len(text) < min_chars:
                return

            max_chars = max(40, int(cfg.get("max_chars", 140)))
            text = text[:max_chars]
            fingerprint = re.sub(r"\W+", "", text.lower())[:80]
            if fingerprint and fingerprint == self.last_audio_fingerprint:
                return

            self.last_audio_roast_time = time.time()
            if fingerprint:
                self.last_audio_fingerprint = fingerprint

            realtime = bool(cfg.get("realtime", True))
            comments = []
            if realtime and segments:
                for seg_text in segments:
                    seg_text = re.sub(r"\s+", " ", str(seg_text or "")).strip()
                    if len(seg_text) < min_chars:
                        continue
                    self.reply_queue.put(("stream", f"[实时听写] {seg_text}"))
                    one = self._request_audio_roast(seg_text, emit=False)
                    if one:
                        comments.append(one)
                        self.reply_queue.put(("stream", f"[实时点评] {one}"))

                final_text = ""
                if comments:
                    final_text = re.sub(r"\s+", " ", " ".join(comments)).strip()
                if not final_text:
                    final_text = self._request_audio_roast(text, emit=False)
                if final_text:
                    if len(final_text) > 80:
                        final_text = final_text[:80] + "…"
                    self._push_history("assistant", f"[音频吐槽汇总] {final_text}")
                    self._post_auto_event("audio_roast_final", f"[最终] {final_text}", priority=8, cooldown_seconds=120, speak=True)
            else:
                self._request_audio_roast(text)
        except Exception as e:
            if not self.audio_roast_warned:
                self.audio_roast_warned = True
                self.reply_queue.put(("tip", f"音频识别暂不可用：{e}"))
        finally:
            self.audio_roast_working = False

    def _monitor_audio_roast(self):
        cfg = self.settings.get("audio_roast", {})
        interval = max(12, int(cfg.get("interval_seconds", 35)))
        try:
            if not cfg.get("enabled", False):
                return
            if self.sleep_mode or self.game_mode or self.busy or self.message_box_visible:
                return
            if self.audio_roast_working:
                return

            now_ts = time.time()
            cooldown = max(interval, int(cfg.get("cooldown_seconds", 75)))
            if now_ts - self.last_audio_roast_time < cooldown:
                return

            self.audio_roast_working = True
            threading.Thread(target=self._audio_roast_worker, args=(cfg,), daemon=True).start()
        finally:
            self.root.after(interval * 1000, self._monitor_audio_roast)

    def _ping_ms(self, host):
        try:
            result = subprocess.run(["ping", "-n", "1", "-w", "1500", host], capture_output=True, text=True, timeout=4)
            m = re.search(r"time[=<](\d+)ms", result.stdout, flags=re.IGNORECASE)
            if m:
                return int(m.group(1))
            return None
        except Exception:
            return None

    def _monitor_game_mode(self):
        if not psutil:
            self.root.after(5000, self._monitor_game_mode)
            return
        whitelist = {x.lower() for x in self.settings.get("game_processes", [])}
        running = False
        try:
            for p in psutil.process_iter(["name"]):
                name = (p.info.get("name") or "").lower()
                if name in whitelist:
                    running = True
                    break
        except Exception:
            running = False

        if running and not self.game_mode:
            self.game_mode = True
            self._refresh_status_display()
            self.root.geometry("300x430+0+0")
            self.show_lines(["检测到游戏，已切换安静模式。"])
        elif (not running) and self.game_mode:
            self.game_mode = False
            self._refresh_status_display()
            self.root.geometry("420x560+100+100")
            self.show_lines(["游戏已结束，恢复陪伴模式。"])

        self.root.after(5000, self._monitor_game_mode)

    def _weather_tick(self):
        cfg = self.settings.get("weather", {})
        if not cfg.get("enabled"):
            return
        marker = datetime.now().strftime("%Y%m%d%H")
        if self.state.get("last_weather_hour") == marker:
            return
        self.state["last_weather_hour"] = marker
        try:
            lat = cfg.get("latitude", 39.90)
            lon = cfg.get("longitude", 116.40)
            url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=rain,weather_code"
            data = requests.get(url, timeout=8).json()
            rain = float(data.get("current", {}).get("rain", 0) or 0)
            if rain > 0:
                self.show_lines(["外面在下雨，记得带伞。"])
        except Exception:
            pass

    def _use_nanobot(self):
        return bool(self.settings.get("nanobot", {}).get("enabled", False))

    def toggle_nanobot(self):
        cfg = self.settings.setdefault("nanobot", {})
        cfg["enabled"] = not bool(cfg.get("enabled", False))
        self._save_json(SETTINGS_PATH, self.settings)
        if cfg["enabled"]:
            self.show_lines(["已切换到 Nanobot 引擎。"])
        else:
            self.show_lines(["已切换回内置引擎。"])
            try:
                self.nanobot.stop()
            except Exception:
                pass

    def toggle_feishu(self):
        cfg = self.settings.setdefault("feishu", {})
        cfg["enabled"] = not bool(cfg.get("enabled", False))
        self._save_json(SETTINGS_PATH, self.settings)
        if cfg["enabled"]:
            self.feishu.start()
            self.show_lines(["飞书桥接已开启。"])
        else:
            self.feishu.stop()
            self.show_lines(["飞书桥接已关闭。"])

    def open_feishu_settings(self):
        """打开飞书配置向导窗口。"""
        win = tk.Toplevel(self.root)
        win.title("飞书设置")
        win.geometry("480x520")
        win.resizable(False, False)
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        cfg = self.settings.setdefault("feishu", {})

        row = 0
        ttk.Label(frame, text="飞书应用配置", font=("", 11, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1

        ttk.Label(frame, text="App ID:").grid(row=row, column=0, sticky="w", pady=3)
        app_id_var = tk.StringVar(value=cfg.get("appId", ""))
        ttk.Entry(frame, textvariable=app_id_var, width=36).grid(row=row, column=1, sticky="ew", pady=3)
        row += 1

        ttk.Label(frame, text="App Secret:").grid(row=row, column=0, sticky="w", pady=3)
        app_secret_var = tk.StringVar(value=cfg.get("appSecret", ""))
        ttk.Entry(frame, textvariable=app_secret_var, width=36, show="*").grid(row=row, column=1, sticky="ew", pady=3)
        row += 1

        ttk.Label(frame, text="默认推送群聊 ID:").grid(row=row, column=0, sticky="w", pady=3)
        chat_id_var = tk.StringVar(value=cfg.get("default_chat_id", ""))
        ttk.Entry(frame, textvariable=chat_id_var, width=36).grid(row=row, column=1, sticky="ew", pady=3)
        row += 1

        ttk.Separator(frame, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        ttk.Label(frame, text="消息与推送", font=("", 11, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))
        row += 1

        notify_events_var = tk.BooleanVar(value=cfg.get("notify_events", True))
        ttk.Checkbutton(frame, text="推送桌宠事件（报时/随机事件）", variable=notify_events_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
        row += 1

        notify_reminders_var = tk.BooleanVar(value=cfg.get("notify_reminders", True))
        ttk.Checkbutton(frame, text="推送日程提醒", variable=notify_reminders_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
        row += 1

        notify_alerts_var = tk.BooleanVar(value=cfg.get("notify_system_alerts", False))
        ttk.Checkbutton(frame, text="推送系统告警", variable=notify_alerts_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
        row += 1

        ttk.Separator(frame, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1

        ttk.Label(frame, text="群聊策略:").grid(row=row, column=0, sticky="w", pady=3)
        group_policy_var = tk.StringVar(value=cfg.get("groupPolicy", "mention"))
        policy_combo = ttk.Combobox(frame, textvariable=group_policy_var, values=["mention", "all"], state="readonly", width=10)
        policy_combo.grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        ttk.Label(frame, text="重连间隔（秒）:").grid(row=row, column=0, sticky="w", pady=3)
        reconnect_var = tk.IntVar(value=cfg.get("reconnect_interval_seconds", 30))
        ttk.Spinbox(frame, from_=5, to=300, textvariable=reconnect_var, width=8).grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        ttk.Label(frame, text="心跳间隔（秒）:").grid(row=row, column=0, sticky="w", pady=3)
        heartbeat_var = tk.IntVar(value=cfg.get("heartbeat_interval_seconds", 60))
        ttk.Spinbox(frame, from_=10, to=600, textvariable=heartbeat_var, width=8).grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        frame.columnconfigure(1, weight=1)

        # 状态显示
        status_frame = ttk.Frame(frame)
        status_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        row += 1
        connected = self.feishu.is_connected if self.feishu else False
        status_text = "已连接" if connected else "未连接"
        status_color = "#2e7d32" if connected else "#c62828"
        status_label = tk.Label(status_frame, text=f"状态: {status_text}", fg=status_color, font=("", 10))
        status_label.pack(side="left")

        def save_settings():
            cfg["appId"] = app_id_var.get().strip()
            cfg["appSecret"] = app_secret_var.get().strip()
            cfg["default_chat_id"] = chat_id_var.get().strip()
            cfg["notify_events"] = notify_events_var.get()
            cfg["notify_reminders"] = notify_reminders_var.get()
            cfg["notify_system_alerts"] = notify_alerts_var.get()
            cfg["groupPolicy"] = group_policy_var.get()
            cfg["reconnect_interval_seconds"] = reconnect_var.get()
            cfg["heartbeat_interval_seconds"] = heartbeat_var.get()
            self._save_json(SETTINGS_PATH, self.settings)
            self.show_lines(["飞书设置已保存。如需生效，请关闭再开启飞书桥接。"])
            win.destroy()

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=row, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_row, text="保存", command=save_settings).pack(side="left", padx=4)
        ttk.Button(btn_row, text="取消", command=win.destroy).pack(side="left", padx=4)

    def on_exit(self):
        self._cancel_blink_now()
        self.clipboard_stop.set()
        self.tts_stop = True
        try:
            self.nanobot.stop()
        except Exception:
            pass
        try:
            self.feishu.stop()
        except Exception:
            pass
        try:
            self.tts_queue.put_nowait(None)
        except Exception:
            pass
        # 退出时强制写盘所有脏数据
        self._save_json(SETTINGS_PATH, self.settings)
        self._save_json(HISTORY_PATH, self.history)
        self._save_json(STATE_PATH, self.state)
        self._save_json(ITEMS_PATH, self.items)
        self._save_json(REMINDERS_PATH, self.reminders)
        self.memory_store.save()
        self.store.force_persist({
            str(STATE_PATH): (STATE_PATH, self.state),
            str(HISTORY_PATH): (HISTORY_PATH, self.history),
        })
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
        import argparse
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('--create-kb-wrappers', action='store_true', help='Create right-click wrapper batch files in workspace')
        parser.add_argument('--write-kb-reg', action='store_true', help='Write kb_context_menu.reg pointing to wrappers')
        parser.add_argument('--install-kb', action='store_true', help='Create wrappers, write reg file and import it into registry')
        parser.add_argument('--uninstall-kb', action='store_true', help='Remove KB context menu from registry')
        parser.add_argument('--python-path', help='Optional python.exe absolute path to embed into wrappers')
        args, unknown = parser.parse_known_args()

        def _write_wrappers(python_path=None):
            work = str(BASE_DIR) + os.sep
            py = python_path or 'python'
            files = {}
            add_path = os.path.join(work, 'rightclick_add_to_kb.bat')
            with open(add_path, 'w', encoding='utf-8') as f:
                f.write(f'@echo off\n')
                f.write(f'set PYTHON={py}\n')
                f.write(f'"%PYTHON%" "{work}kb\\kb_cli.py" add %*\n')
            files['add'] = add_path

            sum_path = os.path.join(work, 'rightclick_summarize.bat')
            with open(sum_path, 'w', encoding='utf-8') as f:
                f.write(f'@echo off\n')
                f.write(f'set PYTHON={py}\n')
                f.write('for %%F in (%*) do (\n')
                f.write(f'    echo Summarizing %%~fF\n')
                f.write(f'    "%PYTHON%" "{work}kb\\kb_cli.py" summarize-file "%%~fF"\n')
                f.write(')\n')
            files['summarize'] = sum_path

            sem_path = os.path.join(work, 'rightclick_semsearch.bat')
            with open(sem_path, 'w', encoding='utf-8') as f:
                f.write(f'@echo off\n')
                f.write(f'set PYTHON={py}\n')
                f.write('powershell -NoExit -Command "$q = Read-Host \'输入语义检索查询\'; & \"%PYTHON%\" \"' + work.replace('\\','\\\\') + 'kb\\kb_cli.py\" semsearch --q \"$q\" --k 5"\n')
            files['semsearch'] = sem_path

            print('Wrappers written:')
            for k,v in files.items():
                print(' -', v)
            return files

        def _write_reg(python_path=None):
            work = str(BASE_DIR).replace('\\', '\\\\') + '\\\\'
            reg_path = os.path.join(str(BASE_DIR), 'kb_context_menu.reg')
            add = work + 'rightclick_add_to_kb.bat'
            summ = work + 'rightclick_summarize.bat'
            sem = work + 'rightclick_semsearch.bat'
            content = []
            content.append('Windows Registry Editor Version 5.00\n')
            content.append('[HKEY_CLASSES_ROOT\\*\\shell\\KBMenu]\n@="Knowledge Base"\n')
            content.append('[HKEY_CLASSES_ROOT\\*\\shell\\KBMenu\\shell\\AddToKB]\n@="加入知识库"\n')
            content.append('[HKEY_CLASSES_ROOT\\*\\shell\\KBMenu\\shell\\AddToKB\\command]\n@="\\"' + add + '\\" \\"%1\\""\n')
            content.append('[HKEY_CLASSES_ROOT\\*\\shell\\KBMenu\\shell\\Summarize]\n@="摘要文件"\n')
            content.append('[HKEY_CLASSES_ROOT\\*\\shell\\KBMenu\\shell\\Summarize\\command]\n@="\\"' + summ + '\\" \\"%1\\""\n')
            content.append('[HKEY_CLASSES_ROOT\\*\\shell\\KBMenu\\shell\\SemSearch]\n@="语义检索 (KB)"\n')
            content.append('[HKEY_CLASSES_ROOT\\*\\shell\\KBMenu\\shell\\SemSearch\\command]\n@="\\"' + sem + '\\""\n')
            with open(reg_path, 'w', encoding='utf-8') as f:
                f.writelines(content)
            print('Wrote registry file:', reg_path)
            return reg_path

        def _install_reg(reg_path):
            try:
                subprocess.run(['reg', 'import', reg_path], check=True)
                print('Registry imported successfully.')
            except Exception as e:
                print('Failed to import registry:', e)

        def _uninstall_reg():
            try:
                subprocess.run(['reg', 'delete', r'HKCR\\*\\shell\\KBMenu', '/f'], check=True)
                print('KB context menu removed from registry.')
            except Exception as e:
                print('Failed to remove KB context menu:', e)

        if args.create_kb_wrappers:
            _write_wrappers(args.python_path)
            raise SystemExit(0)
        if args.write_kb_reg:
            _write_reg(args.python_path)
            raise SystemExit(0)
        if args.install_kb:
            _write_wrappers(args.python_path)
            regp = _write_reg(args.python_path)
            _install_reg(regp)
            raise SystemExit(0)
        if args.uninstall_kb:
            _uninstall_reg()
            raise SystemExit(0)

        app = DesktopPet()
        app.run()
