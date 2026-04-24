"""角色图片加载和动画帧管理"""
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageTk

from ..persistence.paths import BASE_DIR, MEIPASS_DIR, INTERNAL_DIR
from ..utils.logger import get_logger

_logger = get_logger(__name__)


class SpriteLoader:
    """管理桌宠角色图片的加载、缩放和动画帧构建。"""

    def __init__(self, settings: dict, pet_scale: float):
        self.settings = settings
        self.pet_scale = pet_scale
        self.animation_library: dict = {}

    def load_pet_images(self):
        """加载所有表情图片，返回按序的 PhotoImage 元组。"""
        img_cfg = self.settings.get("images", {})

        paths = {
            "normal_closed": self._resolve_asset_path(img_cfg.get("normal_closed", "normal1.png")),
            "normal_open": self._resolve_asset_path(img_cfg.get("normal_open", "normal2.png")),
            "blink": self._resolve_asset_path(img_cfg.get("blink", "blink1.png")),
            "sleep": self._resolve_asset_path(img_cfg.get("sleep", "sleep1.png")),
            "edge": self._resolve_asset_path(img_cfg.get("edge", "edge1.png")),
            "sweat": self._resolve_asset_path(img_cfg.get("sweat", "sweat1.png")),
            "faint": self._resolve_asset_path(img_cfg.get("faint", "faint1.png")),
            "happy_closed": self._resolve_asset_path(img_cfg.get("happy_closed", img_cfg.get("edge", "edge1.png"))),
            "happy_open": self._resolve_asset_path(img_cfg.get("happy_open", img_cfg.get("edge", "edge1.png"))),
            "happy_blink": self._resolve_asset_path(img_cfg.get("happy_blink", img_cfg.get("edge", "edge1.png"))),
            "sad_closed": self._resolve_asset_path(img_cfg.get("sad_closed", img_cfg.get("sweat", "sweat1.png"))),
            "sad_open": self._resolve_asset_path(img_cfg.get("sad_open", img_cfg.get("sweat", "sweat1.png"))),
            "sad_blink": self._resolve_asset_path(img_cfg.get("sad_blink", img_cfg.get("faint", "faint1.png"))),
        }

        self._log_asset_paths(paths)

        # 自动回退
        if not paths["normal_closed"].exists():
            auto_img = self._find_first_image()
            if auto_img:
                paths["normal_closed"] = auto_img

        # 加载并缩放
        max_side = int(300 * float(self.pet_scale))
        max_side = max(120, min(540, max_side))

        closed = self._safe_open_image(paths["normal_closed"])
        opened = self._safe_open_image(paths["normal_open"]) if paths["normal_open"].exists() else closed.copy()
        blinked = self._safe_open_image(paths["blink"]) if paths["blink"].exists() else closed.copy()
        slept = self._safe_open_image(paths["sleep"]) if paths["sleep"].exists() else closed.copy()
        edged = self._safe_open_image(paths["edge"]) if paths["edge"].exists() else closed.copy()
        sweat = self._safe_open_image(paths["sweat"]) if paths["sweat"].exists() else closed.copy()
        faint = self._safe_open_image(paths["faint"]) if paths["faint"].exists() else closed.copy()
        happy_closed = self._safe_open_image(paths["happy_closed"]) if paths["happy_closed"].exists() else edged.copy()
        happy_open = self._safe_open_image(paths["happy_open"]) if paths["happy_open"].exists() else happy_closed.copy()
        happy_blink = self._safe_open_image(paths["happy_blink"]) if paths["happy_blink"].exists() else happy_closed.copy()
        sad_closed = self._safe_open_image(paths["sad_closed"]) if paths["sad_closed"].exists() else sweat.copy()
        sad_open = self._safe_open_image(paths["sad_open"]) if paths["sad_open"].exists() else sad_closed.copy()
        sad_blink = self._safe_open_image(paths["sad_blink"]) if paths["sad_blink"].exists() else faint.copy()

        for img in (closed, opened, blinked, slept, edged, sweat, faint,
                    happy_closed, happy_open, happy_blink, sad_closed, sad_open, sad_blink):
            img.thumbnail((max_side, max_side))

        self.animation_library = self._build_animation_library(max_side)

        return (
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

    def _build_animation_library(self, max_side: int) -> dict:
        """构建动画槽位库。"""
        lib = {}
        slots = self.settings.get("animation_slots", {})
        img_cfg = self.settings.get("images", {})

        fallback = {
            "normal": {
                "idle": [img_cfg.get("normal_closed", "normal1.png")],
                "talk": [img_cfg.get("normal_open", "normal2.png")],
                "blink": [img_cfg.get("blink", "blink1.png")],
            },
            "excited": {
                "idle": [img_cfg.get("happy_closed", img_cfg.get("edge", "edge1.png"))],
                "talk": [img_cfg.get("happy_open", img_cfg.get("edge", "edge1.png"))],
                "blink": [img_cfg.get("happy_blink", img_cfg.get("edge", "edge1.png"))],
            },
            "happy": {
                "idle": [img_cfg.get("happy_closed", img_cfg.get("edge", "edge1.png"))],
                "talk": [img_cfg.get("happy_open", img_cfg.get("edge", "edge1.png"))],
                "blink": [img_cfg.get("happy_blink", img_cfg.get("edge", "edge1.png"))],
            },
            "sad": {
                "idle": [img_cfg.get("sad_closed", img_cfg.get("sweat", "sweat1.png"))],
                "talk": [img_cfg.get("sad_open", img_cfg.get("sweat", "sweat1.png"))],
                "blink": [img_cfg.get("sad_blink", img_cfg.get("faint", "faint1.png"))],
            },
            "angry": {
                "idle": [img_cfg.get("faint", "faint1.png")],
                "talk": [img_cfg.get("sweat", "sweat1.png")],
                "blink": [img_cfg.get("faint", "faint1.png")],
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

    @staticmethod
    def _resolve_asset_path(name: str) -> Path:
        name = str(name)
        candidates = [BASE_DIR, MEIPASS_DIR, INTERNAL_DIR]
        for base in candidates:
            p = base / name
            if p.exists():
                return p
        return candidates[0] / name

    @staticmethod
    def _safe_open_image(path: Path) -> Image.Image:
        if path.exists():
            return Image.open(path).convert("RGBA")
        return Image.new("RGBA", (280, 280), (255, 255, 255, 0))

    @staticmethod
    def _find_first_image() -> Path | None:
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        for base in (BASE_DIR, MEIPASS_DIR, INTERNAL_DIR):
            if not base.exists():
                continue
            for p in sorted(base.iterdir()):
                if p.is_file() and p.suffix.lower() in exts and p.name.lower() not in {"normal1.png", "normal2.png", "blink1.png"}:
                    return p
        return None

    @staticmethod
    def _log_asset_paths(mapping: dict) -> None:
        try:
            log_path = BASE_DIR / "asset_debug.log"
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n[asset_check %s]\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                f.write("BASE_DIR=%s\n" % BASE_DIR)
                f.write("MEIPASS_DIR=%s\n" % MEIPASS_DIR)
                f.write("INTERNAL_DIR=%s\n" % INTERNAL_DIR)
                for key, p in mapping.items():
                    f.write("%s=%s exists=%s\n" % (key, p, p.exists()))
        except Exception as e:
            _logger.debug("资源路径日志写入失败: %s", e)
