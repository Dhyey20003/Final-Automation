"""Deployment post image rendering.

The layout, cover boxes, circle photo, logo box and text placement are taken
from the supplied Relay Post Studio (post_generator) code. With default settings
the output is the same picture that tool produced. Every value can be adjusted
per project: photo crop, logo fit, text sizes and positions, so the result is
also used for the live preview in the browser.
"""
from __future__ import annotations

import copy
import hashlib
import io
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from ..config import automations_dir

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - old Pillow
    RESAMPLE = Image.LANCZOS

TEMPLATE_SIZE = (600, 600)
PANEL_BLUE = (154, 205, 255)
WHITE = (255, 255, 255)
COVER_BOXES = [
    (18, 244, 320, 274, PANEL_BLUE),
    (18, 286, 320, 313, PANEL_BLUE),
    (50, 361, 296, 489, WHITE),
]

DEFAULT_ANNOUNCEMENT = (
    "Resource Deployment Announcement\n"
    "\n"
    "\n"
    "We're super thrilled to announce that   {name}    has been deployed "
    "with our customer, \"{client}\". Please join me in congratulating {pronoun}.\n"
    "Wishing you all the best for your new role and responsibilities!!\n"
    "Thank You!\n"
    "\n"
    "Hitesh Parmar"
)

DEFAULT_LAYOUT: dict[str, Any] = {
    "name": {"x": 24, "y": 264, "max_size": 25, "min_size": 12, "max_width": 290, "bold": True, "upper": True, "color": [0, 0, 0]},
    "designation": {"x": 24, "y": 288, "max_size": 19, "min_size": 10, "max_width": 290, "bold": False, "upper": True,
                    "spacing": 1, "color": [40, 40, 40]},
    "photo": {"center_x": 460, "center_y": 362, "size": 213, "ring_color": [90, 159, 228]},
    "logo": {"left": 56, "top": 367, "right": 290, "bottom": 483, "padding": 8, "mode": "contain", "scale": 1.0, "dx": 0, "dy": 0},
}

FEMALE_FIRST_NAMES = {
    "priya", "neha", "pooja", "puja", "anjali", "karishma", "shreya", "divya", "sneha", "riya", "diya", "aishwarya", "kavya",
    "ananya", "isha", "nisha", "asha", "usha", "meera", "mira", "sita", "gita", "geeta", "radha", "lakshmi", "laxmi", "saloni",
    "simran", "komal", "payal", "kajal", "sonal", "sonali", "rupali", "deepika", "dipika", "priyanka", "tanvi", "tanya", "khushi",
    "mansi", "jhanvi", "janvi", "bhavna", "bhavana", "hetal", "dhwani", "dhruvi", "krupa", "kruti", "foram", "jyoti", "swati",
    "shweta", "sweta", "nidhi", "vidhi", "aarti", "arti", "kirti", "keerti", "preeti", "priti", "smita", "namrata", "mamta",
    "kavita", "savita", "anita", "sunita", "vanita", "lalita", "sarita", "babita", "sangeeta", "sangita", "archana", "rachana",
    "vandana", "sadhana", "kalpana", "aparna", "poonam", "ritu", "rina", "reena", "rita", "heena", "hina", "sapna", "seema",
    "sima", "rekha", "manisha", "monika", "monica", "pallavi", "prachi", "ruchi", "ruchita", "nikita", "ankita", "amruta",
    "amrita", "shraddha", "shradha", "vaishali", "vaishnavi", "gayatri", "aditi", "akshita", "bhumika", "chandni", "darshana",
    "ekta", "falguni", "grishma", "harsha", "ishita", "jagruti", "juhi", "kinjal", "krishna", "mahima", "megha", "mitali",
    "nehal", "pinal", "rajvi", "shivani", "trupti", "twinkle", "urvashi", "vrunda", "yamini", "zeel", "drashti", "meera",
    "saanvi", "jiya", "krina", "palak", "tara", "sarah", "sara", "emily", "jessica", "mary", "linda", "susan", "karen", "nancy",
    "lisa", "sandra", "ashley", "emma", "olivia", "sophia", "isabella", "mia", "amelia", "harper", "evelyn", "abigail", "ella",
    "scarlett", "grace", "chloe", "victoria", "hannah", "lily", "zoe", "natalie", "rachel", "megan", "lauren", "kayla", "anna",
    "julia", "kate", "katie", "jennifer", "michelle", "kimberly", "amy", "angela", "melissa", "nicole", "christina", "alice",
    "julie", "heather", "diana", "tina", "kizzy",
}


def guess_pronoun(full_name: str | None) -> str:
    parts = (full_name or "").strip().split()
    if not parts:
        return "him"
    return "her" if parts[0].lower() in FEMALE_FIRST_NAMES else "him"


def default_template() -> Path:
    return automations_dir() / "posts" / "internal_post_base.png"


def template_path(setting: str | None = None) -> Path:
    if setting:
        p = Path(setting)
        if p.is_file():
            return p
    return default_template()


def merged_layout(overrides: dict | None) -> dict:
    layout = copy.deepcopy(DEFAULT_LAYOUT)
    for section, vals in (overrides or {}).items():
        if section in layout and isinstance(vals, dict):
            layout[section].update({k: v for k, v in vals.items() if v is not None})
    return layout


# ---------------------------------------------------------------- fonts

def _font_candidates(bold: bool) -> list[Path]:
    fonts = automations_dir() / "video" / "assets" / "fonts"
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if bold:
        return [fonts / "Montserrat-Bold.ttf", win / "arialbd.ttf", win / "segoeuib.ttf", fonts / "PlusJakartaSans.ttf",
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"), Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")]
    return [fonts / "Montserrat-Regular.ttf", win / "arial.ttf", win / "segoeui.ttf", fonts / "Poppins-Regular.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")]


_FONT_CACHE: dict[tuple, Any] = {}


def load_font(size: int, bold: bool):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    for path in _font_candidates(bold):
        if path.exists():
            try:
                font = ImageFont.truetype(str(path), size)
                if bold and "PlusJakarta" in path.name:
                    try:
                        font.set_variation_by_name("Bold")
                    except Exception:
                        pass
                break
            except OSError:
                continue
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:  # pragma: no cover
            font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def text_width(text: str, font, spacing: int = 0) -> float:
    try:
        width = font.getlength(text)
    except AttributeError:  # pragma: no cover
        box = font.getbbox(text)
        width = box[2] - box[0]
    return width + max(0, len(text) - 1) * spacing


def fit_font(text: str, start: int, minimum: int, max_width: int, bold: bool, spacing: int = 0):
    for size in range(int(start), int(minimum) - 1, -1):
        font = load_font(size, bold)
        if text_width(text, font, spacing) <= max_width:
            return font, size
    return load_font(int(minimum), bold), int(minimum)


def draw_text(draw, x, y, text, font, color, anchor, spacing=0):
    if spacing <= 0:
        draw.text((x, y), text, font=font, fill=color, anchor=anchor)
        return
    for ch in text:
        draw.text((x, y), ch, font=font, fill=color, anchor=anchor)
        x += round(text_width(ch, font)) + spacing


# ---------------------------------------------------------------- image pieces

def crop_fraction(image: Image.Image, crop: dict | None) -> Image.Image:
    """crop = {x, y, w, h} as fractions of the source image (0..1)."""
    if not crop:
        return image
    try:
        w, h = image.size
        x0 = max(0.0, min(1.0, float(crop.get("x", 0))))
        y0 = max(0.0, min(1.0, float(crop.get("y", 0))))
        cw = max(0.02, min(1.0 - x0, float(crop.get("w", 1))))
        ch = max(0.02, min(1.0 - y0, float(crop.get("h", 1))))
        box = (round(x0 * w), round(y0 * h), round((x0 + cw) * w), round((y0 + ch) * h))
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            return image
        return image.crop(box)
    except (TypeError, ValueError):
        return image


def paste_profile(base: Image.Image, profile: Image.Image, cfg: dict) -> None:
    center_x, center_y = int(cfg["center_x"]), int(cfg["center_y"])
    outer = int(cfg["size"])
    supersample = 4
    ring = tuple(int(c) for c in cfg.get("ring_color", [90, 159, 228]))
    backing_large = Image.new("RGBA", (outer * supersample,) * 2, (0, 0, 0, 0))
    ImageDraw.Draw(backing_large).ellipse((0, 0, outer * supersample - 1, outer * supersample - 1), fill=ring + (255,))
    backing = backing_large.resize((outer, outer), RESAMPLE)
    base.paste(backing, (center_x - outer // 2, center_y - outer // 2), backing)

    profile = ImageOps.exif_transpose(profile)
    profile = ImageOps.fit(profile.convert("RGBA"), (outer, outer), method=RESAMPLE)
    mask_large = Image.new("L", (outer * supersample,) * 2, 0)
    ImageDraw.Draw(mask_large).ellipse((0, 0, outer * supersample - 1, outer * supersample - 1), fill=255)
    circle = mask_large.resize((outer, outer), RESAMPLE)
    combined = ImageChops.multiply(circle, profile.getchannel("A"))
    base.paste(profile.convert("RGB"), (center_x - outer // 2, center_y - outer // 2), combined)


def paste_logo(base: Image.Image, logo: Image.Image, cfg: dict) -> None:
    left, top, right, bottom = int(cfg["left"]), int(cfg["top"]), int(cfg["right"]), int(cfg["bottom"])
    padding = int(cfg.get("padding", 8))
    logo = logo.convert("RGBA")
    visible = logo.getchannel("A").getbbox()
    if visible:
        logo = logo.crop(visible)
    avail_w = max(1, right - left - padding * 2)
    avail_h = max(1, bottom - top - padding * 2)
    w, h = logo.size
    if str(cfg.get("mode", "contain")) == "cover":
        scale = max(avail_w / w, avail_h / h)
    else:
        scale = min(avail_w / w, avail_h / h)
    scale *= max(0.1, float(cfg.get("scale", 1.0) or 1.0))
    resized = logo.resize((max(1, round(w * scale)), max(1, round(h * scale))), RESAMPLE)
    x = left + padding + (avail_w - resized.width) // 2 + int(cfg.get("dx", 0) or 0)
    y = top + padding + (avail_h - resized.height) // 2 + int(cfg.get("dy", 0) or 0)
    if str(cfg.get("mode", "contain")) == "cover" or resized.width > avail_w or resized.height > avail_h:
        # Keep everything inside the white logo box.
        box = Image.new("RGBA", (avail_w, avail_h), (0, 0, 0, 0))
        box.paste(resized, (x - (left + padding), y - (top + padding)), resized)
        base.paste(box, (left + padding, top + padding), box)
        return
    base.paste(resized, (x, y), resized)


def render(template: Path, name: str, designation: str, profile: Image.Image | None, logo: Image.Image | None,
           layout: dict | None = None, photo_crop: dict | None = None) -> Image.Image:
    lay = merged_layout(layout)
    with Image.open(template) as tpl:
        base = tpl.convert("RGB")
    if base.size != TEMPLATE_SIZE:
        raise ValueError("The post template must be 600 x 600 pixels.")
    draw = ImageDraw.Draw(base)
    for l, t, r, b, color in COVER_BOXES:
        draw.rectangle((l, t, r, b), fill=color)
    if profile is not None:
        paste_profile(base, crop_fraction(profile, photo_crop), lay["photo"])
    if logo is not None:
        paste_logo(base, logo, lay["logo"])
    n = lay["name"]
    name_text = (name or "").upper() if n.get("upper", True) else (name or "")
    name_font, _ = fit_font(name_text, n["max_size"], n["min_size"], n["max_width"], bool(n.get("bold", True)))
    draw_text(draw, n["x"], n["y"], name_text, name_font, tuple(n.get("color", [0, 0, 0])), "ls")
    d = lay["designation"]
    des_text = (designation or "").upper() if d.get("upper", True) else (designation or "")
    sp = int(d.get("spacing", 1) or 0)
    des_font, _ = fit_font(des_text, d["max_size"], d["min_size"], d["max_width"], bool(d.get("bold", False)), spacing=sp)
    draw_text(draw, d["x"], d["y"], des_text, des_font, tuple(d.get("color", [40, 40, 40])), "la", spacing=sp)
    return base


def to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def png_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def announcement_texts(template: str, name: str, client: str, pronoun: str) -> tuple[str, str]:
    """Plain text and an HTML fragment (name in bold) for the announcement."""
    tpl = template or DEFAULT_ANNOUNCEMENT

    def fill(s: str) -> str:
        return s.replace("{name}", name or "").replace("{client}", client or "").replace("{pronoun}", pronoun or "")

    plain = fill(tpl)

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    html = esc(tpl).replace(esc("{name}"), "<b>" + esc(name or "") + "</b>").replace(esc("{client}"), esc(client or ""))
    html = html.replace(esc("{pronoun}"), esc(pronoun or ""))
    html = re.sub(r"  +", lambda m: "&nbsp;" * len(m.group(0)), html)
    html = html.replace("\n", "<br>\n")
    return plain, html


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "").strip().rstrip(".")
    return cleaned or "post"
