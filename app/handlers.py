from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message, ReplyKeyboardRemove

from app.access import AdminFilter
from app.config import Settings
from app.constants import ALL_CATEGORIES, TYPE_LABELS
from app.database import Database
from app.formatting import money, summary_text, transaction_preview, transactions_text
from app.keyboards import (
    CANCEL_MENU,
    MAIN_MENU,
    SKIP_COMMENT_MENU,
    SKIP_OBJECT_MENU,
    categories_keyboard,
    confirmation_keyboard,
    reports_keyboard,
)
from app.services.export_excel import create_excel
from app.services.kimi import KimiService
from app.states import AskKimi, ManualTransaction, ObjectReport, SmartInput


def parse_amount(text: str) -> float:
    cleaned = text.strip().replace("₽", "").replace("рублей", "").replace("руб", "")
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        raise ValueError("Введите сумму цифрами, например: 38500")
    amount = float(cleaned)
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    return amount


def period_bounds(period: str, tz: ZoneInfo) -> tuple[str, datetime | None, datetime | None]:
    now = datetime.now(tz).replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return "Отчёт за сегодня", today, today + timedelta(days=1)
    if period == "week":
        return "Отчёт за последние 7 дней", today - timedelta(days=6), today + timedelta(days=1)
    if period == "month":
        start = today.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return "Отчёт за текущий месяц", start, end
    return "Отчёт за всё время", None, None


def build_router(
    settings: Settings,
    db: Database,
    kimi: KimiService,
    export_dir: Path,
) -> Router:
    router = Router(name="finance")
    admin = AdminFilter(settings)
    tz = ZoneInfo(settings.timezone)

    async def start_manual(message: Message, state: FSMContext, tx_type: str) -> None:
        await state.clear()
        await state.set_state(ManualTransaction.amount)
        await state.update_data(tx_type=tx_type)
        await message.answer(
            f"Введите сумму операции «{TYPE_LABELS[tx_type]}».\nНапример: <b>38500</b>",
            reply_markup=CANCEL_MENU,
        )

    async def save_operation(message_or_callback: Message | CallbackQuery, state: FSMContext, source: str) -> None:
        data = await state.get_data()
        user = message_or_callback.from_user
        if not user:
            return
        tx_id = await db.add_transaction(
            tx_type=data["tx_type"],
            amount=float(data["amount"]),
            category=data["category"],
            object_name=data.get("object_name"),
            comment=data.get("comment"),
            created_at=datetime.now(tz).replace(tzinfo=None),
            created_by=user.id,
            source=source,
        )
        await state.clear()
        text = f"✅ Операция #{tx_id} сохранена.\n\n{transaction_preview(data)}"
        if isinstance(message_or_callback, CallbackQuery):
            if message_or_callback.message:
                await message_or_callback.message.edit_reply_markup(reply_markup=None)
                await message_or_callback.message.answer(text, reply_markup=MAIN_MENU)
            await message_or_callback.answer("Сохранено")
        else:
            await message_or_callback.answer(text, reply_markup=MAIN_MENU)

    @router.message(Command("myid"))
    async def my_id(message: Message) -> None:
        await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")

    @router.message(admin, CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        ai_status = "подключён" if kimi.enabled else "не подключён"
        await message.answer(
            "<b>ФД Финансы</b>\n\n"
            "Учёт доходов, расходов и прибыли по объектам.\n"
            f"Kimi: <b>{ai_status}</b>.",
            reply_markup=MAIN_MENU,
        )

    @router.message(admin, Command("cancel"))
    @router.message(admin, F.text == "❌ Отмена")
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=MAIN_MENU)

    @router.callback_query(admin, F.data.in_({"transaction:cancel", "smart:cancel"}))
    async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("Действие отменено.", reply_markup=MAIN_MENU)
        await callback.answer()

    @router.message(admin, F.text == "➕ Доход")
    async def add_income(message: Message, state: FSMContext) -> None:
        await start_manual(message, state, "income")

    @router.message(admin, F.text == "➖ Расход")
    async def add_expense(message: Message, state: FSMContext) -> None:
        await start_manual(message, state, "expense")

    @router.message(ManualTransaction.amount, admin)
    async def manual_amount(message: Message, state: FSMContext) -> None:
        try:
            amount = parse_amount(message.text or "")
        except ValueError as error:
            await message.answer(str(error))
            return
        data = await state.get_data()
        tx_type = data["tx_type"]
        await state.update_data(amount=amount)
        await state.set_state(ManualTransaction.category)
        await message.answer(
            f"Сумма: <b>{money(amount)}</b>\nВыберите категорию:",
            reply_markup=categories_keyboard(tx_type),
        )

    @router.callback_query(ManualTransaction.category, admin, F.data.startswith("category:"))
    async def manual_category(callback: CallbackQuery, state: FSMContext) -> None:
        try:
            _, tx_type, raw_index = (callback.data or "").split(":", maxsplit=2)
            category = ALL_CATEGORIES[tx_type][int(raw_index)]
        except (ValueError, KeyError, IndexError):
            await callback.answer("Категория не найдена", show_alert=True)
            return
        await state.update_data(category=category)
        await state.set_state(ManualTransaction.object_name)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(
                "Введите название объекта, например: <b>Навес Репное</b>.\n"
                "Либо выберите «Без объекта».",
                reply_markup=SKIP_OBJECT_MENU,
            )
        await callback.answer()

    @router.message(ManualTransaction.object_name, admin)
    async def manual_object(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        object_name = None if text.casefold() == "без объекта" else text[:120]
        await state.update_data(object_name=object_name)
        await state.set_state(ManualTransaction.comment)
        await message.answer(
            "Добавьте комментарий или выберите «Без комментария».",
            reply_markup=SKIP_COMMENT_MENU,
        )

    @router.message(ManualTransaction.comment, admin)
    async def manual_comment(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        comment = None if text.casefold() == "без комментария" else text[:250]
        await state.update_data(comment=comment)
        data = await state.get_data()
        await state.set_state(ManualTransaction.confirm)
        await message.answer(
            "Проверьте операцию:\n\n" + transaction_preview(data),
            reply_markup=confirmation_keyboard("transaction"),
        )

    @router.callback_query(ManualTransaction.confirm, admin, F.data == "transaction:confirm")
    async def manual_confirm(callback: CallbackQuery, state: FSMContext) -> None:
        await save_operation(callback, state, "manual")

    @router.callback_query(ManualTransaction.confirm, admin, F.data == "transaction:edit")
    async def manual_edit(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        tx_type = data.get("tx_type", "expense")
        await state.clear()
        await state.set_state(ManualTransaction.amount)
        await state.update_data(tx_type=tx_type)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("Введите операцию заново. Начните с суммы:", reply_markup=CANCEL_MENU)
        await callback.answer()

    @router.message(admin, F.text == "📊 Отчёты")
    async def reports(message: Message) -> None:
        await message.answer("Выберите период:", reply_markup=reports_keyboard())

    @router.callback_query(admin, F.data.startswith("report:"))
    async def report_period(callback: CallbackQuery) -> None:
        period = (callback.data or "report:all").split(":", maxsplit=1)[1]
        title, start_date, end_date = period_bounds(period, tz)
        result = await db.summary(start_date, end_date)
        categories = await db.category_totals(start_date, end_date)
        lines = [summary_text(title, result)]
        expense_categories = [row for row in categories if row["tx_type"] == "expense"][:5]
        if expense_categories:
            lines.append("\n<b>Основные расходы:</b>")
            for row in expense_categories:
                lines.append(f"• {escape(row['category'])}: {money(float(row['total']))}")
        if callback.message:
            await callback.message.answer("\n".join(lines))
        await callback.answer()

    @router.message(admin, F.text == "🏗 По объекту")
    async def object_report_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ObjectReport.object_name)
        await message.answer("Введите название объекта или часть названия:", reply_markup=CANCEL_MENU)

    @router.message(ObjectReport.object_name, admin)
    async def object_report(message: Message, state: FSMContext) -> None:
        query = (message.text or "").strip()
        result, rows = await db.summary_by_object(query)
        await state.clear()
        if result.count == 0:
            await message.answer("По этому названию операций не найдено.", reply_markup=MAIN_MENU)
            return
        text = summary_text(f"Объект: {query}", result) + "\n\n" + transactions_text(rows, "Операции по объекту")
        await message.answer(text, reply_markup=MAIN_MENU)

    @router.message(admin, F.text == "🧾 Последние операции")
    async def recent(message: Message) -> None:
        rows = await db.recent(15)
        await message.answer(transactions_text(rows))

    @router.message(admin, F.text == "📤 Excel")
    async def export(message: Message) -> None:
        rows = await db.all_transactions()
        if not rows:
            await message.answer("Операций пока нет — выгружать нечего.")
            return
        await message.answer("Формирую Excel-отчёт…")
        path = await create_excel(rows, export_dir)
        try:
            await message.answer_document(
                FSInputFile(path),
                caption=f"Финансовый отчёт. Операций: {len(rows)}",
            )
        finally:
            path.unlink(missing_ok=True)

    @router.message(admin, F.text == "🧠 Умный ввод")
    async def smart_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(SmartInput.text)
        status = "Kimi распознает сообщение." if kimi.enabled else "Kimi не подключён — будет использован простой распознаватель."
        await message.answer(
            "Опишите одну операцию обычным текстом.\n\n"
            "Например: <i>Купил металл за 38 500 рублей для навеса в Репном.</i>\n\n"
            + status,
            reply_markup=CANCEL_MENU,
        )

    @router.message(SmartInput.text, admin)
    async def smart_parse(message: Message, state: FSMContext) -> None:
        try:
            parsed = await kimi.parse_operation(message.text or "")
        except ValueError as error:
            await message.answer(f"Не удалось распознать операцию: {escape(str(error))}")
            return
        data = parsed.as_dict()
        await state.update_data(**data)
        await state.set_state(SmartInput.confirm)
        await message.answer(
            "Распознано:\n\n" + transaction_preview(data),
            reply_markup=confirmation_keyboard("smart"),
        )

    @router.callback_query(SmartInput.confirm, admin, F.data == "smart:confirm")
    async def smart_confirm(callback: CallbackQuery, state: FSMContext) -> None:
        await save_operation(callback, state, "kimi" if kimi.enabled else "fallback")

    @router.callback_query(SmartInput.confirm, admin, F.data == "smart:edit")
    async def smart_edit(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(SmartInput.text)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("Опишите операцию заново одним сообщением:", reply_markup=CANCEL_MENU)
        await callback.answer()

    @router.message(admin, F.text == "🤖 Спросить Kimi")
    async def ask_kimi_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(AskKimi.question)
        await message.answer(
            "Задайте вопрос о финансах. Например:\n"
            "• На что ушло больше всего денег?\n"
            "• Какая прибыль по последним операциям?\n"
            "• Кратко оцени расходы мастерской.",
            reply_markup=CANCEL_MENU,
        )

    @router.message(AskKimi.question, admin)
    async def ask_kimi(message: Message, state: FSMContext) -> None:
        question = (message.text or "").strip()
        total = await db.summary()
        categories = await db.category_totals()
        recent_rows = await db.recent(40)
        objects = await db.object_totals()
        context_lines = [
            f"Общие доходы: {total.income:.2f} RUB",
            f"Общие расходы: {total.expense:.2f} RUB",
            f"Общая прибыль: {total.profit:.2f} RUB",
            f"Количество операций: {total.count}",
            "Категории:",
        ]
        for row in categories:
            context_lines.append(
                f"- {row['tx_type']} / {row['category']}: {float(row['total']):.2f} RUB, операций {row['count']}"
            )
        context_lines.append("Объекты:")
        for row in objects:
            profit = float(row["income"]) - float(row["expense"])
            context_lines.append(
                f"- {row['object_name']}: доход {float(row['income']):.2f} RUB, "
                f"расход {float(row['expense']):.2f} RUB, прибыль {profit:.2f} RUB, операций {row['count']}"
            )
        context_lines.append("Последние операции:")
        for row in recent_rows:
            context_lines.append(
                f"- {row['created_at']} | {row['tx_type']} | {float(row['amount']):.2f} RUB | "
                f"{row['category']} | объект: {row.get('object_name') or '-'} | "
                f"комментарий: {row.get('comment') or '-'}"
            )
        try:
            answer = await kimi.answer_finance_question(question, "\n".join(context_lines))
        except Exception as error:
            answer = f"Ошибка обращения к Kimi: {error}"
        await state.clear()
        for start_index in range(0, len(answer), 3900):
            await message.answer(escape(answer[start_index:start_index + 3900]), reply_markup=MAIN_MENU)

    @router.message()
    async def unauthorized_or_unknown(message: Message) -> None:
        if message.from_user and message.from_user.id not in settings.admin_ids:
            await message.answer(
                "Доступ запрещён. Ваш Telegram ID: "
                f"<code>{message.from_user.id}</code>"
            )
            return
        await message.answer("Выберите действие в меню или используйте /start.", reply_markup=MAIN_MENU)

    @router.callback_query()
    async def unauthorized_callback(callback: CallbackQuery) -> None:
        if callback.from_user.id not in settings.admin_ids:
            await callback.answer("Нет доступа", show_alert=True)
        else:
            await callback.answer("Кнопка устарела. Откройте меню заново.", show_alert=True)

    return router
