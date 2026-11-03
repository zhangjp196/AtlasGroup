# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller spec：Windows 桌面端（AtlasX.exe，免控制台窗口）。

用法（Windows）：
    .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ^
        --distpath dist --workpath build\desktop desktop\AtlasX-windows.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
WEBVIEW_DATAS, WEBVIEW_BINARIES, WEBVIEW_HIDDEN = collect_all("webview")

_METADATA_DISTS = (
    "genai_prices", "pydantic_ai_slim", "pydantic_ai", "pydantic_graph",
    "openai", "httpx2", "tiktoken",
)
META_DATAS = []
for _dist in _METADATA_DISTS:
    try:
        META_DATAS += copy_metadata(_dist)
    except Exception:
        pass

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "config" / "agent_zero"), "config/agent_zero"),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "VERSION"), "."),
] + WEBVIEW_DATAS + META_DATAS

hiddenimports = (
    WEBVIEW_HIDDEN
    + collect_submodules("services")
    + collect_submodules("uvicorn")
)

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=WEBVIEW_BINARIES,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "test",
        "torch", "torchvision", "torchaudio", "tensorboardX", "onnxruntime",
        "transformers", "huggingface_hub", "sentence_transformers", "tensorflow",
        "scipy", "pandas", "matplotlib", "pyarrow", "sklearn", "sympy",
        "pytest", "IPython", "logfire",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AtlasX",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AtlasX",
)
