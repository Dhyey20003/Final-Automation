# Test Results (version 2.0)

Run on 13 Oct 2026 in the development environment: Linux (x86_64), Python 3.13.16, FFmpeg 7, headless Chromium for the two browser tests. Email ran in dry-run mode; no real email was sent. Tesseract OCR and classic Outlook are not available on this machine: OCR tests inject the recognised text, and Outlook reply handling is exercised through the dry-run adapter's inbox.

**Result: 97 passed, 0 failed.** Command: `python -m pytest -q`

In addition, `python scripts/video_smoke_test.py` was run: it started the Video Studio against a temporary data folder, uploaded five generated clips, pressed Confirm & Make Video through the API, observed the states Preparing, Rendering, Cleaning audio, Completed (output `Smoke_Test_final.mp4` and `Smoke_Test_final_clean.mp4`, audio engine `ffmpeg afftdn` because deep-filter.exe is Windows only), refused a duplicate submission, restarted the studio and found the completed job recovered from `jobs.json`.

All test data is synthetic (fictional names, `.test` email addresses, generated placeholder images and clips).

## Unchanged suites (all passed)

tests/test_backup_offline.py (5), tests/test_employees.py (10), tests/test_imports.py (17), tests/test_outlook_adapter.py (5), tests/test_packages_dispatch.py (12), tests/test_permissions.py (8), tests/test_photos.py (6, one assertion updated: a filename that is exactly one employee's name is now pre-selected), tests/test_review_regressions.py (7), tests/test_ui_workflows.py (1, assertion loosened to accept name matches among the pre-ticked photos).

## tests/test_posts.py (9 passed)

* `test_assignment_creates_one_project_and_resaving_does_not_duplicate`: saving a client assignment creates one project; resaving the same assignment or other fields creates nothing; "Corp" and "Corporation" are the same client; a typo correction moves the project; a new client or start date is a new project and the old one is kept.
* `test_missing_assets_complete_automatically`: photo upload, designation edit and logo upload each remove their item from the missing list; the draft renders by itself when the last one arrives; the export file is versioned under the project ID.
* `test_edits_are_versioned_and_never_touch_the_employee`: edits create versions, the employee record is untouched, stale versions are refused, a later change to the record adds a review note but keeps the edit, live preview saves nothing, publishing preserves the published picture while later edits create drafts, published posts cannot be deleted.
* `test_two_employees_same_client_keep_their_own_designation`: one shared logo, separate designations.
* `test_imports_do_not_create_posts_but_backfill_does`.
* `test_logo_versions_and_rejections`: tiny and unreadable files rejected with reasons; versions kept; earlier version can be made current; duplicates refused.
* `test_email_request_and_reply_dry_run`: request preview uses the official email and asks only for what is missing; reference in the subject; reminder protection; reply matched by reference; signature icon rejected with a reason and the real logo applied; designation applied to the post only; an older reply does not overwrite; rescans skip handled replies; unmatched replies wait for review and can be ignored.
* `test_send_failure_is_recorded_without_followup_confusion`: a failed send stays failed, a later successful send is separate.
* `test_permissions_viewer_cannot_edit_posts`.

## tests/test_screenshots.py (5 passed)

* `test_labeled_screenshot_updates_only_visible_fields`: labelled lines update designation and client only, other fields untouched, a post is created, a repeat import is "unchanged", the batch is in Import history and undo restores the record.
* `test_ambiguous_and_identity_changes_need_review`: two employees with the same name and an email change are never applied without a decision; operator choice and approval apply exactly the chosen rows.
* `test_first_last_name_and_table_rows`: "John Smith" matches John Michael Smith; table rows with email, name and designation; the old "Client - Name" layout.
* `test_edited_text_and_unmatched_rows`: corrected OCR text is re-read; discard.
* `test_ocr_missing_is_reported_not_hidden`: a missing Tesseract is reported in the batch and the dependency panel.

## tests/test_photos_names.py (3 passed)

* `test_first_and_last_name_match_is_clear_only_when_unique`: unique first and last name is pre-selected; exact full name wins over a longer name; shared names are confirm-only; the photo stays linked after renaming the employee.
* `test_legacy_post_studio_import`: logos, headshots, log.xlsx posts and Uploaded ticks are imported; ambiguous names are listed for a decision; the old folder is untouched.
* `test_video_status_and_dependencies`.

## tests/test_ui_posts.py (1 passed, browser)

First-run setup, Add employee with a client assignment (post created), photo upload matched by first and last name, client logo upload, generated post opened in the editor with the live preview, draft saved with a post-only edit, published, employee record unchanged, screenshot import tab, Videos page (Video Studio running, started by the hub), dependency panel and Posts settings. No JavaScript errors.

## Not verified here (needs the Windows computer)

Windows BAT launchers and the desktop shortcut, classic Outlook sending and reply reading through COM, Tesseract on real HR screenshots, deep-filter.exe audio clean up, the packaged PyInstaller build, clipboard copy in Edge, and a full-length interview render. See docs/WINDOWS_MANUAL_CHECKLIST.md section D.
