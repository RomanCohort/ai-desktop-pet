"""默认配置和常量定义"""


# ── 好感度常量 ──
AFFINITY_MIN = 0
AFFINITY_MAX = 100
AFFINITY_THRESHOLDS = [0, 20, 40, 60, 80, 100]
AFFINITY_LEVEL_LABELS = {
    0: "陌生",
    20: "熟悉",
    40: "亲近",
    60: "暧昧",
    80: "挚友",
    100: "羁绊",
}

# ── 隐藏台词 ──
HIDDEN_DIALOGUES = {
    20: ["偷偷告诉你：每次被你看见，我都会开心一点。"],
    40: ["你在身边的时候，我会更有勇气。"],
    60: ["隐藏台词解锁：我已经开始期待你下一句会说什么。"],
    80: ["隐藏台词解锁：要不要把今天也算作我们的纪念日？"],
    100: ["终极隐藏台词：无论你多忙，我都会一直在这里等你。"],
}


# ── 默认设置 ──
DEFAULT_SETTINGS = {
    "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxx",  # api输入口
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "system_prompt": "你是一只会陪伴用户的Q版OC桌宠，说话简洁、温柔、有点俏皮。",
    "profile": {
        "nickname": "御主",
        "birthday": "",
        "oc_call": "主人",
        "relationship": "亲密搭档"
    },
    "images": {
        "normal_closed": "normal1.png",
        "normal_open": "normal2.png",
        "blink": "blink1.png",
        "sleep": "sleep1.png",
        "edge": "edge1.png",
        "sweat": "sweat1.png",
        "faint": "faint1.png",
        "happy_closed": "edge1.png",
        "happy_open": "edge1.png",
        "happy_blink": "edge1.png",
        "sad_closed": "sweat1.png",
        "sad_open": "sweat1.png",
        "sad_blink": "faint1.png"
    },
    "animation_slots": {
        "normal": {
            "idle": ["normal1.png"],
            "talk": ["normal2.png"],
            "blink": ["blink1.png"]
        },
        "excited": {
            "idle": ["edge1.png"],
            "talk": ["edge1.png"],
            "blink": ["edge1.png"]
        },
        "happy": {
            "idle": ["edge1.png"],
            "talk": ["edge1.png"],
            "blink": ["edge1.png"]
        },
        "sad": {
            "idle": ["sweat1.png"],
            "talk": ["sweat1.png"],
            "blink": ["faint1.png"]
        },
        "angry": {
            "idle": ["faint1.png"],
            "talk": ["sweat1.png"],
            "blink": ["faint1.png"]
        }
    },
    "bookmarks": {
        "B站": "https://www.bilibili.com",
        "GitHub": "https://github.com",
        "ChatGPT": "https://chat.openai.com"
    },
    "launchers": {
        "记事本": "notepad.exe"
    },
    "focus": {
        "safe_keywords": ["code", "pycharm", "vscode", "word", "excel", "chrome", "edge"],
        "reward_per_min": 2
    },
    "network": {
        "ping_host": "www.baidu.com",
        "high_latency_ms": 250
    },
    "weather": {
        "enabled": False,
        "latitude": 39.90,
        "longitude": 116.40
    },
    "game_processes": ["LeagueClientUx.exe", "GenshinImpact.exe", "steam.exe"],
    "random_event_minutes": 60,
    "screen_roast": {
        "enabled": False,
        "interval_seconds": 25,
        "cooldown_seconds": 90,
        "min_chars": 12,
        "max_chars": 160,
        "ocr_lang": "chi_sim+eng",
        "tesseract_cmd": ""
    },
    "screen_comment": {
        "enabled": False,
        "interval_seconds": 60,
        "cooldown_seconds": 180,
        "min_chars": 20,
        "max_chars": 800,
        "ocr_lang": "chi_sim+eng",
        "tesseract_cmd": "",
        "detailed": True,
        "highlight": True,
        "hotkey_enabled": False,
        "hotkey": "ctrl+alt+c"
    },
    "vision": {
        "enabled": False,
        "provider": "",
        "endpoint": "",
        "api_key": ""
    },
    "vision_cache_path": "vision_cache.json",
    "audio_roast": {
        "enabled": False,
        "interval_seconds": 8,
        "sample_seconds": 5,
        "cooldown_seconds": 8,
        "min_chars": 4,
        "max_chars": 140,
        "realtime": True,
        "language": "zh",
        "model_size": "tiny"
    },
    "media": {
        "enabled": True,
        "poll_seconds": 4,
        "comment_cooldown_seconds": 20
    },
    "tts": {
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
        "test_text": "语音设置已应用，听得到吗？"
    },
    "auto_events": {
        "enabled": True,
        "global_min_interval_seconds": 6,
        "allow_in_quiet_mode": False,
        "allow_in_sleep_mode": False
    },
    "proactive": {
        "enabled": True,
        "warmup_seconds": 90,
        "warmup_interval_seconds": 180,
        "steady_interval_seconds": 480,
        "hype_interval_seconds": 300,
        "cooldown_interval_seconds": 720
    },
    "conversation_engine": {
        "enabled": True,
        "hook_cooldown_seconds": 180,
        "hook_history_size": 20,
        "memory_recent_days": 14,
        "topic_hints": ["学习", "工作", "代码", "生活", "游戏", "创作", "情绪", "健康"],
        "hook_pool": [
            "先定一个最小可完成步骤，我们从那里起步。",
            "如果你愿意，我可以把这件事拆成三步并陪你做完。",
            "先别急，我在，你只要告诉我现在最卡的点。",
            "我们按'先完成再完美'的节奏走，会更轻松。"
        ]
    },
    "nanobot": {
        "enabled": False,
        "config_path": "",
        "workspace": "",
        "model": "",
        "timeout_seconds": 60,
        "channel": "desktop_pet",
        "chat_id": "oc",
        "bio_lab_enabled": True,
        "web_enabled": True,
        "source_policy": "fixed_only",
        "fixed_sources": []
    },
    "paper_tool": {
        "enabled": True,
        "base_dir": "",
        "output_dir": "",
        "default_paper_id": "",
        "use_llm": True,
        "timeout_seconds": 90
    },
    "feishu": {
        "enabled": False,
        "appId": "",
        "appSecret": "",
        "allowFrom": [],
        "groupPolicy": "mention",
        "default_chat_id": "",
        "notify_events": True,
        "notify_reminders": True,
        "notify_system_alerts": False,
        "reconnect_interval_seconds": 30,
        "heartbeat_interval_seconds": 60
    },
    "igem_assistant": {
        "enabled": True,
        "team_name": "",
        "meeting_auto_push_feishu": True,
        "doc_watch_folders": [],
        "doc_auto_index": True,
        "doc_index_interval_minutes": 60,
        "task_deadline_remind_hours_before": 24,
        "task_deadline_push_feishu": True,
        "bio_api_url": "http://127.0.0.1:18901",
        "bio_api_timeout": 120
    },
    "ui": {
        "pet_scale": 1.0
    }
}

DEFAULT_STATE = {
    "coins": 0,
    "affinity": 0,
    "affinity_unlocked": [],
    "mood": "Normal",
    "mood_score": 0,
    "last_chime": "",
    "last_weather_hour": "",
    "last_event": "",
    "last_idle_chat": "",
    "click_count": 0,
    "last_journal_day": "",
    "emotion_value": 0
}

DEFAULT_ITEMS = [
    {"name": "曲奇饼", "qty": 3, "price": 8, "affinity": 2, "reply": "脆脆甜甜，好吃！"},
    {"name": "奶茶", "qty": 2, "price": 12, "affinity": 3, "reply": "这口感太幸福了！"},
    {"name": "苹果", "qty": 4, "price": 6, "affinity": 1, "reply": "清爽健康，喜欢！"}
]
