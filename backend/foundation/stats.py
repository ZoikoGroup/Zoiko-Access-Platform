"""Small statistics helpers shared by the report functions."""
from __future__ import annotations

import statistics


def summarize(values: list[float]) -> dict[str, float | int | None]:
    """How many, the middle value and the average, each rounded to one decimal. Empty input gives
    count 0 and no median or average (never a made-up zero)."""
    if not values:
        return {"count": 0, "median": None, "average": None}
    return {"count": len(values), "median": round(statistics.median(values), 1),
            "average": round(statistics.fmean(values), 1)}
