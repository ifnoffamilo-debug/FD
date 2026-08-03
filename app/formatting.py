from __future__ import annotations

from html import escape
from typing import Any

from app.constants import TYPE_LABELS
from app.database import Summary


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".00", "") + " ₽"


def transaction_preview(data: dict[str, Any]) -> str:
    tx_type = data.get("tx_type", "expense")
    sign = "🟢" if tx_type == "income" else "🔴"
    return (
        f"{sign} <b>{TYPE_LABELS.get(tx_type, tx_type)}</b>\n"
        f"Сумма: <b>{money(float(data['amount']))}</b>\n"
        f"Категория: {escape(str(data.get('category') or 'Не указана'))}\n"
        f"Объект: {escape(str(data.get('object_name') or 'Без объекта'))}\n"
        f"Комментарий: {escape(str(data.get('comment') or 'Без комментария'))}"
    )


def summary_text(title: str, summary: Summary) -> str:
    profit_emoji = "🟢" if summary.profit >= 0 else "🔴"
    return (
        f"<b>{escape(title)}</b>\n\n"
        f"Доходы: <b>{money(summary.income)}</b>\n"
        f"Расходы: <b>{money(summary.expense)}</b>\n"
        f"{profit_emoji} Прибыль: <b>{money(summary.profit)}</b>\n"
        f"Операций: {summary.count}"
    )


def transactions_text(rows: list[dict[str, Any]], title: str = "Последние операции") -> str:
    if not rows:
        return f"<b>{escape(title)}</b>\n\nОпераций пока нет."
    lines = [f"<b>{escape(title)}</b>", ""]
    for row in rows:
        icon = "🟢" if row["tx_type"] == "income" else "🔴"
        object_part = f" · {escape(row['object_name'])}" if row.get("object_name") else ""
        changed = " · ✏️ изменено" if row.get('updated_at') else ""
        lines.append(
            f"{icon} #{row['id']} <b>{money(float(row['amount']))}</b> · "
            f"{escape(row['category'])}{object_part}{changed}\n"
            f"<code>{row['created_at']}</code>"
        )
    return "\n\n".join(lines)
