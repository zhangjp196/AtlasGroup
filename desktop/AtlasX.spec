# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：构建 macOS 原生 .app（AtlasX）。

用法：.venv/bin/python -m PyInstaller --noconfirm --clean \
        --distpath dist --workpath build/desktop desktop/AtlasX.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
WEBVIEW_DATAS, WEBVIEW_BINARIES, WEBVIEW_HIDDEN = collect_all("webview")

# 这些包在 import 阶段用 importlib.metadata.version() 读自己的版本号；
# PyInstaller 默认不带 dist-info，缺了就启动报 PackageNotFoundError。
_METADATA_DISTS = (
    "genai_prices", "pydantic_ai_slim", "pydantic_ai", "pydantic_graph",
    "openai", "anthropic", "docstring_parser", "httpx2", "tiktoken",
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
    (str(ROOT / "desktop" / "Atlas.icns"), "desktop"),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "VERSION"), "."),
] + WEBVIEW_DATAS + META_DATAS

hiddenimports = (
    WEBVIEW_HIDDEN
    + collect_submodules("services")
    + collect_submodules("uvicorn")
    + collect_submodules("anthropic")
    + collect_submodules("pydantic_ai.models.anthropic")
    + collect_submodules("pydantic_ai.providers.anthropic")
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
        # 注意：不能排除 unittest —— pydantic-ai → pydantic-graph → logfire_api
        # 在模块导入阶段就会 `import unittest`，排掉会让 LLM 通道整体 ImportError。
        "tkinter", "test",
        # pydantic-ai 的 embeddings → sentence-transformers 是**函数内延迟导入**，
        # 运行时不会用到；PyInstaller 会静态跟进去，把 torch/scipy 等整条链打包，
        # 让 DMG 从 ~50MB 涨到 ~300MB。这里按旧包的基线排除（旧包只带 numpy）。
        "torch", "torchvision", "torchaudio", "tensorboardX", "onnxruntime",
        "transformers", "huggingface_hub", "sentence_transformers", "tensorflow",
        "scipy", "pandas", "matplotlib", "pyarrow", "sklearn", "sympy",
        "pytest", "IPython",
        # 冻结包里 pydantic 的插件机制会拉起 logfire 的 pydantic 集成，
        # 它依赖 inspect.getsource，打包后必然失败；launcher 同时设了
        # PYDANTIC_DISABLE_PLUGINS=1，这里再把模块本身排掉。
        "logfire",
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
    icon=str(ROOT / "desktop" / "Atlas.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AtlasX",
)

app = BUNDLE(
    coll,
    name="AtlasX.app",
    icon=str(ROOT / "desktop" / "Atlas.icns"),
    bundle_identifier="com.aiplatform.atlas",
    info_plist={
        "CFBundleName": "AtlasX",
        "CFBundleDisplayName": "AtlasX",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "AtlasX · local single-user agent",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
