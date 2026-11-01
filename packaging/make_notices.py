# AI Bridge: two AIs, one workspace.
# Copyright (C) 2026 Tyler Watson
# SPDX-License-Identifier: GPL-3.0-or-later  (see LICENSE)
"""Writes THIRD-PARTY-NOTICES.txt: every library bundled into the Windows app, with its license and copyright text.

Run by build_windows.bat with the build's own Python, so it lists exactly what that environment (and so the app) contains:
    python packaging/make_notices.py "dist/AI Bridge/THIRD-PARTY-NOTICES.txt"
"""
import sys
from importlib import metadata
from pathlib import Path

SKIP = {"pip", "wheel"}  # build tools that don't end up in the app
LICENSE_NAMES = ("license", "licence", "copying", "notice", "authors")


def _license_name(md):
    expr = md.get("License-Expression")
    if expr:
        return expr
    lic = (md.get("License") or "").strip()
    if lic and len(lic) < 80 and "\n" not in lic:
        return lic
    classes = [c.split("::")[-1].strip() for c in (md.get_all("Classifier") or []) if c.startswith("License ::")]
    if classes:
        return ", ".join(classes)
    return "see the license text below" if lic else "unknown (see the project's home page)"


def _home(md):
    for u in md.get_all("Project-URL") or []:
        label, _, url = u.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository", "source code", "code"):
            return url.strip()
    return (md.get("Home-page") or "").strip()


def _license_texts(dist):
    seen, out = set(), []
    for f in dist.files or []:
        name = f.name.lower()
        if any(name.startswith(n) for n in LICENSE_NAMES) and ("dist-info" in str(f).lower() or "licenses" in str(f).lower()):
            try:
                txt = Path(f.locate()).read_text(encoding="utf-8", errors="replace").strip()
            except (OSError, UnicodeError):
                continue
            if txt and txt not in seen:
                seen.add(txt)
                out.append((f.name, txt))
    if not out:
        lic = (dist.metadata.get("License") or "").strip()
        if len(lic) > 80:  # some packages put the whole text in the metadata field
            out.append(("License", lic))
    return out


def _python_license():
    for p in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE", Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"):
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace").strip()
    return "Python Software Foundation License. Full text: https://docs.python.org/3/license.html"


def main(out_path):
    dists = {}
    for d in metadata.distributions():
        name = (d.metadata.get("Name") or "").strip()
        if name and name.lower() not in SKIP and name.lower() not in dists:
            dists[name.lower()] = d
    parts = [
        "THIRD-PARTY NOTICES",
        "",
        "AI Bridge (Copyright (C) 2026 Tyler Watson) is free software under the GNU GPL v3 or later; see LICENSE.txt.",
        "The Windows app also contains the following software by other people. Each keeps its own license, reproduced below.",
        "This file is generated from the build environment by packaging/make_notices.py.",
        "",
        "=" * 78,
        f"Python {sys.version.split()[0]}  (the runtime bundled into the app)",
        "License: Python Software Foundation License",
        "Home: https://www.python.org/",
        "-" * 78,
        _python_license(),
        "",
        "=" * 78,
        "PyInstaller bootloader  (starts the app)",
        "License: GPL-2.0-or-later with the PyInstaller bootloader exception (allows bundling any program)",
        "Home: https://pyinstaller.org/",
        "",
    ]
    for key in sorted(dists):
        d = dists[key]
        md = d.metadata
        parts += ["=" * 78, f"{md.get('Name')} {d.version}", f"License: {_license_name(md)}"]
        home = _home(md)
        if home:
            parts.append(f"Home: {home}")
        author = (md.get("Author") or md.get("Author-email") or "").strip()
        if author:
            parts.append(f"Author: {author}")
        for fname, txt in _license_texts(d):
            parts += ["-" * 78, f"[{fname}]", txt]
        parts.append("")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {out_path}: {len(dists)} packages")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "THIRD-PARTY-NOTICES.txt")
