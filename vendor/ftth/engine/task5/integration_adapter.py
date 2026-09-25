"""GEOAgent adapter for Task 5 hierarchical fiber links."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from task5_core import execute


def plan_hierarchical_fiber_links(command: dict[str, Any]) -> dict[str, Any]:
    """Run Task 5 from a GEOAgent command dictionary.

    ``base_dir`` is optional and defines relative Task-6 and output paths.
    The returned object contains only JSON-compatible values.
    """
    payload = copy.deepcopy(command)
    raw_base_dir = payload.pop("base_dir", payload.pop("_base_dir", None))
    base_dir = Path(raw_base_dir).expanduser().resolve() if raw_base_dir else Path.cwd().resolve()
    return execute(payload, base_dir)
