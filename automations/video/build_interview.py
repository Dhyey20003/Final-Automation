#!/usr/bin/env python3
"""
Relay Human Cloud - Interview Video Generator  (v2, high quality)
=================================================================

Builds a finished candidate interview video from separate raw clips.

Sequence:
    open1 -> open2 -> open3   (hard cuts, darkened, name pill)
    then, with smooth crossfade dissolves between every piece:
    card1 -> answer1 (captions) -> card2 -> answer2 -> ... -> card8 -> answer8

WHAT CHANGED IN v2
------------------
* No transcription. You provide caption text files yourself.
* Captions are plain text: one caption line per line in the file. Each
  answer clip's speaking time is split evenly across however many lines
  you wrote.
* B-rolls are darkened with a proper 40% black overlay (no more blowout).
* The 3 opening b-rolls are HARD CUT (no transition between them).
* Everything after is joined with a smooth crossfade dissolve (default 0.6s).
* High quality: the pieces are assembled with a single filtergraph so the
  video is encoded ONCE, not re-encoded per join. Fixes quality loss /
  tiny file size.
* The candidate NAME shown in the opening pill is read from the answer
  filenames, e.g.  q1_Vector S.mp4  ->  pill shows "Vector S."

FILE NAMING
-----------
opening folder : open1.mp4, open2.mp4, open3.mp4
cards folder   : card1.mp4 ... card8.mp4
answers folder : q1_<Name>.mp4 ... q8_<Name>.mp4     e.g. q1_Vector S.mp4
captions folder: q1.txt ... q8.txt   (plain text, one caption per line)

The text after "q#_" in the answer filenames becomes the pill name.
All 8 answers are the same person, so the name is taken from q1.

RUN
---
    python build_interview.py     (or double-click run_interview.bat)
"""

import os
import sys
import re
import json
import shutil
import subprocess
import tempfile

# On Windows, stop every ffmpeg/ffprobe child from opening a console window
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


HERE = os.path.dirname(os.path.abspath(__file__))
# The hub passes the active config (kept in the data folder) through RELAY_VIDEO_CONFIG.
# Running the script by hand still uses the config.json next to it.
CONFIG_PATH = os.environ.get("RELAY_VIDEO_CONFIG") or os.path.join(HERE, "config.json")

if not os.path.exists(CONFIG_PATH):
    sys.exit("config.json not found (%s). Aborting." % CONFIG_PATH)

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

# ---- optional command-line overrides (used by Interview Studio GUI / batch) ----
# Running with no arguments behaves exactly as before.
import argparse
_ap = argparse.ArgumentParser(add_help=False)
_ap.add_argument("--answers", default=None)   # override answers folder
_ap.add_argument("--captions", default=None)  # override captions folder
_ap.add_argument("--opening", default=None)   # override opening b-rolls folder
_ap.add_argument("--name", default=None)      # override candidate name
_ap.add_argument("--no-prompt", action="store_true")  # skip y/n + archiving
_ARGS, _ = _ap.parse_known_args()

if _ARGS.answers:
    CFG["paths"]["answers_folder"] = _ARGS.answers
if _ARGS.captions:
    CFG["paths"]["captions_folder"] = _ARGS.captions
if _ARGS.opening:
    CFG["paths"]["opening_folder"] = _ARGS.opening
if _ARGS.name:
    CFG["candidate_name"] = _ARGS.name
NO_PROMPT = _ARGS.no_prompt

P          = CFG["paths"]
V          = CFG["video"]
W, H, FPS  = V["width"], V["height"], V["fps"]
XFADE      = float(CFG["transitions"]["crossfade_seconds"])
LT         = CFG["lower_third"]
CAP        = CFG["captions"]
AUD        = CFG["audio"]
NAME_CFG   = CFG.get("candidate_name", "").strip()


def _resolve(path):
    """Expand ${DATA} (hub video data folder), ${ASSETS} (bundled cards and fonts) and ${HERE}.
    Relative paths are resolved next to the config file, so the folder can move."""
    if not path:
        return path
    path = (str(path).replace("${DATA}", os.environ.get("RELAY_VIDEO_DATA") or os.path.join(HERE, "data"))
            .replace("${ASSETS}", os.environ.get("RELAY_VIDEO_ASSETS") or os.path.join(HERE, "assets"))
            .replace("${HERE}", HERE))
    if os.path.isabs(path) or (len(path) > 1 and path[1] == ":"):
        return path
    return os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), path)

for _k in list(P.keys()):
    P[_k] = _resolve(P[_k])

WORK = tempfile.mkdtemp(prefix="rhc_interview_")

def log(msg):
    print(msg, flush=True)

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        log("\n--- COMMAND FAILED ---")
        log(" ".join(str(c) for c in cmd))
        log(r.stderr[-2500:])
        raise RuntimeError("ffmpeg step failed (see above).")
    return r

def ffprobe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    return float(r.stdout.strip())


# ---- fonts staged to no-space path (Windows fontconfig fix) ----
def _stage_font(src_path, name):
    dst = os.path.join(WORK, name)
    try:
        shutil.copyfile(src_path, dst)
    except Exception as e:
        raise RuntimeError("Could not read font %s (%s)" % (src_path, e))
    return dst

FONT_CAP  = _stage_font(P["font_regular"], "cap_font.ttf")
FONT_PILL = _stage_font(P["font_pill"],    "pill_font.ttf")
os.environ.setdefault("FONTCONFIG_FILE", "")
os.environ.setdefault("FONTCONFIG_PATH", WORK)

def ffesc(path):
    return path.replace("\\", "/").replace(":", "\\:")

def hex_to_ffmpeg(color, opacity=1.0):
    c = color.lstrip("#")
    return ("0x" + c) if opacity >= 1.0 else ("0x%s@%.3f" % (c, opacity))


VID_EXT = (".mp4", ".mov", ".m4v")

def find_openings(folder):
    if not os.path.isdir(folder):
        return []
    out = []
    for fn in sorted(os.listdir(folder)):
        base, ext = os.path.splitext(fn)
        if ext.lower() in VID_EXT and base.lower().startswith("open"):
            num = base.lower()[4:]
            if num.isdigit():
                out.append((int(num), os.path.join(folder, fn)))
    out.sort(key=lambda x: x[0])
    return [p for _, p in out]

def find_cards(folder):
    if not os.path.isdir(folder):
        return []
    out = []
    for fn in sorted(os.listdir(folder)):
        base, ext = os.path.splitext(fn)
        if ext.lower() in VID_EXT and base.lower().startswith("card"):
            num = base.lower()[4:]
            if num.isdigit():
                out.append((int(num), os.path.join(folder, fn)))
    out.sort(key=lambda x: x[0])
    return [p for _, p in out]

def find_answers(folder):
    """Match q<number>, q<number>_<name>, or plain <number> filenames.
    Return (idx, path, name_or_None) sorted by idx."""
    if not os.path.isdir(folder):
        return []
    pat_q = re.compile(r"^q(\d+)(?:_(.+))?$", re.IGNORECASE)
    pat_n = re.compile(r"^(\d+)$")
    out = []
    for fn in sorted(os.listdir(folder)):
        base, ext = os.path.splitext(fn)
        if ext.lower() not in VID_EXT:
            continue
        m = pat_q.match(base)
        if m:
            out.append((int(m.group(1)), os.path.join(folder, fn),
                        m.group(2).strip() if m.group(2) else None))
            continue
        m = pat_n.match(base)
        if m:
            out.append((int(m.group(1)), os.path.join(folder, fn), None))
    out.sort(key=lambda x: x[0])
    return out


def load_caption_lines(cap_path):
    if not os.path.exists(cap_path):
        return []
    with open(cap_path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f.read().splitlines() if ln.strip()]

def fit_lines_to_bar(lines, font_size, max_text_w):
    """
    Ensure no caption exceeds the bar. Any line wider than max_text_w is
    split at word boundaries into pieces that fit.
    """
    fitted = []
    for line in lines:
        if text_width_px(line, font_size) <= max_text_w:
            fitted.append(line)
            continue
        words = line.split()
        cur = ""
        for wd in words:
            trial = (cur + " " + wd).strip()
            if text_width_px(trial, font_size) <= max_text_w or not cur:
                cur = trial
            else:
                fitted.append(cur)
                cur = wd
        if cur:
            fitted.append(cur)
    return fitted


def split_proportional(lines, duration):
    """
    Give each caption screen time proportional to its character count,
    approximating when the words are actually spoken. Longer sentences
    stay up longer; short ones flip quickly. Sums exactly to duration.
    """
    if not lines:
        return []
    weights = [max(len(t), 1) for t in lines]
    total = float(sum(weights))
    blocks, tcur = [], 0.0
    for t, wgt in zip(lines, weights):
        seg = duration * (wgt / total)
        blocks.append({"text": t, "start": round(tcur, 3),
                       "end": round(tcur + seg, 3)})
        tcur += seg
    blocks[-1]["end"] = round(duration, 3)
    return blocks


def text_width_px(text, font_size):
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(FONT_CAP, font_size)
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return int(len(text) * font_size * 0.55)


# ---- high-quality encode settings ----
INTER_ARGS = ["-c:v", "libx264", "-preset", "slow", "-crf", "14",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
              "-ar", "48000", "-ac", "2"]

BASE_VF = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
           "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d,format=yuv420p"
           % (W, H, W, H, FPS))


def make_opening_darkened(src, dst):
    """Just darken a b-roll with a light overlay (keeps color), silent audio."""
    dark = "drawbox=x=0:y=0:w=iw:h=ih:color=%s:t=fill" % \
           hex_to_ffmpeg("#000000", LT.get("darken_opacity", 0.18))
    vf = BASE_VF + "," + dark
    run(["ffmpeg", "-y", "-i", src,
         "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-map", "0:v", "-map", "1:a", "-shortest",
         "-vf", vf] + INTER_ARGS + [dst])


def make_pill_png(name, out_png):
    """
    Generate the rounded name pill as a transparent PNG with Pillow:
    bigger cream box, rounded corners, name centered, Plus Jakarta Sans Bold.
    """
    from PIL import Image, ImageDraw, ImageFont
    fs = LT["font_size"]
    font = ImageFont.truetype(FONT_PILL, fs)
    try:
        font.set_variation_by_name("Bold")   # variable font -> Bold weight
    except Exception:
        pass
    bbox = font.getbbox(name)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    pad_x  = LT.get("pill_side_padding", 120)
    pill_h = LT.get("pill_height", 150)
    radius = LT.get("pill_corner_radius", 40)
    pill_w = max(tw + pad_x * 2, LT.get("pill_min_width", 520))

    # colors
    pc = LT["pill_color"].lstrip("#")
    pr, pg, pb = int(pc[0:2], 16), int(pc[2:4], 16), int(pc[4:6], 16)
    pa = int(LT["pill_opacity"] * 255)
    tc = LT["text_color"].lstrip("#")
    tr, tg, tb = int(tc[0:2], 16), int(tc[2:4], 16), int(tc[4:6], 16)

    img = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, pill_w - 1, pill_h - 1], radius=radius,
                        fill=(pr, pg, pb, pa))
    tx = (pill_w - tw) // 2 - bbox[0]
    ty = (pill_h - th) // 2 - bbox[1]
    d.text((tx, ty), name, font=font, fill=(tr, tg, tb, 255))
    img.save(out_png)
    return pill_w, pill_h


def add_pill_to_block(src, dst, name):
    """
    Overlay the rounded pill PNG on the whole opening block. Slides in once
    from the left at the start, then stays put.
    """
    pill_png = os.path.join(WORK, "pill.png")
    pill_w, pill_h = make_pill_png(name, pill_png)

    slide  = LT["slide_in_seconds"]
    rest_x = LT.get("pill_rest_x", -30)      # left edge slightly clipped
    pill_y = H - LT.get("pill_bottom_offset", 300)

    # x: starts fully off-screen left, eases to rest_x over `slide` sec, holds
    x_expr = "min(%d\\,-w+((%d+w)*t/%.3f))" % (rest_x, rest_x, slide)

    run(["ffmpeg", "-y", "-i", src, "-i", pill_png,
         "-filter_complex",
         "[0:v][1:v]overlay=x='%s':y=%d" % (x_expr, pill_y),
         "-c:v", "libx264", "-preset", "slow", "-crf", "14",
         "-pix_fmt", "yuv420p", "-c:a", "copy", dst])


def make_card(src, dst):
    run(["ffmpeg", "-y", "-i", src,
         "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-map", "0:v", "-map", "1:a", "-shortest",
         "-vf", BASE_VF] + INTER_ARGS + [dst])


def make_bar_png(out_png):
    """Rounded translucent black caption bar as a transparent PNG."""
    from PIL import Image, ImageDraw
    bar_w = CAP.get("bar_width_px", 1200)
    bar_h = CAP.get("bar_height_px", 64)
    radius = CAP.get("bar_corner_radius", 14)
    alpha = int(CAP["box_opacity"] * 255)
    c = CAP["box_color"].lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    img = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, bar_w - 1, bar_h - 1], radius=radius,
                        fill=(r, g, b, alpha))
    img.save(out_png)
    return bar_w, bar_h


def make_answer(src, dst, blocks):
    af_parts = []
    if AUD.get("fix_right_to_both", True):
        af_parts.append("pan=stereo|c0=c1|c1=c1")
    af_parts.append("aresample=48000")
    af = ",".join(af_parts)

    fs = CAP["font_size"]
    txt_col = hex_to_ffmpeg(CAP["text_color"], 1.0)
    bottom  = CAP["bottom_margin_px"]
    fontfile = ffesc(FONT_CAP)

    if not blocks:
        run(["ffmpeg", "-y", "-i", src, "-vf", BASE_VF, "-af", af]
            + INTER_ARGS + [dst])
        return

    bar_png = os.path.join(WORK, "bar.png")
    bar_w, bar_h = make_bar_png(bar_png)
    bar_x = (W - bar_w) // 2
    bar_y = H - bottom - bar_h

    # rounded bar visible for the whole caption span (blocks are contiguous)
    bar_en = "between(t,%.3f,%.3f)" % (blocks[0]["start"], blocks[-1]["end"])

    text_filters = []
    for b in blocks:
        text = b["text"].replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
        en = "between(t,%.3f,%.3f)" % (b["start"], b["end"])
        text_filters.append(
            "drawtext=fontfile='%s':text='%s':expansion=none:fontsize=%d:fontcolor=%s:"
            "x=(w-text_w)/2:y=%d+(%d-th)/2:enable='%s'"
            % (fontfile, text, fs, txt_col, bar_y, bar_h, en))
    text_chain = ",".join(text_filters)

    fc = ("[0:v]%s[base];[base][1:v]overlay=x=%d:y=%d:enable='%s'[withbar];"
          "[withbar]%s[v]" % (BASE_VF, bar_x, bar_y, bar_en, text_chain))

    run(["ffmpeg", "-y", "-i", src, "-i", bar_png,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
         "-af", af] + INTER_ARGS + [dst])


def hardcut_concat(clips, dst):
    listfile = os.path.join(WORK, "concat_list.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for c in clips:
            f.write("file '%s'\n" % c.replace("\\", "/"))
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
         "-c", "copy", dst], capture_output=True, text=True, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile]
            + INTER_ARGS + [dst])


def crossfade_all(segments, dst, xfade):
    n = len(segments)
    inputs = []
    for s in segments:
        inputs += ["-i", s]
    durs = [ffprobe_duration(s) for s in segments]

    fc = []
    prev = "0:v"
    cum = durs[0]
    for i in range(1, n):
        offset = cum - xfade
        out = "v%d" % i
        fc.append("[%s][%d:v]xfade=transition=fade:duration=%.3f:offset=%.3f[%s]"
                  % (prev, i, xfade, offset, out))
        prev = out
        cum = cum + durs[i] - xfade
    vlabel = prev

    prevA = "0:a"
    for i in range(1, n):
        out = "a%d" % i
        fc.append("[%s][%d:a]acrossfade=d=%.3f[%s]" % (prevA, i, xfade, out))
        prevA = out
    alabel = prevA

    filter_complex = ";".join(fc)
    final_args = ["-c:v", "libx264", "-preset", "slow", "-crf", "15",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
                  "-ar", "48000", "-movflags", "+faststart"]
    cmd = ["ffmpeg", "-y"] + inputs + \
          ["-filter_complex", filter_complex,
           "-map", "[%s]" % vlabel, "-map", "[%s]" % alabel] + \
          final_args + [dst]
    run(cmd)


def normalize_loudness(src, dst):
    tp   = AUD.get("true_peak_max_dbtp", -1.5)
    lufs = AUD.get("target_lufs", -16)
    run(["ffmpeg", "-y", "-i", src,
         "-af", "loudnorm=I=%s:TP=%s:LRA=11" % (lufs, tp),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
         "-movflags", "+faststart", dst])


def main():
    log("Working dir: %s" % WORK)
    for key in ("output_folder", "captions_folder"):
        os.makedirs(P[key], exist_ok=True)

    openings = find_openings(P["opening_folder"])
    cards    = find_cards(P["cards_folder"])
    answers  = find_answers(P["answers_folder"])

    log("Found %d opening clips, %d cards, %d answers."
        % (len(openings), len(cards), len(answers)))

    n = min(len(cards), len(answers))
    if n == 0:
        sys.exit("Need at least one matching card#/q# pair. Nothing to build.")
    extra_cards = cards[n:]   # e.g. card9 = closing white slide, fades in at the end

    name = NAME_CFG or (answers[0][2] if answers and answers[0][2] else "")
    if not name:
        name = "Candidate"
        log("WARNING: no name in filenames or config; using 'Candidate'.")
    log("Candidate name for pill: %s" % name)

    opening_block = None
    if openings:
        # 1) darken each b-roll (light overlay, keeps color)
        dark_pieces = []
        for i, src in enumerate(openings, 1):
            dst = os.path.join(WORK, "open%d_dark.mp4" % i)
            make_opening_darkened(src, dst)
            dark_pieces.append(dst)
            log("  opening %d darkened" % i)
        # 2) hard-cut them into one block (no transition between the 3)
        block_raw = os.path.join(WORK, "opening_raw.mp4")
        hardcut_concat(dark_pieces, block_raw)
        # 3) add the name pill ONCE across the whole block (slides in at start)
        opening_block = os.path.join(WORK, "opening_block.mp4")
        add_pill_to_block(block_raw, opening_block, name)
        log("  opening block ready (pill animates once)")

    segments = []
    if opening_block:
        segments.append(opening_block)

    for k in range(n):
        idx, ans_path, _ = answers[k]
        card_path = cards[k]
        log("Processing pair %d ..." % (k + 1))

        card_dst = os.path.join(WORK, "card%d.mp4" % (k + 1))
        make_card(card_path, card_dst)
        segments.append(card_dst)

        dur = ffprobe_duration(ans_path)
        cap_file = os.path.join(P["captions_folder"], "q%d.txt" % idx)
        lines = load_caption_lines(cap_file)
        if not lines:
            log("  note: no caption file q%d.txt (or empty) - answer %d has no captions"
                % (idx, k + 1))
        # keep every caption inside the bar; split overflowing lines
        max_text_w = CAP.get("bar_width_px", 1200) - 2 * CAP.get("bar_text_padding", 40)
        fitted = fit_lines_to_bar(lines, CAP["font_size"], max_text_w)
        if len(fitted) != len(lines):
            log("  q%d: %d caption line(s) were too wide and were auto-split to fit the bar"
                % (idx, len(fitted) - len(lines)))
        # time each caption proportional to its length (approximates speech)
        blocks = split_proportional(fitted, dur)

        ans_dst = os.path.join(WORK, "q%d.mp4" % (k + 1))
        make_answer(ans_path, ans_dst, blocks)
        segments.append(ans_dst)

    # closing slide(s): any card beyond the last answer (e.g. card9) is
    # appended at the end and fades in with the same crossfade
    for j, extra in enumerate(extra_cards, 1):
        dst = os.path.join(WORK, "ending%d.mp4" % j)
        make_card(extra, dst)
        segments.append(dst)
        log("  closing slide %d added (fades in at the end)" % j)

    log("Assembling %d segments with %.2fs crossfade dissolves ..." % (len(segments), XFADE))
    combined = os.path.join(WORK, "combined.mp4")
    crossfade_all(segments, combined, XFADE)

    final_name = CFG["output_filename"].replace(
        "{name}", name.replace(" ", "_").replace(".", ""))
    final_path = os.path.join(P["output_folder"], final_name)
    normalize_loudness(combined, final_path)

    log("\nSaving into: %s" % final_path)
    log("DONE.")

    try:
        shutil.rmtree(WORK)
    except Exception:
        pass

    # ---------------------------------------------------------------- #
    #  Post-build check: archive inputs if the output is approved
    # ---------------------------------------------------------------- #
    if NO_PROMPT:
        return   # batch/GUI mode: no prompt, no archiving here

    completed_root = P.get("completed_folder") or \
        os.path.join(os.path.dirname(P["answers_folder"]), "completed")

    print("")
    print("Open and check the output video now:")
    print("  %s" % final_path)
    ans = ""
    while ans not in ("y", "n"):
        ans = input("Is the output video perfect? (y/n): ").strip().lower()

    if ans == "n":
        log("No changes made. Fix what you need and run again.")
        return

    # y -> archive answer clips into completed\<Name>\ and blank the captions
    safe_name = re.sub(r'[<>:"/\\|?*]', "", name).strip().rstrip(". ") or "unnamed"
    dest = os.path.join(completed_root, safe_name)
    k = 2
    while os.path.exists(dest):          # never overwrite a previous person
        dest = os.path.join(completed_root, "%s_%d" % (safe_name, k))
        k += 1
    os.makedirs(dest, exist_ok=True)

    moved = 0
    for idx, ans_path, _nm in answers:
        try:
            shutil.move(ans_path, os.path.join(dest, os.path.basename(ans_path)))
            moved += 1
        except Exception as e:
            log("  could not move %s (%s)" % (os.path.basename(ans_path), e))
    log("Moved %d answer clip(s) to: %s" % (moved, dest))

    blanked = 0
    for i in range(1, len(answers) + 1):
        cap = os.path.join(P["captions_folder"], "q%d.txt" % i)
        if os.path.exists(cap):
            open(cap, "w", encoding="utf-8").close()
            blanked += 1
    log("Blanked %d caption file(s) - ready for the next person's text." % blanked)
    log("Answers and captions folders are ready for new data.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("\nERROR: %s" % e)
        sys.exit(1)
