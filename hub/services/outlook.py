"""Email adapters.

* ClassicOutlookAdapter  - classic Outlook for Windows through local COM automation, using the
                           existing Outlook profile. No SMTP credentials, no cloud APIs.
* DryRunAdapter          - development/testing. Never sends anything; writes a record of what
                           would have been sent into exports/dry_run_outbox.
* ManualAdapter          - for computers with only new Outlook or Outlook on the web. Packages are
                           placed in a ready folder and the operator sends and confirms manually.

What each result means (never overstated):
  draft_saved        an unsent message with attachments was saved in Outlook's Drafts folder
  submitted          Outlook accepted the message for sending (it goes to the Outbox)
  confirmed (later)  the message was found in Sent Items by its dispatch reference
Recipient delivery is never claimed: Outlook does not report it to automation.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("hub.outlook")

USER_PROPERTY = "RelayHubDispatchId"


@dataclass
class MailRequest:
    dispatch_uid: str
    sender: str
    to: list[str]
    cc: list[str]
    subject: str
    body: str
    attachments: list[Path] = field(default_factory=list)


@dataclass
class MailResult:
    status: str  # draft_saved | submitted | failed_before_send | uncertain
    message: str = ""
    entry_id: str | None = None
    conversation_id: str | None = None


@dataclass
class InboundMail:
    """A received message, as far as the reply scanner needs it."""
    entry_id: str
    conversation_id: str | None
    sender_email: str
    sender_name: str
    subject: str
    received_at: str | None
    body: str
    attachments: list[dict] = field(default_factory=list)  # {name, size, path}


class AdapterError(Exception):
    pass


class BaseAdapter:
    name = "base"
    can_send = False

    def check(self) -> dict:
        raise NotImplementedError

    def deliver(self, req: MailRequest, mode: str) -> MailResult:
        raise NotImplementedError

    def locate(self, dispatch_uid: str, subject: str) -> dict:
        """Where is the message now? Returns {"where": "sent"|"outbox"|"drafts"|None, "sent_on": str|None}."""
        return {"where": None, "sent_on": None, "message": "Reconciliation is not available in this mode."}

    def list_replies(self, days_back: int, phrase: str, folder_name: str, staging: Path, known_senders: set[str]) -> list[InboundMail]:
        """Received messages that may be replies to post requests. Attachments are saved under ``staging``."""
        raise AdapterError("Reading replies is not available in this email mode.")


# ---------------------------------------------------------------- dry run

class DryRunAdapter(BaseAdapter):
    """Simulates Outlook. Scenario (setting ``dryrun_scenario``) lets tests and demos exercise every path."""

    name = "dryrun"
    can_send = True

    def __init__(self, root: Path, scenario_getter=None):
        self.root = root
        self.scenario_getter = scenario_getter or (lambda: "success")

    def check(self) -> dict:
        return {"available": True, "mode": "dryrun", "version": "dry-run", "profile": "Dry-run (no email is sent)",
                "accounts": [{"name": "Dry-run sender", "smtp": "dry-run@example.invalid"}],
                "message": "Dry-run mode: nothing is sent. Messages are written to exports/dry_run_outbox for review."}

    def deliver(self, req: MailRequest, mode: str) -> MailResult:
        scenario = self.scenario_getter()
        folder = self.root / ("drafts" if mode == "draft" else "sent") / req.dispatch_uid
        if scenario == "fail_before_send":
            return MailResult("failed_before_send", "Dry-run scenario: Outlook refused the message before sending.")
        folder.mkdir(parents=True, exist_ok=True)
        for p in req.attachments:
            shutil.copy2(p, folder / p.name)
        record = {"dispatch_uid": req.dispatch_uid, "mode": mode, "sender": req.sender, "to": req.to, "cc": req.cc,
                  "subject": req.subject, "body": req.body, "attachments": [p.name for p in req.attachments],
                  "recorded_at": datetime.now(timezone.utc).isoformat(), "scenario": scenario,
                  "note": "DRY RUN - no email was sent."}
        (folder / "message.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        if mode == "draft":
            return MailResult("draft_saved", "Dry-run draft recorded.", entry_id=f"dry-{req.dispatch_uid}")
        if scenario == "uncertain":
            (folder / "message.json").rename(folder / "outbox.json")
            return MailResult("uncertain", "Dry-run scenario: the send call did not return a clear result.")
        if scenario == "stuck_in_outbox":
            (folder / "message.json").rename(folder / "outbox.json")
        return MailResult("submitted", "Dry-run: message recorded as submitted.", entry_id=f"dry-{req.dispatch_uid}")

    def list_replies(self, days_back: int, phrase: str, folder_name: str, staging: Path, known_senders: set[str]) -> list[InboundMail]:
        """Dry run: every ``*.json`` file in exports/dry_run_outbox/inbox is one received message.

        {"entry_id", "conversation_id", "sender_email", "sender_name", "subject", "received_at", "body",
         "attachments": [{"name", "path"} or {"name", "data_b64"}]}
        """
        import base64

        inbox = self.root / "inbox"
        out: list[InboundMail] = []
        if not inbox.exists():
            return out
        for f in sorted(inbox.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            entry = str(rec.get("entry_id") or f.stem)
            subject = rec.get("subject") or ""
            sender = (rec.get("sender_email") or "").lower()
            if phrase.lower() not in subject.lower() and sender not in known_senders:
                continue
            atts = []
            dest = staging / entry
            for i, a in enumerate(rec.get("attachments") or []):
                name = a.get("name") or f"attachment{i}"
                dest.mkdir(parents=True, exist_ok=True)
                target = dest / f"{i}_{Path(name).name}"
                try:
                    if a.get("path"):
                        shutil.copyfile(a["path"], target)
                    else:
                        target.write_bytes(base64.b64decode(a.get("data_b64") or ""))
                except (OSError, ValueError):
                    continue
                atts.append({"name": name, "size": target.stat().st_size, "path": str(target)})
            out.append(InboundMail(entry, rec.get("conversation_id"), sender, rec.get("sender_name") or "", subject,
                                   rec.get("received_at"), rec.get("body") or "", atts))
        return out

    def locate(self, dispatch_uid: str, subject: str) -> dict:
        sent = self.root / "sent" / dispatch_uid
        if (sent / "message.json").exists():
            rec = json.loads((sent / "message.json").read_text(encoding="utf-8"))
            return {"where": "sent", "sent_on": rec.get("recorded_at")}
        if (sent / "outbox.json").exists():
            return {"where": "outbox", "sent_on": None}
        if (self.root / "drafts" / dispatch_uid / "message.json").exists():
            return {"where": "drafts", "sent_on": None}
        return {"where": None, "sent_on": None}


# ---------------------------------------------------------------- manual

class ManualAdapter(BaseAdapter):
    name = "manual"
    can_send = False

    def __init__(self, reason: str = ""):
        self.reason = reason

    def check(self) -> dict:
        return {"available": False, "mode": "manual", "accounts": [],
                "message": self.reason or ("Manual mode: classic Outlook automation is not used. Prepared ZIP files are placed in "
                                           "a ready folder with the subject and body to copy. Send the email yourself, then "
                                           "mark the period as sent.")}

    def deliver(self, req: MailRequest, mode: str) -> MailResult:
        return MailResult("failed_before_send", "Outlook automation is not available in manual mode.")


# ---------------------------------------------------------------- classic Outlook (Windows COM)

COM_MESSAGES = {
    -2147221005: "Classic Outlook is not installed or not registered on this computer. New Outlook and Outlook on the web "
                 "cannot be automated; use manual mode instead.",
    -2147221164: "Classic Outlook is not registered on this computer (class not registered). Use manual mode instead.",
    -2147467260: "The operation was cancelled. An Outlook security prompt may have been denied, or company policy blocks "
                 "programmatic sending. Ask IT to review Outlook's programmatic access settings; this app does not bypass them.",
    -2147024891: "Access was denied by Outlook or by a company policy.",
    -2147418111: "Outlook is busy (a dialog box may be open). Close open Outlook dialogs and try again.",
    -2147023174: "Outlook stopped responding or was closed during the operation.",
    -2147417848: "Outlook disconnected during the operation (it may have been closed).",
}


def _com_code(exc) -> int | None:
    """COM error code. Outlook reports most errors as DISP_E_EXCEPTION with the real code in excepinfo[5]."""
    hr = getattr(exc, "hresult", None)
    if hr is None and getattr(exc, "args", None):
        hr = exc.args[0] if isinstance(exc.args[0], int) else None
    if hr == -2147352567:
        try:
            scode = exc.excepinfo[5]  # type: ignore[attr-defined]
            if scode:
                return int(scode)
        except Exception:
            pass
    return hr


def _com_message(exc) -> str:
    hr = _com_code(exc)
    text = COM_MESSAGES.get(hr)
    detail = ""
    try:
        excepinfo = exc.excepinfo  # type: ignore[attr-defined]
        if excepinfo and excepinfo[2]:
            detail = str(excepinfo[2])
    except Exception:
        pass
    if text:
        return text + (f" (Outlook said: {detail})" if detail else "")
    return f"Outlook reported an error: {detail or exc}"


def _is_denied(exc) -> bool:
    return _com_code(exc) in (-2147467260, -2147024891)


def new_outlook_hint() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\16.0\Outlook\Preferences") as k:
            val, _ = winreg.QueryValueEx(k, "UseNewOutlook")
            if int(val) == 1:
                return ("The new Outlook appears to be switched on for this Windows user. Automation requires classic Outlook; "
                        "switch back to classic Outlook or use manual mode.")
    except OSError:
        return None
    return None


class ClassicOutlookAdapter(BaseAdapter):
    name = "classic"
    can_send = True

    def __init__(self, timeout: float = 120):
        self.timeout = timeout
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="outlook-com", initializer=self._init_thread)
        self._stage = threading.local()
        self.last_stage = ""

    @staticmethod
    def _init_thread():
        import pythoncom  # noqa: F401  (pywin32)

        pythoncom.CoInitialize()

    def _run(self, fn):
        fut = self._executor.submit(fn)
        return fut.result(timeout=self.timeout)

    @staticmethod
    def _app():
        import win32com.client

        try:
            return win32com.client.GetActiveObject("Outlook.Application")
        except Exception:
            return win32com.client.dynamic.Dispatch("Outlook.Application")

    def check(self) -> dict:
        if os.name != "nt":
            return {"available": False, "mode": "classic", "accounts": [], "message": "Classic Outlook automation needs Windows."}
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            return {"available": False, "mode": "classic", "accounts": [],
                    "message": "The pywin32 component is not installed, so Outlook cannot be automated."}
        hint = new_outlook_hint()

        def work():
            app = self._app()
            ns = app.GetNamespace("MAPI")
            accounts = []
            for i in range(1, ns.Accounts.Count + 1):
                a = ns.Accounts.Item(i)
                smtp = ""
                try:
                    smtp = a.SmtpAddress
                except Exception:
                    pass
                accounts.append({"name": a.DisplayName, "smtp": smtp})
            profile = ""
            try:
                profile = ns.CurrentProfileName
            except Exception:
                pass
            return {"available": True, "mode": "classic", "version": str(app.Version), "profile": profile,
                    "accounts": accounts, "message": "Classic Outlook is available.", "new_outlook_hint": hint}

        try:
            return self._run(work)
        except FutureTimeout:
            return {"available": False, "mode": "classic", "accounts": [],
                    "message": "Outlook did not respond in time. It may be showing a dialog or profile prompt."}
        except Exception as exc:  # pywintypes.com_error and others
            return {"available": False, "mode": "classic", "accounts": [], "message": _com_message(exc), "new_outlook_hint": hint}

    def deliver(self, req: MailRequest, mode: str) -> MailResult:
        state = {"stage": "starting"}

        def work():
            app = self._app()
            ns = app.GetNamespace("MAPI")
            state["stage"] = "composing"
            mail = app.CreateItem(0)  # olMailItem
            if req.sender:
                acc = None
                for i in range(1, ns.Accounts.Count + 1):
                    a = ns.Accounts.Item(i)
                    try:
                        if (a.SmtpAddress or "").lower() == req.sender.lower():
                            acc = a
                            break
                    except Exception:
                        continue
                if acc is None:
                    raise AdapterError(f"The sender account {req.sender} was not found in the current Outlook profile.")
                try:
                    mail.SendUsingAccount = acc
                except Exception:
                    mail._oleobj_.Invoke(*(64209, 0, 8, 0, acc))  # set SendUsingAccount (late-bound workaround)
            mail.To = "; ".join(req.to)
            mail.CC = "; ".join(req.cc)
            mail.Subject = req.subject
            mail.Body = req.body
            for p in req.attachments:
                mail.Attachments.Add(str(p))
            prop = mail.UserProperties.Add(USER_PROPERTY, 1, False)  # olText
            prop.Value = req.dispatch_uid
            if state.get("cancelled"):
                raise AdapterError("Cancelled after a timeout; nothing was saved or sent.")
            mail.Save()
            entry = mail.EntryID
            conv = None
            try:
                conv = str(mail.ConversationID or "") or None
            except Exception:
                conv = None
            if mode == "draft":
                return MailResult("draft_saved", "Draft saved in Outlook's Drafts folder with the attachments.", entry, conv)
            if state.get("cancelled"):
                raise AdapterError("Cancelled after a timeout; the unsent message is in Drafts.")
            state["stage"] = "sending"
            mail.Send()
            state["stage"] = "sent_call_returned"
            return MailResult("submitted", "Outlook accepted the message for sending (Outbox).", entry, conv)

        try:
            return self._run(work)
        except FutureTimeout:
            state["cancelled"] = True
            if state["stage"] == "starting":
                return MailResult("failed_before_send", "Outlook did not respond, so no message was created. Nothing was sent.")
            # The COM call may still complete in the background, so the outcome cannot be known for certain.
            return MailResult("uncertain", "Outlook did not respond in time. The message may be in Drafts, the Outbox or Sent Items. "
                                           "Check Outlook before trying again.")
        except AdapterError as exc:
            return MailResult("failed_before_send", str(exc))
        except Exception as exc:
            if state["stage"] == "sending" and not _is_denied(exc):
                return MailResult("uncertain", "The send call failed with an unclear result: " + _com_message(exc))
            msg = _com_message(exc)
            if state["stage"] != "starting":
                msg += " An unsent copy may remain in Outlook's Drafts folder."
            return MailResult("failed_before_send", msg)

    def locate(self, dispatch_uid: str, subject: str) -> dict:
        def matches(item) -> bool:
            try:
                up = item.UserProperties.Find(USER_PROPERTY)
                if up is not None and str(up.Value) == dispatch_uid:
                    return True
            except Exception:
                pass
            try:
                return dispatch_uid in (item.Body or "")
            except Exception:
                return False

        def search(folder) -> object | None:
            safe = subject.replace("'", "''")
            try:
                items = folder.Items.Restrict(f"@SQL=\"urn:schemas:httpmail:subject\" = '{safe}'")
            except Exception:
                items = folder.Items
            for i in range(1, min(items.Count, 500) + 1):
                it = items.Item(i)
                if matches(it):
                    return it
            return None

        def work():
            app = self._app()
            ns = app.GetNamespace("MAPI")
            stores = []
            try:
                for i in range(1, ns.Stores.Count + 1):
                    stores.append(ns.Stores.Item(i))
            except Exception:
                stores = []
            def folders(kind):
                out = []
                try:
                    out.append(ns.GetDefaultFolder(kind))
                except Exception:
                    pass
                for s in stores:
                    try:
                        out.append(s.GetDefaultFolder(kind))
                    except Exception:
                        pass
                return out
            for f in folders(5):  # Sent Items
                it = search(f)
                if it is not None:
                    sent_on = None
                    try:
                        sent_on = str(it.SentOn)
                    except Exception:
                        pass
                    return {"where": "sent", "sent_on": sent_on}
            for f in folders(4):  # Outbox
                if search(f) is not None:
                    return {"where": "outbox", "sent_on": None}
            for f in folders(16):  # Drafts
                if search(f) is not None:
                    return {"where": "drafts", "sent_on": None}
            return {"where": None, "sent_on": None}

        try:
            return self._run(work)
        except Exception as exc:
            return {"where": None, "sent_on": None, "error": _com_message(exc)}


    def list_replies(self, days_back: int, phrase: str, folder_name: str, staging: Path, known_senders: set[str]) -> list[InboundMail]:
        """Scan the Inbox (or the named folder) and its first-level subfolders for replies.

        Attachments are saved to disk here; what counts as a logo is decided by the caller,
        so a rejected attachment can be explained and chosen manually later.
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=int(days_back))

        def work():
            app = self._app()
            ns = app.GetNamespace("MAPI")
            base = ns.GetDefaultFolder(6)  # Inbox
            if folder_name:
                found = None
                for f in base.Folders:
                    if str(f.Name).lower() == folder_name.lower():
                        found = f
                        break
                if found is None:
                    for store in ns.Folders:
                        for f in store.Folders:
                            if str(f.Name).lower() == folder_name.lower():
                                found = f
                                break
                        if found is not None:
                            break
                if found is not None:
                    base = found
            folders = [base]
            try:
                for sub in base.Folders:
                    folders.append(sub)
            except Exception:
                pass
            out: list[InboundMail] = []
            for folder in folders:
                try:
                    items = folder.Items
                    items.Sort("[ReceivedTime]", True)
                except Exception:
                    continue
                for msg in items:
                    try:
                        if msg.Class != 43:
                            continue
                        rt = None
                        try:
                            rt = msg.ReceivedTime.replace(tzinfo=None)
                        except Exception:
                            rt = None
                        if rt is not None and rt < cutoff:
                            break
                        subject = str(msg.Subject or "")
                        sender = str(msg.SenderEmailAddress or "").strip().lower()
                        if sender and "@" not in sender:
                            try:
                                sender = str(msg.Sender.GetExchangeUser().PrimarySmtpAddress or "").strip().lower()
                            except Exception:
                                pass
                        if phrase.lower() not in subject.lower() and sender not in known_senders:
                            continue
                        entry = str(msg.EntryID)
                        conv = None
                        try:
                            conv = str(msg.ConversationID or "") or None
                        except Exception:
                            pass
                        body = ""
                        try:
                            body = str(msg.Body or "")
                        except Exception:
                            pass
                        atts = []
                        dest = staging / entry.replace("/", "_")[-80:]
                        try:
                            for i in range(1, msg.Attachments.Count + 1):
                                att = msg.Attachments.Item(i)
                                name = str(att.FileName or f"attachment{i}")
                                dest.mkdir(parents=True, exist_ok=True)
                                target = dest / f"{i}_{Path(name).name}"
                                try:
                                    att.SaveAsFile(str(target))
                                except Exception as exc:
                                    atts.append({"name": name, "size": 0, "path": "", "error": f"could not be saved: {exc}"})
                                    continue
                                atts.append({"name": name, "size": target.stat().st_size, "path": str(target)})
                        except Exception:
                            pass
                        sender_name = ""
                        try:
                            sender_name = str(msg.SenderName or "")
                        except Exception:
                            pass
                        out.append(InboundMail(entry, conv, sender, sender_name, subject, rt.isoformat() if rt else None, body, atts))
                    except Exception:
                        continue
            return out

        try:
            return self._run(work)
        except FutureTimeout:
            raise AdapterError("Outlook did not respond in time while reading replies.")
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(_com_message(exc))


# ---------------------------------------------------------------- factory

_CLASSIC: ClassicOutlookAdapter | None = None


def build_adapter(ctx, mode: str | None = None) -> BaseAdapter:
    global _CLASSIC
    if mode is None:
        mode = ctx.forced_outlook_mode
    if mode is None:
        with ctx.db() as conn:
            from ..db import get_setting

            mode = get_setting(conn, "outlook_mode", "dryrun")
    if mode == "classic":
        if _CLASSIC is None:
            _CLASSIC = ClassicOutlookAdapter()
        return _CLASSIC
    if mode == "manual":
        return ManualAdapter()

    def scenario():
        with ctx.db() as conn:
            from ..db import get_setting

            return get_setting(conn, "dryrun_scenario", "success")

    return DryRunAdapter(ctx.paths.dryrun_outbox, scenario)
