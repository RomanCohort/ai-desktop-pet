"""情绪系统 - 管理情绪值和心情状态"""


class MoodManager:
    """封装情绪值和心情状态的转换逻辑。"""

    MOOD_TO_SCORE = {"Excited": 70, "Happy": 45, "Normal": 0, "Sad": -45, "Angry": -70}

    def __init__(self, state: dict):
        self._state = state

    @property
    def emotion_value(self) -> int:
        v = self._state.get("emotion_value")
        if not isinstance(v, int):
            legacy = self._state.get("mood_score")
            v = legacy if isinstance(legacy, int) else 0
        return max(-100, min(100, int(v)))

    @emotion_value.setter
    def emotion_value(self, val: int):
        val = max(-100, min(100, int(val)))
        self._state["emotion_value"] = val
        self._state["mood_score"] = val  # 兼容旧字段
        self._state["mood"] = self._mood_from_emotion(val)

    @property
    def mood(self) -> str:
        return self._state.get("mood", "Normal")

    @mood.setter
    def mood(self, val: str):
        val = val.title()
        if val not in self.MOOD_TO_SCORE:
            val = "Normal"
        self._state["mood"] = val

    def adjust(self, delta: int, reason: str = "") -> None:
        """调整情绪值并自动更新心情。"""
        delta = int(delta)
        if delta == 0:
            return
        self.emotion_value = self.emotion_value + delta

    def normalize(self) -> None:
        """启动时规范化情绪状态。"""
        mood = str(self._state.get("mood", "Normal")).title()
        if mood not in self.MOOD_TO_SCORE:
            mood = "Normal"

        emotion = self._state.get("emotion_value")
        if not isinstance(emotion, int):
            legacy = self._state.get("mood_score")
            emotion = legacy if isinstance(legacy, int) else self.MOOD_TO_SCORE.get(mood, 0)

        emotion = max(-100, min(100, int(emotion)))
        mood = self._mood_from_emotion(emotion)
        self._state["emotion_value"] = emotion
        self._state["mood_score"] = emotion
        self._state["mood"] = mood

    @staticmethod
    def _mood_from_emotion(emotion: int) -> str:
        if emotion >= 60:
            return "Excited"
        if emotion >= 20:
            return "Happy"
        if emotion <= -60:
            return "Angry"
        if emotion <= -20:
            return "Sad"
        return "Normal"

    @staticmethod
    def sentiment_delta(text: str, strength: float = 1.0) -> int:
        """根据文本情感倾向计算情绪调整值。"""
        positive = ("开心", "高兴", "谢谢", "喜欢", "厉害", "棒", "赞", "爱", "好", "漂亮", "可爱", "有趣", "优秀", "完美")
        negative = ("难过", "烦", "累", "讨厌", "生气", "差", "糟", "烦人", "无聊", "崩溃", "焦虑", "压力", "无语")

        lower = text.lower()
        pos_count = sum(1 for w in positive if w in lower)
        neg_count = sum(1 for w in negative if w in lower)

        if pos_count > neg_count:
            return int(min(8, pos_count * 2) * strength)
        if neg_count > pos_count:
            return int(max(-8, -neg_count * 2) * strength)
        return 0

    def get_mood_rule(self) -> str:
        """获取当前心情对应的对话风格提示。"""
        rules = {
            "Excited": "你当前情绪高涨，回复可更活泼主动。",
            "Happy": "你当前心情很好，回复可更热情。",
            "Normal": "你当前心情平稳，回复自然。",
            "Sad": "你当前心情低落，回复尽量简短温柔。",
            "Angry": "你当前有点烦躁，回复要克制礼貌并尽量简洁。",
        }
        return rules.get(self.mood, "")

    def emotion_key(self) -> str:
        """当前情绪对应的动画槽位键名。"""
        mapping = {"Excited": "excited", "Happy": "happy", "Sad": "sad", "Angry": "angry"}
        return mapping.get(self.mood, "normal")
