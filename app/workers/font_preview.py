"""Genera preview PNG di un testo con un font dato, per l'Inspector."""
import io
import base64
from PIL import Image, ImageDraw, ImageFont


def render_preview(text: str, font_path: str, width: int = 320, height: int = 56,
                   color_hex: str = "#FEFEFE", outline_hex: str = "#030203") -> str:
    """Ritorna una data-URL PNG con il testo renderizzato col font."""
    canvas = Image.new("RGB", (width, height), (20, 20, 22))
    draw = ImageDraw.Draw(canvas)

    target_h = int(height * 0.65)
    size = target_h
    for _ in range(20):
        try:
            font = ImageFont.truetype(font_path, size)
        except Exception:
            return ""
        bbox = draw.textbbox((0, 0), text, font=font)
        h = bbox[3] - bbox[1]
        if h >= target_h:
            break
        size = int(size * 1.15)

    for _ in range(10):
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        h = bbox[3] - bbox[1]
        if h <= target_h:
            break
        size = int(size * 0.9)

    font = ImageFont.truetype(font_path, size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (width - tw) // 2 - bbox[0]
    y = (height - th) // 2 - bbox[1]

    try:
        draw.text((x, y), text, font=font, fill=color_hex,
                  stroke_width=2, stroke_fill=outline_hex)
    except Exception:
        draw.text((x, y), text, font=font, fill=color_hex)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
