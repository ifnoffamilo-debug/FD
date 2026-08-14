from __future__ import annotations

from html import escape
from typing import Any

from app.constants import SCOPES, TYPE_LABELS
from app.database import Summary


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".00", "") + " ₽"


def transaction_preview(data: dict[str, Any]) -> str:
    tx_type = data.get("tx_type", "expense")
    sign = "🟢" if tx_type == "income" else "🔴"
    scope = data.get("finance_scope", "work")
    lines = [
        f"{sign} <b>{TYPE_LABELS.get(tx_type, tx_type)}</b>",
        f"Раздел: {SCOPES.get(scope, scope)}",
        f"Сумма: <b>{money(float(data['amount']))}</b>",
        f"Категория: {escape(str(data.get('category') or 'Не указана'))}",
    ]
    if scope == "work":
        lines.append(f"Объект: {escape(str(data.get('object_name') or 'Без объекта'))}")
    if data.get("vehicle_name"):
        lines.append(f"Авто: {escape(str(data['vehicle_name']))}")
    if data.get("vehicle_expense_type"):
        lines.append(f"Тип авторасхода: {escape(str(data['vehicle_expense_type']))}")
    if data.get("fuel_type"):
        lines.append(f"Топливо: {escape(str(data['fuel_type']))}")
    if data.get("fuel_liters"):
        lines.append(f"Литры: {float(data['fuel_liters']):.1f} л")
    if data.get("odometer"):
        lines.append(f"Пробег: {float(data['odometer']):,.0f} км")
    lines.append(f"Комментарий: {escape(str(data.get('comment') or 'Без комментария'))}")
    return "\n".join(lines)


def summary_text(title: str, summary: Summary) -> str:
    emoji = "🟢" if summary.profit >= 0 else "🔴"
    return (
        f"<b>{escape(title)}</b>\n\n"
        f"Доходы: <b>{money(summary.income)}</b>\n"
        f"Расходы: <b>{money(summary.expense)}</b>\n"
        f"{emoji} Результат: <b>{money(summary.profit)}</b>\n"
        f"Операций: {summary.count}"
    )


def transactions_text(rows: list[dict[str, Any]], title: str = "Последние операции") -> str:
    if not rows:
        return f"<b>{escape(title)}</b>\n\nОпераций пока нет."
    lines = [f"<b>{escape(title)}</b>", ""]
    for row in rows:
        icon = "🟢" if row["tx_type"] == "income" else "🔴"
        obj = f" · {escape(str(row['object_name']))}" if row.get("object_name") else ""
        car = f" · 🚗 {escape(str(row['vehicle_expense_type']))}" if row.get("vehicle_expense_type") else ""
        if row.get("fuel_type"):
            car += f" ({escape(str(row['fuel_type']))})"
        receipt = " · 📷" if row.get("receipt_file_id") else ""
        changed = " · ✏️" if row.get("updated_at") else ""
        lines.append(
            f"{icon} #{row['id']} <b>{money(float(row['amount']))}</b> · {escape(str(row['category']))}{obj}{car}{receipt}{changed}\n"
            f"<code>{row['created_at']}</code>"
        )
    return "\n\n".join(lines)
