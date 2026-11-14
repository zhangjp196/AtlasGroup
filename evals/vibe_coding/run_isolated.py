#!/usr/bin/env python3
"""Run the real-model vibe suite against a disposable, isolated AgentZero app."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "agent_zero" / "config.yaml"
RUNNER = Path(__file__).with_name("run.py")


def _effective_model() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from services.agent_zero.runtime.zero.llm import get_llm_config

    return asyncio.run(get_llm_config("default"))


def _usable_api_key(value: Any) -> bool:
    key = str(value or "").strip()
    return bool(key) and key.casefold() not in {
        "empty", "none", "null", "your_api_key", "your_api_key_here",
        "changeme", "replace_me", "placeholder",
    }


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _wait_for_app(base_url: str, process: subprocess.Popen[str], log_path: Path,
                  timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
            raise RuntimeError(f"评测服务提前退出（{process.returncode}）：\n{tail}")
        try:
            with urlopen(base_url + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.25)
    raise TimeoutError("临时 AgentZero 服务 60 秒内未就绪")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _serve(temp_root: Path, config_path: Path, port: int,
           model_cfg: dict[str, Any], log_file) -> subprocess.Popen[str]:
    server_script = temp_root / "run_server.py"
    server_script.write_text(
        "import os\n"
        "import uvicorn\n"
        "from services.agent_zero.webapp import create_app\n"
        "root = os.environ['AI_PLATFORM_ROOT']\n"
        "config = os.environ['AI_PLATFORM_CONFIG_YAML']\n"
        "app = create_app(config_yaml=config, web_dir=os.path.join(root, 'web'), "
        "logs_dir=os.path.join(root, 'logs'))\n"
        "uvicorn.run(app, host='127.0.0.1', "
        "port=int(os.environ['ATLASX_EVAL_PORT']), log_config=None, access_log=False)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update({
        "AI_PLATFORM_ROOT": str(temp_root),
        "AI_PLATFORM_CONFIG_YAML": str(config_path),
        "AGENT_ZERO_LOGS_DIR": str(temp_root / "logs"),
        "AGENT_ZERO_WEB_DIR": str(ROOT / "web"),
        "ATLASX_EVAL_PORT": str(port),
        "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "services" / "agent_zero"))),
        "PYTHONUNBUFFERED": "1",
        "AGENT_ZERO_LLM_BASE_URL": str(model_cfg.get("base_url") or ""),
        "AGENT_ZERO_LLM_API_KEY": str(model_cfg.get("api_key") or ""),
        "AGENT_ZERO_LLM_MODEL": str(model_cfg.get("model") or ""),
    })
    protocol = str(model_cfg.get("protocol") or "").strip()
    if protocol:
        env["AGENT_ZERO_LLM_PROTOCOL"] = protocol
    return subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _eval_config(temp_root: Path, model_cfg: dict[str, Any], port: int) -> Path:
    config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
    config.setdefault("server", {}).update({
        "host": "127.0.0.1", "port": port, "auto_port": False,
    })
    config["sqlalchemy"] = {"db_path": "data/agent_zero/agent_zero.db", "echo": False}
    llm = dict(config.get("llm") or {})
    llm.update({
        "base_url": str(model_cfg.get("base_url") or ""),
        "api_key": "",  # The key is passed only in the temporary server process environment.
        "model": str(model_cfg.get("model") or ""),
        "active_model": "default",
    })
    llm.pop("models", None)
    llm.pop("subscriptions", None)
    if model_cfg.get("protocol"):
        llm["protocol"] = str(model_cfg["protocol"])
    config["llm"] = llm
    path = temp_root / "config" / "agent_zero" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def _run_suite(base_url: str, timeout: int, case_id: str | None,
               raw_output: Path) -> int:
    command = [sys.executable, str(RUNNER), "--base-url", base_url,
               "--timeout", str(timeout), "--output", str(raw_output)]
    if case_id:
        command.extend(("--case", case_id))
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
    return process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="只运行指定 case id；默认运行全部案例")
    parser.add_argument("--timeout", type=int, default=300,
                        help="单案例运行上限秒数（默认 300）")
    parser.add_argument("--output", type=Path,
                        help="报告路径；默认写入 evals/vibe_coding/reports/")
    args = parser.parse_args()

    model_cfg = _effective_model()
    if not _usable_api_key(model_cfg.get("api_key")):
        parser.error("当前模型 API key 为空或仍是占位值；请先在 AgentZero 模型设置中配置")
    if not model_cfg.get("base_url") or not model_cfg.get("model"):
        parser.error("当前模型配置缺少 base_url 或 model")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / "evals" / "vibe_coding" / "reports" / f"{stamp}.json"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    port = _free_port()

    with tempfile.TemporaryDirectory(prefix="agentzero-vibe-eval-") as temp:
        temp_root = Path(temp)
        (temp_root / "web").mkdir(parents=True)
        (temp_root / "logs").mkdir(parents=True)
        config_path = _eval_config(temp_root, model_cfg, port)
        raw_report = temp_root / "case-results.json"
        log_path = temp_root / "server.log"
        base_url = f"http://127.0.0.1:{port}"
        print(f"模型评测：{model_cfg['model']} @ {urlsplit(model_cfg['base_url']).hostname}")
        print("隔离数据库：临时目录（不会读取或修改现有会话）")
        print(f"案例超时：{max(30, args.timeout)} 秒；报告：{output}")

        process = None
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                process = _serve(temp_root, config_path, port, model_cfg, log_file)
                _wait_for_app(base_url, process, log_path)
                print("隔离 AgentZero 服务已就绪，开始评测。", flush=True)
                return_code = _run_suite(base_url, max(30, args.timeout),
                                         args.case, raw_report)
        except Exception as error:
            print(f"评测失败：{error}", file=sys.stderr)
            return_code = 1
        finally:
            if process is not None:
                _stop(process)

        results = json.loads(raw_report.read_text(encoding="utf-8")) if raw_report.exists() else []
        report = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": model_cfg["model"],
            "base_url_host": urlsplit(model_cfg["base_url"]).hostname,
            "timeout_seconds": max(30, args.timeout),
            "cases": results,
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        print(f"基线报告：{output}")
        return return_code


if __name__ == "__main__":
    raise SystemExit(main())
