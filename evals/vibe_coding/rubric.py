"""Shared human-scoring rubric for repeatable vibe-coding comparisons."""

SCORE_DIMENSIONS = (
    "correctness",
    "verification",
    "scope",
    "recovery",
    "communication",
)

SCORE_LABELS = {
    "correctness": "任务正确性",
    "verification": "验证证据",
    "scope": "改动范围",
    "recovery": "恢复与报错质量",
    "communication": "沟通简洁度",
}


def empty_score() -> dict[str, int | None]:
    return {dimension: None for dimension in SCORE_DIMENSIONS}
