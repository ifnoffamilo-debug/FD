from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


HEADERS = [
    "ID",
    "Тип",
    "Сумма",
    "Категория",
    "Объект",
    "Комментарий",
    "Дата",
    "Telegram ID",
    "Источник",
]


async def create_excel(rows: list[dict], export_dir: Path) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"fd_finance_{datetime.now():%Y%m%d_%H%M%S}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Операции"
    ws.append(HEADERS)

    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for row in rows:
        ws.append(
            [
                row["id"],
                "Доход" if row["tx_type"] == "income" else "Расход",
                float(row["amount"]),
                row["category"],
                row.get("object_name") or "",
                row.get("comment") or "",
                row["created_at"],
                row["created_by"],
                row["source"],
            ]
        )

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    widths = [8, 12, 14, 24, 28, 42, 20, 18, 14]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    for cell in ws["C"][1:]:
        cell.number_format = '#,##0.00 "₽"'

    summary = wb.create_sheet("Итоги")
    income = sum(float(row["amount"]) for row in rows if row["tx_type"] == "income")
    expense = sum(float(row["amount"]) for row in rows if row["tx_type"] == "expense")
    summary.append(["Показатель", "Сумма"])
    summary.append(["Доходы", income])
    summary.append(["Расходы", expense])
    summary.append(["Прибыль", income - expense])
    summary["A1"].font = Font(bold=True)
    summary["B1"].font = Font(bold=True)
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 18
    for cell in summary["B"][1:]:
        cell.number_format = '#,##0.00 "₽"'

    wb.save(path)
    return path
