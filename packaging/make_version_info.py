"""Writes packaging/version_info.txt (the version Windows shows in the .exe's Properties) from aibridge/version.py."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "aibridge"))
from version import __version__  # noqa: E402

nums = [int(x) for x in re.findall(r"\d+", __version__)[:3]] + [0, 0, 0, 0]
t = tuple(nums[:4])
(Path(__file__).parent / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'AI Bridge'),
      StringStruct('FileDescription', 'AI Bridge'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'AIBridge'),
      StringStruct('OriginalFilename', 'AIBridge.exe'),
      StringStruct('ProductName', 'AI Bridge'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
print(__version__)
