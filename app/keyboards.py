from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.constants import ALL_CATEGORIES
from app.formatting import money


MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Доход"), KeyboardButton(text="➖ Расход")],
        [KeyboardButton(text="✏️ Изменить расход"), KeyboardButton(text="🧾 Последние операции")],
        [KeyboardButton(text="📊 Отчёты"), KeyboardButton(text="🏗 По объекту")],
        [KeyboardButton(text="🧠 Умный ввод"), KeyboardButton(text="🤖 Спросить Groq")],
        [KeyboardButton(text="📤 Excel")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие",
)

CANCEL_MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True,
)

SKIP_OBJECT_MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Без объекта")], [KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True,
)

SKIP_COMMENT_MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Без комментария")], [KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True,
)


def categories_keyboard(tx_type: str, *, prefix: str = "category") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, category in enumerate(ALL_CATEGORIES[tx_type]):
        builder.button(text=category, callback_data=f"{prefix}:{tx_type}:{index}")
    builder.adjust(2)
    cancel_data = "edit_expense:menu" if prefix == "edit_category" else "transaction:cancel"
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_data))
    return builder.as_markup()


def confirmation_keyboard(prefix: str = "transaction") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data=f"{prefix}:confirm"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data=f"{prefix}:edit"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}:cancel")],
        ]
    )


def reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="report:today"),
                InlineKeyboardButton(text="7 дней", callback_data="report:week"),
            ],
            [
                InlineKeyboardButton(text="Этот месяц", callback_data="report:month"),
                InlineKeyboardButton(text="За всё время", callback_data="report:all"),
            ],
        ]
    )


def expense_selection_keyboard(rows: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        object_name = str(row.get("object_name") or "Без объекта")
        if len(object_name) > 24:
            object_name = object_name[:21] + "…"
        label = f"#{row['id']} · {money(float(row['amount']))} · {object_name}"
        builder.button(text=label, callback_data=f"edit_expense:select:{row['id']}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="❌ Закрыть", callback_data="edit_expense:cancel"))
    return builder.as_markup()


def expense_edit_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Сумма", callback_data="edit_expense:field:amount"),
                InlineKeyboardButton(text="📁 Категория", callback_data="edit_expense:field:category"),
            ],
            [
                InlineKeyboardButton(text="🏗 Объект", callback_data="edit_expense:field:object"),
                InlineKeyboardButton(text="📝 Комментарий", callback_data="edit_expense:field:comment"),
            ],
            [InlineKeyboardButton(text="✅ Сохранить изменения", callback_data="edit_expense:save")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="edit_expense:cancel")],
        ]
    )
