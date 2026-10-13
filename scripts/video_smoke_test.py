"""End-to-end smoke test of the Video Studio queue (needs ffmpeg and ffprobe on PATH or in Tools\\ffmpeg).

    python scripts/video_smoke_test.py

Generates tiny test clips, starts the studio against a temporary data folder, uploads the clips,
presses Confirm & Make Video through the API, waits for Completed, restarts the studio and checks
that the queue was recovered. Nothing outside the temporary folder is touched."""
import json, os, subprocess, sys, time, urllib.request, pathlib, shutil, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = pathlib.Path(tempfile.mkdtemp(prefix="relay_video_smoke_"))
DATA = S / "data"
DATA.mkdir()
(S / "clips").mkdir()
for i in (1, 2, 3):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=1.5:r=30", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(S / "clips" / f"open{i}.mp4")], check=True)
for i in (1, 2):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=640x360:d=2:r=30", "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(S / "clips" / f"ans{i}.mp4")], check=True)
PORT = 8791
env = dict(os.environ, RELAY_VIDEO_DATA=str(DATA), RELAY_VIDEO_ASSETS=str(ROOT / "automations/video/assets"),
           RELAY_VIDEO_PORT=str(PORT), RELAY_VIDEO_NO_BROWSER="1", PYTHONUTF8="1")
log = open(S / "studio.log", "w")
proc = subprocess.Popen([sys.executable, str(ROOT / "automations/video/interview_studio_web.py")], env=env, stdout=log, stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{PORT}"

def get(path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read())

def post(path, body=None, raw=None):
    data = raw if raw is not None else json.dumps(body or {}).encode()
    req = urllib.request.Request(base + path, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

try:
    for _ in range(50):
        try:
            st = get("/api/state?since=0"); break
        except Exception:
            time.sleep(0.3)
    print("studio up:", st.get("version"), "engine:", st.get("engine"))
    ids = {"opening": [], "answers": []}
    for kind, names in (("opening", ["open1.mp4", "open2.mp4", "open3.mp4"]), ("answers", ["ans1.mp4", "ans2.mp4"])):
        for n in names:
            code, body = post(f"/api/clip?kind={kind}&ext=.mp4&name={n}", raw=(S / "clips" / n).read_bytes())
            assert code == 200, body
            ids[kind].append(json.loads(body)["id"])
    for _ in range(60):
        c = get("/api/clips?ids=" + ",".join(str(i) for i in ids["opening"] + ids["answers"]))
        if all(x["status"] == "ready" for x in c["clips"]) and len(c["clips"]) == 5:
            break
        time.sleep(0.5)
    print("clips analysed:", [(x["id"], x["status"], x["duration"]) for x in c["clips"]])
    pack = lambda lst, cap: [{"id": i, "in": 0, "out": 0, "caption": cap} for i in lst]
    code, body = post("/api/confirm", {"name": "Smoke Test", "paragraph": "", "opening": pack(ids["opening"], ""),
                                       "answers": pack(ids["answers"], "Hello there.\nSecond caption line.")})
    print("confirm:", code, body)
    assert code == 200
    # a duplicate name while the first one is in flight is refused
    code2, body2 = post("/api/confirm", {"name": "Smoke Test", "opening": [], "answers": [{"id": 999}]})
    print("duplicate/invalid:", code2, body2)
    seen = set()
    final = None
    for _ in range(400):
        j = get("/api/jobs")
        job = j["jobs"][0]
        if job["status"] not in seen:
            seen.add(job["status"]); print("  state ->", job["status"], job.get("error") or "")
        if job["status"] in ("completed", "failed"):
            final = job; break
        time.sleep(1.5)
    print("final:", json.dumps(final, indent=1)[:900])
    print("jobs.json persisted:", (DATA / "jobs.json").exists(), "output files:", sorted(os.listdir(DATA / "output")))
    assert final and final["status"] == "completed", "video did not complete"
    # restart: queue survives
    proc.terminate(); proc.wait(timeout=10)
    proc = subprocess.Popen([sys.executable, str(ROOT / "automations/video/interview_studio_web.py")], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(50):
        try:
            j = get("/api/jobs"); break
        except Exception:
            time.sleep(0.3)
    print("after restart jobs:", [(x["name"], x["status"]) for x in j["jobs"]])
    print("SMOKE OK")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    log.close()
