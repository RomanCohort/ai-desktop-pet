from pathlib import Path
from PIL import Image, ImageDraw


BASE_DIR = Path(__file__).resolve().parent

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = getattr(Image, "LANCZOS", 1)


def find_source_image():
    candidates = ["source.png", "source.jpg", "source.jpeg", "source.webp"]
    for name in candidates:
        p = BASE_DIR / name
        if p.exists():
            return p

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    skip = {"normal1.png", "normal2.png", "blink1.png"}
    for p in sorted(BASE_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in exts and p.name.lower() not in skip:
            return p
    return None


def auto_trim_transparent(img: Image.Image) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    return img


def build_normal1(src: Image.Image) -> Image.Image:
    img = auto_trim_transparent(src)
    max_side = 340
    w, h = img.size
    scale = min(max_side / max(w, h), 1.0)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), RESAMPLE)
    return img


def build_normal2(normal1: Image.Image) -> Image.Image:
    img = normal1.copy().convert("RGBA")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    cx = int(w * 0.50)
    cy = int(h * 0.64)
    mouth_w = max(18, int(w * 0.08))
    mouth_h = max(10, int(h * 0.035))

    draw.ellipse(
        [cx - mouth_w // 2, cy - mouth_h // 2, cx + mouth_w // 2, cy + mouth_h // 2],
        fill=(52, 30, 35, 235),
    )
    draw.ellipse(
        [cx - mouth_w // 2 + 3, cy, cx + mouth_w // 2 - 3, cy + mouth_h // 2 + 2],
        fill=(210, 120, 145, 210),
    )
    return img


def build_blink1(normal1: Image.Image) -> Image.Image:
    img = normal1.copy().convert("RGBA")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    y = int(h * 0.48)
    left_x = int(w * 0.41)
    right_x = int(w * 0.59)
    eye_w = max(20, int(w * 0.10))
    line_h = max(3, int(h * 0.012))

    eyelid = (72, 58, 58, 245)
    highlight = (248, 235, 228, 210)

    for center_x in (left_x, right_x):
        x1 = center_x - eye_w // 2
        x2 = center_x + eye_w // 2
        draw.rounded_rectangle([x1, y, x2, y + line_h], radius=line_h, fill=eyelid)
        draw.rounded_rectangle([x1 + 2, y + 1, x2 - 2, y + 2], radius=1, fill=highlight)

    return img


def build_default_base() -> Image.Image:
    size = 340
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    face = (244, 231, 223, 255)
    hair = (126, 104, 97, 255)
    eye = (67, 176, 145, 255)
    cloth = (95, 140, 150, 255)

    draw.rounded_rectangle([50, 28, 290, 240], radius=90, fill=hair)
    draw.ellipse([70, 48, 270, 230], fill=face)

    draw.ellipse([112, 124, 152, 164], fill=eye)
    draw.ellipse([188, 124, 228, 164], fill=eye)
    draw.ellipse([124, 136, 138, 150], fill=(255, 255, 255, 220))
    draw.ellipse([200, 136, 214, 150], fill=(255, 255, 255, 220))

    draw.rounded_rectangle([142, 182, 198, 190], radius=4, fill=(90, 63, 60, 220))
    draw.rounded_rectangle([110, 228, 230, 320], radius=22, fill=cloth)
    draw.rounded_rectangle([132, 250, 208, 292], radius=10, fill=(222, 238, 246, 255))

    return img


def main():
    src_path = find_source_image()
    if not src_path:
        src = build_default_base()
        print("未找到源图片，已自动生成默认占位角色图。")
    else:
        src = Image.open(src_path).convert("RGBA")

    normal1 = build_normal1(src)
    normal2 = build_normal2(normal1)
    blink1 = build_blink1(normal1)

    normal1.save(BASE_DIR / "normal1.png")
    normal2.save(BASE_DIR / "normal2.png")
    blink1.save(BASE_DIR / "blink1.png")

    try:
        icon_img = normal1.copy()
        icon_img.thumbnail((256, 256), RESAMPLE)
        icon_img.save(BASE_DIR / "oc.ico", format="ICO")
    except Exception:
        pass

    if src_path:
        print(f"已使用源图：{src_path.name}")
    print("已生成：normal1.png, normal2.png, blink1.png")
    print("已尝试生成：oc.ico")


if __name__ == "__main__":
    main()
