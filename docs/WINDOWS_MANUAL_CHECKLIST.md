# Windows Manual Validation Checklist

These checks need a real Windows computer with classic Outlook. They were **not** run during development, because the build environment was Linux without Windows or Outlook. Use test recipients you control (for example your own mailbox) before configuring real IT or HR recipients.

Record for each: Pass / Fail, date, tester, notes.

## A. Packaged application

| # | Check | Expected |
|---|-------|----------|
| A1 | Run `scripts\build_windows_exe.bat` with the network cable unplugged / Wi-Fi off | Build completes using only `wheelhouse\`; `dist\RelayEmployeeHub\RelayEmployeeHub.exe` exists |
| A2 | Copy `dist\RelayEmployeeHub` to a second PC without Python; double-click `RelayEmployeeHub.exe` offline | Browser opens the first-run setup page; no console window |
| A3 | Run `create_shortcut.ps1` | Desktop shortcut with the app icon starts the app |
| A4 | Double-click the shortcut again while running | Browser opens the existing instance; no second server, no error |
| A5 | Settings > About shows the data folder `%LOCALAPPDATA%\RelayEmployeeHub\Data` | Folder contains employees.db, images, thumbnails, exports, import_staging, backups |
| A6 | Point the data folder at a OneDrive path with `Choose data folder.bat` | Refused with a clear message |
| A7 | Stop via Settings > About > Stop application, then `Stop Relay Employee Hub.bat` when already stopped | Clean stop; second command says it is not running |
| A8 | `logon_start.ps1 -Enable`, sign out and in | App runs in background (no browser); `-Disable` removes it |
| A9 | Windows Defender SmartScreen / antivirus | Note any warning (the exe is unsigned); company IT may need to allow it |
| A10 | Open the interface in Edge and Chrome; check no requests leave the PC (DevTools > Network) | Only `127.0.0.1` requests |
| A11 | Import `samples\synthetic_historical_data.xlsx`, then `samples\synthetic_current_roster.csv`; upload `samples\synthetic_headshots.zip` and `samples\synthetic_headshots.pdf` | Same results as the automated tests |
| A12 | Folder upload with "Choose a folder" in Edge | All images in the folder are staged |
| A13 | Prepare IT and HR packages; open the ZIPs in Explorer | City ZIP names as configured; HR files are RHC ID.jpg and open as JPG |
| A14 | Try to delete a package ZIP in `exports\packages` | Files are read-only; regenerating creates a new revision folder |
| A15 | Backup to a USB drive; unplug it; wait for the daily backup | Backup failure shown in Settings with a clear note, retried hourly |
| A16 | Restore a backup on a second PC | Data and photos restored, auto-send off, "re-check task" banner shown |
| A17 | Office network: enable LAN, restart, allow port 8765 for Private profile in Windows Firewall; open from another PC | Sign-in page loads; a PC outside the allowed networks is refused |

## B. Classic Outlook (COM automation)

Prerequisites: classic Outlook for Windows (Microsoft 365 Apps or Office 2016/2019/2021) with the operator's profile, signed in; Settings > Email set to Classic Outlook.

| # | Check | Expected |
|---|-------|----------|
| B1 | Tasks > Check Outlook now (Outlook open) | Available, version, profile and account list shown |
| B2 | Same with Outlook closed | Outlook starts in the background, or a clear message if a profile prompt appears |
| B3 | Switch the user to the **new Outlook** toggle and check | "Classic Outlook automation is unavailable" style message; manual mode still works |
| B4 | Task settings: choose the sender from the account list; To = your test mailbox | Saved; auto-send not yet on |
| B5 | Period > Create draft in Outlook | Draft in Drafts with both city ZIPs attached, correct subject/body/To/CC, reference line |
| B6 | Send that draft manually from Outlook; click Check Outlook in the period | Status becomes Confirmed in Sent Items |
| B7 | New period > Send now | Email sent; status Submitted, then Confirmed in Sent Items; test mailbox receives it |
| B8 | Send now with Outlook in Work Offline mode | Stays Submitted with "Still in the Outbox"; becomes Confirmed after Outlook goes back online and sends; never resent |
| B9 | Record outcome > It was NOT sent, then Send now again | Exactly one new email is sent |
| B10 | Second account: choose it as sender | Email sent from that account (check Sent Items of that account) |
| B11 | Sender not in profile (edit the task to a wrong address) | "Sender account ... not found" before sending; nothing sent |
| B12 | Attachment limit set to 1 MB with larger ZIPs | Period Needs Attention: above the limit; not sent |
| B13 | If the Outlook "A program is trying to send an email" security prompt appears, click Deny | Status Failed with the security-prompt message; nothing sent; app does not try to bypass |
| B14 | Company policy blocking programmatic access (if applicable) | Clear error; manual mode remains usable |
| B15 | Turn on automatic sending (confirm recipients), set the schedule due in a few minutes, leave the app running and the user signed in | One email sent automatically; period Confirmed |
| B16 | Kill the app (Task Manager) during a send, restart | Period shows Delivery Uncertain; no automatic resend |
| B17 | Leave the PC off across a due date, then start the app | Missed period shown as Needs Attention ("missed while the application was not running"); not auto-sent |
| B18 | Move the data folder to another PC and start | Automatic sending off; banner asks to re-check the task |

## C. Things automation cannot confirm

* Recipient delivery is never confirmed by the app. "Confirmed in Sent Items" means Outlook sent it.
* The app does not run as a Windows service. If nobody is signed in, nothing is sent.
