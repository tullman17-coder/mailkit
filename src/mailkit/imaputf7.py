"""Modified UTF-7 encoding used by IMAP mailbox names (RFC 3501)."""

from __future__ import annotations


def encode(name: str) -> str:
    if all(ord(c) < 128 and c != "&" for c in name):
        return name.replace("&", "&-")
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        raw = "".join(buf).encode("utf-16be")
        b64 = __import__("base64").b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + b64 + "-")
        buf.clear()

    for ch in name:
        if ch == "&":
            flush()
            out.append("&-")
        elif 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append(ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def decode(name: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(name):
        if name[i] == "&":
            j = name.find("-", i)
            if j == -1:
                out.append(name[i:])
                break
            chunk = name[i + 1 : j]
            if chunk == "":
                out.append("&")
            else:
                b64 = chunk.replace(",", "/")
                pad = "=" * ((4 - len(b64) % 4) % 4)
                raw = __import__("base64").b64decode(b64 + pad)
                out.append(raw.decode("utf-16be"))
            i = j + 1
        else:
            out.append(name[i])
            i += 1
    return "".join(out)
