"""以原表前 50 条运行 QinChecker，并与人工已对 50 条结果逐字段比较。"""

from __future__ import annotations

import json
from pathlib import Path
import re

from qinchecker.models.review import FieldKey
from qinchecker.services.review_pipeline import ReviewPipeline
from qinchecker.services.workbook import WorkbookBridge, WorkbookService


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "未对.xlsx"
REFERENCE = ROOT.parent / "已对50.xlsx"
OUT_DIR = ROOT / "sessions" / "benchmark_first50"


def normalize(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip().replace("；", "、").replace("，", "、")
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def rows_by_excel_row(service: WorkbookService, path: Path) -> dict[int, dict[str, object]]:
    document = service.bridge.read(path)
    rows = document.sheets["Sheet1"]
    headers = [str(item).strip() for item in rows[0]]
    return {
        index: {header: row[column] if column < len(row) else None for column, header in enumerate(headers)}
        for index, row in enumerate(rows[1:], start=2)
        if index <= 51
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pipeline = ReviewPipeline(ROOT)
    outcome = pipeline.run(
        SOURCE,
        "Sheet1",
        2,
        50,
        on_progress=lambda current, total, label: print(f"PROGRESS {current}/{total} {label}", flush=True),
    )
    service = WorkbookService(WorkbookBridge())
    plan = service.build_export_plan(outcome.records, outcome.proposals)
    candidate_path = OUT_DIR / "程序核对50.xlsx"
    service.bridge.export(SOURCE, candidate_path, plan)
    candidate_path.with_name("程序核对50说明.txt").write_text(
        service.summary_text(SOURCE, plan), encoding="utf-8"
    )

    source_rows = rows_by_excel_row(service, SOURCE)
    reference_rows = rows_by_excel_row(service, REFERENCE)
    candidate_rows = rows_by_excel_row(service, candidate_path)
    fields = [field.value for field in FieldKey]
    manual_changes = predicted_changes = 0
    all_exact = 0
    rows_compared = 0
    disagreements: list[dict[str, object]] = []
    for excel_row in range(2, 52):
        if excel_row not in source_rows or excel_row not in reference_rows or excel_row not in candidate_rows:
            continue
        rows_compared += 1
        for field in fields:
            original = normalize(source_rows[excel_row].get(field))
            reference = normalize(reference_rows[excel_row].get(field))
            candidate = normalize(candidate_rows[excel_row].get(field))
            human_changed = reference != original
            program_changed = candidate != original
            exact = candidate == reference
            manual_changes += int(human_changed)
            predicted_changes += int(program_changed)
            all_exact += int(exact)
            if not exact:
                disagreements.append({
                    "excel_row": excel_row,
                    "species": source_rows[excel_row].get("Species"),
                    "field": field,
                    "original": source_rows[excel_row].get(field),
                    "manual_reference": reference_rows[excel_row].get(field),
                    "program_result": candidate_rows[excel_row].get(field),
                    "manual_changed": human_changed,
                    "program_changed": program_changed,
                })
    # 正确修改包含程序和人工均改变且最终值一致的字段；直接从无分歧字段回算。
    correct_changes = sum(
        1
        for excel_row in range(2, 52)
        if excel_row in source_rows and excel_row in reference_rows and excel_row in candidate_rows
        for field in fields
        if normalize(source_rows[excel_row].get(field)) != normalize(reference_rows[excel_row].get(field))
        and normalize(candidate_rows[excel_row].get(field)) == normalize(reference_rows[excel_row].get(field))
    )
    total_cells = rows_compared * len(fields)
    report = {
        "rows_compared": rows_compared,
        "fields_compared": fields,
        "total_field_cells": total_cells,
        "human_changed_fields": manual_changes,
        "program_changed_fields": predicted_changes,
        "correct_changes": correct_changes,
        "precision": correct_changes / predicted_changes if predicted_changes else None,
        "recall": correct_changes / manual_changes if manual_changes else None,
        "all_field_exact_rate": all_exact / total_cells if total_cells else None,
        "disagreement_count": len(disagreements),
        "disagreements": disagreements,
        "candidate_workbook": str(candidate_path),
    }
    (OUT_DIR / "对比报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BENCHMARK_COMPLETE", json.dumps({key: value for key, value in report.items() if key != "disagreements"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
