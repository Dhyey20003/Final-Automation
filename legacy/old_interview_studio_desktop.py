#!/usr/bin/env python3
"""
Relay Human Cloud - Interview Studio (GUI)
==========================================

Easy front-end for build_interview.py. No more naming conventions.

WHAT IT DOES
------------
For each candidate you:
  1. Type their display name (what the intro pill shows, e.g. "Vesta A.")
  2. Click "Select answer clips" and pick their 8 videos. Files can be
     named ANYTHING - 1.mp4, 2.mp4 ... is easiest. They're ordered by
     the numbers in their filenames.
  3. Paste the caption paragraph in the big box. It is split at every
     full stop: sentence 1 -> q1, sentence 2 -> q2, ... sentence 8 -> q8.
  4. Click "Add to batch".

Queue as many candidates as you want, then click BUILD ALL VIDEOS.
Each video renders one after another using your existing pipeline and
lands in the normal output folder. Cards, openings, fonts, config all
stay exactly as they are.

RUN
---
    python interview_studio.py     (or double-click run_studio.bat)

Keep this file in the same folder as build_interview.py and config.json.
"""

import os
import re
import sys
import json
import shutil
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.join(HERE, "build_interview.py")
CONFIG_PATH = os.path.join(HERE, "config.json")
BATCH_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "Raw_Files", "Video", "Batch"))

NUM_QUESTIONS = 8


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_folder_name(name):
    s = re.sub(r'[<>:"/\\|?*]', "", name).strip().rstrip(". ")
    return s or "unnamed"


def numeric_key(path):
    """Order files by the first number in their filename; fallback alpha."""
    base = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"(\d+)", base)
    return (0, int(m.group(1))) if m else (1, base.lower())


def split_captions(paragraph):
    """
    Split a paragraph into sentences at . ? ! keeping the punctuation.
    Sentence i becomes the caption for q(i+1).
    """
    text = " ".join(paragraph.split())   # collapse whitespace/newlines
    if not text:
        return []
    parts = re.findall(r"[^.?!]+[.?!]?", text)
    return [p.strip() for p in parts if p.strip()]


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RHC Interview Studio")
        self.geometry("880x680")
        self.minsize(760, 600)

        self.selected_files = []
        self.selected_openings = []
        self.queue = []          # list of dicts: name, staged answers/captions/opening dirs
        self.building = False

        self._build_ui()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm_top = ttk.LabelFrame(self, text="  Add a candidate  ")
        frm_top.pack(fill="x", **pad)

        row1 = ttk.Frame(frm_top); row1.pack(fill="x", padx=8, pady=4)
        ttk.Label(row1, text="Candidate name (shown in intro):").pack(side="left")
        self.ent_name = ttk.Entry(row1, width=30)
        self.ent_name.pack(side="left", padx=8)

        row2 = ttk.Frame(frm_top); row2.pack(fill="x", padx=8, pady=4)
        ttk.Button(row2, text="Select intro b-rolls (3)",
                   command=self.pick_openings).pack(side="left")
        self.lbl_open = ttk.Label(row2, text="no intro clips selected")
        self.lbl_open.pack(side="left", padx=10)

        row3 = ttk.Frame(frm_top); row3.pack(fill="x", padx=8, pady=4)
        ttk.Button(row3, text="Select answer clips (8)",
                   command=self.pick_files).pack(side="left")
        self.lbl_files = ttk.Label(row3, text="no clips selected")
        self.lbl_files.pack(side="left", padx=10)

        ttk.Label(frm_top, text="Captions paragraph (split at every full stop; "
                                "sentence 1 = q1, sentence 2 = q2, ...):"
                  ).pack(anchor="w", padx=8)
        self.txt_caps = tk.Text(frm_top, height=6, wrap="word")
        self.txt_caps.pack(fill="x", padx=8, pady=4)
        self.txt_caps.bind("<KeyRelease>", self.update_sentence_count)
        self.lbl_sent = ttk.Label(frm_top, text="0 sentences detected")
        self.lbl_sent.pack(anchor="w", padx=8)

        ttk.Button(frm_top, text="Add to batch \u2193",
                   command=self.add_to_batch).pack(pady=6)

        frm_q = ttk.LabelFrame(self, text="  Batch queue  ")
        frm_q.pack(fill="both", expand=True, **pad)
        self.lst = tk.Listbox(frm_q, height=6)
        self.lst.pack(fill="both", expand=True, side="left", padx=8, pady=6)
        btns = ttk.Frame(frm_q); btns.pack(side="left", fill="y", padx=4)
        ttk.Button(btns, text="Remove selected",
                   command=self.remove_selected).pack(pady=4)

        self.btn_build = ttk.Button(self, text="\u25B6  BUILD ALL VIDEOS",
                                    command=self.build_all)
        self.btn_build.pack(pady=4)

        frm_log = ttk.LabelFrame(self, text="  Build log  ")
        frm_log.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(frm_log, height=10, state="disabled",
                           bg="#111", fg="#ddd")
        self.log.pack(fill="both", expand=True, padx=8, pady=6)

    def log_write(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------- actions
    def pick_openings(self):
        files = filedialog.askopenfilenames(
            title="Pick this candidate's 3 intro b-rolls (numbers set the order)",
            filetypes=[("Video files", "*.mp4 *.mov *.m4v")])
        if not files:
            return
        self.selected_openings = sorted(files, key=numeric_key)
        self.lbl_open.config(text="%d intro clips selected" % len(self.selected_openings))

    def pick_files(self):
        files = filedialog.askopenfilenames(
            title="Pick the candidate's answer clips (any names; numbers set the order)",
            filetypes=[("Video files", "*.mp4 *.mov *.m4v")])
        if not files:
            return
        self.selected_files = sorted(files, key=numeric_key)
        self.lbl_files.config(text="%d clips selected" % len(self.selected_files))

    def update_sentence_count(self, _evt=None):
        n = len(split_captions(self.txt_caps.get("1.0", "end")))
        ok = "\u2713" if n == NUM_QUESTIONS else "\u26A0"
        self.lbl_sent.config(text="%s %d sentences detected (need %d: one per question)"
                                  % (ok, n, NUM_QUESTIONS))

    def add_to_batch(self):
        name = self.ent_name.get().strip()
        caps = split_captions(self.txt_caps.get("1.0", "end"))
        if not name:
            messagebox.showerror("Missing name", "Type the candidate's name.")
            return
        if len(self.selected_files) != NUM_QUESTIONS:
            if not messagebox.askyesno(
                    "Clip count",
                    "You selected %d clips (expected %d). Continue anyway?"
                    % (len(self.selected_files), NUM_QUESTIONS)):
                return
        if len(caps) != len(self.selected_files):
            if not messagebox.askyesno(
                    "Caption count",
                    "%d sentences but %d clips. Sentence i goes to clip i; "
                    "extras are dropped / missing ones stay blank. Continue?"
                    % (len(caps), len(self.selected_files))):
                return

        if len(self.selected_openings) not in (0, 3):
            if not messagebox.askyesno(
                    "Intro clips",
                    "You selected %d intro clips (usually 3). Continue anyway?"
                    % len(self.selected_openings)):
                return
        if not self.selected_openings:
            if not messagebox.askyesno(
                    "No intro clips",
                    "No intro b-rolls selected for this candidate. The video "
                    "will start directly at question card 1. Continue?"):
                return

        # stage: copy clips + write caption files under batch\<name>\
        stage = os.path.join(BATCH_ROOT, safe_folder_name(name))
        k = 2
        while os.path.exists(stage):
            stage = os.path.join(BATCH_ROOT, "%s_%d" % (safe_folder_name(name), k))
            k += 1
        ans_dir = os.path.join(stage, "answers")
        cap_dir = os.path.join(stage, "captions")
        opn_dir = os.path.join(stage, "opening")
        os.makedirs(ans_dir); os.makedirs(cap_dir); os.makedirs(opn_dir)

        for i, src in enumerate(self.selected_openings, 1):
            ext = os.path.splitext(src)[1].lower()
            shutil.copyfile(src, os.path.join(opn_dir, "open%d%s" % (i, ext)))

        for i, src in enumerate(self.selected_files, 1):
            ext = os.path.splitext(src)[1].lower()
            shutil.copyfile(src, os.path.join(ans_dir, "q%d%s" % (i, ext)))
        for i in range(1, len(self.selected_files) + 1):
            text = caps[i - 1] if i - 1 < len(caps) else ""
            with open(os.path.join(cap_dir, "q%d.txt" % i), "w",
                      encoding="utf-8") as f:
                f.write(text + ("\n" if text else ""))

        self.queue.append({"name": name, "answers": ans_dir,
                           "captions": cap_dir, "opening": opn_dir})
        self.lst.insert("end", "%s   (%d intro, %d clips, %d captions)"
                        % (name, len(self.selected_openings),
                           len(self.selected_files), len(caps)))
        self.log_write("Queued: %s" % name)

        # reset inputs for the next candidate
        self.ent_name.delete(0, "end")
        self.txt_caps.delete("1.0", "end")
        self.selected_files = []
        self.selected_openings = []
        self.lbl_files.config(text="no clips selected")
        self.lbl_open.config(text="no intro clips selected")
        self.update_sentence_count()

    def remove_selected(self):
        sel = list(self.lst.curselection())
        for i in reversed(sel):
            self.lst.delete(i)
            del self.queue[i]

    # -------------------------------------------------------- build
    def build_all(self):
        if self.building:
            return
        if not self.queue:
            messagebox.showinfo("Empty", "Add at least one candidate first.")
            return
        self.building = True
        self.btn_build.config(state="disabled", text="Building... please wait")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        cfg = load_config()
        out_dir = cfg["paths"]["output_folder"]
        total = len(self.queue)
        done = 0
        for item in list(self.queue):
            name = item["name"]
            self.log_write("\n===== Building %s (%d of %d) =====" % (name, done + 1, total))
            cmd = [sys.executable, BUILDER,
                   "--answers", item["answers"],
                   "--captions", item["captions"],
                   "--opening", item["opening"],
                   "--name", name,
                   "--no-prompt"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    stdin=subprocess.DEVNULL, cwd=HERE,
                                    creationflags=(0x08000000 if os.name == "nt" else 0))
            for line in proc.stdout:
                self.log_write(line.rstrip())
            proc.wait()
            if proc.returncode == 0:
                done += 1
                self.log_write("===== %s DONE =====" % name)
            else:
                self.log_write("===== %s FAILED (see log above) =====" % name)

        self.log_write("\nAll builds finished: %d of %d succeeded." % (done, total))
        self.log_write("Videos are in: %s" % out_dir)
        self.building = False
        self.btn_build.config(state="normal", text="\u25B6  BUILD ALL VIDEOS")


if __name__ == "__main__":
    if not os.path.exists(BUILDER):
        print("build_interview.py not found next to this file.")
        sys.exit(1)
    if not os.path.exists(CONFIG_PATH):
        print("config.json not found next to this file.")
        sys.exit(1)
    app = Studio()
    app.mainloop()
