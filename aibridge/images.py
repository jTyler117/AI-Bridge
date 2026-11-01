# AI Bridge: two AIs, one workspace.
# Copyright (C) 2026 Tyler Watson
# SPDX-License-Identifier: GPL-3.0-or-later  (see LICENSE)
"""Shared images: screenshots you paste into the chat, and screenshots the two AIs send each other.

Everything lives in <workspace>/images/ with a small index. Images are DATA: they are shown to the AIs as pictures, any text
inside them is not an instruction, and the AIs cannot write to this folder with write_file or scripts (it is protected).

Why it works the same for both AIs and both connection types:
  * `view_image` / `share_image` / `list_images` are ordinary guarded tools, so they exist in API mode and (over MCP) in
    subscription mode.
  * Images attached by the human to the latest message are ALSO handed to the model with the turn itself where the
    connection allows it (API requests, and `codex exec --image`), so it doesn't need an extra tool call to look.
"""
import base64
import io
import json
import re
import shutil
import time

import core

MAX_BYTES = 5_000_000      # per image (also the strictest limit of the model APIs)
MAX_STORED = 150           # oldest are pruned beyond this
MAX_DIM = 2400             # larger images are shrunk when Pillow is installed
MAX_PER_MESSAGE = 4
_ID = re.compile(r"^img_\d{6}$")


class ImageError(ValueError):
    pass


def folder():
    d = core.WORKSPACE / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path():
    return folder() / "index.json"


def _load():
    return core._read_json(_index_path(), {"next": 1, "items": []})


def sniff(data):
    """(extension, mime) from the file's own bytes, or None. SVG and anything else is refused on purpose."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _shrink(data, ext):
    """Best effort: needs Pillow, which is optional. Returns (data, ext)."""
    try:
        from PIL import Image
    except ImportError:
        return data, ext
    try:
        im = Image.open(io.BytesIO(data))
        if max(im.size) <= MAX_DIM and len(data) <= MAX_BYTES:
            return data, ext
        im.thumbnail((MAX_DIM, MAX_DIM))
        out = io.BytesIO()
        if ext == "jpg":
            im.convert("RGB").save(out, "JPEG", quality=85)
        else:
            im.save(out, "PNG", optimize=True)
            ext = "png"
        return out.getvalue(), ext
    except Exception:
        return data, ext


def add(by, data, name="", note=""):
    """Validate and store one image. Returns its meta dict. Raises ImageError with a message safe to show."""
    if by not in ("human", "claude", "chatgpt"):
        raise ImageError("unknown sender")
    if not data:
        raise ImageError("that image is empty")
    kind = sniff(data)
    if not kind:
        raise ImageError("only PNG, JPEG, GIF and WebP images are accepted")
    if len(data) > MAX_BYTES:
        data, ext = _shrink(data, kind[0])
        kind = sniff(data) or kind
        if len(data) > MAX_BYTES:
            raise ImageError(f"that image is {len(data) // 1000} KB; the limit is {MAX_BYTES // 1000} KB. "
                             "Capture a smaller area (or install Pillow so big ones are shrunk automatically).")
    else:
        data, ext = _shrink(data, kind[0]) if len(data) > 1_500_000 else (data, kind[0])
        kind = sniff(data) or kind
    ext, mime = kind
    with core.FileLock(folder() / "index.lock"):
        idx = _load()
        iid = f"img_{idx['next']:06d}"
        idx["next"] += 1
        meta = {"id": iid, "by": by, "name": re.sub(r"\.{2,}", ".", re.sub(r"[^\w .()-]+", "_", str(name))).strip(". ")[:60] or iid, "note": str(note)[:300],
                "ext": ext, "mime": mime, "bytes": len(data), "ts": time.strftime("%H:%M:%S")}
        (folder() / f"{iid}.{ext}").write_bytes(data)
        idx["items"].append(meta)
        while len(idx["items"]) > MAX_STORED:
            old = idx["items"].pop(0)
            (folder() / f"{old['id']}.{old['ext']}").unlink(missing_ok=True)
        core._write_json(_index_path(), idx)
    return meta


def meta(iid):
    if not _ID.match(str(iid or "")):
        raise ImageError("that isn't an image id (they look like img_000001). list_images shows them.")
    m = next((x for x in _load()["items"] if x["id"] == iid), None)
    if not m:
        raise ImageError(f"no image {iid} (it may have been cleared by a fresh session)")
    return m


def path(iid):
    m = meta(iid)
    p = folder() / f"{m['id']}.{m['ext']}"
    if not p.exists():
        raise ImageError(f"image {iid} is missing on disk")
    return p


def read(iid):
    m = meta(iid)
    return m, path(iid).read_bytes()


def recent(n=10):
    return _load()["items"][-max(1, int(n)):]


def clear():
    """New fresh session: the transcript they belonged to is gone too. The id counter keeps counting, so an id is never
    reused: a page that was open during the old session would otherwise show its cached old picture for the new id."""
    nxt = _load().get("next", 1)
    shutil.rmtree(folder(), ignore_errors=True)
    folder().mkdir(parents=True, exist_ok=True)
    core._write_json(_index_path(), {"next": nxt, "items": []})


def new_for(me, tail=24):
    """Images posted by anyone else since `me` last spoke (newest MAX_PER_MESSAGE), as [(meta, path)]."""
    out = []
    for e in reversed(core.load_transcript(tail)):
        if e.get("speaker") == me:
            break
        for im in reversed(e.get("images") or []):
            try:
                out.append((meta(im["id"]), path(im["id"])))
            except ImageError:
                pass
    return list(reversed(out[:MAX_PER_MESSAGE]))


def describe(entry_images):
    parts = []
    for im in entry_images or []:
        try:
            m = meta(im["id"])
            parts.append(f"{m['id']} ({m['name']}, from {core.PRETTY.get(m['by'], m['by'])})")
        except ImageError:
            parts.append(f"{im.get('id', '?')} (no longer available)")
    return parts


def b64(p):
    return base64.b64encode(p.read_bytes()).decode("ascii")


def mime_of(p):
    k = sniff(p.read_bytes()[:16])
    return k[1] if k else "image/png"


def post(by, text, ids):
    """Put a message with attached images into the shared transcript."""
    core.append_transcript(by, text, images=[{"id": i, "name": meta(i)["name"]} for i in ids])
