"""
Relay Post Studio

One app for the whole internal-post workflow, in two pages:

PAGE 1  -  Requests & Replies
    - Scan the drop folder for screenshots (OCR reads Client + Employee name)
    - Fix any OCR mistakes by double-clicking a row
    - Send / Make Posts, per row:
        * if the client's logo is ALREADY saved, nobody is emailed for it -
          the post is built straight away using the logo we have, the
          headshot from saved_profile, and the designation from
          Employee_Data.xlsx (or from an earlier reply)
        * otherwise the logo/designation request email goes out
          (repeats are intentional reminders)
      Rows that still need a designation or a headshot are left in place
      and flagged, so nothing is emailed for a logo we already hold.
    - Check Replies: reads Outlook, saves each person's logo into
      saved_logos renamed to the CLIENT name, pulls the Designation
      they typed, and updates log.xlsx

PAGE 2  -  Posts & Publish
    - Generate Posts: crosschecks the emailed designation against
      Employee_Data.xlsx (if they differ, the EMAILED one wins),
      then builds the post image exactly like post_generator.py
      (the image code is untouched, output is identical)
    - Each generated post gets:
        * a thumbnail: click it to copy the image, or drag it to open the
          folder with the file selected (drag it onto the upload box from
          there, since Explorer is a dependable drag source)
        * Copy Image: puts the picture AND the file on the clipboard using
          Windows' own clipboard, so one Ctrl+V works in Teams, Word, Canva
          and most upload boxes
        * Copy Text: copies WITH formatting so the name stays bold when
          pasted into Teams / Outlook / Word
        * Show in Folder: opens the file in Explorer, selected and ready to
          drag or pick with Browse (for upload boxes that will not paste)
        * him/her auto-guessed from the first name, changeable per post
        * an Uploaded checkbox that stays ticked between runs

Files it uses (all your existing ones, nothing new to set up):
    config.json                  - email text, watch folder, tesseract path
    image_generator_config.json  - log.xlsx, saved_logos, saved_profile,
                                   generated_posts, template
    Employee_Data.xlsx           - master Name + Designation list
                                   (kept next to this script, or set
                                    "employee_data_file" in config.json)
    studio_state.json            - remembers Uploaded ticks + him/her picks
                                   (created automatically)

Run with Relay_Post_Studio.bat
"""

import os
import re
import json
import sys
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timedelta

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
except Exception:
    print("tkinter is missing. Reinstall Python with the tcl/tk option ticked.")
    sys.exit(1)

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageTk
    from openpyxl import Workbook, load_workbook
    from openpyxl.utils import get_column_letter
except ImportError:
    print("Required packages missing. Run:  pip install pillow openpyxl pywin32 pytesseract")
    sys.exit(1)

# Windows-only imports; guarded so the file at least opens elsewhere
try:
    import win32com.client
    HAVE_OUTLOOK = True
except ImportError:
    HAVE_OUTLOOK = False

try:
    import pytesseract
    HAVE_TESSERACT = True
except ImportError:
    HAVE_TESSERACT = False

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
IMG_CONFIG_PATH = SCRIPT_DIR / "image_generator_config.json"
STATE_PATH = SCRIPT_DIR.parents[1] / "Data" / "Posts" / "studio_state.json"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
LOGO_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

PLACEHOLDER_DESIG = "please enter your designation here"

# Filename fragments that are almost never the client logo
DEFAULT_IGNORE_NAMES = [
    "relay", "signature", "sig-", "logo_relay", "footer",
    "facebook", "linkedin", "twitter", "instagram", "youtube", "insta",
    "icon", "social", "spacer", "pixel", "image001", "emailsig", "banner",
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

DEFAULT_CONFIG = {
    "watch_folder": "C:\\LogoRequests",
    "tesseract_path": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
    "email_subject": "Logo request - {client}",
    "email_body": (
        "Hi {first_name},\n\n"
        "Hope you're doing well!\n\n"
        "According to our records, you are currently placed with {client}. "
        "We are preparing an internal post and need the following details "
        "from you:\n\n"
        "Designation: [Please enter your designation here]\n\n"
        "Company Logo: Please attach the logo of {client} in a high-resolution "
        "PNG or JPG format. A PNG with a transparent background would be ideal.\n\n"
        "Please share these details by replying to this email rather than "
        "sending them through Teams. Kindly send them as soon as possible so "
        "we can finalize and publish the post.\n\n"
        "Thank you so much!"
    ),
    "reply_subject_match": "Logo request",
    "mail_folder": "",
    "days_back": 30,
    "min_logo_bytes": 3000,
    "ignore_logo_names": DEFAULT_IGNORE_NAMES,
    "employee_data_file": "",
    "announcement_template": DEFAULT_ANNOUNCEMENT,
    "announcement_signature_note": "Hitesh Parmar is part of the template above; edit announcement_template to change it.",
}

# Common female first names (Indian + general) for the him/her auto-guess.
# Anything not in this list defaults to "him"; every post has a toggle.
FEMALE_FIRST_NAMES = {
    "priya", "neha", "pooja", "puja", "anjali", "karishma", "shreya", "divya",
    "sneha", "riya", "diya", "aishwarya", "kavya", "ananya", "isha", "nisha",
    "asha", "usha", "meera", "mira", "sita", "gita", "geeta", "radha",
    "lakshmi", "laxmi", "saloni", "simran", "komal", "payal", "kajal",
    "sonal", "sonali", "rupali", "deepika", "dipika", "priyanka", "tanvi",
    "tanya", "khushi", "mansi", "jhanvi", "janvi", "bhavna", "bhavana",
    "hetal", "dhwani", "dhruvi", "krupa", "kruti", "foram", "jyoti", "swati",
    "shweta", "sweta", "nidhi", "vidhi", "aarti", "arti", "kirti", "keerti",
    "preeti", "priti", "smita", "namrata", "mamta", "kavita", "savita",
    "anita", "sunita", "vanita", "lalita", "sarita", "babita", "sangeeta",
    "sangita", "archana", "rachana", "vandana", "sadhana", "kalpana",
    "aparna", "poonam", "ritu", "rina", "reena", "rita", "heena", "hina",
    "sapna", "seema", "sima", "rekha", "manisha", "monika", "monica",
    "pallavi", "prachi", "ruchi", "ruchita", "nikita", "ankita", "amruta",
    "amrita", "shraddha", "shradha", "vaishali", "vaishnavi", "gayatri",
    "aditi", "akshita", "bhumika", "chandni", "darshana", "ekta", "falguni",
    "grishma", "harsha", "ishita", "jagruti", "juhi", "kinjal", "krishna",
    "mahima", "megha", "mitali", "nehal", "pinal", "rajvi", "shivani",
    "trupti", "twinkle", "urvashi", "vrunda", "yamini", "zeel",
    "sarah", "sara", "emily", "jessica", "mary", "linda", "susan", "karen",
    "nancy", "lisa", "sandra", "ashley", "emma", "olivia", "sophia",
    "isabella", "mia", "amelia", "harper", "evelyn", "abigail", "ella",
    "scarlett", "grace", "chloe", "victoria", "hannah", "lily", "zoe",
    "natalie", "rachel", "megan", "lauren", "kayla", "anna", "julia",
    "kate", "katie", "jennifer", "michelle", "kimberly", "amy", "angela",
    "melissa", "nicole", "christina", "alice", "julie", "heather", "diana",
    "tina", "kizzy",
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def load_json(path, defaults=None):
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if defaults:
        for k, v in defaults.items():
            data.setdefault(k, v)
    return data


def save_json(path, data):
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"(could not save {path.name}: {e})")


def normalize(value):
    """Ignore capitalization, spaces, hyphens and underscores when matching."""
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def safe_name(text):
    return re.sub(r'[\\/:*?"<>|]+', "_", str(text or "")).strip()


def safe_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(".")
    return cleaned or "generated_post"


def guess_pronoun(full_name):
    first = (full_name or "").strip().split()
    if not first:
        return "him"
    return "her" if first[0].lower() in FEMALE_FIRST_NAMES else "him"


def value_text(v):
    return "" if v is None else str(v).strip()


# --------------------------------------------------------------------------
# OCR + Outlook (from logo_request_bot.py)
# --------------------------------------------------------------------------

def extract_name_and_client(image_path):
    text = pytesseract.image_to_string(Image.open(image_path))
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    client = None
    name = None
    for line in lines:
        parts = re.split(r"\s+[-\u2013\u2014]\s+", line, maxsplit=1)
        if len(parts) == 2:
            client, name = parts[0], parts[1]
            break
    if not (client and name) and len(lines) >= 2:
        client = client or lines[0]
        name = name or lines[1]
    clean = lambda s: re.sub(r"[|_~`]+", "", s).strip(" .,:;-") if s else s
    return clean(name), clean(client)


def resolve_email(outlook, person_name):
    try:
        session = outlook.Session
        recipient = session.CreateRecipient(person_name)
        recipient.Resolve()
        if recipient.Resolved:
            addr_entry = recipient.AddressEntry
            display = addr_entry.Name
            email = None
            try:
                exch_user = addr_entry.GetExchangeUser()
                if exch_user:
                    email = exch_user.PrimarySmtpAddress
            except Exception:
                pass
            if not email:
                email = getattr(addr_entry, "Address", None)
            return email, display
    except Exception:
        pass
    return None, None


def get_mail_folder(outlook, folder_name):
    ns = outlook.GetNamespace("MAPI")
    inbox = ns.GetDefaultFolder(6)
    if not folder_name:
        return inbox
    for f in inbox.Folders:
        if f.Name.lower() == folder_name.lower():
            return f
    for store_folder in ns.Folders:
        for f in store_folder.Folders:
            if f.Name.lower() == folder_name.lower():
                return f
    return inbox


# --------------------------------------------------------------------------
# reply parsing (from check_replies.py)
# --------------------------------------------------------------------------

def extract_designation(body):
    if not body:
        return ""
    cut_markers = [
        "\n-----original message-----",
        "\nfrom:",
        "\n________________________________",
        "\nsent from my",
        "\non ",
    ]
    low = body.lower()
    cut_at = len(body)
    for mk in cut_markers:
        idx = low.find(mk)
        if idx != -1:
            cut_at = min(cut_at, idx)
    reply_only = body[:cut_at]
    lines = [l.strip() for l in reply_only.splitlines()]
    for i, line in enumerate(lines):
        m = re.match(r"^\s*designation\s*[:\-]\s*(.*)$", line, re.IGNORECASE)
        if m:
            value = m.group(1).strip()
            if value:
                value = value.strip(" .-")
                if value and PLACEHOLDER_DESIG not in value.lower():
                    return value
                return ""
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    cand = nxt.strip(" .-")
                    if PLACEHOLDER_DESIG in cand.lower():
                        return ""
                    if re.match(r"^\s*(company logo|logo)\s*[:\-]", cand, re.IGNORECASE):
                        return ""
                    return cand
            return ""
    return ""


def name_is_ignored(filename, ignore_fragments):
    fn = (filename or "").lower()
    return any(frag.lower() in fn for frag in ignore_fragments)


def pick_logo(msg, min_bytes, ignore_fragments, tmp_dir):
    candidates = []
    for att in msg.Attachments:
        fn = att.FileName or ""
        ext = Path(fn).suffix.lower()
        if ext not in LOGO_IMAGE_EXTS:
            continue
        try:
            size = int(att.Size)
        except Exception:
            size = 0
        if size < int(min_bytes):
            continue
        if name_is_ignored(fn, ignore_fragments):
            continue
        candidates.append({"att": att, "size": size, "name": fn})
    if not candidates:
        return None
    for c in candidates:
        w = h = None
        try:
            tmp = tmp_dir / ("dim_" + safe_name(c["name"] or "img"))
            c["att"].SaveAsFile(str(tmp))
            with Image.open(tmp) as im:
                w, h = im.size
            try:
                tmp.unlink()
            except Exception:
                pass
        except Exception:
            pass
        c["w"], c["h"] = w, h

    def is_tiny_square(c):
        if c["w"] and c["h"]:
            return c["w"] < 60 and c["h"] < 60
        return False

    real = [c for c in candidates if not is_tiny_square(c)]
    if not real:
        real = candidates
    real.sort(key=lambda c: (c["size"], (c["w"] or 0) * (c["h"] or 0)), reverse=True)
    return real[0]["att"]


# --------------------------------------------------------------------------
# clipboard with formatting (bold name survives paste into Teams/Word/Outlook)
# --------------------------------------------------------------------------

def build_cf_html(fragment_html):
    """Wrap an HTML fragment in the Windows CF_HTML clipboard header."""
    prefix = "<html><body><!--StartFragment-->"
    suffix = "<!--EndFragment--></body></html>"
    header_tpl = (
        "Version:0.9\r\n"
        "StartHTML:{:09d}\r\n"
        "EndHTML:{:09d}\r\n"
        "StartFragment:{:09d}\r\n"
        "EndFragment:{:09d}\r\n"
    )
    dummy = header_tpl.format(0, 0, 0, 0)
    header_len = len(dummy.encode("utf-8"))
    prefix_b = prefix.encode("utf-8")
    fragment_b = fragment_html.encode("utf-8")
    suffix_b = suffix.encode("utf-8")
    start_html = header_len
    start_frag = header_len + len(prefix_b)
    end_frag = start_frag + len(fragment_b)
    end_html = end_frag + len(suffix_b)
    header = header_tpl.format(start_html, end_html, start_frag, end_frag)
    return header.encode("utf-8") + prefix_b + fragment_b + suffix_b


def _ps_quote(text):
    """Quote a value as a PowerShell single-quoted string literal."""
    return "'" + str(text).replace("'", "''") + "'"


def _run_powershell(script):
    """Run a short PowerShell script out of process, with no console window.

    Uses -EncodedCommand so quoting can never break the script, and so it runs
    even on machines where plain script execution is locked down. Windows
    PowerShell (powershell.exe) is built in, so there is nothing to pip install
    and nothing that can get truncated the way the old hand-rolled memory calls
    did. Returns True on a clean exit."""
    import base64
    import subprocess
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Sta",
             "-EncodedCommand", encoded],
            creationflags=creationflags,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=25,
        )
        return result.returncode == 0
    except Exception:
        return False


def copy_image_to_clipboard(path):
    """Put the picture AND the file on the clipboard, using Windows' own
    clipboard through PowerShell instead of hand-rolled ctypes.

      - the picture pastes into Word, Canva, Paint, chat message bodies
      - the file pastes into Teams, Explorer, Gmail, most upload drop zones

    SetDataObject(..., $true) flushes the data so it stays on the clipboard
    after PowerShell exits, so one Ctrl+V just works. Returns True on success
    so the caller can fall back to opening the folder if anything goes wrong."""
    script = (
        "$ErrorActionPreference='Stop';"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$p=" + _ps_quote(path) + ";"
        "$img=[System.Drawing.Image]::FromFile($p);"
        "$files=New-Object System.Collections.Specialized.StringCollection;"
        "[void]$files.Add($p);"
        "$d=New-Object System.Windows.Forms.DataObject;"
        "$d.SetImage($img);"
        "$d.SetFileDropList($files);"
        "[System.Windows.Forms.Clipboard]::SetDataObject($d,$true);"
    )
    return _run_powershell(script)


def copy_text_to_clipboard(plain_text, fragment_html, root):
    """Put the announcement on the clipboard with the name still bold (HTML)
    plus a plain-text copy, again through Windows' own clipboard. If PowerShell
    is not available for any reason, fall back to a plain-text-only copy via Tk
    so Copy Text never fully breaks."""
    try:
        cf_html = build_cf_html(fragment_html).decode("utf-8")
        script = (
            "$ErrorActionPreference='Stop';"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$plain=" + _ps_quote(plain_text) + ";"
            "$html=" + _ps_quote(cf_html) + ";"
            "$d=New-Object System.Windows.Forms.DataObject;"
            "$d.SetData([System.Windows.Forms.DataFormats]::UnicodeText,$plain);"
            "$d.SetData([System.Windows.Forms.DataFormats]::Html,$html);"
            "[System.Windows.Forms.Clipboard]::SetDataObject($d,$true);"
        )
        if _run_powershell(script):
            return True
    except Exception:
        pass
    try:
        root.clipboard_clear()
        root.clipboard_append(plain_text)
        return True
    except Exception:
        return False


def reveal_in_explorer(path):
    """Open the folder in Explorer with the file already selected, so it can be
    dragged straight onto an upload box or picked with the Browse button.

    Explorer is a rock-solid file drag source, which a Tk window simply is not.
    Handing the drag off to Explorer is the dependable way to drop the picture
    onto the few upload boxes that will not accept a paste."""
    p = str(path)
    try:
        import subprocess
        if os.name == "nt":
            # explorer is fussy: the path must be quoted and glued to /select,
            subprocess.Popen('explorer /select,"' + p + '"')
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(p)])
        return True
    except Exception:
        return False


def announcement_texts(template, name, client, pronoun):
    """Return (plain_text, html_fragment) with the name bold in the HTML."""
    plain = template.format(name=name, client=client, pronoun=pronoun)

    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))

    html_tpl = esc(template)
    html = html_tpl.replace(esc("{name}"), "<b>" + esc(name) + "</b>")
    html = html.replace(esc("{client}"), esc(client))
    html = html.replace(esc("{pronoun}"), esc(pronoun))
    # keep multi-space runs exactly (HTML collapses spaces otherwise)
    html = re.sub(r"  +", lambda m: "&nbsp;" * len(m.group(0)), html)
    html = html.replace("\n", "<br>\n")
    return plain, html


# --------------------------------------------------------------------------
# post image generation - UNCHANGED from post_generator.py
# --------------------------------------------------------------------------

PANEL_BLUE = (154, 205, 255)
WHITE = (255, 255, 255)
COVER_BOXES = [
    (18, 244, 320, 274, PANEL_BLUE),
    (18, 286, 320, 313, PANEL_BLUE),
    (50, 361, 296, 489, WHITE),
]

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS


def open_image(path):
    image = Image.open(path)
    image.load()
    return image


def font_paths(bold):
    if bold:
        return [
            SCRIPT_DIR / "Montserrat-Bold.ttf",
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path(r"C:\Windows\Fonts\segoeuib.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
    return [
        SCRIPT_DIR / "Montserrat-Regular.ttf",
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]


def load_font(size, bold):
    for path in font_paths(bold):
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def text_width(text, font, spacing=0):
    try:
        width = font.getlength(text)
    except AttributeError:
        box = font.getbbox(text)
        width = box[2] - box[0]
    return width + max(0, len(text) - 1) * spacing


def fit_font(text, start_size, minimum_size, max_width, bold, spacing=0):
    for size in range(start_size, minimum_size - 1, -1):
        font = load_font(size, bold)
        if text_width(text, font, spacing) <= max_width:
            return font
    return load_font(minimum_size, bold)


def draw_text(draw, x, y, text, font, color, anchor, spacing=0):
    if spacing <= 0:
        draw.text((x, y), text, font=font, fill=color, anchor=anchor)
        return
    for character in text:
        draw.text((x, y), character, font=font, fill=color, anchor=anchor)
        x += round(text_width(character, font)) + spacing


def paste_profile(base, profile):
    center_x, center_y = 460, 362
    outer_size = 213
    inset = 0
    supersample = 4
    blue = (90, 159, 228)

    backing_large = Image.new("RGBA", (outer_size * supersample,) * 2, (0, 0, 0, 0))
    ImageDraw.Draw(backing_large).ellipse(
        (0, 0, outer_size * supersample - 1, outer_size * supersample - 1),
        fill=blue + (255,),
    )
    backing = backing_large.resize((outer_size, outer_size), RESAMPLE)
    base.paste(backing, (center_x - outer_size // 2, center_y - outer_size // 2), backing)

    profile_size = outer_size - inset * 2
    profile = ImageOps.fit(profile.convert("RGBA"), (profile_size, profile_size), method=RESAMPLE)

    mask_large = Image.new("L", (profile_size * supersample,) * 2, 0)
    ImageDraw.Draw(mask_large).ellipse(
        (0, 0, profile_size * supersample - 1, profile_size * supersample - 1),
        fill=255,
    )
    circle_mask = mask_large.resize((profile_size, profile_size), RESAMPLE)

    alpha = profile.getchannel("A")
    combined_mask = ImageChops.multiply(circle_mask, alpha)

    base.paste(profile.convert("RGB"), (center_x - profile_size // 2, center_y - profile_size // 2), combined_mask)


def paste_logo(base, logo):
    left, top, right, bottom = 56, 367, 290, 483
    padding = 8
    logo = logo.convert("RGBA")
    visible = logo.getchannel("A").getbbox()
    if visible:
        logo = logo.crop(visible)

    available_width = right - left - padding * 2
    available_height = bottom - top - padding * 2
    width, height = logo.size
    scale = min(available_width / width, available_height / height)
    resized = logo.resize((max(1, round(width * scale)), max(1, round(height * scale))), RESAMPLE)
    x = left + padding + (available_width - resized.width) // 2
    y = top + padding + (available_height - resized.height) // 2
    base.paste(resized, (x, y), resized)


def create_post(template, name, designation, profile, logo):
    base = open_image(template).convert("RGB")
    if base.size != (600, 600):
        raise ValueError("internal_post_base.png must be 600 x 600 pixels.")

    draw = ImageDraw.Draw(base)
    for left, top, right, bottom, color in COVER_BOXES:
        draw.rectangle((left, top, right, bottom), fill=color)

    paste_profile(base, open_image(profile))
    paste_logo(base, open_image(logo))

    name_text = name.upper()
    name_font = fit_font(name_text, 25, 12, 290, True)
    draw_text(draw, 24, 264, name_text, name_font, (0, 0, 0), "ls")

    designation_text = designation.upper()
    designation_font = fit_font(designation_text, 19, 10, 290, False, spacing=1)
    draw_text(draw, 24, 288, designation_text, designation_font, (40, 40, 40), "la", spacing=1)
    return base


def image_index(folder):
    found = defaultdict(list)
    if not folder.is_dir():
        return found
    for file in sorted(folder.iterdir()):
        if file.is_file() and file.suffix.casefold() in LOGO_IMAGE_EXTS:
            found[normalize(file.stem)].append(file)
    return found


def find_image(label, index):
    matches = index.get(normalize(label), [])
    if not matches:
        return None
    order = {".png": 0, ".jpg": 1, ".jpeg": 2, ".webp": 3, ".bmp": 4, ".gif": 5}
    return sorted(matches, key=lambda f: (order.get(f.suffix.casefold(), 99), f.name))[0]


# --------------------------------------------------------------------------
# Excel helpers
# --------------------------------------------------------------------------

LOG_HEADERS = ["Timestamp", "Screenshot", "Employee", "Client", "Email", "Status"]


def append_log_row(log_file, image_name, name, client, email, status):
    if log_file.exists():
        wb = load_workbook(log_file)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(LOG_HEADERS)
    ws.append([datetime.now().strftime("%Y-%m-%d %H:%M"),
               image_name, name, client, email, status])
    wb.save(log_file)


def read_log_rows(log_file):
    """Return (headers_lower, list of dict rows)."""
    if not log_file.exists():
        return [], []
    wb = load_workbook(log_file, data_only=True, read_only=True)
    ws = wb.active
    headers = [str(c.value).strip() if c.value else "" for c in ws[1]]
    hl = [h.lower() for h in headers]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        d = {}
        for i, h in enumerate(hl):
            d[h] = row[i] if i < len(row) else None
        rows.append(d)
    return hl, rows


def load_email_to_client(log_file):
    mapping = {}
    hl, rows = read_log_rows(log_file)
    if "email" not in hl or "client" not in hl:
        return mapping
    for r in rows:
        email = value_text(r.get("email"))
        client = value_text(r.get("client"))
        if email and client:
            mapping[email.lower()] = client
    return mapping


def update_log_reply_info(log_file, results):
    """Write Designation + Logo status columns, matched by client name."""
    if not log_file.exists() or not results:
        return
    wb = load_workbook(log_file)
    ws = wb.active

    def ensure_col(name, width):
        hl = [str((ws.cell(row=1, column=c).value or "")).strip().lower()
              for c in range(1, ws.max_column + 1)]
        if name.lower() in hl:
            return hl.index(name.lower()) + 1
        col = ws.max_column + 1
        ws.cell(row=1, column=col, value=name)
        ws.column_dimensions[get_column_letter(col)].width = width
        return col

    hl = [str((ws.cell(row=1, column=c).value or "")).strip().lower()
          for c in range(1, ws.max_column + 1)]
    if "client" not in hl:
        return
    client_col = hl.index("client") + 1
    desig_col = ensure_col("Designation", 26)
    status_col = ensure_col("Logo status", 26)

    for r in range(2, ws.max_row + 1):
        cell_client = ws.cell(row=r, column=client_col).value
        if not cell_client:
            continue
        key = str(cell_client).strip().lower()
        if key in results:
            info = results[key]
            if info.get("designation"):
                ws.cell(row=r, column=desig_col, value=info["designation"])
            if info.get("logo"):
                ws.cell(row=r, column=status_col,
                        value="Logo received: " + Path(info["logo"]).name)
            else:
                ws.cell(row=r, column=status_col, value="Logo MISSING")
    wb.save(log_file)


def load_employee_master(path):
    """{normalized name: designation} from Employee_Data.xlsx."""
    master = {}
    if not path or not Path(path).is_file():
        return master
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
        ws = wb.active
        headers = [normalize(c.value) for c in ws[1]]
        if "name" not in headers or "designation" not in headers:
            return master
        n_i = headers.index("name")
        d_i = headers.index("designation")
        for row in ws.iter_rows(min_row=2, values_only=True):
            nm = value_text(row[n_i] if n_i < len(row) else "")
            dg = value_text(row[d_i] if d_i < len(row) else "")
            if nm:
                master[normalize(nm)] = dg
    except Exception as e:
        print(f"(could not read employee master: {e})")
    return master


def resolve_designation(name, master, log_desig):
    """Work out the designation to use, same rule as Generate Posts:
    a designation typed in an email reply wins over the master list on any
    mismatch. Returns (designation, short_note)."""
    key = normalize(name)
    reply = value_text(log_desig.get(key, ""))
    master_val = value_text(master.get(key, ""))
    if reply and master_val:
        if normalize(reply) == normalize(master_val):
            return reply, "match"
        return reply, "reply differs, used reply"
    if reply:
        return reply, "from reply"
    if master_val:
        return master_val, "from Employee_Data"
    return "", ""


# --------------------------------------------------------------------------
# THE APP
# --------------------------------------------------------------------------

class RelayPostStudio:
    def __init__(self, root):
        self.root = root
        root.title("Relay Post Studio")
        root.geometry("1150x760")

        self.cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        self.img_cfg = load_json(IMG_CONFIG_PATH, {})
        self.state = load_json(STATE_PATH, {})
        self.state.setdefault("posts", {})   # name_key -> {uploaded, pronoun}

        # resolve paths
        self.watch = Path(self.cfg.get("watch_folder", "C:\\LogoRequests"))
        self.log_file = Path(self.img_cfg.get("log_file") or (self.watch / "log.xlsx"))
        self.logos_dir = Path(self.img_cfg.get("saved_logos_folder")
                              or self.cfg.get("logo_save_folder")
                              or (self.watch / "saved_logos"))
        self.profiles_dir = Path(self.img_cfg.get("saved_profile_folder")
                                 or (self.watch / "saved_profile"))
        self.output_dir = Path(self.img_cfg.get("output_folder")
                               or (self.watch / "generated_posts"))
        tmpl = self.img_cfg.get("template_file", "internal_post_base.png")
        tp = Path(tmpl)
        self.template = tp if tp.is_absolute() else SCRIPT_DIR / tp

        emp = str(self.cfg.get("employee_data_file", "")).strip()
        self.employee_file = self._find_employee_file(emp)

        for d in (self.watch, self.logos_dir, self.output_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

        self.outlook = None
        self.queue = []           # page 1 scanned rows
        self.post_rows = []       # page 2 widgets/data
        self.thumb_refs = []      # keep PhotoImage refs alive

        self._build_ui()
        self.log(f"Drop folder: {self.watch}")
        self.log(f"log.xlsx:    {self.log_file}")
        self.log(f"Logos:       {self.logos_dir}")
        self.log(f"Profiles:    {self.profiles_dir}")
        self.log(f"Employee master: {self.employee_file}"
                 + ("" if self.employee_file.is_file() else "  (NOT FOUND yet)"))
        self.refresh_posts_page(scan_existing=True)

    # ---------------- UI scaffolding ----------------

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=8)

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True)

        self.page1 = ttk.Frame(self.nb)
        self.page2 = ttk.Frame(self.nb)
        self.page3 = ttk.Frame(self.nb)
        self.nb.add(self.page1, text="  1. Requests & Replies  ")
        self.nb.add(self.page2, text="  2. Posts & Publish  ")
        self.nb.add(self.page3, text="  3. Done  ")

        # ----- page 1 -----
        top = ttk.Frame(self.page1)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Button(top, text="Scan Screenshots", style="Big.TButton",
                   command=self.scan_screenshots).pack(side="left", padx=4)
        ttk.Button(top, text="Send / Make Posts", style="Big.TButton",
                   command=self.send_all).pack(side="left", padx=4)
        ttk.Button(top, text="Check Replies", style="Big.TButton",
                   command=self.check_replies).pack(side="left", padx=4)
        ttk.Label(top, text="Emails anyone whose logo you still need; for "
                            "anyone whose logo is already saved, it just makes "
                            "the post. Double-click a row to fix Client / Name "
                            "first.", wraplength=520, justify="left"
                  ).pack(side="left", padx=14)

        cols = ("screenshot", "client", "name", "email", "status")
        self.tree = ttk.Treeview(self.page1, columns=cols, show="headings", height=14)
        for c, w in zip(cols, (220, 220, 200, 260, 180)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=10)
        self.tree.bind("<Double-1>", self.edit_row)

        ttk.Label(self.page1, text="Activity log:").pack(anchor="w", padx=10, pady=(8, 0))
        self.logbox = tk.Text(self.page1, height=10, state="disabled",
                              font=("Consolas", 9))
        self.logbox.pack(fill="both", expand=False, padx=10, pady=(2, 10))

        # ----- page 2 -----
        top2 = ttk.Frame(self.page2)
        top2.pack(fill="x", padx=10, pady=8)
        ttk.Button(top2, text="Generate Posts", style="Big.TButton",
                   command=self.generate_posts).pack(side="left", padx=4)
        ttk.Button(top2, text="Refresh List", command=lambda:
                   self.refresh_posts_page(scan_existing=True)).pack(side="left", padx=4)
        self.posts_summary = ttk.Label(top2, text="")
        self.posts_summary.pack(side="left", padx=14)
        ttk.Label(top2, text="Tip: CLICK a thumbnail to copy the image, or "
                             "DRAG it into your upload box."
                  ).pack(side="right", padx=6)

        self.canvas, self.inner = self._scrolling_list(self.page2)

        # ----- page 3 (Done) -----
        top3 = ttk.Frame(self.page3)
        top3.pack(fill="x", padx=10, pady=8)
        self.done_summary = ttk.Label(top3, text="")
        self.done_summary.pack(side="left", padx=4)
        ttk.Label(top3, text="Untick Uploaded on any row to move it back to Pending."
                  ).pack(side="right", padx=6)

        self.done_canvas, self.done_inner = self._scrolling_list(self.page3)

        self.root.bind_all("<MouseWheel>", self._on_wheel)

    def _scrolling_list(self, parent):
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        canvas = tk.Canvas(holder, highlightthickness=0)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(
            inner_id, width=e.width))
        return canvas, inner

    def _on_wheel(self, event):
        tab = self.nb.index(self.nb.select())
        if tab == 1:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif tab == 2:
            self.done_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        self.root.update_idletasks()

    def get_outlook(self):
        if not HAVE_OUTLOOK:
            messagebox.showerror("Outlook", "pywin32 is not installed. Run setup.bat.")
            return None
        if self.outlook is None:
            try:
                self.outlook = win32com.client.Dispatch("Outlook.Application")
            except Exception as e:
                messagebox.showerror("Outlook",
                    f"Could not start Outlook:\n{e}\n\n"
                    "Make sure classic Outlook desktop is installed and signed in.")
                return None
        return self.outlook

    # ---------------- shared: matching + one-post builder ----------------

    def _load_match_data(self):
        """Everything needed to decide 'do we already have this?': the saved
        logos, the saved headshots, the Employee_Data list, and any
        designations already captured from replies in log.xlsx."""
        logos = image_index(self.logos_dir)
        profiles = image_index(self.profiles_dir)
        self.employee_file = self._find_employee_file(
            str(self.cfg.get("employee_data_file", "")).strip())
        master = load_employee_master(self.employee_file)
        log_desig = {}
        try:
            _hl, rows = read_log_rows(self.log_file)
            latest = {}
            for r in rows:
                nm = value_text(r.get("employee") or r.get("name"))
                if nm:
                    latest[normalize(nm)] = r
            for k, r in latest.items():
                dg = value_text(r.get("designation"))
                if dg:
                    log_desig[k] = dg
        except Exception:
            pass
        return logos, profiles, master, log_desig

    def classify_row(self, row, logos, profiles, master, log_desig):
        """Decide what should happen to one screenshot row and stash the
        resolved bits on it. Key rule you asked for: if the client's logo is
        already saved, nobody gets emailed for it."""
        row["logo_on_file"] = False
        row["auto_ready"] = False
        row["designation"] = ""
        row["desig_note"] = ""
        row["profile"] = None
        row["logo"] = None

        name = row.get("name", "")
        client = row.get("client", "")
        if not name or not client:
            row["status"] = "OCR failed - edit me"
            return

        logo = find_image(client, logos)
        if logo is not None:
            # We already have this company's logo -> no email at all.
            row["logo_on_file"] = True
            row["logo"] = logo
            designation, note = resolve_designation(name, master, log_desig)
            row["designation"] = designation
            row["desig_note"] = note
            profile = find_image(name, profiles)
            row["profile"] = profile
            if designation and profile is not None:
                row["auto_ready"] = True
                row["status"] = "Will make post (logo on file)"
            elif not designation:
                row["status"] = "Logo on file - need designation"
            else:
                row["status"] = "Logo on file - need photo"
            return

        # No logo yet -> normal request-by-email path.
        row["status"] = "Ready to email" if row.get("email") else "No email found"

    def _make_one_post(self, name, client, designation, profile, logo, note=""):
        """Build + save one post image and remember it in studio_state.
        Shared by 'Send / Make Posts' and 'Generate Posts' so both behave
        identically."""
        post = create_post(self.template, name, designation, profile, logo)
        out = self.output_dir / f"{safe_filename(name)}.png"
        post.save(out, format="PNG", optimize=True)
        st = self.state["posts"].setdefault(normalize(name), {})
        st["name"] = name
        st["client"] = client
        st["file"] = str(out)
        st.setdefault("uploaded", False)
        st.setdefault("pronoun", guess_pronoun(name))
        return out

    # ---------------- page 1 actions ----------------

    def scan_screenshots(self):
        if not HAVE_TESSERACT:
            messagebox.showerror("OCR", "pytesseract is not installed. Run setup.bat.")
            return
        pytesseract.pytesseract.tesseract_cmd = self.cfg["tesseract_path"]
        images = [p for p in self.watch.iterdir()
                  if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file()]
        self.tree.delete(*self.tree.get_children())
        self.queue = []
        if not images:
            self.log("No screenshots found in the drop folder.")
            return
        self.log(f"Scanning {len(images)} screenshot(s)...")
        logos, profiles, master, log_desig = self._load_match_data()
        outlook = self.get_outlook()
        for img in images:
            try:
                name, client = extract_name_and_client(img)
            except Exception as e:
                name, client = None, None
                self.log(f"  OCR error on {img.name}: {e}")
            row = {"img": img, "name": name or "", "client": client or "",
                   "email": "", "resolved": "", "status": ""}
            # only look up the email if we still need a logo from this person
            have_logo = bool(row["client"]) and (
                find_image(row["client"], logos) is not None)
            if row["name"] and not have_logo and outlook:
                email, resolved = resolve_email(outlook, row["name"])
                row["email"] = email or ""
                row["resolved"] = resolved or ""
            self.classify_row(row, logos, profiles, master, log_desig)
            self.queue.append(row)
            self.tree.insert("", "end", values=(img.name, row["client"],
                                                row["name"], row["email"],
                                                row["status"]))
        n_email = sum(1 for r in self.queue if r["status"] == "Ready to email")
        n_make = sum(1 for r in self.queue if r.get("auto_ready"))
        self.log(f"Scan done: {n_email} to email, {n_make} ready to make now "
                 f"(logo already saved), {len(self.queue)} total. "
                 "Double-click any row to fix Client / Name.")

    def edit_row(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        row = self.queue[idx]
        client = simpledialog.askstring("Edit", "Client name:",
                                        initialvalue=row["client"], parent=self.root)
        if client is None:
            return
        name = simpledialog.askstring("Edit", "Employee name:",
                                      initialvalue=row["name"], parent=self.root)
        if name is None:
            return
        row["client"], row["name"] = client.strip(), name.strip()
        logos, profiles, master, log_desig = self._load_match_data()
        row["email"], row["resolved"] = "", ""
        have_logo = bool(row["client"]) and (
            find_image(row["client"], logos) is not None)
        if row["name"] and not have_logo:
            outlook = self.get_outlook()
            if outlook:
                email, resolved = resolve_email(outlook, row["name"])
                row["email"] = email or ""
                row["resolved"] = resolved or ""
        self.classify_row(row, logos, profiles, master, log_desig)
        self.tree.item(sel[0], values=(row["img"].name, row["client"], row["name"],
                                       row["email"], row["status"]))

    def send_all(self):
        to_email = [r for r in self.queue if r["status"] == "Ready to email"]
        to_make = [r for r in self.queue if r.get("auto_ready")]
        if not to_email and not to_make:
            messagebox.showinfo(
                "Nothing to do",
                "No emails to send and no posts to make right now.\n\n"
                "Scan first. Rows that say 'need designation' or 'need photo' "
                "need that one item added before the post can be built, and "
                "rows without an email need fixing (double-click them).")
            return

        # confirmation, spelling out both actions
        lines = []
        if to_email:
            lines.append(f"Send {len(to_email)} email(s):")
            for r in to_email[:12]:
                lines.append(f"   {r['client']}  ->  {r['resolved'] or r['email']}")
            if len(to_email) > 12:
                lines.append(f"   ...and {len(to_email) - 12} more")
        if to_make:
            if lines:
                lines.append("")
            lines.append(f"Make {len(to_make)} post(s) now "
                         "(logo already saved, no email sent):")
            for r in to_make[:12]:
                lines.append(f"   {r['name']}  ->  {r['client']}")
            if len(to_make) > 12:
                lines.append(f"   ...and {len(to_make) - 12} more")
        if to_email:
            lines.append("")
            lines.append("Repeated emails are sent on purpose (reminders).")
        if not messagebox.askyesno("Confirm", "\n".join(lines)):
            return

        processed = self.watch / "processed"
        failed = self.watch / "failed"
        processed.mkdir(exist_ok=True)
        failed.mkdir(exist_ok=True)

        # 1) build posts for people whose logo we already have (no email)
        made = 0
        if to_make:
            if not self.template.is_file():
                messagebox.showerror("Template",
                    f"Template not found:\n{self.template}")
            else:
                for r in to_make:
                    try:
                        self._make_one_post(r["name"], r["client"],
                                            r["designation"], r["profile"],
                                            r["logo"], r.get("desig_note", ""))
                        made += 1
                        self.log(f"  post made: {r['name']} | {r['client']} | "
                                 f"{r['designation']}  (logo already saved)")
                        try:
                            shutil.move(str(r["img"]), processed / r["img"].name)
                        except Exception:
                            pass
                        append_log_row(self.log_file, r["img"].name, r["name"],
                                       r["client"], r["email"],
                                       "Post made (logo on file)")
                        r["status"] = "Post made"
                    except Exception as e:
                        self.log(f"  could not make post for {r['name']}: {e}")
                        r["status"] = "Make failed"
                save_json(STATE_PATH, self.state)

        # 2) email everyone who still needs to send a logo
        sent = 0
        if to_email:
            outlook = self.get_outlook()
            if not outlook:
                self.refresh_tree()
                self.refresh_posts_page(scan_existing=False)
                return
            for r in to_email:
                first = r["name"].split()[0].title() if r["name"].split() else r["name"]
                subject = self.cfg["email_subject"].format(client=r["client"],
                                                           first_name=first)
                body = self.cfg["email_body"].format(first_name=first,
                                                     client=r["client"])
                try:
                    mail = outlook.CreateItem(0)
                    mail.To = r["email"]
                    mail.Subject = subject
                    mail.Body = body
                    mail.Send()
                    shutil.move(str(r["img"]), processed / r["img"].name)
                    append_log_row(self.log_file, r["img"].name, r["name"],
                                   r["client"], r["email"], "Sent")
                    r["status"] = "Sent"
                    sent += 1
                    self.log(f"  sent -> {r['email']}  ({r['client']})")
                except Exception as e:
                    self.log(f"  FAILED -> {r['email']}: {e}")
                    try:
                        shutil.move(str(r["img"]), failed / r["img"].name)
                    except Exception:
                        pass
                    append_log_row(self.log_file, r["img"].name, r["name"],
                                   r["client"], r["email"], "Send failed")
                    r["status"] = "Send failed"

        # unfixable rows: clear them out so the drop folder stays tidy.
        # 'need designation' / 'need photo' rows are left in place on purpose,
        # so they get picked up again next scan once you add the missing item.
        for r in self.queue:
            if r["status"] in ("OCR failed - edit me", "No email found"):
                try:
                    shutil.move(str(r["img"]), failed / r["img"].name)
                except Exception:
                    pass
                append_log_row(self.log_file, r["img"].name, r["name"],
                               r["client"], r["email"],
                               "OCR failed" if "OCR" in r["status"]
                               else "Email not found")

        self.refresh_tree()
        self.refresh_posts_page(scan_existing=False)
        done_bits = []
        if sent:
            done_bits.append(f"sent {sent} email(s)")
        if made:
            done_bits.append(f"made {made} post(s)")
        self.log("Done: " + (", ".join(done_bits) if done_bits
                             else "nothing changed") + ".")
        if made:
            self.log("New posts are waiting on the '2. Posts & Publish' tab.")

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.queue:
            self.tree.insert("", "end", values=(r["img"].name, r["client"],
                                                r["name"], r["email"], r["status"]))

    def check_replies(self):
        outlook = self.get_outlook()
        if not outlook:
            return
        cfg = self.cfg
        match_phrase = cfg.get("reply_subject_match", "Logo request")
        days_back = int(cfg.get("days_back", 30))
        min_bytes = int(cfg.get("min_logo_bytes", 3000))
        ignore_fragments = cfg.get("ignore_logo_names", DEFAULT_IGNORE_NAMES)
        cutoff = datetime.now() - timedelta(days=days_back)

        tmp_dir = self.watch / "_tmp_dim"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        processed_file = self.watch / "processed_replies.json"
        processed = load_json(processed_file)

        recheck_all = False
        if processed:
            recheck_all = messagebox.askyesno(
                "Check replies",
                f"{len(processed)} reply email(s) were handled in past runs.\n\n"
                "Re-check those too?\n\n"
                "Yes = scan everything again (use this if a reply seems missed)\n"
                "No = only new replies")

        email_to_client = load_email_to_client(self.log_file)
        self.log(f"({len(email_to_client)} people known from log.xlsx; replies "
                 "from them match even if the subject was changed)")

        # scan the chosen folder AND its subfolders one level down,
        # in case an Outlook rule moved the reply
        base = get_mail_folder(outlook, cfg.get("mail_folder", ""))
        folders = [base]
        try:
            for sub in base.Folders:
                folders.append(sub)
        except Exception:
            pass

        results = {}
        seen = new_saved = missing = 0
        skipped_processed = 0
        scanned = 0
        recent_subjects = []

        def received_naive(msg):
            try:
                rt = msg.ReceivedTime
                return rt.replace(tzinfo=None)
            except Exception:
                return None

        for folder in folders:
            self.log(f"Scanning folder: {folder.Name}")
            try:
                messages = folder.Items
                messages.Sort("[ReceivedTime]", True)
            except Exception as e:
                self.log(f"  (could not read this folder: {e})")
                continue

            for msg in messages:
                try:
                    if msg.Class != 43:
                        continue
                    rt = received_naive(msg)
                    if rt is not None and rt < cutoff:
                        break  # newest first, older than window, next folder
                    scanned += 1

                    subject = msg.Subject or ""
                    if len(recent_subjects) < 12:
                        recent_subjects.append(subject)

                    # sender email (Exchange X500 -> SMTP if needed)
                    try:
                        sender_email = (msg.SenderEmailAddress or "").strip().lower()
                    except Exception:
                        sender_email = ""
                    if sender_email and "@" not in sender_email:
                        try:
                            sender_email = (msg.Sender.GetExchangeUser()
                                            .PrimarySmtpAddress or "").strip().lower()
                        except Exception:
                            pass

                    subject_hit = match_phrase.lower() in subject.lower()
                    sender_hit = sender_email in email_to_client
                    if not subject_hit and not sender_hit:
                        continue

                    try:
                        entry_id = msg.EntryID
                    except Exception:
                        entry_id = None
                    if entry_id and entry_id in processed and not recheck_all:
                        skipped_processed += 1
                        continue

                    seen += 1

                    client = email_to_client.get(sender_email)
                    if not client:
                        m = re.search(re.escape(match_phrase) + r"\s*[-:]\s*(.+)",
                                      subject, re.IGNORECASE)
                        client = m.group(1).strip() if m else subject.strip()

                    try:
                        body = msg.Body or ""
                    except Exception:
                        body = ""
                    designation = extract_designation(body)

                    att = pick_logo(msg, min_bytes, ignore_fragments, tmp_dir)
                    saved_path = ""
                    if att is not None:
                        ext = Path(att.FileName or "").suffix.lower() or ".png"
                        dest = self.logos_dir / (safe_name(client) + ext)
                        for other in LOGO_IMAGE_EXTS:
                            alt = self.logos_dir / (safe_name(client) + other)
                            if alt.exists() and alt != dest:
                                try:
                                    alt.unlink()
                                except Exception:
                                    pass
                        att.SaveAsFile(str(dest))
                        saved_path = str(dest)

                    key = client.strip().lower()
                    results[key] = {"client": client, "logo": saved_path,
                                    "designation": designation}
                    how = "subject" if subject_hit else "sender email"
                    if saved_path:
                        new_saved += 1
                        self.log(f"  logo saved: {client}  "
                                 f"(designation: {designation or 'none'}, "
                                 f"matched by {how})")
                    else:
                        missing += 1
                        self.log(f"  NO logo in reply from {client}  "
                                 f"(designation: {designation or 'none'}, "
                                 f"matched by {how})")
                    if entry_id:
                        processed[entry_id] = {
                            "client": client, "saved": bool(saved_path),
                            "designation": designation,
                            "when": datetime.now().strftime("%Y-%m-%d %H:%M")}
                except Exception:
                    continue

        try:
            for f in tmp_dir.iterdir():
                try:
                    f.unlink()
                except Exception:
                    pass
            tmp_dir.rmdir()
        except Exception:
            pass

        save_json(processed_file, processed)
        try:
            update_log_reply_info(self.log_file, results)
        except Exception as e:
            self.log(f"(could not update log.xlsx: {e})")

        self.log(f"Replies done: scanned {scanned} email(s) in the last "
                 f"{days_back} days, {seen} matched, {new_saved} logo(s) saved, "
                 f"{missing} without a logo"
                 + (f", {skipped_processed} skipped as already handled "
                    "(press Check Replies again and choose Yes to re-check them)"
                    if skipped_processed else "") + ".")
        if seen == 0 and scanned > 0:
            self.log("Nothing matched. The 12 newest subjects I saw were:")
            for s in recent_subjects:
                self.log(f"    - {s}")
            self.log("If the reply is in this list, tell Karishma the exact "
                     "subject shown. If it is not in the list, the reply may "
                     "be in a different mailbox or older than "
                     f"{days_back} days (days_back in config.json).")

    # ---------------- page 2 actions ----------------

    def _find_employee_file(self, configured):
        """Find the employee master excel. Tries, in order: the config path,
        exact names in the script folder and the drop folder, then any xlsx
        whose filename contains 'employee' in either folder."""
        candidates = []
        if configured:
            candidates.append(Path(configured))
        for base in (SCRIPT_DIR, self.watch):
            candidates.append(base / "Employee_Data.xlsx")
            candidates.append(base / "Employee Data.xlsx")
        for base in (SCRIPT_DIR, self.watch):
            try:
                for f in sorted(base.glob("*.xlsx")):
                    if "employee" in f.name.lower():
                        candidates.append(f)
            except Exception:
                pass
        for c in candidates:
            try:
                if c.is_file():
                    return c
            except Exception:
                continue
        return SCRIPT_DIR / "Employee_Data.xlsx"  # best guess for messages

    def generate_posts(self):
        if not self.template.is_file():
            messagebox.showerror("Template",
                f"Template not found:\n{self.template}")
            return
        # re-find every time, in case she just dropped the file in
        self.employee_file = self._find_employee_file(
            str(self.cfg.get("employee_data_file", "")).strip())
        master = load_employee_master(self.employee_file)
        if master:
            self.log(f"Employee master loaded: {self.employee_file.name} "
                     f"({len(master)} names)")
        else:
            self.log("WARNING: Employee_Data.xlsx NOT loaded. Looked in:")
            self.log(f"    - {SCRIPT_DIR}")
            self.log(f"    - {self.watch}")
            self.log("  Put the excel (columns: Name, Designation) in either "
                     "folder, or set employee_data_file in config.json.")
            self.log("  Without it, only designations typed in email replies "
                     "can be used, so more rows get skipped.")
            if not messagebox.askyesno(
                    "Employee master missing",
                    "Employee_Data.xlsx was not found, so the designation "
                    "crosscheck cannot run.\n\nGenerate anyway using only "
                    "the designations people emailed?"):
                return
        hl, rows = read_log_rows(self.log_file)
        if not rows:
            messagebox.showinfo("Generate", "log.xlsx has no rows yet.")
            return

        profiles = image_index(self.profiles_dir)
        logos = image_index(self.logos_dir)
        # newest row per employee wins (repeated reminder rows collapse)
        latest = {}
        for r in rows:
            nm = value_text(r.get("employee") or r.get("name"))
            if nm:
                latest[normalize(nm)] = r

        made = skipped = 0
        for key, r in latest.items():
            name = value_text(r.get("employee") or r.get("name"))
            client = value_text(r.get("client"))
            email_desig = value_text(r.get("designation"))
            master_desig = master.get(normalize(name), "")

            # crosscheck: emailed designation wins on any mismatch
            if email_desig and master_desig:
                if normalize(email_desig) == normalize(master_desig):
                    designation, note = email_desig, "match"
                else:
                    designation, note = email_desig, "MISMATCH, used email"
            elif email_desig:
                designation, note = email_desig, "email only"
            elif master_desig:
                designation, note = master_desig, "master only"
            else:
                designation, note = "", ""

            problems = []
            if not name:
                problems.append("no employee name in log.xlsx")
            if not client:
                problems.append("no client in log.xlsx")
            if not designation:
                problems.append("no designation (not in their email reply "
                                "and not in Employee_Data)")
            profile = find_image(name, profiles) if name else None
            logo = find_image(client, logos) if client else None
            if name and profile is None:
                problems.append(f"add a photo named '{name}' to "
                                f"{self.profiles_dir.name}")
            if client and logo is None:
                problems.append(f"no logo for '{client}' in "
                                f"{self.logos_dir.name} (run Check Replies, "
                                "or they haven't sent it)")
            if problems:
                skipped += 1
                self.log(f"  SKIP {name or '(no name)'}: missing "
                         + ", ".join(problems))
                continue
            try:
                self._make_one_post(name, client, designation, profile, logo, note)
                made += 1
                self.log(f"  OK  {name} | {client} | {designation}  ({note})")
            except Exception as e:
                skipped += 1
                self.log(f"  SKIP {name}: {e}")

        save_json(STATE_PATH, self.state)
        self.log(f"Generation done: {made} made, {skipped} skipped.")
        self.refresh_posts_page(scan_existing=False)
        self.nb.select(self.page2)

    def refresh_posts_page(self, scan_existing=False):
        if scan_existing:
            # pick up posts generated in past runs too
            hl, rows = read_log_rows(self.log_file)
            client_by_name = {}
            for r in rows:
                nm = value_text(r.get("employee") or r.get("name"))
                cl = value_text(r.get("client"))
                if nm and cl:
                    client_by_name[normalize(nm)] = cl
            if self.output_dir.is_dir():
                for f in sorted(self.output_dir.glob("*.png")):
                    key = normalize(f.stem)
                    st = self.state["posts"].setdefault(key, {})
                    st.setdefault("name", f.stem)
                    st.setdefault("client", client_by_name.get(key, ""))
                    st["file"] = str(f)
                    st.setdefault("uploaded", False)
                    st.setdefault("pronoun", guess_pronoun(f.stem))
            save_json(STATE_PATH, self.state)

        for w in self.inner.winfo_children():
            w.destroy()
        for w in self.done_inner.winfo_children():
            w.destroy()
        self.thumb_refs.clear()
        self.post_rows.clear()

        posts = [(k, v) for k, v in self.state["posts"].items()
                 if v.get("file") and Path(v["file"]).is_file()]
        posts.sort(key=lambda kv: kv[1].get("name", ""))
        pending = [(k, v) for k, v in posts if not v.get("uploaded")]
        done = [(k, v) for k, v in posts if v.get("uploaded")]

        self.posts_summary.configure(
            text=f"{len(pending)} pending  |  {len(done)} in Done")
        self.done_summary.configure(
            text=f"{len(done)} uploaded post(s)")
        self.nb.tab(self.page3, text=f"  3. Done ({len(done)})  ")

        tmpl = self.cfg.get("announcement_template", DEFAULT_ANNOUNCEMENT)
        for key, info in pending:
            self._add_post_row(self.inner, key, info, tmpl)
        for key, info in done:
            self._add_post_row(self.done_inner, key, info, tmpl)

    def _add_post_row(self, parent, key, info, tmpl):
        frame = ttk.Frame(parent, relief="groove", borderwidth=1, padding=8)
        frame.pack(fill="x", pady=4, padx=2)

        # thumbnail: click copies the image; a drag gesture opens the folder
        # in Explorer (Explorer is a dependable drag source, this window is not)
        try:
            im = Image.open(info["file"])
            im.thumbnail((130, 130))
            photo = ImageTk.PhotoImage(im)
            self.thumb_refs.append(photo)
            thumb = tk.Label(frame, image=photo, cursor="hand2")
        except Exception:
            thumb = tk.Label(frame, text="(no preview)", cursor="hand2")
        thumb.grid(row=0, column=0, rowspan=3, padx=(0, 10))
        ttk.Label(frame, text="click = copy\ndrag = open folder",
                  justify="center",
                  font=("Segoe UI", 8)).grid(row=3, column=0)

        drag_state = {"start": None, "active": False}

        def on_press(event):
            drag_state["start"] = (event.x, event.y)
            drag_state["active"] = False

        def on_motion(event):
            if drag_state["active"] or drag_state["start"] is None:
                return
            sx, sy = drag_state["start"]
            if abs(event.x - sx) + abs(event.y - sy) < 6:
                return
            drag_state["active"] = True
            # copy it AND open the folder with the file selected, so she can
            # paste (Ctrl+V) OR drag the file straight out of Explorer
            copy_image_to_clipboard(info["file"])
            reveal_in_explorer(info["file"])
            self.log("Opened the folder with the file selected, drag it from "
                     "there onto the upload box. I also copied the image, so "
                     "Ctrl+V works too.")

        def on_release(event):
            was_drag = drag_state["active"]
            drag_state["start"] = None
            drag_state["active"] = False
            if not was_drag:
                # a plain click on the picture just copies it
                do_copy_image()

        thumb.bind("<ButtonPress-1>", on_press)
        thumb.bind("<B1-Motion>", on_motion)
        thumb.bind("<ButtonRelease-1>", on_release)

        name = info.get("name", "")
        client = info.get("client", "")
        ttk.Label(frame, text=name, font=("Segoe UI", 11, "bold")
                  ).grid(row=0, column=1, sticky="w")
        ttk.Label(frame, text=client).grid(row=1, column=1, sticky="w")

        pron_var = tk.StringVar(value=info.get("pronoun", guess_pronoun(name)))
        pron_row = ttk.Frame(frame)
        pron_row.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(pron_row, text="Pronoun: ").pack(side="left")
        pron = ttk.Combobox(pron_row, textvariable=pron_var,
                            values=("him", "her"), width=5, state="readonly")
        pron.pack(side="left")

        up_var = tk.BooleanVar(value=info.get("uploaded", False))

        def on_pron(*_):
            self.state["posts"][key]["pronoun"] = pron_var.get()
            save_json(STATE_PATH, self.state)
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", announcement_texts(
                tmpl, name, client, pron_var.get())[0])
            preview.configure(state="disabled")

        def on_upload(*_):
            self.state["posts"][key]["uploaded"] = up_var.get()
            save_json(STATE_PATH, self.state)
            # rebuild both pages so the row jumps between Pending and Done
            self.refresh_posts_page(scan_existing=False)

        def do_copy():
            plain, html = announcement_texts(tmpl, name, client, pron_var.get())
            ok = copy_text_to_clipboard(plain, html, self.root)
            self.log(("Copied the text for " + name +
                      "  -  Ctrl+V into Teams, Outlook or Word, the name stays "
                      "bold.") if ok else ("Copy FAILED for " + name))

        def do_copy_image():
            ok = copy_image_to_clipboard(info["file"])
            if ok:
                self.log("Copied the image for " + name +
                         "  -  press Ctrl+V in Teams, your upload box, Word or "
                         "Canva. If an upload box only takes a drop, use Show "
                         "in Folder and drag it from there.")
            else:
                # never leave her stuck: open the folder so she can drag/browse
                reveal_in_explorer(info["file"])
                self.log("Couldn't reach the clipboard, so I opened the folder "
                         "with the file selected. Drag it onto the upload box, "
                         "or use its Browse button.")

        def do_reveal():
            if not reveal_in_explorer(info["file"]):
                messagebox.showerror(
                    "Show in Folder", "Couldn't open the folder for this file.")

        pron_var.trace_add("write", on_pron)

        btns = ttk.Frame(frame)
        btns.grid(row=3, column=1, sticky="w", pady=2)
        ttk.Button(btns, text="Copy Text", command=do_copy).pack(side="left", padx=2)
        ttk.Button(btns, text="Copy Image", command=do_copy_image).pack(side="left", padx=2)
        ttk.Button(btns, text="Show in Folder", command=do_reveal).pack(side="left", padx=2)
        ttk.Checkbutton(btns, text="Uploaded", variable=up_var,
                        command=on_upload).pack(side="left", padx=10)

        preview = tk.Text(frame, height=8, width=72, font=("Segoe UI", 9),
                          wrap="word")
        preview.insert("1.0", announcement_texts(
            tmpl, name, client, pron_var.get())[0])
        preview.configure(state="disabled")
        preview.grid(row=0, column=2, rowspan=4, padx=(16, 0), sticky="e")

        frame.columnconfigure(1, weight=1)


def main():
    root = tk.Tk()
    RelayPostStudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
