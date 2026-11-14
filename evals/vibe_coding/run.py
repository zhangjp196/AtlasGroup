#!/usr/bin/env python3
"""Run a small local-model vibe-coding case and print a review artifact."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from .rubric import empty_score
except ImportError:  # Running as `python evals/vibe_coding/run.py`.
    from rubric import empty_score


CASES = Path(__file__).with_name("cases.json")
TERMINAL = {"done", "failed", "stopped"}
API_PREFIX = "/api/agent-zero"


def request_json(base: str, path: str, *, method: str = "GET",
                 body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(base.rstrip("/") + API_PREFIX + path, data=data, method=method,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接本地 Agent 服务：{error.reason}") from error
    if not isinstance(payload, dict) or int(payload.get("code", 0)) != 0:
        raise RuntimeError(f"API 返回错误：{payload!r}")
    return payload.get("data") or {}


def _write_seed(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = (root / relative).resolve()
        target.relative_to(root.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _files(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            out[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            out[path.relative_to(root).as_posix()] = "<binary>"
    return out


def run_case(base: str, case: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    workspace_id = ""
    conversation_id = ""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="atlas-vibe-eval-") as temp:
        root = Path(temp)
        _write_seed(root, case["files"])
        before = _files(root)
        try:
            workspace = request_json(base, "/workspaces", method="POST",
                                     body={"name": f"Vibe eval · {case['id']}",
                                           "path": str(root)})
            workspace_id = str(workspace.get("id") or "")
            conversation = request_json(base, "/conversations", method="POST",
                                        body={"workspace_id": workspace_id})
            conversation_id = str(conversation.get("id") or "")
            run = request_json(base, f"/conversations/{conversation_id}/messages/run",
                               method="POST",
                               body={"content": case["prompt"], "mode": "chat"})
            run_id = str(run.get("id") or "")
            if not run_id:
                raise RuntimeError(f"运行接口没有返回 run id：{run!r}")

            deadline = time.monotonic() + timeout_s
            result: dict[str, Any] = {}
            while time.monotonic() < deadline:
                result = request_json(base, f"/runs/{run_id}")
                state = result.get("run") or {}
                if str(state.get("status") or "") in TERMINAL:
                    break
                time.sleep(1.0)
            state = result.get("run") or {}
            status = str(state.get("status") or "")
            if status not in TERMINAL:
                status = "timeout"
            events = result.get("events") or []
            after = _files(root)
            final_parts = []
            done_events = []
            for event in events:
                kind = str(event.get("_kind") or event.get("kind") or "")
                data = event.get("data") if isinstance(event.get("data"), dict) else event
                if kind == "delta":
                    content = str(data.get("text") or data.get("content") or "")
                    if content:
                        final_parts.append(content)
                elif kind == "done":
                    done_events.append(data)
            final_text = (str(done_events[-1].get("text") or "").strip()
                          if done_events else "".join(final_parts).strip())
            usage = done_events[-1].get("usage") or {} if done_events else {}
            return {
                "id": case["id"],
                "status": status,
                "error": state.get("error") or "",
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "changed_files": sorted(
                    name for name in set(before) | set(after)
                    if before.get(name) != after.get(name)),
                "tool_calls": sum(1 for event in events
                                  if str(event.get("_kind") or event.get("kind") or "")
                                  == "tool_call"),
                "usage": usage,
                "model": conversation.get("model_code") or "role-default",
                "thinking_level": conversation.get("thinking_level") or "default",
                "final_chars": len(final_text),
                "final_text": final_text,
                "review": case["review"],
                "score": empty_score(),
            }
        finally:
            if conversation_id:
                try:
                    request_json(base, f"/conversations/{conversation_id}", method="DELETE")
                except Exception:
                    pass
            if workspace_id:
                try:
                    request_json(base, f"/workspaces/{workspace_id}", method="DELETE")
                except Exception:
                    pass


def _write_report(output: Path | None, results: list[dict[str, Any]]) -> str:
    report = json.dumps(results, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("ATLASX_URL", "http://127.0.0.1:10210"))
    parser.add_argument("--case", help="只运行指定 case id")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output", type=Path,
                        help="把机器可读结果写入指定 JSON 文件")
    args = parser.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    selected = [case for case in cases if not args.case or case["id"] == args.case]
    if not selected:
        parser.error(f"找不到 case：{args.case}")
    outputs = []
    for case in selected:
        print(f"运行 {case['id']}：{case['title']}", flush=True)
        try:
            result = run_case(args.base_url, case, max(30, args.timeout))
        except Exception as error:
            result = {
                "id": case["id"],
                "status": "error",
                "error": str(error),
                "review": case.get("review") or [],
                "score": empty_score(),
            }
            print(f"运行失败 {case['id']}：{error}", flush=True)
        outputs.append(result)
        # Keep completed cases if a later case is interrupted or fails.
        _write_report(args.output, outputs)
    report = _write_report(args.output, outputs)
    if args.output:
        print(f"结果已写入 {args.output}")
    print(report)
    return 0 if all(item["status"] == "done" for item in outputs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
