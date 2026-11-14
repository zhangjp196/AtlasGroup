#!/usr/bin/env python3
"""Compare vibe-coding reports as a compact Markdown review artifact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .rubric import SCORE_DIMENSIONS, SCORE_LABELS
except ImportError:  # Running as `python evals/vibe_coding/summarize.py`.
    from rubric import SCORE_DIMENSIONS, SCORE_LABELS


def _load(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):  # Legacy output from run.py.
        return {"label": path.stem, "cases": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError(f"报告格式无效：{path}")
    label = raw.get("label")
    if not label:
        label = " · ".join(str(value) for value in
                           (raw.get("model"), raw.get("created_at")) if value)
    return {**raw, "label": str(label or path.stem),
            "source": str(path)}


def _score(case: dict[str, Any]) -> tuple[int, int] | None:
    scores = case.get("score") or case.get("scores")
    if not isinstance(scores, dict):
        return None
    values = []
    for dimension in SCORE_DIMENSIONS:
        value = scores.get(dimension)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in (0, 1, 2):
            raise ValueError(f"案例 {case.get('id', '?')} 的 {dimension} 分必须是 0、1、2 或 null")
        values.append(int(value))
    return (sum(values), len(values) * 2) if values else None


def _tokens(case: dict[str, Any]) -> int | None:
    usage = case.get("usage")
    if not isinstance(usage, dict) or not usage:
        return None
    total = usage.get("total_tokens") or usage.get("totalTokens")
    if total is not None:
        return int(total)
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
    total = int(input_tokens or 0) + int(output_tokens or 0)
    return total or None


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def render(reports: list[dict[str, Any]]) -> str:
    lines = [
        "# Vibe coding 评测对比",
        "",
        "评分由人工复核后填写在案例 `score` 字段；每项 0–2 分，总分 10 分。",
        "未评分维度不计入均分。失败或超时案例仍显示状态、用量和错误，不会被误计为通过。",
        "",
        "## 总览",
        "",
        "| 报告 | 时间 | 模型 | 通过 | 已评分维度 | 平均分 / 10 | 用时 | Tokens |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        cases = report["cases"]
        done = sum(case.get("status") == "done" for case in cases)
        scores = [_score(case) for case in cases]
        scored = [score for score in scores if score is not None]
        scored_dimensions = sum(maximum // 2 for _, maximum in scored)
        total_dimensions = len(cases) * len(SCORE_DIMENSIONS)
        score_text = "—"
        if scored:
            possible = sum(maximum for _, maximum in scored)
            score_text = f"{sum(value for value, _ in scored) / possible * 10:.1f}"
        elapsed = [float(case["elapsed_seconds"]) for case in cases
                   if case.get("elapsed_seconds") is not None]
        tokens = [_tokens(case) for case in cases]
        token_values = [value for value in tokens if value is not None]
        tokens_text = f"{sum(token_values):,}" if token_values else "—"
        lines.append(
            f"| {_cell(report['label'])} | {_cell(report.get('created_at'))} "
            f"| {_cell(report.get('model') or '—')} | {done}/{len(cases)} "
            f"| {scored_dimensions}/{total_dimensions} | {score_text} "
            f"| {sum(elapsed):.1f}s | {tokens_text} |"
        )

    for report in reports:
        lines.extend(["", f"## {report['label']}", "",
                      "| 案例 | 状态 | 得分 | 用时 | Tokens | 变更文件 | 错误 |",
                      "| --- | --- | ---: | ---: | ---: | --- | --- |"])
        for case in report["cases"]:
            score = _score(case)
            score_text = f"{score[0]}/{score[1]}" if score else "—"
            elapsed = case.get("elapsed_seconds")
            token_count = _tokens(case)
            files = ", ".join(case.get("changed_files") or []) or "—"
            error = str(case.get("error") or "").strip()
            lines.append(
                f"| {_cell(case.get('id'))} | {_cell(case.get('status'))} "
                f"| {score_text} | {f'{float(elapsed):.1f}s' if elapsed is not None else '—'} "
                f"| {f'{token_count:,}' if token_count is not None else '—'} "
                f"| {_cell(files)} | {_cell(error)} |"
            )
            scores = case.get("score") or case.get("scores") or {}
            provided = [f"{SCORE_LABELS[key]} {scores[key]}/2"
                        for key in SCORE_DIMENSIONS if scores.get(key) is not None]
            if provided:
                lines.append(f"\n评分：{'；'.join(provided)}。")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path,
                        help="按基线到新版本的顺序传入 JSON 报告")
    parser.add_argument("--output", type=Path, help="Markdown 输出路径；默认打印到终端")
    args = parser.parse_args()
    try:
        result = render([_load(path) for path in args.reports])
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
        print(f"对比摘要已写入 {args.output}")
    else:
        print(result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
