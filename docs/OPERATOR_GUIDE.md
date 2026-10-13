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
