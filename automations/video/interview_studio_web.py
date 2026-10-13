#!/usr/bin/env python3
r"""
Relay Human Cloud - Interview Studio (Browser version, v2 with trimming)
========================================================================

Same job as interview_studio.py, with a browser UI instead of the Tkinter
window, plus a per-clip trim window so you never have to open DaVinci just
to shave the first and last second off a take.

NOTHING about the video pipeline changes. This file only:
  1. takes the candidate name, intro b-rolls, answer clips and the caption
     paragraph from a web page,
  2. lets you trim each clip (in and out points only, no other editing),
     with a waveform and detected silences so you can see where speech
     starts and stops,
  3. writes the trimmed clips into batch\<name>\{opening,answers,captions}
     exactly the way the old GUI staged them,
  4. runs the SAME command:
        python build_interview.py --answers ... --captions ... --opening ...
                                  --name "..." --no-prompt

build_interview.py, config.json, cards, fonts, output folder: untouched.

CAPTIONS
--------
Clips are transcribed automatically. Two engines are supported:

  local   faster-whisper running on this machine, no internet after the
          first model download, no cost. Install faster-whisper once (see START_HERE.txt).
  openai  OpenAI speech to text, needs an API key and internet.

The transcript is only a starting point. Every answer clip has an editable
caption box in its trim window, so names and anything else can be fixed by
hand before the build. Words typed in the vocabulary field are fed to the
model as a spelling hint, which is the reliable way to get names right.

TRIM AND CAPTION SETTINGS (optional)
------------------------------------
Add these blocks to config.json to change the defaults. The builder ignores
unknown keys, so they are safe to leave out entirely.

    "trim": {
      "mode": "precise",
      "crf": 14,
      "preset": "veryfast",
      "silence_min_seconds": 0.35,
      "threshold_db": "auto",
      "min_db_above_floor": 8,
      "sustain_ms": 60
    },
    "transcribe": {
      "engine": "auto",
      "model": "medium",
      "language": "en",
      "device": "auto",
      "compute_type": "int8",
      "openai_model": "whisper-1",
      "openai_api_key": "",
      "pre_roll_seconds": 0.6,
      "post_roll_seconds": 0.6,
      "max_line_chars": 62,
      "vocabulary": ""
    }

model can be small, medium, large-v3 or distil-large-v3. large-v3 is the
most accurate and the slowest on a CPU. pre_roll_seconds is the breathing
room left in front of the first spike when an answer clip is auto trimmed.

AUDIO CLEAN UP
--------------
After a build finishes, the audio of the finished video is denoised and
written back with the video stream copied, never re-encoded, so the picture
is bit for bit what the builder produced. Controlled by the "denoise" block
in config.json:

    "denoise": {
      "enabled": true,
      "keep_original": true,
      "engine": "auto",
      "clean_suffix": "_clean",
      "mix": 0.9,
      "renormalize": true,
      "ffmpeg_nr_db": 12
    }

keep_original true  -> you get both files, Name_final.mp4 and
                       Name_final_clean.mp4
keep_original false -> only the cleaned file, under the normal name

engine auto tries DeepFilterNet, then ClearVoice, then ffmpeg's own filter,
and uses whichever is available. DeepFilterNet is looked for twice: first as
the precompiled deep-filter binary on PATH, then as the python package. The
binary is the easy route on Windows, since the pip package needs a Rust
toolchain on newer Python versions. The ffmpeg one always works, so the clean
up never fails outright. mix 0.9 blends 90 percent cleaned with 10 percent
original, which keeps the voice sounding natural instead of processed. Set
it to 1.0 for a full clean. renormalize puts the loudness back on the
target from the "audio" block.

To clean a video you already built:

    python interview_studio_web.py --denoise "C:\path\to\video.mp4"

SPEECH DETECTION
----------------
Trim points come from the waveform itself, not from the transcript.

The audio is decoded once and measured in 20 ms frames. The threshold
adapts to each clip: it sits between that clip's own room tone and its loud
parts, so a quiet room and a noisy one both work.

The start of speech is the first burst that either runs for
min_speech_seconds on its own, or is short but followed within
max_gap_seconds by more talking. A cough, a chair creak or a mic bump is
short AND sits alone in the quiet, so it fails both tests and is ignored. The clip is then
cut pre_roll_seconds ahead of that burst. The tail works the same way in
reverse.

Only the start and the end are ever touched. Pauses between sentences are
left exactly as recorded.

This applies to the 8 answer clips only. Intro b-rolls are never auto
trimmed and never transcribed.

mode "precise" is a frame accurate cut (re-encode). mode "fast" is a stream
copy, instant but it snaps to the nearest keyframe. A clip left at full
length is copied, never re-encoded, so untrimmed clips lose nothing.

RUN
---
    python interview_studio_web.py      (or double-click run_studio_web.bat)

Pure standard library. Needs python and ffmpeg, which you already have.
"""

import os
import re
import sys
import json
import shutil
import uuid
import queue
import threading
import subprocess
import webbrowser
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.join(HERE, "build_interview.py")
# All writable data lives in the hub's data folder (passed as RELAY_VIDEO_DATA). Running the
# file by hand without the hub keeps everything in a "data" folder next to it.
DATA_ROOT = os.path.abspath(os.environ.get("RELAY_VIDEO_DATA") or os.path.join(HERE, "data"))
ASSETS_ROOT = os.path.abspath(os.environ.get("RELAY_VIDEO_ASSETS") or os.path.join(HERE, "assets"))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
CONFIG_PATH = os.path.join(DATA_ROOT, "config.json")
BATCH_ROOT = os.path.join(DATA_ROOT, "batch")
RAW_ROOT = os.path.join(BATCH_ROOT, "_raw")
JOBS_FILE = os.path.join(DATA_ROOT, "jobs.json")
os.environ["RELAY_VIDEO_DATA"] = DATA_ROOT
os.environ["RELAY_VIDEO_ASSETS"] = ASSETS_ROOT
os.environ["RELAY_VIDEO_CONFIG"] = CONFIG_PATH

VERSION = "6.0  (confirm and make, persistent queue)"
NUM_QUESTIONS = 8
PORT = int(os.environ.get("RELAY_VIDEO_PORT") or 8756)
VID_EXT = (".mp4", ".mov", ".m4v")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
# How the builder is started. The hub overrides this in the packaged build.
BUILDER_CMD = [sys.executable, BUILDER]

LOCK = threading.Lock()
JOBS = []           # persistent queue (see jobs.json)
RAW = {}            # clip_id -> clip dict
LOG = []
WORKER = {"busy": False, "job": None, "stage": ""}
WAKE = threading.Event()
_ids = [1]

JOB_STATES = ("preparing", "queued", "rendering", "cleaning_audio", "completed", "failed", "interrupted")

TJOBS = queue.Queue()       # clips waiting to be transcribed
_WHISPER = {"model": None, "name": None}
ENGINE = {"name": "off", "note": ""}


# --------------------------------------------------------------- helpers
def _expand(path):
    if not path:
        return path
    path = (str(path).replace("${DATA}", DATA_ROOT).replace("${ASSETS}", ASSETS_ROOT).replace("${HERE}", HERE))
    if os.path.isabs(path) or (len(path) > 1 and path[1] == ":"):
        return path
    return os.path.join(DATA_ROOT, path)


def ensure_config():
    """The active config lives in the data folder; it is created from the bundled default once."""
    os.makedirs(DATA_ROOT, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        shutil.copyfile(DEFAULT_CONFIG, CONFIG_PATH)
    try:
        cfg = load_config()
    except Exception:
        return
    for key in ("output_folder", "completed_folder", "opening_folder", "answers_folder", "captions_folder"):
        try:
            os.makedirs(cfg["paths"][key], exist_ok=True)
        except Exception:
            pass


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    paths = cfg.get("paths", {}) or {}
    cfg["paths"] = {k: (_expand(v) if not str(k).startswith("_") else v) for k, v in paths.items()}
    return cfg


def now_iso():
    return _dt.datetime.now().replace(microsecond=0).isoformat()


# --------------------------------------------------------------- persistent jobs
def save_jobs():
    with LOCK:
        data = json.dumps({"jobs": JOBS, "saved_at": now_iso()}, indent=2, ensure_ascii=False)
    os.makedirs(DATA_ROOT, exist_ok=True)
    tmp = JOBS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, JOBS_FILE)


def load_jobs():
    """Read the queue back and mark interrupted work clearly instead of losing it."""
    global JOBS
    if not os.path.exists(JOBS_FILE):
        JOBS = []
        return
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            JOBS = json.load(f).get("jobs", [])
    except Exception as e:
        log_write("jobs.json could not be read (%s); starting with an empty queue. The file was kept as jobs.json.bad." % e)
        try:
            shutil.copyfile(JOBS_FILE, JOBS_FILE + ".bad")
        except Exception:
            pass
        JOBS = []
        return
    for j in JOBS:
        st = j.get("status")
        if st in ("rendering", "cleaning_audio"):
            j["status"] = "interrupted"
            j["error"] = ("The studio stopped while this video was %s. Use Retry to continue from that step."
                          % ("rendering" if st == "rendering" else "having its audio cleaned"))
            j["resume_stage"] = "render" if st == "rendering" else "clean"
        elif st == "preparing":
            j["status"] = "failed"
            j["error"] = "The studio stopped while the clips were being cut. Add this candidate again."
        j["updated_at"] = now_iso()
    save_jobs()


def job_public(j):
    keys = ("id", "name", "status", "stage", "error", "n_open", "n_ans", "n_cap", "created_at", "updated_at",
            "started_at", "finished_at", "output", "clean_output", "engine", "attempts", "cleanup_error", "resume_stage")
    d = {k: j.get(k) for k in keys}
    d["stage_dir"] = j.get("stage_dir")
    return d


def find_job(jid):
    with LOCK:
        for j in JOBS:
            if str(j.get("id")) == str(jid):
                return j
    return None


def set_job(j, **kw):
    with LOCK:
        j.update(kw)
        j["updated_at"] = now_iso()
    save_jobs()


def trim_cfg():
    try:
        t = load_config().get("trim", {}) or {}
    except Exception:
        t = {}
    return {
        "mode": str(t.get("mode", "precise")).lower(),
        "crf": int(t.get("crf", 14)),
        "preset": str(t.get("preset", "veryfast")),
        "silence_min_seconds": float(t.get("silence_min_seconds", 0.35)),
        "threshold_db": t.get("threshold_db", "auto"),
        "min_db_above_floor": float(t.get("min_db_above_floor", 8)),
        "sustain_ms": float(t.get("sustain_ms", 120)),
        "min_speech_seconds": float(t.get("min_speech_seconds", 0.5)),
        "speech_window_seconds": float(t.get("speech_window_seconds", 1.5)),
        "max_gap_seconds": float(t.get("max_gap_seconds", 0.6)),
    }


def transcribe_cfg():
    try:
        t = load_config().get("transcribe", {}) or {}
    except Exception:
        t = {}
    return {
        "engine": str(t.get("engine", "auto")).lower(),
        "model": str(t.get("model", "medium")),
        "language": str(t.get("language", "en")),
        "device": str(t.get("device", "auto")),
        "compute_type": str(t.get("compute_type", "int8")),
        "openai_model": str(t.get("openai_model", "whisper-1")),
        "openai_api_key": str(t.get("openai_api_key", "")
                               or os.environ.get("OPENAI_API_KEY", "")),
        "pre_roll_seconds": float(t.get("pre_roll_seconds", 0.8)),
        "post_roll_seconds": float(t.get("post_roll_seconds", 0.8)),
        "max_line_chars": int(t.get("max_line_chars", 62)),
        "vocabulary": str(t.get("vocabulary", "")),
    }


def safe_folder_name(name):
    s = re.sub(r'[<>:"/\\|?*]', "", name).strip().rstrip(". ")
    return s or "unnamed"


def split_captions(paragraph):
    """Identical to the Tkinter studio: split at . ? ! and keep punctuation."""
    text = " ".join(paragraph.split())
    if not text:
        return []
    parts = re.findall(r"[^.?!]+[.?!]?", text)
    return [p.strip() for p in parts if p.strip()]


def log_write(msg):
    with LOCK:
        lines = str(msg).splitlines()
        LOG.extend(lines if lines else [""])


def new_id():
    with LOCK:
        i = _ids[0]
        _ids[0] += 1
    return i


def new_job_id():
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def clean_ext(ext):
    ext = ("." + ext.lstrip(".")).lower()
    return ext if ext in VID_EXT else ".mp4"


def make_stage(name):
    stage = os.path.join(BATCH_ROOT, safe_folder_name(name))
    k = 2
    while os.path.exists(stage):
        stage = os.path.join(BATCH_ROOT, "%s_%d" % (safe_folder_name(name), k))
        k += 1
    ans_dir = os.path.join(stage, "answers")
    cap_dir = os.path.join(stage, "captions")
    opn_dir = os.path.join(stage, "opening")
    for d in (ans_dir, cap_dir, opn_dir):
        os.makedirs(d)
    return stage, ans_dir, cap_dir, opn_dir


# --------------------------------------------------------------- ffmpeg
def probe_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return -90.0
    i = int(round((len(sorted_vals) - 1) * pct))
    return sorted_vals[max(0, min(len(sorted_vals) - 1, i))]


def audio_profile(src, cfg):
    """
    Decode the audio once and work out, from the actual waveform energy:
      peaks       a display envelope for the timeline
      sound_start the first real spike, sustained long enough to be speech
      sound_end   the last one
      silences    every quiet stretch, using the same threshold
    The threshold adapts to each clip: it sits between the room tone floor
    and the loud parts, so a quiet room and a noisy one both work.
    """
    sr = 8000
    frame = 160                      # 20 ms
    empty = {"peaks": [], "sound_start": 0.0, "sound_end": 0.0,
             "silences": [], "has_audio": False, "weak": False,
             "floor_db": -90.0, "thresh_db": -90.0, "loud_db": -90.0}
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1",
             "-ar", str(sr), "-f", "s16le", "-"],
            capture_output=True, stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
    except Exception:
        return empty
    raw = r.stdout or b""
    if len(raw) < frame * 2 * 4:
        return empty

    import array
    import math
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) - (len(raw) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()

    n_frames = len(samples) // frame
    if n_frames < 5:
        return empty

    db = [0.0] * n_frames
    amp = [0.0] * n_frames
    for i in range(n_frames):
        chunk = samples[i * frame:(i + 1) * frame]
        total = 0
        top = 0
        for v in chunk:
            total += v * v
            av = v if v >= 0 else -v
            if av > top:
                top = av
        rms = math.sqrt(total / frame) / 32768.0
        amp[i] = top / 32768.0
        db[i] = 20.0 * math.log10(rms + 1e-7)

    ordered = sorted(db)
    floor_db = _percentile(ordered, 0.20)
    loud_db = _percentile(ordered, 0.95)
    above = float(cfg.get("min_db_above_floor", 8.0))
    thresh = floor_db + max(above, 0.30 * (loud_db - floor_db))
    thresh = min(thresh, loud_db - 6.0)
    fixed = cfg.get("threshold_db", "auto")
    if isinstance(fixed, (int, float)):
        thresh = float(fixed)
    if loud_db - floor_db < 12.0:
        # not enough contrast between room tone and speech to be sure where
        # the talking starts. Say so instead of guessing, and keep the whole
        # clip so nothing gets cut off by a bad guess.
        dur = round(n_frames * 0.02, 3)
        buckets = 1400
        step = max(1, n_frames / float(buckets))
        pk, i = [], 0.0
        while i < n_frames:
            a, b = int(i), min(n_frames, int(i + step) + 1)
            pk.append(round(max(amp[a:b] or [0.0]), 4))
            i += step
        return {"peaks": pk, "sound_start": 0.0, "sound_end": dur,
                "silences": [], "has_audio": True, "weak": True,
                "floor_db": round(floor_db, 1), "thresh_db": round(thresh, 1),
                "loud_db": round(loud_db, 1)}

    # A real start of speech is not one blip. It is a burst that holds for a
    # moment AND is followed by more talking. A cough, a chair creak or a mic
    # bump crosses the threshold for a few frames and then stops, so it gets
    # ignored. Pauses in the middle are never touched.
    sustain = max(1, int(round(float(cfg.get("sustain_ms", 120)) / 20.0)))
    window = max(sustain, int(round(float(cfg.get("speech_window_seconds", 1.5)) / 0.02)))
    need = max(1, int(round(float(cfg.get("min_speech_seconds", 0.5)) / 0.02)))
    loud_flag = [d >= thresh for d in db]

    def runs():
        """(start, end_exclusive) of every stretch above the threshold."""
        out, a = [], None
        for i, is_loud in enumerate(loud_flag + [False]):
            if is_loud and a is None:
                a = i
            elif not is_loud and a is not None:
                out.append((a, i))
                a = None
        return out

    max_gap = max(1, int(round(float(cfg.get("max_gap_seconds", 0.6)) / 0.02)))
    all_runs = runs()

    first = last = None
    for k, (a, b) in enumerate(all_runs):
        if b - a < sustain:
            continue
        if b - a >= need:                       # long enough on its own
            first = a
            break
        nxt = all_runs[k + 1][0] if k + 1 < len(all_runs) else None
        if nxt is None or nxt - b > max_gap:
            continue                            # a lone blip in the quiet
        if sum(loud_flag[a:min(n_frames, a + window)]) >= need:
            first = a
            break
    for k in range(len(all_runs) - 1, -1, -1):
        a, b = all_runs[k]
        if b - a < sustain:
            continue
        if b - a >= need:
            last = b - 1
            break
        prev = all_runs[k - 1][1] if k > 0 else None
        if prev is None or a - prev > max_gap:
            continue
        if sum(loud_flag[max(0, b - window):b]) >= need:
            last = b - 1
            break
    if first is None or last is None or last <= first:
        long_runs = [r for r in all_runs if r[1] - r[0] >= sustain] or all_runs
        if long_runs:
            first, last = long_runs[0][0], long_runs[-1][1] - 1
        else:
            first, last = 0, n_frames - 1

    min_sil = float(cfg.get("silence_min_seconds", 0.35))
    silences, gap = [], None
    for i, is_loud in enumerate(loud_flag + [True]):
        if not is_loud and gap is None:
            gap = i
        elif is_loud and gap is not None:
            if (i - gap) * 0.02 >= min_sil:
                silences.append([round(gap * 0.02, 3), round(i * 0.02, 3)])
            gap = None

    buckets = 1400
    step = max(1, n_frames / float(buckets))
    peaks = []
    i = 0.0
    while i < n_frames:
        a, b = int(i), min(n_frames, int(i + step) + 1)
        peaks.append(round(max(amp[a:b] or [0.0]), 4))
        i += step

    return {"peaks": peaks, "weak": False,
            "sound_start": round(first * 0.02, 3),
            "sound_end": round(min(n_frames, last + 1) * 0.02, 3),
            "silences": silences, "has_audio": True,
            "floor_db": round(floor_db, 1), "thresh_db": round(thresh, 1),
            "loud_db": round(loud_db, 1)}


# ------------------------------------------------------- caption fitting
_FONT = {"key": None, "obj": None}


def caption_metrics():
    """Font size, usable bar width and font file, straight from config.json,
    matching what build_interview.py uses when it burns the captions in."""
    try:
        cfg = load_config()
    except Exception:
        return 26, 1120, ""
    cap = cfg.get("captions", {}) or {}
    fs = int(cap.get("font_size", 26))
    max_w = int(cap.get("bar_width_px", 1200)) - 2 * int(cap.get("bar_text_padding", 40))
    path = (cfg.get("paths", {}) or {}).get("font_regular", "")
    return fs, max_w, path


def text_width_px(text, fs, path):
    """Same measurement the builder makes: real Poppins metrics via Pillow."""
    try:
        from PIL import ImageFont
        key = (path, fs)
        if _FONT["key"] != key:
            _FONT["obj"] = ImageFont.truetype(path, fs)
            _FONT["key"] = key
        bbox = _FONT["obj"].getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return int(len(text) * fs * 0.55)


def fit_caption_lines(text):
    """
    Fill each caption bar as far as it will go before wrapping, exactly the
    way the builder does it, so no line breaks early with space left over.
    A short leftover last line is evened out against the one before it.
    """
    fs, max_w, path = caption_metrics()
    words = " ".join((text or "").split()).split(" ")
    words = [w for w in words if w]
    if not words:
        return []

    lines, cur = [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if text_width_px(trial, fs, path) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)

    # pull words back onto the last line so it is not left as an orphan
    for _ in range(8):
        if len(lines) < 2:
            break
        last_w = text_width_px(lines[-1], fs, path)
        if last_w >= 0.45 * max_w:
            break
        prev_words = lines[-2].split()
        if len(prev_words) < 2:
            break
        cand_prev = " ".join(prev_words[:-1])
        cand_last = prev_words[-1] + " " + lines[-1]
        if text_width_px(cand_last, fs, path) > max_w:
            break
        if text_width_px(cand_prev, fs, path) < text_width_px(cand_last, fs, path) * 0.6:
            break
        lines[-2], lines[-1] = cand_prev, cand_last
    return lines


# --------------------------------------------------------- transcription
def pick_engine():
    """Decide once, at startup, how speech to text will be done."""
    cfg = transcribe_cfg()
    want = cfg["engine"]
    if want == "off":
        return "off", "captions typed by hand"
    if want in ("auto", "local"):
        try:
            import faster_whisper  # noqa: F401
            return "local", "faster-whisper, model %s" % cfg["model"]
        except Exception:
            if want == "local":
                return "off", "faster-whisper is not installed, install faster-whisper (see START_HERE.txt)"
    if want in ("auto", "openai"):
        if cfg["openai_api_key"]:
            return "openai", "OpenAI %s" % cfg["openai_model"]
        if want == "openai":
            return "off", "no OpenAI key in config.json or OPENAI_API_KEY"
    return "off", "no speech engine available, install faster-whisper (see START_HERE.txt)"


def extract_audio(src, dst, for_api=False):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1"]
    if for_api:
        cmd += ["-ar", "16000", "-c:a", "aac", "-b:a", "64k"]
    else:
        cmd += ["-ar", "16000", "-c:a", "pcm_s16le"]
    cmd += [dst]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    return r.returncode == 0 and os.path.exists(dst)


def get_local_model(cfg):
    from faster_whisper import WhisperModel
    if _WHISPER["model"] is None or _WHISPER["name"] != cfg["model"]:
        log_write("Loading the %s speech model. The first run downloads it, "
                  "which takes a few minutes." % cfg["model"])
        try:
            m = WhisperModel(cfg["model"], device=cfg["device"],
                             compute_type=cfg["compute_type"])
        except Exception:
            m = WhisperModel(cfg["model"], device="cpu", compute_type="int8")
        _WHISPER["model"] = m
        _WHISPER["name"] = cfg["model"]
        log_write("Speech model ready.")
    return _WHISPER["model"]


def transcribe_local(src, hint, cfg):
    wav = os.path.splitext(src)[0] + ".wav"
    if not extract_audio(src, wav):
        raise RuntimeError("no audio track to transcribe")
    try:
        model = get_local_model(cfg)
        segs, _info = model.transcribe(
            wav,
            language=(cfg["language"] or None),
            initial_prompt=(hint or None),
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            word_timestamps=False)
        out = []
        for sg in segs:
            txt = (sg.text or "").strip()
            if txt:
                out.append({"start": round(float(sg.start), 3),
                            "end": round(float(sg.end), 3), "text": txt})
        return out
    finally:
        try:
            os.remove(wav)
        except Exception:
            pass


def _multipart(fields, filename, filedata):
    b = "----rhc" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                    % (b, k, v)).encode("utf-8"))
    out.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n" % (b, filename)).encode("utf-8"))
    out.append(filedata)
    out.append(("\r\n--%s--\r\n" % b).encode("utf-8"))
    return b, b"".join(out)


def transcribe_openai(src, hint, cfg):
    m4a = os.path.splitext(src)[0] + ".m4a"
    if not extract_audio(src, m4a, for_api=True):
        raise RuntimeError("no audio track to transcribe")
    try:
        if os.path.getsize(m4a) > 24 * 1024 * 1024:
            raise RuntimeError("clip audio is over the 25 MB API limit")
        fields = {"model": cfg["openai_model"], "response_format": "verbose_json"}
        if cfg["language"]:
            fields["language"] = cfg["language"]
        if hint:
            fields["prompt"] = hint
        with open(m4a, "rb") as f:
            data = f.read()
        boundary, body = _multipart(fields, os.path.basename(m4a), data)
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions", data=body,
            headers={"Authorization": "Bearer " + cfg["openai_api_key"],
                     "Content-Type": "multipart/form-data; boundary=" + boundary})
        with urllib.request.urlopen(req, timeout=600) as r:
            payload = json.loads(r.read().decode("utf-8"))
        out = []
        for sg in payload.get("segments") or []:
            txt = (sg.get("text") or "").strip()
            if txt:
                out.append({"start": round(float(sg.get("start", 0)), 3),
                            "end": round(float(sg.get("end", 0)), 3), "text": txt})
        if not out and payload.get("text"):
            out = [{"start": 0.0, "end": 0.0, "text": payload["text"].strip()}]
        return out
    finally:
        try:
            os.remove(m4a)
        except Exception:
            pass


def transcribe_worker():
    """One clip at a time, so the speech model is loaded once and reused."""
    while True:
        clip = TJOBS.get()
        if clip is None:
            return
        if clip["id"] not in RAW:
            continue
        cfg = transcribe_cfg()
        hint = (clip.get("hint") or cfg["vocabulary"] or "").strip()
        clip["t_status"] = "working"
        try:
            if ENGINE["name"] == "local":
                clip["segments"] = transcribe_local(clip["path"], hint, cfg)
            elif ENGINE["name"] == "openai":
                clip["segments"] = transcribe_openai(clip["path"], hint, cfg)
            else:
                clip["segments"] = []
                clip["t_status"] = "off"
                continue
            clip["t_status"] = "done"
            log_write("Transcribed %s: %d caption line(s)."
                      % (clip["name"], len(clip["segments"])))
        except Exception as e:
            clip["t_status"] = "error"
            clip["t_error"] = str(e)[:300]
            log_write("Could not transcribe %s: %s" % (clip["name"], e))


def analyze(clip):
    cfg = trim_cfg()
    clip["duration"] = probe_duration(clip["path"])
    prof = audio_profile(clip["path"], cfg)
    clip["peaks"] = prof["peaks"]
    clip["silences"] = prof["silences"]
    clip["has_audio"] = prof["has_audio"]
    clip["sound_start"] = prof["sound_start"]
    clip["sound_end"] = prof["sound_end"] or clip["duration"]
    clip["weak"] = bool(prof.get("weak"))
    clip["levels"] = {"floor": prof["floor_db"], "thresh": prof["thresh_db"],
                      "loud": prof["loud_db"]}
    clip["status"] = "ready"
    if clip["kind"] != "answers":
        clip["t_status"] = "skip"      # intro b-rolls are trimmed by hand only
    elif ENGINE["name"] == "off":
        clip["t_status"] = "off"
    else:
        clip["t_status"] = "queued"
        TJOBS.put(clip)


def write_trimmed(src, dst, t_in, t_out, cfg):
    """Trim src into dst. A full length clip is copied, never re-encoded."""
    dur = probe_duration(src)
    t_in = max(0.0, float(t_in or 0.0))
    t_out = float(t_out) if t_out else dur
    if dur and t_out > dur:
        t_out = dur
    if t_out - t_in < 0.05:
        t_out = (min(dur, t_in + 0.05) if dur else t_in + 0.05)

    if t_in <= 0.02 and (not dur or t_out >= dur - 0.02):
        shutil.copyfile(src, dst)
        return "kept whole clip"

    keep = t_out - t_in
    if cfg["mode"] == "fast":
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % t_in, "-i", src,
               "-t", "%.3f" % keep, "-c", "copy",
               "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", dst]
        note = "stream copy"
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % t_in, "-i", src,
               "-t", "%.3f" % keep,
               "-c:v", "libx264", "-preset", cfg["preset"], "-crf", str(cfg["crf"]),
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
               "-movflags", "+faststart", dst]
        note = "precise cut"
    r = subprocess.run(cmd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg failed")[-600:])
    return "%s %.2fs to %.2fs" % (note, t_in, t_out)


# --------------------------------------------------------- stage worker
def prepare_worker(item, plan):
    """plan = {'opening': [{id,in,out}], 'answers': [{id,in,out}]}"""
    cfg = trim_cfg()
    try:
        log_write("Cutting clips for %s ..." % item["name"])
        for kind, folder, stem in (("opening", item["opening"], "open"),
                                   ("answers", item["answers"], "q")):
            for i, part in enumerate(plan.get(kind, []), 1):
                clip = RAW.get(int(part.get("id", 0)))
                if not clip:
                    raise RuntimeError("a clip went missing before staging")
                dst = os.path.join(folder, "%s%d%s" % (stem, i, clip["ext"]))
                note = write_trimmed(clip["path"], dst, part.get("in"),
                                     part.get("out"), cfg)
                log_write("   %s%d  <-  %s  (%s)" % (stem, i, clip["name"], note))
        set_job(item, status="queued", stage="render", error=None)
        log_write("Queued: %s (it starts automatically when the worker is free)" % item["name"])
        WAKE.set()
    except Exception as e:
        set_job(item, status="failed", error="Could not prepare the clips: %s" % e)
        log_write("Could not prepare %s: %s" % (item["name"], e))
    finally:
        for kind in ("opening", "answers"):
            for part in plan.get(kind, []):
                clip = RAW.pop(int(part.get("id", 0)), None)
                if clip:
                    try:
                        os.remove(clip["path"])
                    except Exception:
                        pass


# ------------------------------------------------------------- denoising
_DF = {"checked": False, "ok": False, "why": ""}


def denoise_cfg():
    try:
        d = load_config().get("denoise", {}) or {}
    except Exception:
        d = {}
    val = d.get("enabled", True)
    if isinstance(val, str):
        val = val.strip().lower() in ("yes", "y", "true", "1", "on")
    keep = d.get("keep_original", True)
    if isinstance(keep, str):
        keep = keep.strip().lower() in ("yes", "y", "true", "1", "on")
    return {
        "enabled": bool(val),
        "keep_original": bool(keep),
        "engine": str(d.get("engine", "auto")).lower(),
        "clean_suffix": str(d.get("clean_suffix", "_clean")),
        "mix": max(0.0, min(1.0, float(d.get("mix", 0.9)))),
        "renormalize": bool(d.get("renormalize", True)),
        "ffmpeg_nr_db": float(d.get("ffmpeg_nr_db", 12)),
    }


def deepfilter_available():
    if _DF["checked"]:
        return _DF["ok"]
    _DF["checked"] = True
    try:
        r = subprocess.run([sys.executable, "-c", "import df, torchaudio"],
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        _DF["ok"] = r.returncode == 0
        _DF["why"] = "" if _DF["ok"] else "deepfilternet is not installed"
    except Exception as e:
        _DF["ok"] = False
        _DF["why"] = str(e)
    return _DF["ok"]


DF_SCRIPT = """
import sys, torch, torchaudio
from df.enhance import enhance, init_df, load_audio
model, state, _ = init_df()
audio, _ = load_audio(sys.argv[1], sr=state.sr())
out = enhance(model, state, audio)
if out.dim() == 1:
    out = out.unsqueeze(0)
torchaudio.save(sys.argv[2], out.cpu(), state.sr())
print("denoised")
"""


def clean_with_deepfilternet(wav_in, wav_out):
    helper = os.path.join(RAW_ROOT, "_df_run.py")
    os.makedirs(RAW_ROOT, exist_ok=True)
    with open(helper, "w", encoding="utf-8") as f:
        f.write(DF_SCRIPT)
    r = subprocess.run([sys.executable, helper, wav_in, wav_out],
                       capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if r.returncode != 0 or not os.path.exists(wav_out):
        raise RuntimeError((r.stderr or "DeepFilterNet failed")[-400:])
    return "DeepFilterNet"


CV_SCRIPT = """
import sys, os, glob, shutil
from clearvoice import ClearVoice
src, dst = sys.argv[1], sys.argv[2]
cv = ClearVoice(task='speech_enhancement', model_names=['MossFormer2_SE_48K'])
wav = cv(input_path=src, online_write=False)
try:
    cv.write(wav, output_path=dst)
except Exception:
    pass
if not os.path.exists(dst):
    # some versions treat output_path as a folder
    hits = glob.glob(os.path.join(dst, '**', '*.wav'), recursive=True) if os.path.isdir(dst) else []
    if not hits:
        d = os.path.dirname(dst) or '.'
        hits = [p for p in glob.glob(os.path.join(d, '**', '*.wav'), recursive=True)
                if os.path.abspath(p) != os.path.abspath(src)]
    if not hits:
        raise SystemExit('ClearVoice wrote no output')
    newest = max(hits, key=os.path.getmtime)
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.move(newest, dst)
print("denoised")
"""


def df_binary_path():
    """Next to this script first, then anywhere on PATH."""
    for folder in (HERE, os.path.join(HERE, "tools")):
        for name in ("deep-filter.exe", "deep-filter"):
            local = os.path.join(folder, name)
            if os.path.exists(local):
                return local
    return shutil.which("deep-filter") or shutil.which("deep-filter.exe")


def clean_with_df_binary(wav_in, wav_out):
    """
    The official precompiled deep-filter.exe. No Python packages at all,
    so none of the Rust or PyTorch install trouble applies.
    """
    exe = df_binary_path()
    if not exe:
        raise RuntimeError("deep-filter binary not found")
    outdir = os.path.join(os.path.dirname(wav_out), "_dfout")
    shutil.rmtree(outdir, ignore_errors=True)
    os.makedirs(outdir, exist_ok=True)
    try:
        r = subprocess.run([exe, "-o", outdir, wav_in], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except OSError as e:
        shutil.rmtree(outdir, ignore_errors=True)
        raise RuntimeError("deep-filter could not be started: %s" % e)
    made = [os.path.join(outdir, f) for f in os.listdir(outdir)
            if f.lower().endswith(".wav")]
    if r.returncode != 0 or not made:
        shutil.rmtree(outdir, ignore_errors=True)
        raise RuntimeError((r.stderr or "deep-filter failed")[-400:])
    newest = max(made, key=os.path.getmtime)
    shutil.move(newest, wav_out)
    shutil.rmtree(outdir, ignore_errors=True)
    return "DeepFilterNet (binary)"


def clean_with_clearvoice(wav_in, wav_out):
    helper = os.path.join(RAW_ROOT, "_cv_run.py")
    os.makedirs(RAW_ROOT, exist_ok=True)
    with open(helper, "w", encoding="utf-8") as f:
        f.write(CV_SCRIPT)
    r = subprocess.run([sys.executable, helper, wav_in, wav_out],
                       capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if r.returncode != 0 or not os.path.exists(wav_out):
        raise RuntimeError((r.stderr or "ClearVoice failed")[-400:])
    return "ClearVoice MossFormer2"


def clean_with_ffmpeg(wav_in, wav_out, nr_db):
    """Always available fallback. Plain DSP, gentle, no model needed."""
    chain = "highpass=f=80,afftdn=nr=%s:nf=-25:tn=1" % nr_db
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", wav_in, "-af", chain,
         "-c:a", "pcm_s16le", wav_out],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        creationflags=NO_WINDOW)
    if r.returncode != 0 or not os.path.exists(wav_out):
        raise RuntimeError((r.stderr or "ffmpeg denoise failed")[-400:])
    return "ffmpeg afftdn"


def denoise_video(video_path, cfg=None):
    """
    Clean the audio of a finished video. The video stream is copied, never
    re-encoded, so the picture is bit for bit what the builder produced.
    Returns the path of the cleaned file.
    """
    cfg = cfg or denoise_cfg()
    try:
        acfg = load_config().get("audio", {}) or {}
    except Exception:
        acfg = {}
    stem, ext = os.path.splitext(video_path)
    work = stem + "__dn"
    raw_wav, clean_wav = work + "_raw.wav", work + "_clean.wav"
    tmp_out = work + ext

    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_path, "-vn",
         "-c:a", "pcm_s16le", "-ar", "48000", raw_wav],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        creationflags=NO_WINDOW)
    if r.returncode != 0 or not os.path.exists(raw_wav):
        raise RuntimeError("could not read the audio out of the finished video")

    want = cfg["engine"]
    order = []
    if want in ("auto", "deepfilternet"):
        order.append(("deepfilternet", clean_with_df_binary))
        order.append(("deepfilternet", clean_with_deepfilternet))
    if want in ("auto", "clearvoice"):
        order.append(("clearvoice", clean_with_clearvoice))
    order.append(("ffmpeg", None))

    engine = None
    used = None
    last_err = ""
    for name, fn in order:
        try:
            if fn is clean_with_df_binary and not df_binary_path():
                last_err = ("deep-filter.exe not found, place deep-filter.exe in automations\\video\\tools")
                continue
            if fn is clean_with_deepfilternet and not deepfilter_available():
                last_err = "the deepfilternet python package is not installed"
                continue
            if fn is None:
                engine = clean_with_ffmpeg(raw_wav, clean_wav, cfg["ffmpeg_nr_db"])
            else:
                engine = fn(raw_wav, clean_wav)
            used = name
            break
        except Exception as e:
            last_err = str(e)[:200]
    if engine and want not in ("auto", used):
        # never fail outright, but be clear that it is not what was asked for
        engine += " (fell back from %s: %s)" % (want, last_err or "not available")
    if engine is None:
        for p in (raw_wav, clean_wav):
            try:
                os.remove(p)
            except Exception:
                pass
        raise RuntimeError(last_err or "no denoise engine worked")

    mix = cfg["mix"]
    lufs = acfg.get("target_lufs", -16)
    tp = acfg.get("true_peak_max_dbtp", -1.5)
    tail = ",loudnorm=I=%s:TP=%s:LRA=11" % (lufs, tp) if cfg["renormalize"] else ""

    if mix >= 0.999:
        fc = "[1:a]anull%s[a]" % tail
    else:
        fc = ("[1:a]volume=%.3f[c];[0:a]volume=%.3f[o];"
              "[c][o]amix=inputs=2:normalize=0[m];[m]anull%s[a]"
              % (mix, 1.0 - mix, tail))

    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_path, "-i", clean_wav,
         "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-ar", "48000",
         "-movflags", "+faststart", "-shortest", tmp_out],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        creationflags=NO_WINDOW)
    for p in (raw_wav, clean_wav):
        try:
            os.remove(p)
        except Exception:
            pass
    if r.returncode != 0 or not os.path.exists(tmp_out):
        try:
            os.remove(tmp_out)
        except Exception:
            pass
        raise RuntimeError((r.stderr or "could not remux the cleaned audio")[-400:])

    if cfg["keep_original"]:
        final = stem + cfg["clean_suffix"] + ext
        if os.path.exists(final):
            os.remove(final)
        os.replace(tmp_out, final)
    else:
        final = video_path
        os.replace(tmp_out, final)
    return final, engine


DN_BUSY = [False]


def clean_job(path, move_to=None, cleanup_src=False, cfg=None):
    """Denoise one file on its own, outside any build."""
    name = os.path.basename(path)
    try:
        DN_BUSY[0] = True
        log_write("")
        log_write("Cleaning audio: %s" % name)
        out, engine = denoise_video(path, cfg or denoise_cfg())
        if move_to:
            os.makedirs(move_to, exist_ok=True)
            dest = os.path.join(move_to, os.path.basename(out))
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(out, dest)
            out = dest
        log_write("   done with %s" % engine)
        log_write("   saved: %s" % out)
    except Exception as e:
        log_write("   clean up failed for %s: %s" % (name, e))
    finally:
        if cleanup_src:
            try:
                os.remove(path)
            except Exception:
                pass
        DN_BUSY[0] = False


def list_output_videos():
    try:
        out_dir = load_config()["paths"]["output_folder"]
    except Exception:
        return []
    rows = []
    try:
        names = sorted(os.listdir(out_dir))
    except Exception:
        return []
    suffix = denoise_cfg()["clean_suffix"]
    for f in names:
        if not f.lower().endswith((".mp4", ".mov", ".mkv")):
            continue
        fp = os.path.join(out_dir, f)
        stem, ext = os.path.splitext(f)
        rows.append({"name": f,
                     "size_mb": round(os.path.getsize(fp) / 1048576.0, 1),
                     "is_clean": stem.endswith(suffix),
                     "has_clean": os.path.exists(
                         os.path.join(out_dir, stem + suffix + ext))})
    return rows


def find_finished_video(out_dir, before):
    """Whatever mp4 the builder just wrote, without guessing its name."""
    try:
        now = {}
        for f in os.listdir(out_dir):
            if f.lower().endswith((".mp4", ".mov", ".mkv")):
                fp = os.path.join(out_dir, f)
                now[fp] = os.path.getmtime(fp)
    except Exception:
        return None
    fresh = [p for p, t in now.items() if before.get(p) != t]
    if not fresh:
        return None
    return max(fresh, key=lambda p: now[p])


def snapshot_outputs(out_dir):
    try:
        return {os.path.join(out_dir, f): os.path.getmtime(os.path.join(out_dir, f))
                for f in os.listdir(out_dir)
                if f.lower().endswith((".mp4", ".mov", ".mkv"))}
    except Exception:
        return {}


# ----------------------------------------------------------------- queue worker
def _run_builder(item, out_dir):
    cmd = BUILDER_CMD + ["--answers", item["answers"], "--captions", item["captions"], "--opening", item["opening"],
                         "--name", item["name"], "--no-prompt"]
    before = snapshot_outputs(out_dir)
    env = os.environ.copy()
    env["RELAY_VIDEO_CONFIG"] = CONFIG_PATH
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL,
                                cwd=HERE, creationflags=NO_WINDOW, env=env, encoding="utf-8", errors="replace")
        tail = []
        for line in proc.stdout:
            log_write(line.rstrip())
            tail.append(line.rstrip())
            tail = tail[-25:]
        proc.wait()
        rc = proc.returncode
    except Exception as e:
        log_write("Could not start the builder: %s" % e)
        return 1, None, ["Could not start the builder: %s" % e]
    return rc, find_finished_video(out_dir, before), tail


def _clean_stage(item, made):
    dn = denoise_cfg()
    if not dn["enabled"]:
        return
    if not made or not os.path.exists(made):
        set_job(item, cleanup_error="The finished file could not be found, so the audio clean up was skipped.")
        log_write("   (could not find the finished file, skipping the audio clean up)")
        return
    set_job(item, status="cleaning_audio", stage="clean")
    WORKER["stage"] = "cleaning_audio"
    try:
        log_write("   Cleaning up the audio ...")
        clean, engine = denoise_video(made, dn)
        set_job(item, clean_output=clean, engine=engine, cleanup_error=None)
        if dn["keep_original"]:
            log_write("   original : %s" % os.path.basename(made))
            log_write("   cleaned  : %s   (%s)" % (os.path.basename(clean), engine))
        else:
            log_write("   cleaned in place: %s   (%s)" % (os.path.basename(clean), engine))
    except Exception as e:
        # The rendered video is kept; only the optional clean up failed.
        set_job(item, cleanup_error="Audio clean up failed, the rendered video is untouched: %s" % e)
        log_write("   Audio clean up failed, the original video is untouched: %s" % e)


def process_job(item):
    cfg = load_config()
    out_dir = cfg["paths"]["output_folder"]
    os.makedirs(out_dir, exist_ok=True)
    resume = item.get("resume_stage") or "render"
    set_job(item, attempts=int(item.get("attempts") or 0) + 1, started_at=now_iso(), error=None, resume_stage=None)
    made = item.get("output")
    if resume == "render" or not made or not os.path.exists(made):
        set_job(item, status="rendering", stage="render")
        WORKER["stage"] = "rendering"
        log_write("")
        log_write("===== Rendering %s =====" % item["name"])
        if not os.path.isdir(item.get("answers") or ""):
            set_job(item, status="failed", error="The prepared clips folder is missing (%s). Add this candidate again." % item.get("stage_dir"))
            log_write("===== %s FAILED: prepared clips are missing =====" % item["name"])
            return
        rc, made, tail = _run_builder(item, out_dir)
        if rc != 0 or not made:
            set_job(item, status="failed", error="Rendering failed (exit code %s). Last lines: %s" % (rc, " | ".join(tail[-6:])))
            log_write("===== %s FAILED (see log above) =====" % item["name"])
            return
        set_job(item, output=made)
    _clean_stage(item, made)
    set_job(item, status="completed", stage="done", finished_at=now_iso())
    log_write("===== %s DONE =====" % item["name"])
    log_write("Saved: %s" % (item.get("clean_output") or made))


def queue_worker():
    """One video at a time, forever. Wakes up when a job is queued."""
    while True:
        WAKE.wait(timeout=5)
        WAKE.clear()
        while True:
            nxt = None
            with LOCK:
                for j in JOBS:
                    if j.get("status") == "queued":
                        nxt = j
                        break
            if nxt is None:
                break
            WORKER["busy"] = True
            WORKER["job"] = nxt["id"]
            try:
                process_job(nxt)
            except Exception as e:
                set_job(nxt, status="failed", error="Unexpected error: %s" % e)
                log_write("Unexpected error while processing %s: %s" % (nxt.get("name"), e))
            finally:
                WORKER["busy"] = False
                WORKER["job"] = None
                WORKER["stage"] = ""


def start_worker():
    threading.Thread(target=queue_worker, name="video-queue", daemon=True).start()
    WAKE.set()


# ------------------------------------------------------------------ page
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RHC Interview Studio</title>
<style>
  :root{
    --bg:#0f1113; --panel:#17191d; --panel2:#1e2126; --line:#2b2f36;
    --ink:#e9e6df; --muted:#9aa1ab; --cream:#FFFCF2; --accent:#e0c68a;
    --ok:#5fbf7d; --warn:#e0a33c; --bad:#e2685f;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:"Segoe UI",Inter,system-ui,-apple-system,sans-serif;font-size:14px}
  .wrap{max-width:1080px;margin:0 auto;padding:26px 22px 60px}
  header{display:flex;align-items:baseline;gap:14px;margin-bottom:20px}
  header h1{font-size:19px;margin:0}
  header span{color:var(--muted);font-size:12.5px}
  .grid{display:grid;grid-template-columns:1.15fr 1fr;gap:18px}
  @media (max-width:920px){.grid{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px}
  .card h2{font-size:12px;text-transform:uppercase;letter-spacing:1.3px;
           color:var(--muted);margin:0 0 14px;font-weight:600}
  label{display:block;font-size:12.5px;color:var(--muted);margin:0 0 6px}
  input[type=text],textarea{width:100%;background:var(--panel2);color:var(--ink);
       border:1px solid var(--line);border-radius:8px;padding:10px 12px;font:inherit;outline:none}
  input[type=text]:focus,textarea:focus{border-color:var(--accent)}
  textarea{resize:vertical;min-height:96px;line-height:1.55}
  .field{margin-bottom:16px}
  .drop{border:1.5px dashed var(--line);border-radius:10px;padding:14px;text-align:center;
        cursor:pointer;transition:.15s;background:var(--panel2)}
  .drop:hover,.drop.over{border-color:var(--accent);background:#22262c}
  .drop b{display:block;font-size:13px;margin-bottom:2px}
  .drop small{color:var(--muted)}
  button{font:inherit;border:0;border-radius:8px;padding:9px 15px;cursor:pointer}
  .primary{background:var(--cream);color:#161616;font-weight:600}
  .primary:disabled{opacity:.4;cursor:default}
  .ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}
  .ghost:hover{color:var(--ink);border-color:var(--accent)}
  .ghost:disabled{opacity:.4;cursor:default}
  .mini{padding:5px 10px;font-size:12px;border-radius:7px}
  .row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
  .hint{font-size:12.5px;margin-top:7px}
  .hint.ok{color:var(--ok)} .hint.warn{color:var(--warn)}
  .clips{margin-top:9px}
  .clip{display:flex;align-items:center;gap:9px;padding:8px 10px;background:var(--panel2);
        border:1px solid var(--line);border-radius:9px;margin-bottom:6px;font-size:12.7px}
  .clip .idx{color:var(--accent);width:16px;flex:none}
  .clip .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px}
  .clip .st{margin-left:auto;font-size:12px;white-space:nowrap;color:var(--muted)}
  .clip .st.cut{color:var(--ok)}
  .clip .st.todo{color:var(--warn)}
  .clip .x{background:none;border:0;color:var(--muted);cursor:pointer;font-size:15px;padding:0 2px}
  .clip .x:hover{color:var(--bad)}
  ul.queue{list-style:none;margin:0;padding:0}
  ul.queue li{display:flex;align-items:center;gap:10px;padding:10px 12px;background:var(--panel2);
      border:1px solid var(--line);border-radius:9px;margin-bottom:8px}
  ul.queue .nm{font-weight:600}
  ul.queue .meta{color:var(--muted);font-size:12.5px;margin-left:auto}
  .pill{font-size:11px;padding:3px 9px;border-radius:20px;border:1px solid var(--line);color:var(--muted)}
  .pill.done,.pill.completed{color:var(--ok);border-color:var(--ok)}
  .pill.building,.pill.preparing,.pill.rendering,.pill.cleaning_audio,.pill.queued{color:var(--warn);border-color:var(--warn)}
  .pill.failed,.pill.interrupted{color:var(--bad);border-color:var(--bad)}
  ul.queue li{flex-wrap:wrap}
  ul.queue .err{flex-basis:100%;color:var(--bad);font-size:12px;margin-top:4px;white-space:pre-wrap}
  ul.queue .out{flex-basis:100%;color:var(--muted);font-size:12px;margin-top:2px;word-break:break-all}
  pre.log{background:#0b0d0f;border:1px solid var(--line);border-radius:10px;padding:14px;
      height:260px;overflow:auto;margin:0;font-family:"Cascadia Mono",Consolas,monospace;
      font-size:12.2px;line-height:1.6;color:#cfd3d8;white-space:pre-wrap}
  .bar{height:5px;background:var(--panel2);border-radius:4px;overflow:hidden;margin-top:12px;display:none}
  .bar>div{height:100%;width:0;background:var(--accent);transition:width .12s}
  .empty{color:var(--muted);font-size:12.5px;padding:4px 2px}
  .foot{margin-top:14px;color:var(--muted);font-size:12px}

  .veil{position:fixed;inset:0;background:rgba(6,7,9,.84);display:none;z-index:50;
        align-items:center;justify-content:center;padding:20px}
  .veil.on{display:flex}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:14px;
         width:min(980px,100%);max-height:94vh;overflow:auto;padding:20px}
  .modal h3{margin:0 0 3px;font-size:15px}
  .modal .sub{color:var(--muted);font-size:12.5px;margin-bottom:14px}
  video{width:100%;max-height:330px;background:#000;border-radius:10px;display:block}
  .tl{position:relative;height:120px;background:#0b0d0f;border:1px solid var(--line);
      border-radius:10px;margin-top:14px;overflow:hidden;cursor:pointer;user-select:none}
  .tl canvas{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
  .mark{position:absolute;top:0;bottom:0;width:1px;background:rgba(95,191,125,.85);pointer-events:none}
  .mark:after{content:"";position:absolute;top:0;left:-4px;border-left:4px solid transparent;
       border-right:4px solid transparent;border-top:7px solid var(--ok)}
  .mask{position:absolute;top:0;bottom:0;background:rgba(10,11,13,.72);pointer-events:none}
  .hand{position:absolute;top:0;bottom:0;width:11px;margin-left:-5px;cursor:ew-resize;
        background:var(--accent);opacity:.92;border-radius:3px}
  .hand:after{content:"";position:absolute;top:50%;left:4px;width:3px;height:20px;
        margin-top:-10px;background:#161616;border-radius:2px}
  .ph{position:absolute;top:0;bottom:0;width:2px;background:var(--cream);pointer-events:none}
  .ticks{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:5px}
  .readout{display:flex;gap:18px;margin-top:12px;font-size:13px;flex-wrap:wrap}
  .readout b{color:var(--accent);font-weight:600}
  .keys{color:var(--muted);font-size:11.5px;margin-top:10px;line-height:1.6}
  .capbox{margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
  .capbox textarea{min-height:120px;font-size:13px}
  .capbox .hdr{display:flex;align-items:center;gap:9px;margin-bottom:7px;flex-wrap:wrap}
  .capbox .hdr label{margin:0}
  .capbox .spacer{flex:1}
  .badge{font-size:11px;padding:2px 8px;border-radius:20px;border:1px solid var(--line);color:var(--muted)}
  .badge.done{color:var(--ok);border-color:var(--ok)}
  .badge.working,.badge.queued{color:var(--warn);border-color:var(--warn)}
  .badge.error,.badge.off{color:var(--bad);border-color:var(--bad)}
  .engine{font-size:12px;color:var(--muted);margin-top:8px}
  .modal .actions{display:flex;gap:9px;margin-top:16px;flex-wrap:wrap;align-items:center}
  .spacer{flex:1}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>RHC Interview Studio</h1>
    <span>trim in the browser, build with the same pipeline</span>
    <span style="margin-left:auto;opacity:.55" id="ver"></span>
  </header>

  <div class="grid">
    <div class="card">
      <h2>Add a candidate</h2>

      <div class="field">
        <label>Candidate name (shown in the intro pill)</label>
        <input type="text" id="name" placeholder="Vesta A." autocomplete="off">
      </div>

      <div class="field">
        <label>Names and words to spell right (helps the transcriber)</label>
        <input type="text" id="vocab" placeholder="Vesta Acharya, Relay Human Cloud, Ahmedabad, Vadodara" autocomplete="off">
        <div class="engine" id="engineNote"></div>
      </div>

      <div class="field">
        <label>Intro b-rolls (3)</label>
        <div class="drop" id="dropOpen">
          <b>Drop clips here or click to browse</b>
          <small>numbers in the filename set the order</small>
        </div>
        <div class="clips" id="listOpen"></div>
        <input type="file" id="fileOpen" multiple accept=".mp4,.mov,.m4v" hidden>
      </div>

      <div class="field">
        <label>Answer clips (8)</label>
        <div class="drop" id="dropAns">
          <b>Drop clips here or click to browse</b>
          <small>1.mp4, 2.mp4 ... is easiest</small>
        </div>
        <div class="clips" id="listAns"></div>
        <input type="file" id="fileAns" multiple accept=".mp4,.mov,.m4v" hidden>
      </div>

      <div class="row" style="margin:-4px 0 14px">
        <button class="ghost mini" id="autoSil">Auto trim the 8 answers to their spikes</button>
        <button class="ghost mini" id="cut1">Cut 1s off both ends of the 8 answers</button>
      </div>

      <div class="field">
        <label>Fallback captions, only used for clips with an empty caption box (one sentence per clip)</label>
        <textarea id="caps" placeholder="First answer caption. Second answer caption. ..."></textarea>
        <div class="hint" id="sent">0 sentences detected</div>
      </div>

      <div class="row">
        <button class="primary" id="add">Confirm &amp; Make Video</button>
        <button class="ghost" id="clear">Clear form</button>
      </div>
      <div class="hint" style="margin-top:8px">One click: the clips are cut, the video is queued, rendered and its audio cleaned
        automatically. If another video is still being made, this one waits its turn.</div>
      <div class="hint warn" id="gate"></div>
      <div class="bar" id="bar"><div></div></div>
    </div>

    <div class="card">
      <h2>Video queue</h2>
      <div class="hint" id="workerNote" style="margin:-6px 0 10px"></div>
      <ul class="queue" id="queue"></ul>
      <div class="empty" id="qEmpty">Nothing queued yet.</div>
      <div class="row" style="margin-top:14px">
        <button class="ghost" id="openOut">Open output folder</button>
        <button class="ghost" id="clearDone">Clear completed</button>
      </div>
      <div class="foot" id="outPath"></div>
    </div>
  </div>

  <div class="card" style="margin-top:18px">
    <h2>Clean audio only <span style="text-transform:none;letter-spacing:0;font-weight:400">
      (nothing to do with building, just noise removal on a video)</span></h2>
    <div class="grid" style="gap:16px">
      <div>
        <label>Videos in your output folder</label>
        <div id="outList" class="clips"></div>
        <button class="ghost mini" id="refreshOut" style="margin-top:6px">Refresh list</button>
      </div>
      <div>
        <label>Or drop any other video from anywhere</label>
        <div class="drop" id="dropClean">
          <b>Drop a video here or click to browse</b>
          <small>the cleaned copy lands in your output folder</small>
        </div>
        <input type="file" id="fileClean" accept=".mp4,.mov,.m4v,.mkv" hidden>
        <div class="hint" id="cleanNote"></div>
        <div class="bar" id="bar2"><div></div></div>
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:18px">
    <h2>Build log</h2>
    <pre class="log" id="log">Ready.</pre>
  </div>
</div>

<div class="veil" id="veil">
  <div class="modal">
    <h3 id="mTitle">Trim clip</h3>
    <div class="sub" id="mSub"></div>
    <video id="mVid" preload="metadata"></video>
    <div class="tl" id="mTl">
      <canvas id="mWave" width="1600" height="120"></canvas>
      <div class="mark" id="mS1"></div>
      <div class="mark" id="mS2"></div>
      <div class="mask" id="mMaskL"></div>
      <div class="mask" id="mMaskR"></div>
      <div class="hand" id="mIn"></div>
      <div class="hand" id="mOut"></div>
      <div class="ph" id="mPh"></div>
    </div>
    <div class="ticks"><span>0.00s</span><span id="mMid"></span><span id="mEnd"></span></div>
    <div class="readout">
      <span>In <b id="rIn">0.00s</b></span>
      <span>Out <b id="rOut">0.00s</b></span>
      <span>Keeping <b id="rKeep">0.00s</b></span>
      <span id="rCut" style="color:var(--muted)"></span>
    </div>
    <div class="actions">
      <button class="ghost mini" id="bPlay">Play kept part</button>
      <button class="ghost mini" id="bSetIn">Set in here</button>
      <button class="ghost mini" id="bSetOut">Set out here</button>
      <button class="ghost mini" id="b1s">1s off both ends</button>
      <button class="ghost mini" id="bSpeech">Snap to speech</button>
      <button class="ghost mini" id="bIn08">Start before first spike</button>
      <button class="ghost mini" id="bReset">Reset</button>
      <span class="spacer"></span>
      <button class="ghost" id="bCancel">Cancel</button>
      <button class="primary" id="bSave">Save trim</button>
    </div>
    <div class="capbox" id="capBox">
      <div class="hdr">
        <label>Captions for this answer, one line per caption bar</label>
        <span class="badge" id="capStatus">waiting</span>
        <span class="spacer"></span>
        <button class="ghost mini" id="bRefill">Refill from transcript</button>
        <button class="ghost mini" id="bReflow">Reflow lines</button>
        <button class="ghost mini" id="bRedo">Transcribe again</button>
      </div>
      <textarea id="capText" placeholder="Captions appear here once the clip has been transcribed. Edit freely, especially names."></textarea>
      <div class="keys" id="capInfo"></div>
    </div>
    <div class="keys">Drag the gold handles, or click the timeline to move the playhead.
      Keys: space to play or pause, I sets in, O sets out, arrows nudge 0.1s (hold shift for 1s),
      enter saves, esc cancels. The green marker is the first real spike of speech.</div>
  </div>
</div>

<script>
const NUM_Q = 8;
const PAGE_VERSION = "__PAGE_VERSION__";
let reloadedForVersion = false;
const clips = {};
let openIds = [], ansIds = [];
let busy = false, since = 0, autoscroll = true;
let ENGINE = 'off', PRE_ROLL = 0.8, POST_ROLL = 0.8, MAXCHARS = 62;

function esc(s){return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmt(t){ return (Math.round((t||0)*100)/100).toFixed(2)+'s'; }
function numKey(f){
  const b = f.name.replace(/\.[^.]+$/, '');
  const m = b.match(/(\d+)/);
  return m ? [0, parseInt(m[1],10), ''] : [1, 0, b.toLowerCase()];
}
function sortFiles(list){
  return list.slice().sort((a,b)=>{
    const x=numKey(a), y=numKey(b);
    if(x[0]!==y[0]) return x[0]-y[0];
    if(x[1]!==y[1]) return x[1]-y[1];
    return x[2]<y[2]?-1:x[2]>y[2]?1:0;
  });
}
function splitCaps(t){
  const s = t.split(/\s+/).filter(Boolean).join(' ');
  if(!s) return [];
  return (s.match(/[^.?!]+[.?!]?/g) || []).map(x=>x.trim()).filter(Boolean);
}

function wrapLine(text, max){
  const words = text.split(/\s+/).filter(Boolean);
  const out = []; let cur = '';
  for(const w of words){
    if(!cur) cur = w;
    else if((cur+' '+w).length <= max) cur += ' '+w;
    else { out.push(cur); cur = w; }
  }
  if(cur) out.push(cur);
  return out;
}
function drawWave(c){
  const cv = document.getElementById('mWave'), g = cv.getContext('2d');
  const W = cv.width, H = cv.height, mid = H/2;
  g.clearRect(0,0,W,H);
  const pk = c.peaks || [];
  if(!pk.length){
    g.fillStyle = 'rgba(154,161,171,.55)'; g.font = '16px sans-serif';
    g.textAlign = 'center';
    g.fillText(c.hasAudio===false ? 'no usable audio in this clip' : 'reading audio...', W/2, mid+5);
    return;
  }
  const step = W/pk.length;
  g.fillStyle = '#e0c68a';
  for(let i=0;i<pk.length;i++){
    const h = Math.max(1, Math.pow(pk[i], 0.7) * (H*0.92));
    g.fillRect(i*step, mid-h/2, Math.max(1, step-0.35), h);
  }
  g.strokeStyle = 'rgba(233,230,223,.16)';
  g.beginPath(); g.moveTo(0,mid); g.lineTo(W,mid); g.stroke();
}

function textInRange(c){
  // everything spoken inside the kept part, as one continuous piece of text.
  // The server then fills each caption bar to its real width, the same way
  // the builder does, instead of breaking at every pause.
  const segs = c.segments || [];
  const keep = segs.filter(s => s.end > c.inS + 0.05 && s.start < c.outS - 0.05);
  const src = keep.length ? keep : segs;
  return src.map(s => s.text.trim()).join(' ').replace(/\s+/g, ' ').trim();
}
async function fitLines(text){
  if(!text) return [];
  try{
    const r = await fetch('/api/fitlines', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({text:text})});
    if(!r.ok) throw new Error('fit failed');
    return (await r.json()).lines || [];
  }catch(e){
    return wrapLine(text, MAXCHARS);   // fallback, rough but never empty
  }
}
async function refillCaptions(c, force){
  if(!c || c.kind!=='answers') return;
  if(c.capEdited && !force) return;
  if(!(c.segments||[]).length) return;
  const lines = await fitLines(textInRange(c));
  c.capText = lines.join('\n');
  if(force) c.capEdited = false;
}
function speechBounds(c){
  // the measured waveform wins: first and last spike that is loud enough
  // and lasts long enough to be speech
  if(c.hasAudio && !c.weak && c.soundEnd > c.soundStart) return [c.soundStart, c.soundEnd];
  const segs = c.segments || [];
  if(segs.length) return [segs[0].start, segs[segs.length-1].end];
  const sil = c.silences || [];
  let a = 0, b = c.dur;
  if(sil.length && sil[0][0] <= 0.05) a = sil[0][1];
  const last = sil[sil.length-1];
  if(last && last[1] >= c.dur - 0.05) b = last[0];
  return [a, b];
}

function upload(url, file, onProgress){
  return new Promise((res, rej)=>{
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url, true);
    xhr.upload.onprogress = e=>{ if(e.lengthComputable) onProgress(e.loaded); };
    xhr.onload = ()=> xhr.status===200 ? res(JSON.parse(xhr.responseText))
                                       : rej(new Error(xhr.responseText||xhr.status));
    xhr.onerror = ()=> rej(new Error('upload failed'));
    xhr.send(file);
  });
}

async function addFiles(files, kind){
  const ids = kind==='opening' ? openIds : ansIds;
  const bar = document.getElementById('bar'), fill = bar.firstElementChild;
  bar.style.display='block';
  const totalBytes = files.reduce((s,f)=>s+f.size,0) || 1;
  let doneBytes = 0;
  for(const f of files){
    const ext = (f.name.match(/\.[^.]+$/) || ['.mp4'])[0];
    try{
      const hint = document.getElementById('vocab').value.trim();
      const r = await upload('/api/clip?kind='+kind+'&ext='+encodeURIComponent(ext)
                             +'&name='+encodeURIComponent(f.name)
                             +'&hint='+encodeURIComponent(hint), f,
                             loaded=>{ fill.style.width =
                               Math.min(100,((doneBytes+loaded)/totalBytes)*100)+'%'; });
      clips[r.id] = {id:r.id, name:f.name, file:f, url:URL.createObjectURL(f), kind:kind,
                     dur:0, silences:[], peaks:[], hasAudio:false,
                     soundStart:0, soundEnd:0, inS:0, outS:0,
                     reviewed:false, status:'analysing', autoSet:false,
                     segments:[], tStatus:'waiting', tError:'',
                     capText:'', capEdited:false};
      ids.push(r.id);
    }catch(e){ alert('Upload failed for '+f.name+': '+e.message); }
    doneBytes += f.size;
    fill.style.width = (doneBytes/totalBytes)*100+'%';
    renderClips();
  }
  setTimeout(()=>{ bar.style.display='none'; fill.style.width='0'; }, 400);
  pollClips();
}

let polling = false;
async function pollClips(){
  if(polling) return; polling = true;
  try{
    while(true){
      const pending = Object.values(clips).filter(
        c => c.status==='analysing' || ['waiting','queued','working'].includes(c.tStatus));
      if(!pending.length) break;
      const data = await (await fetch('/api/clips?ids='+pending.map(c=>c.id).join(','))).json();
      for(const info of data.clips){
        const c = clips[info.id];
        if(!c) continue;
        if(info.status==='ready' && c.status!=='ready'){
          // intro b-rolls are left completely alone, trimmed by hand
          c.dur = info.duration; c.silences = info.silences||[];
          c.hasAudio = !!info.has_audio;
          c.soundStart = info.sound_start||0; c.soundEnd = info.sound_end||c.dur;
          c.weak = !!info.weak;
          if(!c.outS) c.outS = c.dur;
          c.status = 'ready';
          if(c.kind==='answers' && !c.reviewed && !c.autoSet) autoSpeechTrim(c);
          fetch('/api/peaks?id='+c.id).then(r=>r.json()).then(d=>{
            c.peaks = d.peaks||[]; c.levels = d.levels||{};
            if(T && T.id===c.id){ drawWave(c); drawMarks(); }
          }).catch(()=>{});
        }
        c.tStatus = info.t_status || c.tStatus;
        c.tError = info.t_error || '';
        if(info.t_status==='done' && (info.segments||[]).length && !c.segments.length){
          c.segments = info.segments;
          await refillCaptions(c, false);
          if(T && T.id===c.id) fillCapUI();
        }
      }
      renderClips();
      await new Promise(r=>setTimeout(r, 800));
    }
  }finally{ polling = false; renderClips(); }
}
function autoSpeechTrim(c){
  if(!c.dur) return;
  const b = speechBounds(c);
  let a = Math.max(0, b[0] - PRE_ROLL);
  let z = Math.min(c.dur, b[1] + POST_ROLL);
  if(z - a < 0.3){ a = 0; z = c.dur; }
  c.inS = a; c.outS = z; c.autoSet = true;
}

function clipState(c){
  if(c.status==='analysing') return ['reading audio...',''];
  if(c.tStatus==='queued'||c.tStatus==='working') return ['transcribing...','todo'];
  if(c.tStatus==='error') return ['transcribe failed','todo'];
  if(c.kind==='opening' && !c.reviewed) return ['trim by hand','todo'];
  const head = c.inS, tail = Math.max(0, c.dur - c.outS);
  if(!c.reviewed) return ['check trim','todo'];
  const cap = c.kind==='answers'
     ? ', ' + (c.capText ? c.capText.split('\n').filter(Boolean).length + ' captions' : 'no captions')
     : '';
  if(head<0.02 && tail<0.02) return ['full clip'+cap,'cut'];
  return ['-'+fmt(head)+' / -'+fmt(tail)+cap,'cut'];
}
function renderClips(){
  const pairs = [['listOpen', openIds], ['listAns', ansIds]];
  for(const [listId, ids] of pairs){
    const el = document.getElementById(listId);
    if(!ids.length){ el.innerHTML = '<div class="empty">nothing selected</div>'; continue; }
    el.innerHTML = ids.map((id,i)=>{
      const c = clips[id]; if(!c) return '';
      const st = clipState(c);
      return '<div class="clip"><span class="idx">'+(i+1)+'</span>'
        + '<span class="nm" title="'+esc(c.name)+'">'+esc(c.name)+'</span>'
        + (c.dur ? '<span style="color:var(--muted)">'+fmt(c.dur)+'</span>' : '')
        + '<span class="st '+st[1]+'">'+st[0]+'</span>'
        + '<button class="ghost mini" '+(c.status==='ready'?'':'disabled')
        +   ' onclick="openTrim('+id+')">Trim</button>'
        + '<button class="x" onclick="dropClip('+id+')" title="remove">&times;</button></div>';
    }).join('');
  }
  updateGate();
}
function dropClip(id){
  openIds = openIds.filter(x=>x!==id);
  ansIds  = ansIds.filter(x=>x!==id);
  if(clips[id]) URL.revokeObjectURL(clips[id].url);
  delete clips[id];
  fetch('/api/clip/drop', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id:id})});
  renderClips();
}
function updateGate(){
  const all = openIds.concat(ansIds).map(id=>clips[id]).filter(Boolean);
  const g = document.getElementById('gate'), btn = document.getElementById('add');
  if(!all.length){ g.textContent=''; btn.disabled=false; return; }
  const busyCount = all.filter(c=>c.status!=='ready').length;
  const todo = all.filter(c=>c.status==='ready' && !c.reviewed).length;
  if(busyCount){ g.textContent = busyCount+' clip(s) still being read.'; btn.disabled=true; return; }
  if(todo){ g.textContent = todo+' clip(s) still need a look. Open each one to confirm the trim and captions.';
            btn.disabled=true; return; }
  g.textContent=''; btn.disabled=false;
}

let T = null, playUntil = null;
const veil=document.getElementById('veil'), vid=document.getElementById('mVid'),
      tl=document.getElementById('mTl'), hIn=document.getElementById('mIn'),
      hOut=document.getElementById('mOut'), ph=document.getElementById('mPh'),
      mL=document.getElementById('mMaskL'), mR=document.getElementById('mMaskR');

function openTrim(id){
  const c = clips[id]; if(!c || c.status!=='ready') return;
  T = c;
  if(!c.outS) c.outS = c.dur;
  document.getElementById('mTitle').textContent = 'Trim  '+c.name;
  document.getElementById('mSub').textContent =
     (c.kind==='opening' ? 'Intro b-roll, trim this one by hand' : 'Answer clip')
     +'  |  full length '+fmt(c.dur)
     + (!c.hasAudio ? '  |  no usable audio, trim by eye'
        : c.weak ? '  |  speech and room tone are too close to call, trim by eye'
        : ('  |  sound runs '+fmt(c.soundStart)+' to '+fmt(c.soundEnd)));
  drawWave(c);
  document.getElementById('mMid').textContent = fmt(c.dur/2);
  document.getElementById('mEnd').textContent = fmt(c.dur);
  vid.src = c.url;
  vid.currentTime = c.inS;
  document.getElementById('capBox').style.display = c.kind==='answers' ? 'block' : 'none';
  fillCapUI();
  veil.classList.add('on');
  draw();
}
function fillCapUI(){
  if(!T || T.kind!=='answers') return;
  const box = document.getElementById('capText'), bad = document.getElementById('capStatus');
  const label = {waiting:'waiting', queued:'in the queue', working:'transcribing...',
                 done:'transcribed', error:'failed', off:'no engine',
                 skip:'not transcribed'}[T.tStatus] || T.tStatus;
  bad.textContent = label;
  bad.className = 'badge '+T.tStatus;
  if(document.activeElement !== box) box.value = T.capText || '';
  const n = (T.capText||'').split('\n').filter(x=>x.trim()).length;
  document.getElementById('capInfo').textContent =
     T.tStatus==='error' ? ('Transcription failed: '+T.tError)
     : (n+' caption line(s). Each line becomes one caption bar, filled to the bar width and timed across the kept part of the clip. '
        + (T.capEdited ? 'Edited by hand, so it will not be refilled automatically.' : ''));
}
function closeTrim(){
  veil.classList.remove('on'); vid.pause(); vid.removeAttribute('src'); T=null; playUntil=null;
}
function drawMarks(){
  if(!T || !T.dur) return;
  const s1 = document.getElementById('mS1'), s2 = document.getElementById('mS2');
  if(!T.hasAudio || T.weak){ s1.style.display='none'; s2.style.display='none'; return; }
  s1.style.display='block'; s2.style.display='block';
  s1.style.left = (T.soundStart/T.dur)*100+'%';
  s2.style.left = (T.soundEnd/T.dur)*100+'%';
  s1.title = 'first sound '+fmt(T.soundStart);
  s2.title = 'last sound '+fmt(T.soundEnd);
}
function draw(){
  if(!T || !T.dur) return;
  drawMarks();
  const pIn=(T.inS/T.dur)*100, pOut=(T.outS/T.dur)*100;
  hIn.style.left=pIn+'%'; hOut.style.left=pOut+'%';
  mL.style.left='0'; mL.style.width=pIn+'%';
  mR.style.left=pOut+'%'; mR.style.width=(100-pOut)+'%';
  document.getElementById('rIn').textContent = fmt(T.inS);
  document.getElementById('rOut').textContent = fmt(T.outS);
  document.getElementById('rKeep').textContent = fmt(Math.max(0,T.outS-T.inS));
  document.getElementById('rCut').textContent =
     'cutting '+fmt(T.inS)+' from the start and '+fmt(Math.max(0,T.dur-T.outS))+' from the end'
     + ((T.hasAudio && !T.weak) ? ('  |  lead-in before first sound '
                      + fmt(Math.max(0, T.soundStart - T.inS))) : '');
}
vid.addEventListener('timeupdate', ()=>{
  if(!T || !T.dur) return;
  ph.style.left = (vid.currentTime/T.dur)*100+'%';
  if(playUntil!==null && vid.currentTime>=playUntil){ vid.pause(); playUntil=null; }
});
tl.addEventListener('mousedown', ev=>{
  if(!T || !T.dur) return;
  const r = tl.getBoundingClientRect();
  const at = x => Math.max(0, Math.min(T.dur, ((x-r.left)/r.width)*T.dur));
  if(ev.target===hIn || ev.target===hOut){
    const which = ev.target===hIn ? 'in' : 'out';
    const move = e=>{
      const tt = at(e.clientX);
      if(which==='in') T.inS = Math.min(tt, T.outS-0.05);
      else T.outS = Math.max(tt, T.inS+0.05);
      vid.currentTime = which==='in' ? T.inS : Math.max(0, T.outS-0.4);
      draw();
    };
    const up = ()=>{ document.removeEventListener('mousemove',move);
                     document.removeEventListener('mouseup',up); };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  } else {
    const t = at(ev.clientX);
    vid.currentTime = t; ph.style.left = (t/T.dur)*100+'%';
  }
});
function cut1(c){
  c.inS = Math.min(1, Math.max(0, c.dur/2 - 0.1));
  c.outS = Math.max(c.inS+0.05, c.dur-1);
}
function snapSpeech(c){
  const b = speechBounds(c);
  let a = Math.max(0, b[0]-PRE_ROLL), z = Math.min(c.dur, b[1]+POST_ROLL);
  if(z-a < 0.3){ a = 0; z = c.dur; }
  c.inS = a; c.outS = z;
}
document.getElementById('bPlay').onclick = ()=>{ if(T){ vid.currentTime=T.inS; playUntil=T.outS; vid.play(); } };
document.getElementById('bSetIn').onclick = ()=>{ if(T){ T.inS = Math.min(vid.currentTime, T.outS-0.05); draw(); } };
document.getElementById('bSetOut').onclick = ()=>{ if(T){ T.outS = Math.max(vid.currentTime, T.inS+0.05); draw(); } };
document.getElementById('b1s').onclick = ()=>{ if(T){ cut1(T); vid.currentTime=T.inS; draw(); } };
document.getElementById('bSpeech').onclick = async ()=>{
  if(!T) return;
  snapSpeech(T); vid.currentTime=T.inS; draw();
  await refillCaptions(T, false); fillCapUI();
};
document.getElementById('bIn08').onclick = async ()=>{
  if(!T) return;
  T.inS = Math.max(0, Math.min(speechBounds(T)[0]-PRE_ROLL, T.outS-0.05));
  vid.currentTime = T.inS; draw();
  await refillCaptions(T, false); fillCapUI();
};
document.getElementById('bReset').onclick = ()=>{ if(T){ T.inS=0; T.outS=T.dur; vid.currentTime=0; draw(); } };
document.getElementById('capText').addEventListener('keydown', e=>{ e.stopPropagation(); });
document.getElementById('vocab').addEventListener('keydown', e=>{ e.stopPropagation(); });
document.getElementById('capText').addEventListener('input', e=>{
  if(!T) return;
  T.capText = e.target.value; T.capEdited = true;
  const n = T.capText.split('\n').filter(x=>x.trim()).length;
  document.getElementById('capInfo').textContent =
     n+' caption line(s). Edited by hand, so it will not be refilled automatically.';
});
document.getElementById('bRefill').onclick = async ()=>{
  if(!T) return;
  if(!(T.segments||[]).length) return alert('Nothing transcribed for this clip yet.');
  await refillCaptions(T, true); fillCapUI();
};
document.getElementById('bReflow').onclick = async ()=>{
  if(!T) return;
  const box = document.getElementById('capText');
  const flat = (box.value||'').replace(/\s+/g, ' ').trim();
  if(!flat) return;
  const lines = await fitLines(flat);
  T.capText = lines.join('\n');
  box.value = T.capText;
  fillCapUI();
};
document.getElementById('bRedo').onclick = async ()=>{
  if(!T) return;
  const hint = document.getElementById('vocab').value.trim();
  const r = await fetch('/api/retranscribe', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({id:T.id, hint:hint})});
  if(!r.ok) return alert(await r.text());
  T.segments = []; T.tStatus = 'queued'; T.capEdited = false;
  fillCapUI(); renderClips(); pollClips();
};
document.getElementById('bCancel').onclick = closeTrim;
document.getElementById('bSave').onclick = async ()=>{
  if(T){ await refillCaptions(T, false); T.reviewed=true; }
  closeTrim(); renderClips();
};
veil.addEventListener('mousedown', e=>{ if(e.target===veil) closeTrim(); });

function isTyping(el){
  if(!el) return false;
  const tag = (el.tagName||'').toLowerCase();
  return tag==='textarea' || tag==='input' || tag==='select' || el.isContentEditable;
}
document.addEventListener('keydown', e=>{
  if(!T) return;
  // typing in the caption box or any field: hands off, let the browser do
  // its normal thing (space is a space, enter is a new line)
  if(isTyping(e.target)){
    if(e.key==='Escape') e.target.blur();
    return;
  }
  const step = e.shiftKey ? 1 : 0.1;
  if(e.key===' '){ e.preventDefault(); vid.paused ? vid.play() : vid.pause(); }
  else if(e.key==='i'||e.key==='I'){ T.inS = Math.min(vid.currentTime, T.outS-0.05); draw(); }
  else if(e.key==='o'||e.key==='O'){ T.outS = Math.max(vid.currentTime, T.inS+0.05); draw(); }
  else if(e.key==='ArrowLeft'){ e.preventDefault(); vid.currentTime = Math.max(0, vid.currentTime-step); }
  else if(e.key==='ArrowRight'){ e.preventDefault(); vid.currentTime = Math.min(T.dur, vid.currentTime+step); }
  else if(e.key==='Enter'){ document.getElementById('bSave').onclick(); }
  else if(e.key==='Escape'){ closeTrim(); }
});

function readyAnswers(){
  return ansIds.map(id=>clips[id]).filter(c=>c && c.status==='ready');
}
document.getElementById('cut1').onclick = async ()=>{
  const all = readyAnswers();
  if(!all.length) return alert('Add the answer clips first.');
  all.forEach(c=>{ cut1(c); c.reviewed=true; });
  renderClips();
  await Promise.all(all.map(c=>refillCaptions(c, false)));
  renderClips();
};
document.getElementById('autoSil').onclick = async ()=>{
  const all = readyAnswers();
  if(!all.length) return alert('Add the answer clips first.');
  all.forEach(c=>{ snapSpeech(c); c.reviewed=true; });
  renderClips();
  await Promise.all(all.map(c=>refillCaptions(c, false)));
  renderClips();
};

function wireDrop(dropId, inputId, kind){
  const drop = document.getElementById(dropId), input = document.getElementById(inputId);
  drop.onclick = ()=> input.click();
  input.onchange = ()=>{ const f = sortFiles([...input.files]); input.value=''; if(f.length) addFiles(f, kind); };
  ['dragenter','dragover'].forEach(e=>drop.addEventListener(e, ev=>{ ev.preventDefault(); drop.classList.add('over'); }));
  ['dragleave','drop'].forEach(e=>drop.addEventListener(e, ev=>{ ev.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', ev=>{
    const f = sortFiles([...ev.dataTransfer.files].filter(x=>/\.(mp4|mov|m4v)$/i.test(x.name)));
    if(f.length) addFiles(f, kind);
  });
}
wireDrop('dropOpen','fileOpen','opening');
wireDrop('dropAns','fileAns','answers');
renderClips();

const caps = document.getElementById('caps'), sent = document.getElementById('sent');
caps.oninput = ()=>{
  const n = splitCaps(caps.value).length;
  sent.textContent = n+' sentence'+(n===1?'':'s')+' detected (need '+NUM_Q+', one per question)';
  sent.className = 'hint '+(n===NUM_Q ? 'ok' : 'warn');
};
caps.oninput();

document.getElementById('add').onclick = async ()=>{
  if(busy) return;
  const name = document.getElementById('name').value.trim();
  if(!name) return alert('Type the candidate name first.');
  if(!ansIds.length) return alert('Select the answer clips first.');
  const nCaps = splitCaps(caps.value).length;
  if(ansIds.length !== NUM_Q &&
     !confirm(ansIds.length+' answer clips (expected '+NUM_Q+', one per question card). Extra clips beyond the cards are not used; missing ones leave cards without an answer. Continue?')) return;
  if(openIds.length !== 3 &&
     !confirm(openIds.length+' intro clips (usually 3). Continue?')) return;
  if(nCaps !== ansIds.length &&
     !confirm(nCaps+' sentences but '+ansIds.length+' clips. Sentence i goes to clip i, '
              +'extras are dropped and missing ones stay blank. Continue?')) return;

  busy = true;
  const btn = document.getElementById('add');
  btn.disabled = true; btn.textContent = 'Preparing...';
  const pack = ids => ids.map(id=>({id:id, in:clips[id].inS, out:clips[id].outS,
                                    caption:(clips[id].capText||'')}));
  try{
    const r = await fetch('/api/confirm', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name:name, paragraph:caps.value,
                            opening:pack(openIds), answers:pack(ansIds)})});
    if(!r.ok) throw new Error(await r.text());
    openIds.concat(ansIds).forEach(id=>{
      if(clips[id]) URL.revokeObjectURL(clips[id].url);
      delete clips[id];
    });
    openIds = []; ansIds = [];
    document.getElementById('name').value=''; caps.value=''; caps.oninput();
    renderClips();
  }catch(err){ alert('Something went wrong: '+err.message); }
  finally{ busy=false; btn.textContent='Confirm & Make Video'; updateGate(); refresh(); }
};

document.getElementById('clear').onclick = ()=>{
  openIds.concat(ansIds).slice().forEach(id=>dropClip(id));
  document.getElementById('name').value=''; caps.value=''; caps.oninput();
};
document.getElementById('openOut').onclick = ()=> fetch('/api/openfolder',{method:'POST'});
document.getElementById('clearDone').onclick = async ()=>{
  await fetch('/api/jobs/clear-completed', {method:'POST'});
  refresh();
};
async function jobAction(action, id){
  const r = await fetch('/api/jobs/'+action, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id:id})});
  if(!r.ok) alert(await r.text());
  refresh();
}
const STATE_LABEL = {preparing:'Preparing', queued:'Queued', rendering:'Rendering', cleaning_audio:'Cleaning audio',
                     completed:'Completed', failed:'Failed', interrupted:'Interrupted'};

/* ------------------------------------------------- clean audio only */
let cleanBusy = false;

async function loadOutFiles(){
  try{
    const d = await (await fetch('/api/outfiles')).json();
    cleanBusy = d.busy;
    const el = document.getElementById('outList');
    if(!d.files.length){
      el.innerHTML = '<div class="empty">no videos in the output folder yet</div>';
      return;
    }
    el.innerHTML = d.files.map(f=>{
      let tag = '';
      if(f.is_clean) tag = '<span class="st cut">cleaned copy</span>';
      else if(f.has_clean) tag = '<span class="st cut">already has a cleaned copy</span>';
      else tag = '<span class="st">' + f.size_mb + ' MB</span>';
      const btn = f.is_clean ? ''
        : '<button class="ghost mini" ' + (d.busy ? 'disabled' : '')
          + ' onclick="cleanExisting(' + JSON.stringify(f.name).replace(/"/g,'&quot;') + ')">'
          + (f.has_clean ? 'Clean again' : 'Clean audio') + '</button>';
      return '<div class="clip"><span class="nm" style="max-width:230px" title="'
        + esc(f.name) + '">' + esc(f.name) + '</span>' + tag + btn + '</div>';
    }).join('');
  }catch(e){}
}
async function cleanExisting(name){
  const r = await fetch('/api/clean', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:name})});
  if(!r.ok) return alert(await r.text());
  document.getElementById('cleanNote').textContent =
    'Cleaning ' + name + '. Watch the build log below.';
  document.getElementById('cleanNote').className = 'hint warn';
  loadOutFiles();
}
document.getElementById('refreshOut').onclick = loadOutFiles;

function wireCleanDrop(){
  const drop = document.getElementById('dropClean');
  const input = document.getElementById('fileClean');
  const note = document.getElementById('cleanNote');
  const send = async (file)=>{
    if(!file) return;
    if(cleanBusy) return alert('Something is already being cleaned, give it a moment.');
    const bar = document.getElementById('bar2'), fill = bar.firstElementChild;
    bar.style.display = 'block';
    note.className = 'hint warn';
    note.textContent = 'Uploading ' + file.name + '...';
    try{
      const r = await upload('/api/cleanupload?name=' + encodeURIComponent(file.name), file,
                             loaded=>{ fill.style.width = (loaded/file.size)*100 + '%'; });
      note.textContent = 'Cleaning ' + file.name
                       + '. It will appear in the output folder as ' + r.name + '.';
    }catch(e){
      note.textContent = 'Upload failed: ' + e.message;
    }finally{
      setTimeout(()=>{ bar.style.display='none'; fill.style.width='0'; }, 500);
      input.value = '';
      loadOutFiles();
    }
  };
  drop.onclick = ()=> input.click();
  input.onchange = ()=> send(input.files[0]);
  ['dragenter','dragover'].forEach(e=>drop.addEventListener(e, ev=>{
    ev.preventDefault(); drop.classList.add('over'); }));
  ['dragleave','drop'].forEach(e=>drop.addEventListener(e, ev=>{
    ev.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', ev=>{
    const f = [...ev.dataTransfer.files].filter(x=>/\.(mp4|mov|m4v|mkv)$/i.test(x.name))[0];
    send(f);
  });
}
wireCleanDrop();
loadOutFiles();
setInterval(loadOutFiles, 4000);

const logEl = document.getElementById('log');
logEl.addEventListener('scroll', ()=>{
  autoscroll = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
});
async function refresh(){
  try{
    const s = await (await fetch('/api/state?since='+since)).json();
    if(s.lines.length){
      if(logEl.textContent==='Ready.') logEl.textContent='';
      logEl.textContent += s.lines.join('\n')+'\n';
      since = s.next;
      if(autoscroll) logEl.scrollTop = logEl.scrollHeight;
    }
    document.getElementById('queue').innerHTML = s.queue.map(q=>{
      const busyNow = ['preparing','rendering','cleaning_audio'].includes(q.status);
      const retry = (q.status==='failed'||q.status==='interrupted')
        ? '<button class="ghost mini" onclick="jobAction(\'retry\','+JSON.stringify(String(q.id))+')">Retry</button>' : '';
      const remove = busyNow ? '' : '<button class="x" title="remove from the queue" onclick="jobAction(\'remove\','+JSON.stringify(String(q.id))+')">&times;</button>';
      const out = q.status==='completed' && (q.clean_output||q.output)
        ? '<span class="out">Saved: '+esc(q.clean_output||q.output)+(q.cleanup_error ? '  ('+esc(q.cleanup_error)+')' : '')+'</span>' : '';
      const err = q.error ? '<span class="err">'+esc(q.error)+'</span>' : '';
      return '<li><span class="nm">'+esc(q.name)+'</span>'
        + '<span class="pill '+q.status+'">'+(STATE_LABEL[q.status]||q.status)+'</span>'
        + '<span class="meta">'+q.n_open+' intro, '+q.n_ans+' clips, '+q.n_cap+' captions</span>'
        + retry + remove + out + err + '</li>';
    }).join('');
    document.getElementById('qEmpty').style.display = s.queue.length ? 'none':'block';
    const wn = document.getElementById('workerNote');
    const waiting = s.queue.filter(q=>q.status==='queued').length;
    wn.textContent = s.building ? ('Working on '+(s.worker_job_name||'a video')+(waiting ? ', '+waiting+' waiting' : '')+'.')
                                : (waiting ? 'Starting the next video...' : 'The worker is idle. Confirmed videos start automatically.');
    document.getElementById('outPath').textContent = 'Output folder: '+s.output_folder;
    document.getElementById('ver').textContent = 'v'+(s.version||'?');
    if(s.version && s.version !== PAGE_VERSION && !reloadedForVersion){
      // this tab was loaded from an older run of the studio; fetch the new page
      reloadedForVersion = true;
      location.reload();
      return;
    }
    ENGINE = s.engine; PRE_ROLL = s.pre_roll; POST_ROLL = s.post_roll; MAXCHARS = s.max_line_chars;
    const en = document.getElementById('engineNote');
    en.textContent = s.engine==='off' ? ('Auto captions are off: '+s.engine_note)
                                      : ('Auto captions on, '+s.engine_note);
    en.style.color = s.engine==='off' ? 'var(--bad)' : 'var(--muted)';
    if(s.vocabulary && !document.getElementById('vocab').value)
      document.getElementById('vocab').value = s.vocabulary;
  }catch(e){}
}
refresh();
setInterval(refresh, 900);
</script>
</body>
</html>
"""


# --------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json; charset=utf-8")

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else "{}"
        return json.loads(raw or "{}")

    # ------------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE.replace("__PAGE_VERSION__", VERSION),
                              "text/html; charset=utf-8")

        if u.path == "/api/state":
            since = int((q.get("since") or ["0"])[0])
            with LOCK:
                lines = LOG[since:]
                nxt = len(LOG)
                queue = [job_public(c) for c in JOBS]
                cur = next((c for c in JOBS if c.get("id") == WORKER.get("job")), None)
            try:
                out = load_config()["paths"]["output_folder"]
            except Exception:
                out = "(config.json unreadable)"
            tc = transcribe_cfg()
            dn = denoise_cfg()
            return self._json({"lines": lines, "next": nxt, "queue": queue,
                               "building": WORKER["busy"], "worker_stage": WORKER["stage"],
                               "worker_job_name": cur.get("name") if cur else None, "output_folder": out,
                               "engine": ENGINE["name"], "engine_note": ENGINE["note"],
                               "pre_roll": tc["pre_roll_seconds"],
                               "post_roll": tc["post_roll_seconds"],
                               "max_line_chars": tc["max_line_chars"],
                               "vocabulary": tc["vocabulary"],
                               "app": "rhc-studio",
                               "version": VERSION,
                               "denoise": dn["enabled"],
                               "denoise_keep": dn["keep_original"]})

        if u.path == "/api/jobs":
            with LOCK:
                jobs = [job_public(c) for c in JOBS]
            try:
                out = load_config()["paths"]["output_folder"]
            except Exception:
                out = ""
            return self._json({"jobs": jobs, "worker": dict(WORKER), "output_folder": out, "engine": ENGINE,
                               "denoise": denoise_cfg(), "version": VERSION, "app": "rhc-studio"})

        if u.path == "/api/clips":
            ids = [int(x) for x in (q.get("ids") or [""])[0].split(",")
                   if x.strip().isdigit()]
            out = []
            for i in ids:
                c = RAW.get(i)
                if c:
                    out.append({"id": i, "status": c["status"],
                                "duration": c.get("duration", 0),
                                "silences": c.get("silences", []),
                                "has_audio": bool(c.get("has_audio")),
                                "sound_start": c.get("sound_start", 0.0),
                                "sound_end": c.get("sound_end", 0.0),
                                "weak": bool(c.get("weak")),
                                "t_status": c.get("t_status", "waiting"),
                                "t_error": c.get("t_error", ""),
                                "segments": c.get("segments", [])})
            return self._json({"clips": out})

        if u.path == "/api/outfiles":
            return self._json({"files": list_output_videos(),
                               "busy": DN_BUSY[0],
                               "keep_original": denoise_cfg()["keep_original"]})

        if u.path == "/api/peaks":
            c = RAW.get(int((q.get("id") or ["0"])[0]))
            if not c:
                return self._send(404, "unknown clip")
            return self._json({"peaks": c.get("peaks", []),
                               "levels": c.get("levels", {}),
                               "has_audio": bool(c.get("has_audio"))})

        return self._send(404, "not found")

    # ------------------------------------------------------------ POST
    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/clip":
                kind = (q.get("kind") or [""])[0]
                if kind not in ("opening", "answers"):
                    return self._send(400, "bad clip kind")
                ext = clean_ext((q.get("ext") or [".mp4"])[0])
                name = os.path.basename(unquote((q.get("name") or ["clip"])[0]))
                cid = new_id()
                os.makedirs(RAW_ROOT, exist_ok=True)
                path = os.path.join(RAW_ROOT, "raw%d%s" % (cid, ext))
                left = int(self.headers.get("Content-Length") or 0)
                with open(path, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(1024 * 512, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                clip = {"id": cid, "kind": kind, "name": name, "ext": ext,
                        "path": path,
                        "status": "analysing", "duration": 0,
                        "silences": [], "peaks": [], "has_audio": False,
                        "sound_start": 0.0, "sound_end": 0.0, "levels": {},
                        "weak": False,
                        "t_status": "waiting", "segments": [], "t_error": "",
                        "hint": unquote((q.get("hint") or [""])[0])}
                RAW[cid] = clip
                threading.Thread(target=analyze, args=(clip,), daemon=True).start()
                return self._json({"id": cid})

            if u.path == "/api/retranscribe":
                d = self._read_json()
                c = RAW.get(int(d.get("id") or 0))
                if not c:
                    return self._send(400, "unknown clip")
                if c["kind"] != "answers":
                    return self._send(400, "intro b-rolls are not transcribed")
                if ENGINE["name"] == "off":
                    return self._send(400, "no speech engine available")
                c["hint"] = (d.get("hint") or "").strip()
                c["segments"] = []
                c["t_error"] = ""
                c["t_status"] = "queued"
                TJOBS.put(c)
                return self._send(200, "ok")

            if u.path == "/api/clean":
                if DN_BUSY[0]:
                    return self._send(400, "already cleaning something, one moment")
                d = self._read_json()
                name = os.path.basename(d.get("name") or "")
                if not name:
                    return self._send(400, "no file given")
                out_dir = load_config()["paths"]["output_folder"]
                path = os.path.join(out_dir, name)
                if not os.path.exists(path):
                    return self._send(400, "that file is not in the output folder")
                threading.Thread(target=clean_job, args=(path,), daemon=True).start()
                return self._send(200, "ok")

            if u.path == "/api/cleanupload":
                if DN_BUSY[0]:
                    return self._send(400, "already cleaning something, one moment")
                raw_name = os.path.basename(unquote((q.get("name") or ["video.mp4"])[0]))
                stem, ext = os.path.splitext(raw_name)
                if ext.lower() not in (".mp4", ".mov", ".m4v", ".mkv"):
                    ext = ".mp4"
                stem = re.sub(r'[<>:"/\\|?*]', "", stem).strip() or "video"
                os.makedirs(RAW_ROOT, exist_ok=True)
                path = os.path.join(RAW_ROOT, stem + ext)
                left = int(self.headers.get("Content-Length") or 0)
                with open(path, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(1024 * 512, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                out_dir = load_config()["paths"]["output_folder"]
                cfg = denoise_cfg()
                cfg["keep_original"] = True     # the real original is on their disk
                threading.Thread(target=clean_job,
                                 args=(path,),
                                 kwargs={"move_to": out_dir, "cleanup_src": True,
                                         "cfg": cfg},
                                 daemon=True).start()
                return self._json({"name": stem + cfg["clean_suffix"] + ext})

            if u.path == "/api/fitlines":
                d = self._read_json()
                lines = fit_caption_lines(d.get("text") or "")
                fs, max_w, _path = caption_metrics()
                return self._json({"lines": lines, "font_size": fs, "max_w": max_w})

            if u.path == "/api/clip/drop":
                d = self._read_json()
                c = RAW.pop(int(d.get("id") or 0), None)
                if c:
                    try:
                        os.remove(c["path"])
                    except Exception:
                        pass
                return self._send(200, "ok")

            if u.path in ("/api/stage", "/api/confirm"):
                d = self._read_json()
                name = (d.get("name") or "").strip()
                if not name:
                    return self._send(400, "candidate name is required")
                plan = {"opening": d.get("opening") or [],
                        "answers": d.get("answers") or []}
                if not plan["answers"]:
                    return self._send(400, "no answer clips")
                ids = [int(x.get("id", 0)) for x in plan["opening"] + plan["answers"]]
                missing = [i for i in ids if i not in RAW]
                if missing:
                    return self._send(400, "some clips are no longer available; add them again")
                dup = [c for c in JOBS if c.get("name", "").lower() == name.lower()
                       and c.get("status") in ("preparing", "queued", "rendering", "cleaning_audio")]
                if dup and not d.get("allow_duplicate"):
                    return self._send(409, "a video for %s is already queued or being made; wait for it or give this one another name" % name)
                caps = split_captions(d.get("paragraph") or "")
                stage, ans_dir, cap_dir, opn_dir = make_stage(name)
                for i, part in enumerate(plan["answers"], 1):
                    own = (part.get("caption") or "").strip()
                    if own:
                        lines = [ln.strip() for ln in own.splitlines() if ln.strip()]
                    else:
                        one = caps[i - 1] if i - 1 <= len(caps) - 1 else ""
                        lines = [one] if one else []
                    with open(os.path.join(cap_dir, "q%d.txt" % i), "w",
                              encoding="utf-8") as f:
                        f.write("\n".join(lines) + ("\n" if lines else ""))
                item = {"id": new_job_id(), "name": name, "stage_dir": stage, "stage": "prepare",
                        "answers": ans_dir, "captions": cap_dir, "opening": opn_dir,
                        "n_open": len(plan["opening"]), "n_ans": len(plan["answers"]),
                        "n_cap": sum(1 for p2 in plan["answers"]
                                     if (p2.get("caption") or "").strip()) or len(caps),
                        "status": "preparing", "created_at": now_iso(), "updated_at": now_iso(),
                        "attempts": 0, "error": None, "output": None, "clean_output": None,
                        "inputs": {"opening": [{"name": RAW[int(x["id"])]["name"], "in": x.get("in"), "out": x.get("out")} for x in plan["opening"]],
                                   "answers": [{"name": RAW[int(x["id"])]["name"], "in": x.get("in"), "out": x.get("out")} for x in plan["answers"]]}}
                with LOCK:
                    JOBS.append(item)
                save_jobs()
                threading.Thread(target=prepare_worker, args=(item, plan),
                                 daemon=True).start()
                return self._json({"id": item["id"], "status": "preparing"})

            if u.path in ("/api/remove", "/api/jobs/remove"):
                d = self._read_json()
                item = find_job(d.get("id"))
                if item:
                    if item["status"] in ("preparing", "rendering", "cleaning_audio"):
                        return self._send(400, "that one is busy right now")
                    with LOCK:
                        JOBS.remove(item)
                    save_jobs()
                    if item.get("status") != "completed" and item.get("stage_dir"):
                        shutil.rmtree(item["stage_dir"], ignore_errors=True)
                    log_write("Removed from the queue: %s (finished videos stay in the output folder)" % item["name"])
                return self._send(200, "ok")

            if u.path == "/api/jobs/retry":
                d = self._read_json()
                item = find_job(d.get("id"))
                if not item:
                    return self._send(404, "unknown job")
                if item["status"] not in ("failed", "interrupted"):
                    return self._send(400, "only failed or interrupted videos can be retried")
                if not os.path.isdir(item.get("answers") or ""):
                    return self._send(400, "the prepared clips for this video are gone; add the candidate again")
                set_job(item, status="queued", error=None, resume_stage=item.get("resume_stage"))
                log_write("Retry queued: %s" % item["name"])
                WAKE.set()
                return self._send(200, "ok")

            if u.path == "/api/jobs/cancel":
                d = self._read_json()
                item = find_job(d.get("id"))
                if not item or item["status"] != "queued":
                    return self._send(400, "only a waiting video can be cancelled")
                set_job(item, status="failed", error="Cancelled before it started.")
                return self._send(200, "ok")

            if u.path == "/api/jobs/clear-completed":
                with LOCK:
                    JOBS[:] = [c for c in JOBS if c.get("status") != "completed"]
                save_jobs()
                return self._send(200, "ok")

            if u.path == "/api/build":
                # Kept for old pages: videos now start by themselves once confirmed.
                WAKE.set()
                return self._send(200, "ok")

            if u.path == "/api/openfolder":
                try:
                    out = load_config()["paths"]["output_folder"]
                    os.makedirs(out, exist_ok=True)
                    if os.name == "nt":
                        os.startfile(out)
                    else:
                        subprocess.Popen(["xdg-open", out])
                except Exception as e:
                    log_write("Could not open the output folder: %s" % e)
                return self._send(200, "ok")

        except Exception as e:
            return self._send(500, "server error: %s" % e)

        return self._send(404, "not found")

    def _find(self, cid):
        return find_job(cid)


def clean_one_file(path):
    """python interview_studio_web.py --denoise "C:\\path\\to\\video.mp4" """
    if not os.path.exists(path):
        sys.exit("File not found: %s" % path)
    os.makedirs(RAW_ROOT, exist_ok=True)
    cfg = denoise_cfg()
    print("Cleaning the audio on %s ..." % os.path.basename(path))
    try:
        clean, engine = denoise_video(path, cfg)
    except Exception as e:
        sys.exit("Failed: %s" % e)
    print("Done with %s" % engine)
    if cfg["keep_original"]:
        print("  original : %s" % path)
        print("  cleaned  : %s" % clean)
    else:
        print("  cleaned in place: %s" % clean)


def _other_instance(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/state?since=0" % port, timeout=2) as r:
            other = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    return other if other.get("app") == "rhc-studio" else None


def clean_raw_folder():
    """Remove leftover uploads that no job refers to. Prepared clips live in the job folders and are kept."""
    if not os.path.isdir(RAW_ROOT):
        os.makedirs(RAW_ROOT, exist_ok=True)
        return
    for f in os.listdir(RAW_ROOT):
        p = os.path.join(RAW_ROOT, f)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except Exception:
            pass


def serve(open_browser=True):
    """Start the studio (blocking). Used by main() and by the hub when it runs the studio in-process."""
    if not os.path.exists(BUILDER):
        sys.exit("build_interview.py not found next to this file.")
    ensure_config()
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit("%s not found on PATH. Put ffmpeg.exe and ffprobe.exe in the hub's Tools\\ffmpeg folder." % tool)

    port = PORT
    srv = None
    while port < PORT + 20:
        other = _other_instance(port)
        if other:
            # Another copy already serves this port: never start a second one and never touch its files.
            print("=" * 52)
            print("  The studio is ALREADY RUNNING at http://127.0.0.1:%d/  (version %s)" % (port, other.get("version")))
            print("  Not starting a second copy.")
            print("=" * 52)
            if open_browser:
                webbrowser.open("http://127.0.0.1:%d/" % port)
            return
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    if srv is None:
        sys.exit("Could not find a free port for the Video Studio.")

    # Only now, knowing we are the single running copy, recover the queue and tidy temporary uploads.
    load_jobs()
    clean_raw_folder()
    ENGINE["name"], ENGINE["note"] = pick_engine()
    threading.Thread(target=transcribe_worker, daemon=True).start()
    start_worker()

    url = "http://127.0.0.1:%d/" % port
    print("=" * 52)
    print("  RHC Interview Studio %s" % VERSION)
    print("  %s" % url)
    print("  Data folder: %s" % DATA_ROOT)
    print("  Captions: %s" % ENGINE["note"])
    _dn = denoise_cfg()
    if _dn["enabled"]:
        print("  Denoise engine: %s" % ("DeepFilterNet (%s)" % os.path.basename(df_binary_path()) if df_binary_path() else "ffmpeg filter"))
    print("  Audio clean up: %s" % ("off" if not _dn["enabled"] else ("on, keeping both files" if _dn["keep_original"] else "on, replacing the original")))
    waiting = sum(1 for j in JOBS if j.get("status") == "queued")
    if waiting:
        print("  %d video(s) were waiting in the queue and will be made now." % waiting)
    print("=" * 52)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--denoise":
        ensure_config()
        return clean_one_file(sys.argv[2])
    serve(open_browser=os.environ.get("RELAY_VIDEO_NO_BROWSER") != "1")


if __name__ == "__main__":
    main()
