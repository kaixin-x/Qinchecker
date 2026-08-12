"""对已缓存的连续前 N 条做阶段性基准比较；不访问网络也不改写工作簿。"""

from __future__ import annotations

import json
from pathlib import Path
import re

from qinchecker.models.review import FieldKey, ReviewState
from qinchecker.services.cache import SourceCache
from qinchecker.services.parsing import CountyIndex, HabitatGlossary, SourceParser
from qinchecker.services.workbook import WorkbookBridge, WorkbookService


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "未对.xlsx"
REFERENCE = ROOT.parent / "已对50.xlsx"


def norm(value: object, field: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("，", "、").replace("；", "、").strip()).casefold()
    if field in {FieldKey.EARLIEST_FLOWERING.value, FieldKey.LATEST_FLOWERING.value}:
        month = re.fullmatch(r"(\d{1,2})月", text)
        if month:
            return month.group(1)
    return text


def main() -> None:
    service = WorkbookService(WorkbookBridge())
    records = service.load_records(SOURCE, "Sheet1", 2, 50)
    ref_records = service.load_records(REFERENCE, "Sheet1", 2, 50)
    references = {record.excel_row: record for record in ref_records}
    cache = SourceCache(ROOT / "sessions" / "cache")
    parser = SourceParser(
        HabitatGlossary.from_csv(ROOT / "config" / "habitat_terms.csv"),
        CountyIndex.from_csv(ROOT / "config" / "qinling_counties.csv", ROOT / "config" / "admin_aliases.csv"),
    )
    fields = [item.value for item in FieldKey]
    rows_used = 0
    skipped_incomplete_rows = 0
    manual_changed = program_changed = correct_changes = exact_cells = judged_cells = policy_preserved_fields = 0
    disagreements: list[dict[str, object]] = []
    for record in records:
        snapshot = cache.load(record.latin_name)
        if snapshot is None or record.excel_row not in references:
            continue
        if not snapshot.is_complete:
            skipped_incomplete_rows += 1
            continue
        rows_used += 1
        proposals = parser.parse(record, snapshot).proposals
        proposal_by_field = {proposal.field.value: proposal for proposal in proposals}
        calculated = dict(record.values)
        for proposal in proposals:
            if proposal.state is ReviewState.AUTO_READY and proposal.has_change:
                calculated[proposal.field.value] = proposal.final_value
        reference = references[record.excel_row]
        for field in fields:
            proposal = proposal_by_field.get(field)
            # FOC 未给出该字段，或 FOC 生境与原中文相近而明确保留原值时，
            # 不以人工表的扩展内容反向判定程序错误。
            if proposal is None or proposal.parser_rule == "FOC 生境与原值语义相近，保留原值":
                policy_preserved_fields += 1
                continue
            judged_cells += 1
            original, program, human = (
                norm(record.values.get(field), field),
                norm(calculated.get(field), field),
                norm(reference.values.get(field), field),
            )
            human_changed = human != original
            program_changed_here = program != original
            manual_changed += int(human_changed)
            program_changed += int(program_changed_here)
            correct_changes += int(human_changed and program == human)
            exact_cells += int(program == human)
            if program != human:
                disagreements.append({"row": record.excel_row, "species": record.latin_name, "field": field,
                                      "original": record.values.get(field), "manual": reference.values.get(field),
                                      "program": calculated.get(field), "complete_snapshot": snapshot.is_complete})
    report = {
        "cached_rows_compared": rows_used,
        "skipped_incomplete_rows": skipped_incomplete_rows,
        "human_changed_fields": manual_changed,
        "program_changed_fields": program_changed,
        "correct_changes": correct_changes,
        "policy_preserved_fields_excluded": policy_preserved_fields,
        "judged_field_cells": judged_cells,
        "precision": correct_changes / program_changed if program_changed else None,
        "recall": correct_changes / manual_changed if manual_changed else None,
        "all_cell_exact_rate": exact_cells / judged_cells if judged_cells else None,
        "disagreements": disagreements,
    }
    destination = ROOT / "sessions" / "benchmark_first50" / "阶段性对比报告.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "disagreements": disagreements[:12]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
