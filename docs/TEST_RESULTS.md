# Test Results

Run on 29 Sep 2026 in the development environment: Linux (x86_64), Python 3.11.15, headless Chromium for the browser test. Email ran in dry-run mode; no real email was sent.

**Result: 79 passed, 0 failed.** Command: `python -m pytest -v`

Second run on **Python 3.13** (the Windows target version) in a clean environment installed only from the pinned `requirements.txt`, with deprecation warnings treated as errors: **78 passed, 0 failed** (all tests except the browser test, which needs Playwright).

All test data is synthetic (fictional names, `.test` email addresses, generated placeholder images).

## tests/test_backup_offline.py  (5 passed)

Backup/restore with photos, tamper and path checks, retention, pre-migration backup, local-only assets

* `test_backup_restore_roundtrip_with_photos`: PASSED
* `test_tampered_or_unsafe_backup_is_rejected`: PASSED
* `test_backup_destination_and_retention`: PASSED
* `test_pre_migration_backup`: PASSED
* `test_interface_assets_are_all_local`: PASSED

## tests/test_employees.py  (10 passed)

Employee records, inline editing, leading-zero IDs, bench days, custom fields, export safety

* `test_create_edit_and_leading_zero_ids`: PASSED
* `test_version_conflict_prevents_silent_overwrite`: PASSED
* `test_date_validation_and_statuses`: PASSED
* `test_bench_days_calculation`: PASSED
* `test_bench_days_filter_and_sort_in_sql`: PASSED
* `test_assignment_history_is_preserved`: PASSED
* `test_archive_keeps_history`: PASSED
* `test_custom_fields_everywhere`: PASSED
* `test_export_protects_against_formula_injection`: PASSED
* `test_bulk_paste_is_previewed_and_atomic`: PASSED

## tests/test_imports.py  (17 passed)

XLSX/CSV imports: duplicates, ambiguity, historical + roster, partial updates, blanks, repeat detection, undo

* `test_roster_import_creates_and_preserves_leading_zeros`: PASSED
* `test_xlsx_leading_zero_number_format_and_formulas_not_run`: PASSED
* `test_rejects_macro_and_old_formats`: PASSED
* `test_duplicates_and_invalid_rows_block_commit`: PASSED
* `test_ambiguous_email_and_name_matches_are_never_merged`: PASSED
* `test_name_suggestion_can_be_linked_explicitly`: PASSED
* `test_email_fallback_matches_unique_email`: PASSED
* `test_partial_update_blank_cells_and_no_deactivation`: PASSED
* `test_designation_and_selected_field_updates_touch_only_allowed_fields`: PASSED
* `test_historical_then_roster_workflow`: PASSED
* `test_complete_roster_requires_confirmation_to_mark_left`: PASSED
* `test_roster_needs_declared_scope`: PASSED
* `test_repeated_import_detected_and_plan_changes_detected`: PASSED
* `test_import_audit_and_error_report`: PASSED
* `test_undo_import_and_conflicts`: PASSED
* `test_full_undo_restores_deactivated_and_promoted`: PASSED
* `test_mapping_templates_are_reusable`: PASSED

## tests/test_outlook_adapter.py  (5 passed)

Classic Outlook adapter logic against a FAKE COM object model (mocks only)

* `test_draft_and_send_and_reconcile`: PASSED
* `test_unknown_sender_fails_before_send`: PASSED
* `test_security_prompt_denied_is_a_definite_failure`: PASSED
* `test_unclear_send_error_is_uncertain`: PASSED
* `test_outlook_not_installed`: PASSED

## tests/test_packages_dispatch.py  (16 passed)

Packages, filenames, conversion, omissions, baselines, dispatch states, locking, scheduler, restart, transfer

* `test_it_packages_city_zips_names_and_omissions`: PASSED
* `test_regenerate_creates_new_immutable_revision`: PASSED
* `test_hr_names_by_rhc_id_and_real_jpg_conversion`: PASSED
* `test_changed_only_baseline_including_location_changes`: PASSED
* `test_failed_and_uncertain_sends_never_advance_baseline_or_resend`: PASSED
* `test_database_prevents_two_active_sends`: PASSED
* `test_draft_then_send_requires_confirmation_and_reconciles`: PASSED
* `test_outbox_and_manual_completion`: PASSED
* `test_manual_mode_ready_folder`: PASSED
* `test_attachment_size_ceiling`: PASSED
* `test_recipients_are_required_and_never_invented`: PASSED
* `test_restart_recovery`: PASSED
* `test_monthly_schedule_uses_last_day_of_short_months`: PASSED
* `test_scheduler_is_restart_safe_flags_backlog_and_auto_sends_latest`: PASSED
* `test_transfer_detection_disables_auto_send`: PASSED
* `test_second_instance_is_refused`: PASSED

## tests/test_permissions.py  (11 passed)

Accounts, roles, location scope, field restrictions, CSRF, lockout, LAN guard, fetch tokens, audit

* `test_first_run_setup_only_once_and_no_default_credentials`: PASSED
* `test_setup_refused_from_network`: PASSED
* `test_lan_requires_admin_enable_and_private_network`: PASSED
* `test_csrf_and_session_required`: PASSED
* `test_login_lockout`: PASSED
* `test_roles_are_enforced_on_the_server`: PASSED
* `test_last_admin_cannot_be_removed`: PASSED
* `test_location_scope_applies_to_records_photos_and_exports`: PASSED
* `test_field_restrictions_and_dob_masking`: PASSED
* `test_fetch_tokens_are_scoped_hashed_and_revocable`: PASSED
* `test_audit_records_account_and_data_changes`: PASSED

## tests/test_photos.py  (7 passed)

Photo matching and review, versions, duplicates, safe ZIP, sequence mapping, PDF extraction and crop

* `test_matching_rules`: PASSED
* `test_assign_store_versions_and_duplicates`: PASSED
* `test_zip_upload_is_safe`: PASSED
* `test_zip_limits`: PASSED
* `test_explicit_mapping_and_sequence_mapping`: PASSED
* `test_pdf_extraction_flags_and_crop`: PASSED
* `test_missing_photos_and_close_batch`: PASSED

## tests/test_review_regressions.py  (7 passed)

Regression tests for issues found in the internal code review

* `test_promotion_never_touches_fields_the_user_cannot_edit`: PASSED
* `test_period_and_import_scope`: PASSED
* `test_assignment_history_respects_field_restrictions`: PASSED
* `test_token_drops_fields_marked_sensitive_later`: PASSED
* `test_required_field_added_later_does_not_block_other_edits_or_photos`: PASSED
* `test_failed_assignment_rolls_back_cleanly`: PASSED
* `test_outlook_disp_exception_codes_are_unwrapped`: PASSED

## tests/test_ui_workflows.py  (1 passed)

Browser end-to-end run (Chromium via Playwright) of setup, import, edit, paste, photos, tasks, send, backup

* `test_main_workflows`: PASSED

## Acceptance criteria coverage

| Criterion | Covered by |
|---|---|
| Employee CRUD and inline editing | test_employees: create/edit, conflicts, bulk paste; UI test inline edit and paste |
| Leading-zero IDs | test_employees (0457), test_imports (CSV 0099, XLSX number format 00123) |
| Custom fields | test_employees custom fields (form, filter, export), test_review_regressions (required/sensitive later) |
| Role, location and field permissions | test_permissions, test_review_regressions |
| Duplicate and ambiguous imports | test_imports duplicates, ambiguous email/name |
| Historical data plus current roster | test_imports historical then roster, roster confirmation |
| Partial updates without deactivation; blank cells | test_imports partial update |
| Import rollback/conflict handling | test_imports undo with conflicts, full undo |
| Image matching and manual review | test_photos matching, mapping, sequence; UI test photo review |
| PDF extraction and cropping | test_photos PDF test (6 embedded images, scanned page flag, crop) |
| City ZIP contents and filename rules | test_packages_dispatch IT and HR package tests |
| Missing-photo reporting | test_packages_dispatch omissions, test_photos missing photos |
| Restart-safe schedules | test_packages_dispatch scheduler, restart recovery, monthly clamp |
| Changed-photo baselines | test_packages_dispatch changed-only baseline (photo and location changes) |
| Duplicate and uncertain email handling (mocks) | test_packages_dispatch failed/uncertain/draft/outbox, DB lock; test_outlook_adapter |
| Full backup/restore with photos | test_backup_offline roundtrip, tamper, retention |
| Offline operation with local assets | test_backup_offline local assets and CSP; UI test found no external requests or console errors |

## Packaging trial (Linux)

The same PyInstaller spec (`packaging/relay_hub.spec`, PyInstaller 6.22.3, Python 3.13) was built on Linux as a trial. The frozen program started offline, served the interface from the bundled static files, completed first-run setup, extracted 6 photos from the sample PDF (PDFium bundled), computed the next HR due time in Asia/Kolkata (tzdata bundled), created and verified a backup, and stopped cleanly with `--stop`. This confirms the packaging approach, but it is **not** a Windows executable.

## Checks that could NOT run here

* Anything involving real Windows: the launcher on Windows, `.bat`/`.ps1` scripts, desktop shortcut, logon start, Windows Firewall, file locking behaviour on NTFS.
* Building and running the PyInstaller `.exe` (PyInstaller does not cross-compile; no Windows build machine was available).
* Real classic Outlook COM automation: availability check, sender selection, drafts, sending, security prompts, Sent Items reconciliation. Only a fake object model was used.
* New Outlook detection (Windows registry).

These are listed step by step in `docs/WINDOWS_MANUAL_CHECKLIST.md`.
