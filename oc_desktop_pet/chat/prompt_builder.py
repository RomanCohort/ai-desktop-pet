"""Prompt 构建器 - 动态组装系统提示词、话题提示、Hook指令"""
import random
import re
import time


class PromptBuilder:
    """构建发往 LLM 的系统提示词和上下文。"""

    def __init__(self, settings: dict, state: dict, memory_store):
        self.settings = settings
        self.state = state
        self.memory = memory_store
        self.recent_hook_keys: list[str] = []
        self.recent_topic_hints: list[str] = []
        self.auto_event_last_emit: dict[str, float] = {}

    def compose_system_prompt(self) -> str:
        """构建结构化的系统提示词。"""
        sections = []

        # 身份段
        sections.append(f"## 身份\n{self.settings.get('system_prompt', '')}")

        # 用户档案段
        profile = self.settings.get("profile", {})
        profile_lines = []
        if profile.get("nickname"):
            profile_lines.append(f"用户昵称：{profile['nickname']}")
        if profile.get("birthday"):
            profile_lines.append(f"生日：{profile['birthday']}")
        if profile.get("oc_call"):
            profile_lines.append(f"对OC称呼：{profile['oc_call']}")
        if profile.get("relationship"):
            profile_lines.append(f"关系设定：{profile['relationship']}")
        if profile_lines:
            sections.append("## 用户档案\n" + "\n".join(profile_lines))

        # 当前状态段
        mood = self.state.get("mood", "Normal")
        mood_score = int(self.state.get("emotion_value", self.state.get("mood_score", 0)))
        from ..emotions.mood import MoodManager
        mood_rule = MoodManager._mood_from_emotion(mood_score)
        mood_rule_text = {
            "Excited": "你当前情绪高涨，回复可更活泼主动。",
            "Happy": "你当前心情很好，回复可更热情。",
            "Normal": "你当前心情平稳，回复自然。",
            "Sad": "你当前心情低落，回复尽量简短温柔。",
            "Angry": "你当前有点烦躁，回复要克制礼貌并尽量简洁。",
        }.get(mood_rule, "")
        time_str = time.strftime("%H:%M")
        sections.append(
            f"## 当前状态\n"
            f"情绪：{mood} (值{mood_score})\n"
            f"时间：{time_str}\n"
            f"{mood_rule_text}"
        )

        # 行为规则段
        sections.append(
            "## 行为规则\n"
            "- 说话简洁温柔俏皮\n"
            "- 好感度高时可以更亲密\n"
            "- 情绪低落时回复简短温柔"
        )

        return "\n\n".join(sections)

    def build_topic_hint(self, user_text: str) -> str:
        """构建话题提示。"""
        cfg = self.settings.get("conversation_engine", {})
        tags = self.memory._extract_topic_tags(user_text) if self.memory else []
        if not tags:
            hint_pool = [str(x) for x in cfg.get("topic_hints", []) if str(x).strip()]
            if hint_pool:
                tags = [random.choice(hint_pool)]
        if not tags:
            return ""
        for t in tags:
            self.recent_topic_hints.append(t)
        self.recent_topic_hints = self.recent_topic_hints[-12:]
        return "当前优先话题: " + " / ".join(tags)

    def pick_non_repeating_hook(self) -> tuple[str, str]:
        """选择一个未冷却的 Hook 文案。"""
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

    def maybe_build_hook_directive(self) -> str:
        """构建 Hook 风格提示指令。"""
        hook, key = self.pick_non_repeating_hook()
        if not hook:
            return ""
        full_key = "hook:" + key
        self.auto_event_last_emit[full_key] = time.time()
        self.recent_hook_keys.append(full_key)
        limit = int(self.settings.get("conversation_engine", {}).get("hook_history_size", 20))
        if len(self.recent_hook_keys) > limit:
            self.recent_hook_keys = self.recent_hook_keys[-limit:]
        return "风格提示: 可自然融入一句，不要逐字照抄 -> " + hook

    def build_full_context(self, user_text: str) -> str:
        """构建完整的上下文附加块（记忆 + 话题 + Hook）。"""
        memory_block = self.memory.build_layered_memory_block(user_text, topk=4) if self.memory else ""
        topic_hint = self.build_topic_hint(user_text)
        hook_hint = self.maybe_build_hook_directive()

        style_guidance = ""
        if topic_hint or hook_hint:
            style_guidance = "\n\n对话导演提示(内部):\n"
            if topic_hint:
                style_guidance += topic_hint + "\n"
            if hook_hint:
                style_guidance += hook_hint + "\n"

        return memory_block + style_guidance
