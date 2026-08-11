from __future__ import annotations

from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

TX_HEADERS=["ID","Тип","Сумма","Категория","Объект","Авто","Тип авторасхода","Комментарий","Чек","Дата","Источник"]


def _sheet_for_rows(ws, rows):
    ws.append(TX_HEADERS)
    for c in ws[1]: c.font=Font(bold=True)
    for r in rows:
        ws.append([r["id"],"Доход" if r["tx_type"]=="income" else "Расход",float(r["amount"]),r["category"],r.get("object_name") or "",r.get("vehicle_name") or "",r.get("vehicle_expense_type") or "",r.get("comment") or "","Да" if r.get("receipt_file_id") else "",r["created_at"],r["source"]])
    ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions
    widths=[8,12,15,24,28,24,22,40,10,20,14]
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
    for c in ws["C"][1:]: c.number_format='#,##0.00 "₽"'


async def create_excel(rows:list[dict], objects:list[dict], vehicles:list[dict], export_dir:Path)->Path:
    export_dir.mkdir(parents=True,exist_ok=True);path=export_dir/f"fd_finance_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    wb=Workbook();wb.remove(wb.active)
    for scope,title in (("work","Рабочие финансы"),("personal","Личные финансы")):
        ws=wb.create_sheet(title);_sheet_for_rows(ws,[r for r in rows if r.get("finance_scope","work")==scope])
    ws=wb.create_sheet("Объекты");ws.append(["ID","Объект","Цена заказа","Бюджет расходов","Статус"])
    for r in objects: ws.append([r["id"],r["name"],r["contract_amount"],r["budget_amount"],r["status"]])
    ws=wb.create_sheet("Автомобили");ws.append(["ID","Название","Пробег","Статус"])
    for r in vehicles: ws.append([r["id"],r["name"],r["current_mileage"],r["status"]])
    wb.save(path);return path
