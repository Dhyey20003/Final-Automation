# Changes in Relay Employee Hub 2.0

The Employee Hub web application is now the single hub for employees, deployment posts and
interview videos. The separate Tkinter dashboard and desktop Post Studio are retired; the
Video Studio keeps its browser interface and rendering pipeline and is started by the hub.

## One launcher

* `Start Relay Employee Hub.bat` (or the desktop shortcut) starts the hub and the Video Studio
  service, then opens the browser. Running it again only opens the browser (single instance).
* No terminal, no manual server starts. Startup problems are shown in a message box and in
  `Data\logs\hub.log`; the Video Studio writes `Data\logs\video_studio.log`.
* All paths are relative to the program folder and the data folder. Nothing points at one
  person's desktop any more. `Tools\ffmpeg` is the place for FFmpeg.
* Settings > About lists every dependency and bundled asset with a real presence check.

## Navigation

* **Posts** (new): deployment post projects, clients and logos, email requests and replies, backfill.
* **Videos** (new): Video Studio status and queue, "Open Video Studio" button.
* **Import Data > Screenshots** (new): OCR import with review.
* Existing pages, data, imports, exports, tasks, backups and permissions are unchanged.
  New permissions: posts.view, posts.manage, posts.email, videos.view, videos.operate
  (editors get all of them, viewers get view only).

## Screenshot import

* Paste (Ctrl+V), drag and drop or choose screenshots. Text is read with Tesseract OCR
  (local). Labelled lines (`Designation: ...`), table rows and the old `Client - Name` layout
  are understood; a review screen shows exactly what was read, with the OCR text editable.
* Employees are identified by RHC ID, official email, full name, or unique first and last name.
  Ambiguous names show the candidates; nothing is guessed.
* Only fields present in the picture change. Name, email and RHC ID changes always need a tick.
  Designation, client and department changes on a clearly identified employee apply after one
  click. Repeated imports are "unchanged" and create nothing.
* Every screenshot import is an import batch: the picture is kept, each field change is logged,
  and it can be undone from Import history.

## Photos

* `John Smith.jpg` matches `John Michael Smith` when exactly one employee has that first and
  last name (capitalisation, spaces, underscores and separators are ignored). Several possible
  people, or an exact name shared by two people, are left for manual choice.
* Upload summary line, Crop button on each file (adds a cropped copy), everything stored by
  employee ID as before (renaming an employee keeps the photo).

## Posts

* Saving an employee with Assignment status "Client" and a client name creates one post project
  for that deployment. Saving again changes nothing. A new client or a new start date is a new
  deployment with its own project; spelling corrections of the client name move the open
  project instead of duplicating it. Spreadsheet imports never create posts; the Backfill tab
  offers them explicitly.
* The preview renders by itself when name, designation, photo and logo are available. Missing
  items are listed on the card and the draft completes automatically when they arrive (photo
  upload, designation edit, logo upload or email reply).
* Editor with live preview: display name, designation, client display name, pronoun,
  announcement text, photo choice and crop, logo choice and fit, text sizes and positions.
  Save draft, Regenerate, Export PNG (versioned filename), Copy image, Copy text (name bold),
  Mark as published, Unpublish, version history with restore.
* Post edits never change the employee record. Fields edited in a post stop following the record
  and a note appears when the record changes later. "Follow the record again" resets a field.
* Every project has a unique ID; exports live in `Data\exports\posts\<project id>\v001_...png`.
  Publishing copies the picture to `published_vNNN.png`; later edits are new draft versions.

## Clients and logos

* Clients get stable IDs. One logo per client, shared by every post for that client, with full
  version history. A logo is replaced only after the new file is written and validated.

## Email requests and replies (classic Outlook)

* "Request by email" opens a dialog with the recipient (official email), subject and body for
  review. Only what is missing is asked for. A reference `[POSTREQ-...]` is added to the subject.
* Sending is recorded separately from everything that follows; a send that succeeded is never
  shown as unsent. Repeat sends within 24 hours need an explicit reminder tick.
* "Check replies now" matches by reference, then Outlook conversation, then sender email.
  Clear matches with one usable logo are applied automatically; the rest wait for review with
  attachment previews and the reason each rejected attachment was rejected. Any attachment can
  be chosen by hand. A designation from a reply goes to that one post (optionally also to that
  one employee), never to other people at the same client. Older replies never overwrite newer
  accepted information. Failed messages are listed with their error and can be re-checked.
* Dry-run mode (non-Windows or chosen in Settings > Email) writes outgoing mail to
  `Data\exports\dry_run_outbox` and reads test replies from `dry_run_outbox\inbox\*.json`.

## Video Studio

* "Add to batch" and "Build all videos" are replaced by one **Confirm & Make Video** button:
  validate, cut the clips, queue, render, clean the audio, save. A second video confirmed while
  one is being made waits and starts by itself. One render at a time; duplicate names in flight
  are refused.
* States: Preparing, Queued, Rendering, Cleaning audio, Completed, Failed, Interrupted. Failed
  jobs show their error and have Retry; a failed optional audio clean up keeps the rendered
  video and says so. The queue is saved to `Data\video\jobs.json` and recovered on restart.
  Temporary uploads are only removed after the studio has confirmed it is the single running copy.
* Config lives in `Data\video\config.json` (created from the bundled default) with `${DATA}`
  and `${ASSETS}` placeholders. Cards, fonts and deep-filter.exe are under `automations\video`.
* Captions, trimming, transcription, transitions and the renderer are unchanged.

## Migration

* Settings > Posts and OCR > "Bring in data from the old Post Studio" imports saved logos,
  headshots, `log.xlsx` posts and the Uploaded ticks (as published). Uncertain name matches are
  listed for a decision. The old folder is never modified.
* Database schema 2 adds tables only; existing tables are untouched. A pre-migration backup is
  taken automatically.
