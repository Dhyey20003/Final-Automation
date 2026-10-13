# Relay Employee Hub: Operator Guide

## 1. Setup

1. Start the app from the desktop shortcut. It opens in your browser at `http://127.0.0.1:8765/`.
2. First run only: create the administrator account (your work email and a password of at least 10 characters). There are no default passwords.
3. Settings > General: check the locations (Ahmedabad, Vadodara), the timezone (Asia/Kolkata) and how ambiguous dates are read (day first by default).
4. Settings > Email: choose the mode.
   * **Classic Outlook**: the app creates drafts and sends through your own Outlook profile. Requires classic Outlook for Windows.
   * **Manual**: for new Outlook or Outlook on the web. The app prepares the ZIPs and email text; you send them yourself and mark the period as sent.
   * **Dry-run**: for testing. Nothing is sent.
5. Settings > Backups and restore: set the backup folder (an external drive is a good choice) and check that daily backups are on.

The app only works while this computer is on and the app is running. Scheduled sending also needs you to be signed in to Windows with Outlook available.

## 2. Employees

* The table shows active employees by default. Use the chips for Pending review, Left, All current records, Historical only and Archived.
* Search by name, RHC ID or email. Type in the filter row under each column heading. Filters accept text, `=value`, `>value`, `<value`, `empty` and `!empty`.
* Click a column heading to sort. Drag the edge of a heading to resize. **Columns** chooses which columns are shown (saved for you).
* **Edit a cell**: click it and press Enter (or double-click), type, press Enter. If someone else changed the record meanwhile, you see both versions and nothing is overwritten.
* **Paste from Excel**: click the first cell, press Ctrl+V. A preview shows every change and any problems; all rows are saved together or none are.
* **Add employee** opens a form with all fields, including custom fields.
* Click a name to open the profile: all fields you may see, the photo (click for full size), Replace photo, assignment history, change history and photo versions.
* Former employees: set Employment status to Left (with the last working date), then Archive. Nothing is deleted.
* **Export to Excel** exports exactly the current view and columns.

Current bench days = calendar days from the bench start date to today (0 on the start date). It is shown only when the assignment status is Bench.

## 3. Importing spreadsheets

Import Data > New import:

1. Choose what the file is for:
   * **Historical employee data**: past records. New people go into "Historical only", not the active directory. Existing current employees only get blank fields filled.
   * **Complete current employee roster**: the full current list for the locations you declare. It decides who is active.
   * **Partial employee update**: some employees, some fields. Nobody is ever deactivated.
   * **Designation update** / **Selected field update**: only those fields change.
2. Drop the .xlsx or .csv file. (Save .xls or .xlsm files as .xlsx first. Formulas are not run; their saved values are used.)
3. Pick the worksheet and header row, choose or create a mapping template, and match columns to fields. Unmapped columns are ignored.
4. Preview. Review new records, changes (old value crossed out, new value in green), unchanged rows, errors, duplicates, unmatched rows and rows outside the scope.
   * Rows are matched by RHC ID. Matching by exact official email only happens if you tick it. Names are only ever suggestions; use "Link to ..." only if you are sure.
   * Fix blocking errors in the file and upload again, or skip those rows.
   * Blank cells never erase data unless you tick the field under "Blank cells (advanced)".
5. For a complete roster, employees missing from the file are listed. They are only marked Left if you tick them and confirm.
6. Commit. Everything is saved together. Download the results and error report if needed.

**Recommended order for the first load:** import historical data first, then the current roster (with Ahmedabad and Vadodara in scope). Historical records that appear in the roster become current, keeping only the historical fields you approved.

Import history shows every import (file, fingerprint, purpose, template, scope, operator, time and changes). **Undo** reverts an import only where nothing has been changed since; conflicts are listed and never overwritten.

## 4. Photos

Photos > Upload and review > Start a new photo upload. Drop single photos, a whole folder (Choose a folder), ZIP files or PDFs.

* Photos whose filename contains an RHC ID (for example `RHC1234.jpg`) are **pre-selected**.
* A matching name, or an RHC ID printed next to a photo in a PDF, is a **suggestion to confirm**.
* For camera-style names (IMG_1234.jpg) either type the employee on each card, paste a "filename, RHC ID" list, or use **Sequence mapping**: paste the ordered list of RHC IDs, check the side-by-side preview and confirm. File or page order is never assumed.
* Tick the photos you are sure about and click **Assign**. Previous photos are kept as earlier versions.
* PDFs: images and nearby text are extracted. Pages that look scanned are flagged; open the page viewer, drag a rectangle around each photo and add it to the review list.
* **Missing photos** lists active employees without a photo.
* When done, **Finish and close this upload** removes the staged copies.

Facial recognition is never used.

## 5. Tasks and exports

Tasks and Exports shows the **IT headshots** (every 14 days) and **HR headshots** (monthly) tasks.

1. **Task settings** (administrator): schedule (IT anchor date; HR day of month, for example 31 means the last day in shorter months), which employees to include (all active, or only new/changed since the last completed submission), file names, image format, ZIP names, recipients and email text.
2. **Turn schedule on**. Periods appear when due. **Prepare packages now** builds packages for the current period at any time.
3. Open a period to see each city's ZIP, the manifest (who is included, file names, photo versions, who was left out and why).
   * If anyone was left out (no photo, pending review, no location) the period shows **Needs Attention**. Fix and **Regenerate** (a new revision; earlier revisions are kept), or **Accept omissions** with a note.
4. Send:
   * **Create draft in Outlook**: an email with both ZIPs appears in your Drafts. Send it from Outlook; the app confirms it when it appears in Sent Items.
   * **Send now**: Outlook sends it. Status becomes "Submitted to Outlook", then "Confirmed in Sent Items". Neither proves the recipients received it.
   * **Manual mode**: **Prepare ready folder**, attach the ZIPs yourself, then **Mark as sent manually** with a note.
5. **Automatic sending** (administrator): switch on per task after recipients are set, confirming the recipients once. Missed periods, incomplete packages and uncertain results are never sent automatically.
6. **Delivery Uncertain**: the app never resends by itself. Check Outlook's Sent Items and Outbox for the reference shown, then use **Record outcome**.

## 6. Users and access

Settings > Users and access (administrator):

* Roles: Administrator (everything), Editor (employees, imports, photos), Viewer (read-only; export only if ticked), Export Operator (exports and task operations).
* Per user: limit to locations, adjust permissions, hide fields, and set date-of-birth access (full, day and month only, or hidden).
* New users and password resets get a one-time temporary password; the user must choose their own at first sign-in. Accounts are locked for 15 minutes after 5 wrong passwords.
* Settings > Integration tokens: read-only "fetch-only" access for other local tools. Tokens never see date of birth or notes, cannot sign in, and can be revoked.
* Settings > Audit log records changes, imports, exports, account changes and task actions.

Anyone who is a Windows administrator on this computer can read the data folder directly; application permissions cannot prevent that.

## 7. Backup and restore

* **Back up now** creates one verified ZIP with the database, all photo versions, thumbnails and package snapshots. Daily backups run automatically while the app is running (30 kept by default).
* **Restore**: choose a backup from the list or a file, check the summary, type RESTORE. A safety backup of the current data is taken first. Everyone signs in again, and automatic sending stays off until an administrator re-checks each task.

## 8. Moving to another computer

Recommended: back up here, install on the new computer, restore the backup there.
Alternative: stop the app, copy the whole data folder, point the new installation at it (`Choose data folder.bat`), start.
After a move the app switches automatic sending off until an administrator confirms the sender, recipients and task ownership. Never run two copies at once, and never keep the live data folder in OneDrive or SharePoint.

## 9. Office network access (optional)

Settings > Network: tick "Allow access from the office network", list the office network (for example 192.168.1.0/24), save, and restart the app. Allow the port (default 8765) in Windows Firewall for private networks only. Others then open `http://<computer name>:8765/` and sign in with their own accounts. All users share the same central database on this computer.

## 10. Importing from screenshots

Import Data > Screenshots.

1. Paste a screenshot with Ctrl+V (the box is focused when the page opens), drag pictures in, or choose files. Several screenshots can go in one batch.
2. The text is read with Tesseract OCR on this computer. The review screen shows the picture, the text that was read (editable), and one row per employee found:
   * **Will update** (green): the employee is certain (RHC ID, official email, full name, or a unique first and last name) and only designation, client, department or location would change.
   * **Needs your decision** (amber): the name fits several people, the row would change an identity field (name, email, RHC ID), or a designation was only guessed. Choose the employee in the dropdown or search box, correct the text, and tick Apply.
   * **No employee found**: pick the person or skip the row.
   * **No change**: the record already matches.
3. Fix mistakes in the text box (for example add `Designation: Senior Developer`) and press **Re-read from this text**, or edit the fields on the row and press **Re-check with my edits**.
4. **Apply ticked rows**. Only the fields visible in the picture change; everything else is untouched. A new client assignment creates a deployment post. The import appears in Import history and can be undone there.

If Tesseract is not installed the picture is still stored and you can type the details into the rows by hand. Settings > About > Dependencies shows whether OCR is available.

## 11. Posts

Posts lists every deployment post project.

* **Where posts come from**: saving an employee (Add employee, Edit, inline edit, paste or screenshot import) with Assignment status **Client** and a client name creates one project for that deployment. Saving the same assignment again does nothing. Correcting the client's spelling moves the open project to the corrected client. A different client, or the same client with a new assignment start date, is a new deployment and gets its own project. Spreadsheet imports never create posts; use the **Backfill** tab to create them for chosen employees.
* **Needs attention** means something is missing: photo, designation or client logo. The card says exactly what. As soon as the item arrives (photo upload, designation on the record, logo upload, email reply) the preview is generated by itself.
* **Open** a post to edit it. The preview on the left updates as you type. You can change the display name, designation, client display name, pronoun, announcement text, the photo (any version on the employee record, or a photo used only for this post) and its zoom and position, the logo (any version of the client logo, or one for this post only) and how it fits, and text sizes and positions. **Save draft** keeps a version every time. Fields you edit stop following the employee or client record; a note appears if the record changes later, and **Follow the record again** resets a field.
* **Regenerate** renders again, **Export PNG** downloads the picture with a versioned name, **Copy image** and **Copy text** put the picture or the announcement (name in bold) on the clipboard for Teams, Outlook or Word.
* **Mark as published** keeps a copy of the published picture. Editing afterwards creates a new draft version; the published version stays in the version list.
* Post edits never change the employee record, and nothing on the Posts page sends email by itself.

Finished pictures are in the data folder under `exports\posts\<project id>\`.

## 12. Clients and logos

Posts > Clients and logos. One logo per client, shared by every post for that client. Upload or replace a logo here; every version is kept and an earlier one can be made current again. The previous logo stays in place until the new file is saved and checked. Rename corrects spelling (posts move with the client). Designations are never shared between people at the same client.

## 13. Logo and designation requests by email

Open a post that is missing a logo or designation and press **Request by email**. Review the recipient (the employee's official email), subject and text; only the missing items are asked for. **Send** sends through classic Outlook; **Save as Outlook draft** lets you send from Outlook. A reference such as `[POSTREQ-20261013-A1B2C3]` in the subject ties replies to this request. A second request within 24 hours needs the reminder tick.

Posts > Requests and replies > **Check replies now** reads Outlook (the Inbox or the folder set in Settings > Posts and OCR, plus its subfolders, for the number of days set there):

* Replies matched by reference or Outlook conversation, with one usable logo attachment, are applied automatically: the logo goes to the client, the designation to that one post.
* Everything else waits here: choose the post, pick an attachment (rejected ones say why: icon size, signature name, too small, not an image; you can still pick any of them), tick the designation and optionally also update the employee record. Older replies never overwrite newer accepted information.
* Requests whose sending was uncertain or failed show **Record outcome**: check Outlook and record what you found. Nothing is resent automatically.

## 14. Videos

Videos shows whether the Video Studio service is running, its queue, and **Open Video Studio**, which opens the studio's own page in a new tab (http://127.0.0.1:8756/). In the studio, add the candidate name, the intro clips and the answer clips, check the trims and captions, then press **Confirm & Make Video**. The clips are cut, the video is queued, rendered and its audio cleaned, and the file is saved to the data folder under `video\output`. Confirm the next candidate straight away; it waits and starts by itself when the previous one finishes.

States: Preparing, Queued, Rendering, Cleaning audio, Completed, Failed, Interrupted. A failed video shows its error and has **Retry**. If only the optional audio clean up failed, the rendered video is kept and the message says so. The queue survives restarts; an interrupted video is marked and can be retried.

FFmpeg and FFprobe must be in `Tools\ffmpeg` next to the application or on PATH. Automatic captions need faster-whisper (optional); without it captions are typed in the studio as before.

## 15. Dependencies

Settings > About > **Dependencies and bundled assets** checks FFmpeg, FFprobe, Tesseract, Pillow, openpyxl, pypdfium2, pywin32, Outlook, the post template, the video cards and fonts, deep-filter.exe and faster-whisper, and says what each one is needed for and how to fix it. Nothing is reported as present unless the file really exists on this computer.

## 16. Moving from the old tools

Settings > Posts and OCR > **Bring in data from the old Post Studio**: enter the old `files` folder. The preview lists the logos, headshots and posts found and the ones that need a decision (names matching several employees or none). Import creates client logos, employee photos (new version, old kept) and post projects (published where the Uploaded tick was set, with the old PNG attached as a legacy version). The old folder is not changed.
