"""Small display-formatting helpers shared by panels."""

from __future__ import annotations


def fmt_float(value: float | None, decimals: int = 3) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}"
