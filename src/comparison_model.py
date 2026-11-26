"""Pie slices for app usage; ranking rows remain independent of presentation."""
from __future__ import annotations

from math import fsum

from dashboard_model import OTHER


def pie_parts(rows, total=None, threshold=5.0):
    """Group positive app rows below ``threshold`` percent into a final Other slice.

    Shares are calculated before rounding against the complete supplied period.
    Every returned row is a copy, including the individual rows inside Other.
    A nonpositive denominator falls back to the sum of positive app durations.
    """
    apps = [dict(row) for row in rows if row["screen"] > 0]
    positive_total = fsum(row["screen"] for row in apps)
    denominator = positive_total if total is None or total <= 0 else total
    if not apps:
        return []

    slices = []
    children = []
    for row in apps:
        row["share"] = row["screen"] / denominator * 100
        if row["share"] < threshold:
            children.append(row)
        else:
            row.update(is_other=False, children=(), members=(row["name"],))
            slices.append(row)

    if children:
        seconds = fsum(row["screen"] for row in children)
        slices.append(dict(name="Khác", kind="other", color=OTHER,
                           screen=seconds, share=seconds / denominator * 100,
                           is_other=True, children=tuple(children),
                           members=tuple(row["name"] for row in children)))
    return slices
