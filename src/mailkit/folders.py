"""IMAP folder mapping using SPECIAL-USE (RFC 6154) then name heuristics."""

from __future__ import annotations

from dataclasses import dataclass

from mailkit.models import Mailbox

SPECIAL_USE = {
    "\\inbox": "inbox",
    "\\sent": "sent",
    "\\drafts": "drafts",
    "\\trash": "trash",
    "\\junk": "junk",
    "\\spam": "junk",
    "\\archive": "archives",
    "\\all": "archives",
    "\\flagged": "saved",
    "\\starred": "saved",
}

NAME_ROLES: list[tuple[str, tuple[str, ...]]] = [
    ("inbox", ("inbox",)),
    ("sent", ("sent", "sent items", "sent mail", "[gmail]/sent mail", "sent messages")),
    ("drafts", ("drafts", "draft", "[gmail]/drafts")),
    ("archives", ("archive", "archives", "archived", "[gmail]/all mail", "all mail")),
    ("trash", ("trash", "deleted", "deleted items", "bin", "[gmail]/trash")),
    ("junk", ("junk", "spam", "bulk", "junk email", "[gmail]/spam", "bulk mail")),
    ("saved", ("starred", "flagged", "[gmail]/starred")),
]

SECTION_ORDER = ("inbox", "saved", "sent", "drafts", "archives")


@dataclass
class FolderMap:
    mailboxes: list[Mailbox]
    by_role: dict[str, Mailbox]
    by_name: dict[str, Mailbox]

    def name_for_role(self, role: str, override: str | None = None) -> str | None:
        if override:
            return override
        box = self.by_role.get(role)
        return box.name if box else None

    def role_for_name(self, name: str) -> str:
        box = self.by_name.get(name) or self.by_name.get(name.upper())
        return box.role if box else "custom"

    def sections(self) -> list[Mailbox]:
        out: list[Mailbox] = []
        seen: set[str] = set()
        for role in SECTION_ORDER:
            box = self.by_role.get(role)
            if box and box.name not in seen:
                out.append(box)
                seen.add(box.name)
        for box in self.mailboxes:
            if box.name not in seen and box.selectable:
                out.append(box)
                seen.add(box.name)
        return out


def normalize_attr(attr: str) -> str:
    return attr.strip().lower()


def role_from_attrs(name: str, attrs: list[str]) -> str:
    lowered = {normalize_attr(a) for a in attrs}
    if name.upper() == "INBOX" or "\\inbox" in lowered:
        return "inbox"
    for attr in lowered:
        if attr in SPECIAL_USE:
            return SPECIAL_USE[attr]
    return role_from_name(name)


def role_from_name(name: str) -> str:
    lowered = name.strip().lower().replace("\\", "/")
    leaf = lowered.split("/")[-1]
    for role, aliases in NAME_ROLES:
        if lowered in aliases or leaf in aliases:
            return role
    return "custom"


def parse_list_line(line: str | bytes) -> Mailbox | None:
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line:
        return None
    # Typical: (\HasNoChildren \Sent) "/" "Sent"
    if not line.startswith("("):
        parts = line.split()
        if len(parts) >= 3:
            name = parts[-1].strip('"')
            delim = parts[-2].strip('"')
            return Mailbox(name=name or "INBOX", delimiter=delim, role=role_from_name(name))
        return None
    attrs_end = line.find(")")
    attrs_raw = line[1:attrs_end]
    attrs = [a for a in attrs_raw.split() if a]
    rest = line[attrs_end + 1 :].strip()
    delim = "/"
    name = ""
    if rest.startswith('"'):
        delim = rest[1 : rest.find('"', 1)]
        rest = rest[rest.find('"', 1) + 1 :].strip()
    else:
        bits = rest.split(None, 1)
        if bits:
            delim = bits[0]
            rest = bits[1] if len(bits) > 1 else ""
    rest = rest.strip()
    if rest.startswith('"'):
        name = rest[1:-1] if rest.endswith('"') else rest[1:]
    else:
        name = rest
    selectable = "\\Noselect" not in attrs and "\\NonExistent" not in attrs
    special = [a for a in attrs if a.lower() in SPECIAL_USE or a.lower().startswith("\\")]
    role = role_from_attrs(name, attrs)
    return Mailbox(
        name=name or "INBOX",
        role=role,
        delimiter=delim if delim != "NIL" else "/",
        flags=attrs,
        special_use=special,
        selectable=selectable,
    )


def build_folder_map(listed: list[Mailbox], overrides: dict[str, str] | None = None) -> FolderMap:
    overrides = overrides or {}
    by_name = {m.name: m for m in listed}
    by_role: dict[str, Mailbox] = {}
    for role, raw in overrides.items():
        if raw and raw in by_name:
            box = by_name[raw]
            box.role = role
            by_role[role] = box
    for mailbox in listed:
        if mailbox.role != "custom" and mailbox.role not in by_role:
            by_role[mailbox.role] = mailbox
    if "inbox" not in by_role:
        for mailbox in listed:
            if mailbox.name.upper() == "INBOX":
                mailbox.role = "inbox"
                by_role["inbox"] = mailbox
                break
    return FolderMap(mailboxes=listed, by_role=by_role, by_name=by_name)
