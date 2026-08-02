from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.constants import ALL_CATEGORIES


MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Доход"), KeyboardButton(text="➖ Расход")],
        [KeyboardButton(text="📊 Отчёты"), KeyboardButton(text="🏗 По объекту")],
        [KeyboardButton(text="🧠 Умный ввод"), KeyboardButton(text="🤖 Спросить Kimi")],
        [KeyboardButton(text="🧾 Последние операции"), KeyboardButton(text="📤 Excel")],
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


def categories_keyboard(tx_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, category in enumerate(ALL_CATEGORIES[tx_type]):
        builder.button(text=category, callback_data=f"category:{tx_type}:{index}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="transaction:cancel"))
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
