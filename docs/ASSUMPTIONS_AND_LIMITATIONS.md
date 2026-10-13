# Assumptions, Design Notes and Limitations

## Assumptions made (no clarification was requested)

1. **Target platform**: Windows 10/11 64-bit, one designated computer, operator signed in to Windows. Python 3.12 or 3.13 is used to build or run from source.
2. **RHC IDs** are unique, stored as text, compared case-insensitively, with spaces removed (for example `rhc 0012` becomes `RHC0012`). Leading zeros are always kept. Allowed characters: letters, digits, `- _ / .`.
3. **Dates** are stored as `YYYY-MM-DD`. Ambiguous spreadsheet dates such as `03/04/2026` are read day first (Indian convention); this can be switched to month first in Settings. Real Excel dates are read directly.
4. **Locations** start as Ahmedabad and Vadodara. Common spellings are accepted in imports (AMD, Ahd, Baroda, VDR). More locations can be added in Settings.
5. **Official emails** must be unique across records.
6. **Current bench days** = calendar days from bench start to today in the configured timezone; 0 on the start date; counts stop at a past bench end date; blank if not on Bench or if the start is in the future.
7. **Historical records** imported for people not in the current roster are kept as "Historical only" and never appear in the active directory or packages. Default employment status for new historical rows without a status is Pending Review.
8. **Complete roster default**: rows without a status are treated as Active. People who were Left or archived but appear in the roster are reactivated, with a visible warning in the preview.
9. **Packages include** employees who are Active, in the directory, not archived, with a location. Pending Review employees in a location are listed as exclusions. Active employees without a location are listed on every manifest and block the period until fixed or accepted.
10. **IT file names** default to the original uploaded filename. If two employees' files would have the same name, every file in that group gets `_<RHC ID>` added (and `_p<photo id>` in the unlikely case that still collides). If the original extension does not match the real image format, the correct extension is used and noted in the manifest.
11. **HR file names** are `<RHC ID>.jpg`. JPEG originals are copied byte for byte; other formats are re-encoded as real JPEG (transparency flattened onto white, EXIF rotation applied).
12. **ZIP names**: `{location}_IT_Headshots_{YYYY-MM-DD}.zip` (period date) and `{location}_HR_Headshots_{YYYY-MM}.zip`. Both are templates.
13. **One email per task period** with both city ZIPs attached. A location with nothing to send is omitted and mentioned in the email body.
14. **Attachment ceiling** defaults to 18 MB (below the common 20 to 25 MB mail limits after encoding).
15. **Catch-up window** defaults to 48 hours: a period that became due more than 48 hours before the app noticed it is a "missed" period, shown for review and never sent automatically.
16. **Sessions** expire after 60 minutes of inactivity or 12 hours in total (both configurable). Accounts lock for 15 minutes after 5 failed sign-ins; a single computer is throttled after 20 failures in 15 minutes.
17. **Date of birth**: administrators see the full date; other roles default to day and month only (useful for birthdays), configurable per user (full, day and month, hidden).
18. **Fetch-only** access is implemented as revocable integration tokens rather than a user role that can sign in. Tokens never return date of birth, notes or fields marked sensitive.
19. **Import batches** are visible to the uploader and administrators only.
20. **Backups** default to `<data folder>\backups` and include prepared package snapshots. Manual backups are never deleted automatically; daily backups keep the newest 30.

## How key safety rules are implemented

| Requirement | Implementation |
|---|---|
| No silent overwrite between users | Every record has a version number; updates use `WHERE version = ?` and return 409 with both versions |
| Atomic imports | Commit re-runs the analysis inside one `BEGIN IMMEDIATE` transaction and refuses if the plan changed since the preview |
| Safe undo | Each imported field change links to its history entry; undo refuses any field changed later and lists the conflict |
| Duplicate sends | Unique database index allows one active send per period, plus an in-process lock |
| Uncertain sends | Never retried; reconciled against Sent Items/Outbox/Drafts by a dispatch reference (Outlook user property and a reference line in the body) or resolved by the operator |
| Baseline for "new or changed" | Advanced only when a send is confirmed in Sent Items or the operator marks it complete; tracks employee, photo version and location |
| Immutable packages | Written once to `exports\packages\<task>\<period>\<location>\rev<N>`, set read-only, SHA-256 verified before attaching; regenerate creates rev N+1 |
| Restart safety | Periods have unique task and period keys; interrupted preparation restarts; interrupted sends become Delivery Uncertain |
| Consistent backups | SQLite online backup API snapshot, then immutable photo and package files; every file hashed and verified |
| Safe archives | ZIP names are never used as paths; traversal, absolute paths, symlinks, encrypted entries, nested ZIPs, size, count and ratio limits are enforced |
| Formula injection | Exported text beginning with `= + - @` or control characters is prefixed with an apostrophe and stored as text |
| Cross-site requests | SameSite=Strict HttpOnly session cookie, CSRF token header on every change, Host header check, strict Content-Security-Policy |
| Two active copies | A heartbeat lock file in the data folder refuses a second instance; moving the folder to another computer disables automatic sending |

## Limitations (honest list)

* **Not tested on Windows or with Outlook.** Development and automated tests ran on Linux. The Outlook COM adapter was exercised only against a fake object model. Use `docs/WINDOWS_MANUAL_CHECKLIST.md`.
* **No packaged .exe was built here.** PyInstaller cannot cross-compile from Linux. The build script, spec and all pinned Windows wheels are included; the build must run once on Windows.
* **Python itself is not bundled** for the source installation (python.org was not reachable from the build environment). Installing Python once is the only download; every other component installs offline from `wheelhouse\`. The packaged build does not need Python on the operator's computer.
* **Recipient delivery is never confirmed.** "Confirmed in Sent Items" means Outlook sent it.
* **Outlook security prompts and company policies** can block automated sending. The app reports this and does not bypass it; manual mode remains available.
* **Scheduling needs the computer on**, the operator signed in and the app running. It is not a Windows service by design.
* **No OCR.** Scanned PDF pages are handled with manual crop and manual assignment.
* **Office network mode uses plain HTTP** on the local network. Enable only on a trusted network and restrict with Windows Firewall.
* **HEIC photos** are not supported (convert to JPG first). Legacy `.xls` and macro `.xlsm` workbooks are rejected (save as `.xlsx`).
* **Windows administrators** can read the data folder directly; application permissions cannot prevent that.
* **Multi-row paste** applies to rows on the current page; larger updates should use Import Data.
* **Birthday and work-anniversary emails** are not implemented in version 1 (see extension points).

## Extension points for later versions

* **New recurring email tasks** (birthdays, work anniversaries): add a task kind in `hub/context.py` (`default_tasks`) and a builder next to `hub/services/packages.py`; periods, dispatch locking, Outlook adapters, reconciliation, statuses and the Tasks page are generic. Day-and-month date-of-birth access and integration tokens already support a birthday tool.
* **Additional locations**: Settings > General; tasks choose which locations get a ZIP.
* **Office LAN access**: Settings > Network (restart required).
* **Read-only integrations**: `/api/fetch/v1/employees` with scoped tokens.
* **Schema changes**: append a script to `MIGRATIONS` in `hub/db.py`; a database snapshot is taken automatically before migrating.

## Version 2.0 additions (posts, screenshots, videos)

### Assumptions

* A "deployment" is identified by employee, client (normalised: case, punctuation and suffixes such as Pvt Ltd, Corp, Inc are ignored) and assignment start date. Two client spellings that are at least 80 percent similar, saved one after the other on the same employee, are treated as a correction, not a new deployment.
* The old Post Studio's post layout and announcement wording are the defaults. The template file is the supplied `internal_post_base.png` (600 x 600).
* The bundled Montserrat font of the old tool was not supplied; rendering uses Arial or Segoe UI on Windows (what the old tool used as its fallback), then the bundled Poppins and Plus Jakarta Sans, then DejaVu. Set `template_path` in Settings > Posts and OCR to use another template.
* Logo and designation requests go to the employee's official email on record. If it is missing, the operator types the address.
* Screenshot OCR uses Tesseract through its command line (no extra Python package). Accuracy depends on the picture; everything is shown for review and nothing unreadable is invented.
* The Video Studio runs as a separate local process started by the hub (in-process in the packaged build) on port 8756, one render at a time.

### Limitations

* OCR, Outlook automation and video rendering need their Windows dependencies (Tesseract, classic Outlook with pywin32, FFmpeg). Each is checked and reported; none is bundled except deep-filter.exe, the video cards, the fonts and the post template.
* Classic Outlook reply scanning, conversation IDs and attachment saving are implemented with the same COM calls as the old tool but could only be tested with the dry-run adapter in the development environment.
* Copy image and Copy text use the browser clipboard (Chrome and Edge). Firefox may not support copying images; Export PNG always works.
* The screenshot parser understands labelled lines, table rows with an email and the old `Client - Name` layout. Other layouts land in the review rows with the raw text shown.
* Video captions still use proportional timing across the trimmed clip (unchanged renderer, as requested).
* The DeepFilterNet binary only runs on Windows; elsewhere the FFmpeg filter is used and the dependency panel says which engine is active.
