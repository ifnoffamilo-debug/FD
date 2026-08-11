from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.constants import CATEGORIES, VEHICLE_EXPENSE_TYPES
from app.formatting import money


def scope_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏭 Рабочие финансы"), KeyboardButton(text="👤 Личные финансы")]],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел",
    )


def main_menu(scope: str) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="➕ Доход"), KeyboardButton(text="➖ Расход")],
        [KeyboardButton(text="✏️ Изменить расход"), KeyboardButton(text="🧾 Последние операции")],
        [KeyboardButton(text="📊 Отчёты"), KeyboardButton(text="📈 Сравнение")],
    ]
    if scope == "work":
        rows.append([KeyboardButton(text="📁 Объекты"), KeyboardButton(text="🧠 Умный ввод")])
    else:
        rows.append([KeyboardButton(text="🚗 Автомобиль"), KeyboardButton(text="🧠 Умный ввод")])
    rows.extend([
        [KeyboardButton(text="🤖 Спросить Groq"), KeyboardButton(text="📤 Excel")],
        [KeyboardButton(text="🔄 Сменить раздел")],
    ])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Выберите действие")


CANCEL_MENU = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)
SKIP_COMMENT_MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Без комментария")], [KeyboardButton(text="❌ Отмена")]], resize_keyboard=True
)


def categories_keyboard(scope: str, tx_type: str, prefix: str = "category") -> InlineKeyboardMarkup:
    b=InlineKeyboardBuilder()
    for i,c in enumerate(CATEGORIES[scope][tx_type]): b.button(text=c,callback_data=f"{prefix}:{scope}:{tx_type}:{i}")
    b.adjust(2); b.row(InlineKeyboardButton(text="❌ Отмена",callback_data="flow:cancel")); return b.as_markup()


def object_choice_keyboard(objects: list[dict[str,Any]], prefix: str="txobj") -> InlineKeyboardMarkup:
    b=InlineKeyboardBuilder()
    for obj in objects[:25]: b.button(text=f"🏗 {obj['name']}",callback_data=f"{prefix}:{obj['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="➕ Добавить новый объект",callback_data=f"{prefix}:new"))
    b.row(InlineKeyboardButton(text="🚫 Без объекта",callback_data=f"{prefix}:none"))
    b.row(InlineKeyboardButton(text="❌ Отмена",callback_data="flow:cancel")); return b.as_markup()


def confirmation_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить",callback_data=f"{prefix}:confirm"),InlineKeyboardButton(text="✏️ Заново",callback_data=f"{prefix}:edit")],
        [InlineKeyboardButton(text="❌ Отмена",callback_data="flow:cancel")],
    ])


def receipt_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📷 Прикрепить фото чека",callback_data=f"receipt:add:{tx_id}")],
        [InlineKeyboardButton(text="Пропустить",callback_data=f"receipt:skip:{tx_id}")],
    ])


def reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня",callback_data="report:today"),InlineKeyboardButton(text="7 дней",callback_data="report:week")],
        [InlineKeyboardButton(text="Этот месяц",callback_data="report:month"),InlineKeyboardButton(text="Этот год",callback_data="report:year")],
        [InlineKeyboardButton(text="За всё время",callback_data="report:all")],
    ])


def compare_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Этот месяц ↔ прошлый",callback_data="compare:month")],
        [InlineKeyboardButton(text="Эта неделя ↔ прошлая",callback_data="compare:week")],
        [InlineKeyboardButton(text="Этот год ↔ прошлый",callback_data="compare:year")],
    ])


def expense_selection_keyboard(rows: list[dict[str,Any]]) -> InlineKeyboardMarkup:
    b=InlineKeyboardBuilder()
    for r in rows:
        extra=str(r.get("object_name") or r.get("vehicle_expense_type") or r.get("category") or "")
        if len(extra)>22: extra=extra[:19]+"…"
        b.button(text=f"#{r['id']} · {money(float(r['amount']))} · {extra}",callback_data=f"edit:select:{r['id']}")
    b.adjust(1);b.row(InlineKeyboardButton(text="❌ Закрыть",callback_data="flow:cancel"));return b.as_markup()


def expense_edit_keyboard(scope: str, has_receipt: bool=False) -> InlineKeyboardMarkup:
    rows=[
        [InlineKeyboardButton(text="💰 Сумма",callback_data="edit:field:amount"),InlineKeyboardButton(text="📁 Категория",callback_data="edit:field:category")],
    ]
    if scope=="work": rows.append([InlineKeyboardButton(text="🏗 Объект",callback_data="edit:field:object"),InlineKeyboardButton(text="📝 Комментарий",callback_data="edit:field:comment")])
    else: rows.append([InlineKeyboardButton(text="📝 Комментарий",callback_data="edit:field:comment")])
    if has_receipt: rows.append([InlineKeyboardButton(text="📷 Посмотреть чек",callback_data="edit:receipt:view")])
    rows += [[InlineKeyboardButton(text="✅ Сохранить",callback_data="edit:save")],[InlineKeyboardButton(text="❌ Отмена",callback_data="flow:cancel")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def objects_menu(objects: list[dict[str,Any]]) -> InlineKeyboardMarkup:
    b=InlineKeyboardBuilder()
    for o in objects[:30]: b.button(text=f"🏗 {o['name']}",callback_data=f"object:view:{o['id']}")
    b.adjust(1); b.row(InlineKeyboardButton(text="➕ Новый объект",callback_data="object:add"));
    b.row(InlineKeyboardButton(text="📦 Архив",callback_data="object:list:archived")); return b.as_markup()


def object_card_keyboard(object_id: int, archived: bool=False) -> InlineKeyboardMarkup:
    action="restore" if archived else "archive"; action_text="♻️ Вернуть" if archived else "📦 В архив"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Название",callback_data=f"object:rename:{object_id}"),InlineKeyboardButton(text="💵 Цена заказа",callback_data=f"object:contract:{object_id}")],
        [InlineKeyboardButton(text="💰 Бюджет расходов",callback_data=f"object:budget:{object_id}"),InlineKeyboardButton(text=action_text,callback_data=f"object:{action}:{object_id}")],
        [InlineKeyboardButton(text="⬅️ К объектам",callback_data="object:list:active")],
    ])


def vehicles_keyboard(vehicles: list[dict[str,Any]]) -> InlineKeyboardMarkup:
    b=InlineKeyboardBuilder()
    for v in vehicles: b.button(text=f"🚗 {v['name']}",callback_data=f"vehicle:view:{v['id']}")
    b.adjust(1);b.row(InlineKeyboardButton(text="➕ Добавить автомобиль",callback_data="vehicle:add"));return b.as_markup()


def vehicle_card_keyboard(vehicle_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛽ Заправка",callback_data=f"auto:fuel:{vehicle_id}"),InlineKeyboardButton(text="🔧 Ремонт / ТО",callback_data=f"auto:expense:{vehicle_id}")],
        [InlineKeyboardButton(text="📏 Пробег",callback_data=f"auto:mileage:{vehicle_id}"),InlineKeyboardButton(text="📊 Статистика",callback_data=f"auto:stats:{vehicle_id}")],
        [InlineKeyboardButton(text="📖 История",callback_data=f"auto:history:{vehicle_id}"),InlineKeyboardButton(text="🛢 Сервис",callback_data=f"auto:service:{vehicle_id}")],
        [InlineKeyboardButton(text="⚙️ Переименовать",callback_data=f"vehicle:rename:{vehicle_id}"),InlineKeyboardButton(text="➕ Другая машина",callback_data="vehicle:add")],
        [InlineKeyboardButton(text="⬅️ Все автомобили",callback_data="vehicle:list")],
    ])


def vehicle_expense_type_keyboard(vehicle_id: int) -> InlineKeyboardMarkup:
    b=InlineKeyboardBuilder()
    for i,t in enumerate(VEHICLE_EXPENSE_TYPES):
        if t!="Топливо": b.button(text=t,callback_data=f"auto:type:{vehicle_id}:{i}")
    b.adjust(2);b.row(InlineKeyboardButton(text="❌ Отмена",callback_data="flow:cancel"));return b.as_markup()


def vehicle_stats_keyboard(vehicle_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Этот месяц",callback_data=f"autostat:{vehicle_id}:month"),InlineKeyboardButton(text="Этот год",callback_data=f"autostat:{vehicle_id}:year")],
        [InlineKeyboardButton(text="Всё время",callback_data=f"autostat:{vehicle_id}:all")],
        [InlineKeyboardButton(text="⬅️ К авто",callback_data=f"vehicle:view:{vehicle_id}")],
    ])


def service_keyboard(vehicle_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить сервис",callback_data=f"service:add:{vehicle_id}")],
        [InlineKeyboardButton(text="⬅️ К авто",callback_data=f"vehicle:view:{vehicle_id}")],
    ])
