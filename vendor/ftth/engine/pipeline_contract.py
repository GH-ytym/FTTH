"""Cross-task contracts for the reproducible FTTH pipeline.

The individual task implementations intentionally keep their native formats.
This module is the small, format-aware boundary between them: it extracts the
authoritative IMB identity set, checks exact hand-off coverage, and records a
run manifest so a result can never be mistaken for a product from another
scenario.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


class PipelineContractError(ValueError):
    """Raised when two task hand-offs describe different service areas."""


def load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise PipelineContractError(f"Required pipeline input is missing: {source}")
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise PipelineContractError(f"Expected a JSON object at {source}")
    return payload


def _records(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for key in ("imb_points", "imbs", "nodes"):
        values = payload.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    yield item
            return


def imb_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = list(_records(payload))
    typed = [item for item in records if str(item.get("type") or item.get("TYPE") or "").upper() == "IMB"]
    return typed or records


def imb_code(record: dict[str, Any]) -> str:
    return str(record.get("code") or record.get("CODE") or record.get("id") or "").strip()


def imb_internal_id(record: dict[str, Any]) -> str:
    return str(record.get("id") or record.get("ID") or record.get("code") or record.get("CODE") or "").strip()


def imb_codes(payload: dict[str, Any]) -> set[str]:
    result = {imb_code(item) for item in imb_records(payload)}
    result.discard("")
    if not result:
        raise PipelineContractError("Payload contains no identifiable IMB records")
    return result


def imb_ids(payload: dict[str, Any]) -> set[str]:
    result = {imb_internal_id(item) for item in imb_records(payload)}
    result.discard("")
    if not result:
        raise PipelineContractError("Payload contains no identifiable IMB records")
    return result


def validate_imb_coverage(
    expected_payload: dict[str, Any],
    actual_payload: dict[str, Any],
    *,
    expected_label: str,
    actual_label: str,
) -> dict[str, Any]:
    """Require exact IMB code and internal-id coverage across a hand-off."""
    expected_codes = imb_codes(expected_payload)
    actual_codes = imb_codes(actual_payload)
    missing_codes = sorted(expected_codes - actual_codes)
    extra_codes = sorted(actual_codes - expected_codes)
    expected_ids = imb_ids(expected_payload)
    actual_ids = imb_ids(actual_payload)
    missing_ids = sorted(expected_ids - actual_ids)
    extra_ids = sorted(actual_ids - expected_ids)
    result = {
        "status": "PASS" if not any((missing_codes, extra_codes, missing_ids, extra_ids)) else "FAIL",
        "expected": expected_label,
        "actual": actual_label,
        "expected_count": len(expected_codes),
        "actual_count": len(actual_codes),
        "missing_codes": missing_codes,
        "extra_codes": extra_codes,
        "missing_internal_ids": missing_ids,
        "extra_internal_ids": extra_ids,
    }
    if result["status"] != "PASS":
        raise PipelineContractError(
            f"IMB coverage mismatch ({expected_label} -> {actual_label}): "
            f"missing={missing_codes[:5] or missing_ids[:5]}, "
            f"extra={extra_codes[:5] or extra_ids[:5]}"
        )
    return result


def validate_partition_membership(
    task2_payload: dict[str, Any], partition_payload: dict[str, Any]
) -> dict[str, Any]:
    """Check that every Task-2 IMB occurs exactly once in the ZPM result."""
    expected = imb_ids(task2_payload)
    regions = partition_payload.get("zpm_regions") or []
    members: list[str] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        members.extend(str(value) for value in (region.get("imb_ids") or []) if value not in (None, ""))
    counts: dict[str, int] = {}
    for value in members:
        counts[value] = counts.get(value, 0) + 1
    actual = set(counts)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    duplicate = sorted(value for value, count in counts.items() if count > 1)
    result = {
        "status": "PASS" if not any((missing, extra, duplicate)) else "FAIL",
        "expected_count": len(expected),
        "member_count": len(members),
        "missing_internal_ids": missing,
        "extra_internal_ids": extra,
        "duplicate_internal_ids": duplicate,
    }
    if result["status"] != "PASS":
        raise PipelineContractError(
            "Task-2 partition does not cover the exact IMB set: "
            f"missing={missing[:5]}, extra={extra[:5]}, duplicate={duplicate[:5]}"
        )
    return result


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


__all__ = [
    "PipelineContractError",
    "imb_records",
    "imb_codes",
    "imb_ids",
    "validate_imb_coverage",
    "validate_partition_membership",
    "load_json",
    "write_manifest",
]
