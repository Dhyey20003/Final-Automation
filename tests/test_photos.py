from __future__ import annotations

import io
import zipfile
from pathlib import Path

from conftest import emp, image_bytes

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def batch(api):
    return api.post("/api/photo-batches", {})["id"]


def upload(api, bid, files):
    return api.post(f"/api/photo-batches/{bid}/files", files=[("files", f) for f in files])


def by_name(detail):
    return {c["original_name"]: c for c in detail["candidates"]}


def test_matching_rules(api):
    a = emp(api, "RHC0001", "Asha Patel")
    b = emp(api, "RHC0002", "Ravi Shah")
    n = emp(api, "1234", "Numeric Id")
    bid = batch(api)
    d = upload(api, bid, [("RHC0001_asha.jpg", image_bytes()), ("Ravi Shah.png", image_bytes("PNG")),
                          ("IMG_1234.jpg", image_bytes()), ("1234.jpg", image_bytes(color=(1, 2, 3))), ("random.jpg", image_bytes(color=(9, 9, 9))),
                          ("notes.txt", b"hello"), ("photo.heic", b"x")])
    c = by_name(d)
    assert c["RHC0001_asha.jpg"]["suggestion"]["employee_id"] == a["id"] and c["RHC0001_asha.jpg"]["suggestion"]["certain"]
    # a filename that is exactly one employee's name is a clear match (pre-selected); multiple matches stay manual
    assert c["Ravi Shah.png"]["suggestion"]["employee_id"] == b["id"] and c["Ravi Shah.png"]["suggestion"]["certain"]
    assert not c["IMG_1234.jpg"]["suggestion"]["certain"]  # bare number inside a camera name is only a suggestion
    assert c["1234.jpg"]["suggestion"]["certain"] and c["1234.jpg"]["suggestion"]["employee_id"] == n["id"]
    assert c["random.jpg"]["suggestion"] is None
    assert c["notes.txt"]["status"] == "error" and c["photo.heic"]["status"] == "error"
    # duplicate within the upload is flagged
    d = upload(api, bid, [("copy.jpg", image_bytes())])
    assert any("Duplicate" in f for f in by_name(d)["copy.jpg"]["flags"])


def test_assign_store_versions_and_duplicates(api, ctx):
    a = emp(api, "RHC0001", "Asha Patel")
    bid = batch(api)
    d = upload(api, bid, [("Original Name.jpg", image_bytes())])
    cid = d["candidates"][0]["id"]
    res = api.post(f"/api/photo-batches/{bid}/assign", {"assignments": [{"candidate_id": cid, "employee_id": a["id"]}]})
    pid = res["assigned"][0]["photo_id"]
    with ctx.db() as conn:
        p = conn.execute("SELECT * FROM photos WHERE id = ?", (pid,)).fetchone()
    assert p["original_filename"] == "Original Name.jpg"
    assert p["stored_path"] == f"images/{a['id']}/{a['id']}_p{pid}_v1.jpg"
    assert (ctx.paths.data / p["thumb_path"]).exists()
    # same image again is refused as a duplicate of the current photo
    r = api.post(f"/api/employees/{a['id']}/photo", files={"file": ("again.jpg", image_bytes())}, status=400)
    assert "already the current photo" in r["error"]["message"]
    # replacement keeps the old version
    api.post(f"/api/employees/{a['id']}/photo", files={"file": ("new.png", image_bytes("PNG", (10, 200, 10)))})
    versions = api.get(f"/api/employees/{a['id']}")["photos"]
    assert [v["version_no"] for v in versions] == [2, 1] and versions[0]["is_current"] and not versions[1]["is_current"]
    assert (ctx.paths.data / p["stored_path"]).exists()
    api.get(f"/api/photos/{versions[1]['id']}/file")  # full-size preview of an old version
    # the same image uploaded later is flagged as already stored
    d = upload(api, bid, [("dup.jpg", image_bytes())])
    assert any("already stored" in f for f in by_name(d)["dup.jpg"]["flags"])


def test_zip_upload_is_safe(api, ctx):
    emp(api, "RHC0001", "Zip Person")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("folder/RHC0001.jpg", image_bytes())
        z.writestr("../../evil.jpg", image_bytes(color=(1, 1, 1)))
        z.writestr("/abs/path.jpg", image_bytes(color=(2, 2, 2)))
        z.writestr("__MACOSX/._RHC0001.jpg", b"junk")
        z.writestr("inner.zip", b"PK")
        z.writestr("readme.txt", b"x")
    bid = batch(api)
    d = upload(api, bid, [("photos.zip", buf.getvalue())])
    c = by_name(d)
    assert c["RHC0001.jpg"]["status"] == "pending" and c["RHC0001.jpg"]["suggestion"]["certain"]
    errors = [x for x in d["candidates"] if x["status"] == "error"]
    assert len(errors) == 4
    assert any("Unsafe path" in e["message"] for e in errors)
    assert not (ctx.paths.data.parent / "evil.jpg").exists()
    assert not list(ctx.paths.data.rglob("evil.jpg"))


def test_zip_limits(api):
    api.put("/api/settings/general", {"limits": {"max_zip_entries": 2}})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(3):
            z.writestr(f"{i}.jpg", image_bytes(color=(i, i, i)))
    d = upload(api, batch(api), [("big.zip", buf.getvalue())])
    assert d["candidates"][0]["status"] == "error" and "limit" in d["candidates"][0]["message"]


def test_explicit_mapping_and_sequence_mapping(api):
    e1, e2, e3 = emp(api, "RHC0001", "One A"), emp(api, "RHC0002", "Two B"), emp(api, "RHC0003", "Three C")
    bid = batch(api)
    d = upload(api, bid, [("IMG_1.jpg", image_bytes(color=(1, 50, 90))), ("IMG_2.jpg", image_bytes(color=(2, 60, 90))),
                          ("IMG_3.jpg", image_bytes(color=(3, 70, 90)))])
    res = api.post(f"/api/photo-batches/{bid}/mapping", {"text": "Filename,RHC ID\nIMG_1.jpg, RHC0003\nmissing.jpg,RHC0001"})
    assert res["applied"] == 1 and len(res["problems"]) == 1
    c = by_name(api.get(f"/api/photo-batches/{bid}"))
    assert c["IMG_1.jpg"]["suggestion"]["employee_id"] == e3["id"] and c["IMG_1.jpg"]["suggestion"]["certain"]
    ids = [c["IMG_2.jpg"]["id"], c["IMG_3.jpg"]["id"]]
    prev = api.post(f"/api/photo-batches/{bid}/sequence-preview", {"candidate_ids": ids, "rhc_list": "RHC0001"})
    assert not prev["ok"] and "Counts must match" in prev["problems"][0]
    prev = api.post(f"/api/photo-batches/{bid}/sequence-preview", {"candidate_ids": ids, "rhc_list": "RHC0001\nRHC0002"})
    assert prev["ok"] and prev["pairs"][1]["employee_id"] == e2["id"]
    api.post(f"/api/photo-batches/{bid}/sequence-assign", {"candidate_ids": ids, "rhc_list": "RHC0001\nRHC0002"}, status=422)
    res = api.post(f"/api/photo-batches/{bid}/sequence-assign", {"candidate_ids": ids, "rhc_list": "RHC0001\nRHC0002", "confirm": True})
    assert len(res["assigned"]) == 2
    assert api.get(f"/api/employees/{e1['id']}")["employee"]["photo_id"]


def test_pdf_extraction_flags_and_crop(api):
    names = {}
    import csv

    with open(SAMPLES / "synthetic_initial_roster.csv", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))[1:]
    for r in rows[36:44]:
        e = emp(api, r[0], r[1], r[5])
        names[r[0]] = e["id"]
    bid = batch(api)
    d = upload(api, bid, [("sheet.pdf", (SAMPLES / "synthetic_headshots.pdf").read_bytes())])
    assert len(d["pdfs"]) == 1
    pdf = d["pdfs"][0]
    assert pdf["page_count"] == 2
    assert pdf["pages"][0]["image_count"] == 6
    assert any("scanned" in f.lower() for f in pdf["pages"][1]["flags"])
    pdf_cands = [c for c in d["candidates"] if c["source_kind"] == "pdf"]
    assert len(pdf_cands) == 6
    for c in pdf_cands:
        s = c["suggestion"]
        assert s and not s["certain"], c  # PDF text proximity is only a suggestion
        assert s["employee_id"] == names[s["rhc_id"]]
        assert s["rhc_id"] in c["context_text"]
    api.get(f"/api/photo-batches/{bid}/pdfs/{pdf['id']}/pages/2")
    crop = api.post(f"/api/photo-batches/{bid}/pdfs/{pdf['id']}/crop", {"page_no": 2, "rect": [0.08, 0.1, 0.36, 0.34]})
    assert crop["status"] == "pending" and crop["source_kind"] == "pdf_crop" and crop["width"] > 200
    api.post(f"/api/photo-batches/{bid}/pdfs/{pdf['id']}/crop", {"page_no": 2, "rect": [0.1, 0.1, 0.1005, 0.1]}, status=400)


def test_missing_photos_and_close_batch(api, ctx):
    emp(api, "RHC0001", "No Photo")
    miss = api.get("/api/photos/missing")
    assert [m["rhc_id"] for m in miss] == ["RHC0001"]
    bid = batch(api)
    upload(api, bid, [("x.jpg", image_bytes())])
    api.post(f"/api/photo-batches/{bid}/close")
    assert not (ctx.paths.photo_staging / str(bid)).exists()
