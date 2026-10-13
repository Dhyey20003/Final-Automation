"""Roles, action permissions, location scope and field restrictions.

Every API endpoint checks permissions on the server. The interface only hides
controls for convenience; hiding is never relied on for security.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

ACTIONS: dict[str, str] = {
    "employees.view": "View employee records",
    "employees.edit": "Edit employee records",
    "employees.create": "Add employees",
    "employees.archive": "Archive and restore employees",
    "employees.export": "Export employee data to Excel",
    "imports.run": "Import spreadsheets",
    "imports.undo": "Undo import batches",
    "photos.view": "View photos",
    "photos.manage": "Upload, assign and replace photos",
    "tasks.view": "View headshot tasks and history",
    "tasks.operate": "Prepare packages, create drafts, send and complete tasks",
    "tasks.configure": "Configure tasks, recipients and automatic sending",
    "fields.manage": "Manage custom fields",
    "users.manage": "Manage users, roles and integration tokens",
    "settings.manage": "Change application, network and Outlook settings",
    "backups.manage": "Create backups and restore data",
    "audit.view": "View the audit log",
    "posts.view": "View deployment posts and clients",
    "posts.manage": "Create, edit, render and publish deployment posts; manage client logos",
    "posts.email": "Send logo and designation request emails and process replies",
    "videos.view": "View the video queue",
    "videos.operate": "Open Video Studio and manage the video queue",
}

ADMIN_ONLY = {"users.manage", "settings.manage", "backups.manage", "fields.manage", "tasks.configure"}

ROLES: dict[str, dict] = {
    "admin": {"label": "Administrator", "actions": set(ACTIONS)},
    "editor": {
        "label": "Editor",
        "actions": {
            "employees.view",
            "employees.edit",
            "employees.create",
            "employees.archive",
            "imports.run",
            "imports.undo",
            "photos.view",
            "photos.manage",
            "tasks.view",
            "posts.view",
            "posts.manage",
            "posts.email",
            "videos.view",
            "videos.operate",
        },
    },
    "viewer": {"label": "Viewer", "actions": {"employees.view", "photos.view", "tasks.view", "posts.view", "videos.view"}},
    "export_operator": {
        "label": "Export Operator",
        "actions": {"employees.view", "employees.export", "photos.view", "tasks.view", "tasks.operate", "posts.view", "videos.view"},
    },
}

# Fields a non-administrator can be restricted from. Internal ID and RHC ID and name are always visible.
ALWAYS_VISIBLE = {"id", "rhc_id", "full_name"}


@dataclass
class CurrentUser:
    id: int
    email: str
    display_name: str
    role: str
    actions: set[str]
    locations: list[str]  # empty = all locations
    restricted_fields: set[str] = field(default_factory=set)
    dob_access: str = "full"
    must_change_password: bool = False
    session_id: str | None = None
    csrf_token: str | None = None
    ip: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def can(self, action: str) -> bool:
        return action in self.actions

    def in_scope(self, location: str | None) -> bool:
        if not self.locations:
            return True
        return location in self.locations

    def can_see_field(self, key: str) -> bool:
        if self.is_admin or key in ALWAYS_VISIBLE:
            return True
        if key == "date_of_birth" and self.dob_access == "none":
            return False
        return key not in self.restricted_fields

    def can_edit_field(self, key: str) -> bool:
        if not self.can_see_field(key):
            return False
        if key == "date_of_birth" and not self.is_admin and self.dob_access != "full":
            return False
        return True

    def public(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "role_label": ROLES[self.role]["label"],
            "actions": sorted(self.actions),
            "locations": self.locations,
            "restricted_fields": sorted(self.restricted_fields),
            "dob_access": "full" if self.is_admin else self.dob_access,
            "must_change_password": self.must_change_password,
            "csrf_token": self.csrf_token,
        }


def effective_actions(role: str, extra: list[str], revoked: list[str]) -> set[str]:
    if role == "admin":
        return set(ACTIONS)
    base = set(ROLES.get(role, {"actions": set()})["actions"])
    base |= {a for a in extra if a in ACTIONS and a not in ADMIN_ONLY}
    base -= set(revoked)
    return base


def user_from_row(row, session_id=None, csrf=None, ip=None) -> CurrentUser:
    extra = json.loads(row["extra_permissions"] or "[]")
    revoked = json.loads(row["revoked_permissions"] or "[]")
    return CurrentUser(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        actions=effective_actions(row["role"], extra, revoked),
        locations=[] if row["role"] == "admin" else json.loads(row["location_scope"] or "[]"),
        restricted_fields=set() if row["role"] == "admin" else set(json.loads(row["restricted_fields"] or "[]")),
        dob_access="full" if row["role"] == "admin" else row["dob_access"],
        must_change_password=bool(row["must_change_password"]),
        session_id=session_id,
        csrf_token=csrf,
        ip=ip,
    )


def system_user(name: str = "system") -> CurrentUser:
    """Identity used by the scheduler for automatic actions."""
    return CurrentUser(id=0, email=name, display_name="Scheduler", role="admin", actions=set(ACTIONS), locations=[])
