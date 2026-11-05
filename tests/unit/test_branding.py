"""品牌改名回归：显示层是 AtlasX，标识层保持 Atlas。

V3 改名只动显示层（窗口 / 文案 / 产物名），数据目录、bundle id、Keychain、
`.atlas/` 约定、模块与接口路径都保持旧名——否则升级后会话、附件与已存 API Key
会失联。这组测试把两边的边界钉死，避免以后「顺手」把标识层也改了。
"""
from __future__ import annotations

from pathlib import Path

from services.agent_zero import version

ROOT = Path(__file__).resolve().parents[2]


def test_display_name_and_version():
    assert version.APP_NAME == "AtlasX"
    assert version.LEGACY_APP_NAME == "Atlas"
    # 版本号只认 VERSION 文件，且 V3 起跳（不把具体小版本写死在测试里）
    assert version.APP_VERSION == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version.APP_VERSION.split(".")[0] == "3"


def test_app_info_reports_brand(client):
    data = client.get("/api/agent-zero/app/info").json()["data"]
    assert data["name"] == version.APP_NAME
    assert data["version"] == version.APP_VERSION


def test_default_role_uses_brand_and_legacy_name_is_migrated(app_env):
    from services.agent_zero import database
    from services.agent_zero.stores import zero_store

    database.init_database()
    zero_store.ensure_defaults()
    role = zero_store.get_role("default")
    assert role["name"] == version.APP_NAME

    # 用户自定义过的名字不动
    zero_store.upsert_role({"id": "custom", "group_id": "default",
                            "name": "我的助手", "envelope": {}})
    zero_store.ensure_defaults()
    assert zero_store.get_role("custom")["name"] == "我的助手"

    # 旧默认名（用户没改过）跟随改名
    zero_store.upsert_role({"id": "default", "group_id": "default",
                            "name": version.LEGACY_APP_NAME,
                            "envelope": role.get("envelope") or {}})
    zero_store.ensure_defaults()
    assert zero_store.get_role("default")["name"] == version.APP_NAME


def test_user_agent_uses_brand_and_real_version():
    from services.agent_zero.routes.app_routes import _user_agent

    assert _user_agent() == f"{version.APP_NAME}/{version.APP_VERSION}"


def test_diagnostics_bundle_is_branded():
    from services.agent_zero.services import diagnostics_service

    assert diagnostics_service.bundle_name().startswith("atlasx-diagnostics-")


def test_identifier_layer_keeps_legacy_names():
    """这些是「改了会丢数据 / 影响签名」的标识，必须保持 Atlas。"""
    launcher = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
    assert 'APP_HOME_DIR = "Atlas"' in launcher
    assert 'home / "atlas.lock"' in launcher
    spec = (ROOT / "desktop" / "AtlasX.spec").read_text(encoding="utf-8")
    assert 'bundle_identifier="com.aiplatform.atlas"' in spec
    secret = (ROOT / "services" / "agent_zero" / "stores" / "secret_store.py").read_text(
        encoding="utf-8")
    assert 'SERVICE = "com.atlas.agentzero"' in secret
    client = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(
        encoding="utf-8")
    assert "API_PREFIX = '/api/agent-zero'" in client


def test_packaging_artifacts_are_branded():
    spec = (ROOT / "desktop" / "AtlasX.spec").read_text(encoding="utf-8")
    assert 'name="AtlasX.app"' in spec
    assert '"CFBundleName": "AtlasX"' in spec
    assert '"CFBundleDisplayName": "AtlasX"' in spec
    assert 'name="Atlas"' not in spec
    script = (ROOT / "desktop" / "build_macos.sh").read_text(encoding="utf-8")
    assert "AtlasX-${VERSION}.dmg" in script
    assert "dist/AtlasX.app" in script
    assert 'volname "AtlasX"' in script
    win = (ROOT / "desktop" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "AtlasX.exe" in win
