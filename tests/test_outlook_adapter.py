"""Classic Outlook adapter logic tested against a fake COM object model (mocks).

These tests do NOT prove that real Outlook works; they check how results and errors are
classified. Real Outlook must be checked with docs/WINDOWS_MANUAL_CHECKLIST.md.
"""
from __future__ import annotations

import sys
import types

import pytest

from hub.services.outlook import ClassicOutlookAdapter, MailRequest, USER_PROPERTY


class ComError(Exception):
    def __init__(self, hresult, text=""):
        super().__init__(hresult)
        self.hresult = hresult
        self.excepinfo = (0, "Outlook", text, None, 0, hresult)


class Prop:
    def __init__(self):
        self.Value = None


class Props:
    def __init__(self):
        self.items = {}

    def Add(self, name, kind, add_to_folder):
        self.items[name] = Prop()
        return self.items[name]

    def Find(self, name):
        return self.items.get(name)


class Mail:
    def __init__(self, world):
        self.world = world
        self.Attachments = types.SimpleNamespace(Add=lambda p: world["attached"].append(p))
        self.UserProperties = Props()
        self.EntryID = "ENTRY1"
        self.Body = ""
        self.Subject = ""
        self.SentOn = "2026-10-01 10:00"

    def Save(self):
        self.world["drafts"].append(self)

    def Send(self):
        if self.world.get("send_error"):
            raise self.world["send_error"]
        self.world["drafts"].remove(self)
        self.world["sent"].append(self)


class Folder:
    def __init__(self, items):
        self._items = items

    @property
    def Items(self):
        items = self._items

        class Coll:
            Count = len(items)

            def Item(self, i):
                return items[i - 1]

            def Restrict(self, q):
                return self

        return Coll()


class Account:
    def __init__(self, smtp):
        self.SmtpAddress = smtp
        self.DisplayName = smtp


def install_fake(monkeypatch, world):
    accounts = [Account("hr.ops@relay-sample.test")]

    class NS:
        Accounts = types.SimpleNamespace(Count=len(accounts), Item=lambda i: accounts[i - 1])
        Stores = types.SimpleNamespace(Count=0, Item=lambda i: None)
        CurrentProfileName = "Outlook"

        def GetDefaultFolder(self, kind):
            return Folder({5: world["sent"], 4: world["outbox"], 16: world["drafts"]}[kind])

    class App:
        Version = "16.0.0"

        def GetNamespace(self, name):
            return NS()

        def CreateItem(self, kind):
            if world.get("create_error"):
                raise world["create_error"]
            return Mail(world)

    client = types.ModuleType("win32com.client")
    client.GetActiveObject = lambda name: App()
    client.dynamic = types.SimpleNamespace(Dispatch=lambda name: App())
    pkg = types.ModuleType("win32com")
    pkg.client = client
    monkeypatch.setitem(sys.modules, "win32com", pkg)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(CoInitialize=lambda: None))


@pytest.fixture
def world(monkeypatch):
    w = {"drafts": [], "sent": [], "outbox": [], "attached": []}
    install_fake(monkeypatch, w)
    return w


def req(tmp_path):
    f = tmp_path / "Ahmedabad_IT_Headshots_2026-10-01.zip"
    f.write_bytes(b"PK")
    return MailRequest("RH-20261001-ABCD", "hr.ops@relay-sample.test", ["it@relay-sample.test"], [], "IT headshots", "Body\nReference: RH-20261001-ABCD", [f])


def test_draft_and_send_and_reconcile(world, tmp_path):
    a = ClassicOutlookAdapter(timeout=5)
    r = a.deliver(req(tmp_path), "draft")
    assert r.status == "draft_saved" and world["drafts"] and world["attached"]
    assert world["drafts"][0].UserProperties.Find(USER_PROPERTY).Value == "RH-20261001-ABCD"
    assert a.locate("RH-20261001-ABCD", "IT headshots")["where"] == "drafts"
    r = a.deliver(req(tmp_path), "send")
    assert r.status == "submitted"
    assert a.locate("RH-20261001-ABCD", "IT headshots")["where"] == "sent"
    assert a.locate("RH-OTHER", "IT headshots")["where"] in ("drafts", None)


def test_unknown_sender_fails_before_send(world, tmp_path):
    a = ClassicOutlookAdapter(timeout=5)
    rq = req(tmp_path)
    rq.sender = "someone@else.test"
    r = a.deliver(rq, "send")
    assert r.status == "failed_before_send" and "not found" in r.message
    assert not world["sent"]


def test_security_prompt_denied_is_a_definite_failure(world, tmp_path):
    world["send_error"] = ComError(-2147467260, "Operation aborted")
    r = ClassicOutlookAdapter(timeout=5).deliver(req(tmp_path), "send")
    assert r.status == "failed_before_send" and "security prompt" in r.message and "does not bypass" in r.message


def test_unclear_send_error_is_uncertain(world, tmp_path):
    world["send_error"] = ComError(-2147023174, "RPC server unavailable")
    r = ClassicOutlookAdapter(timeout=5).deliver(req(tmp_path), "send")
    assert r.status == "uncertain"


def test_outlook_not_installed(world, tmp_path):
    world["create_error"] = ComError(-2147221005)
    r = ClassicOutlookAdapter(timeout=5).deliver(req(tmp_path), "send")
    assert r.status == "failed_before_send" and "not installed" in r.message
