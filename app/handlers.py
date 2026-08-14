from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.access import AdminFilter
from app.config import Settings
from app.constants import CATEGORIES, SCOPES, TYPE_LABELS, VEHICLE_EXPENSE_TYPES, DEBT_DIRECTIONS
from app.database import Database, Summary
from app.formatting import money, summary_text, transaction_preview, transactions_text
from app.keyboards import (
    CANCEL_MENU, SKIP_COMMENT_MENU, categories_keyboard, compare_keyboard, confirmation_keyboard,
    expense_edit_keyboard, expense_selection_keyboard, main_menu, object_card_keyboard,
    object_choice_keyboard, objects_menu, receipt_keyboard, reports_keyboard, scope_menu,
    service_keyboard, vehicle_card_keyboard, vehicle_expense_type_keyboard, vehicle_stats_keyboard,
    vehicles_keyboard, fuel_type_keyboard, debts_menu_keyboard, debt_direction_keyboard,
    debt_list_keyboard, debt_card_keyboard, debt_edit_keyboard, debt_payment_record_keyboard,
    debt_close_confirm_keyboard,
)
from app.services.ai import AIService
from app.services.export_excel import create_excel
from app.states import (
    AskAI, AutoExpense, EditExpense, ManualTransaction, MileageFlow, ObjectFlow,
    ReceiptUpload, ServiceFlow, SmartInput, VehicleFlow, DebtFlow,
)


def parse_amount(text: str, allow_zero: bool = False) -> float:
    cleaned=(text or "").strip().casefold().replace("₽","").replace("рублей","").replace("руб","")
    cleaned=cleaned.replace(" ","").replace(",",".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?",cleaned):
        raise ValueError("Введите сумму цифрами, например: 38500")
    value=float(cleaned)
    if value < 0 or (value == 0 and not allow_zero): raise ValueError("Сумма должна быть больше нуля")
    return value


def parse_number(text: str, allow_zero: bool = True) -> float:
    cleaned=(text or "").strip().replace(" ","").replace(",",".").replace("км","").replace("л","")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?",cleaned): raise ValueError("Введите число цифрами")
    value=float(cleaned)
    if value < 0 or (value == 0 and not allow_zero): raise ValueError("Значение должно быть больше нуля")
    return value


def parse_debt_date(text: str) -> str | None:
    raw=(text or "").strip()
    if raw.casefold() in {"без срока","нет","-","без даты"}: return None
    for fmt in ("%d.%m.%Y","%d.%m.%y","%Y-%m-%d"):
        try:return datetime.strptime(raw,fmt).strftime("%Y-%m-%d")
        except ValueError:pass
    raise ValueError("Введите дату в формате ДД.ММ.ГГГГ или «Без срока»")


def debt_date_text(value: str | None) -> str:
    if not value:return "Без срока"
    try:return datetime.strptime(value,"%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:return value


def month_end(start: datetime) -> datetime:
    return start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)


def period_bounds(period: str, tz: ZoneInfo) -> tuple[str, datetime|None, datetime|None]:
    now=datetime.now(tz).replace(tzinfo=None); today=now.replace(hour=0,minute=0,second=0,microsecond=0)
    if period=="today": return "Сегодня",today,today+timedelta(days=1)
    if period=="week": return "Последние 7 дней",today-timedelta(days=6),today+timedelta(days=1)
    if period=="month":
        start=today.replace(day=1);return "Текущий месяц",start,month_end(start)
    if period=="year":
        start=today.replace(month=1,day=1);return "Текущий год",start,start.replace(year=start.year+1)
    return "За всё время",None,None


def compare_bounds(kind: str, tz: ZoneInfo) -> tuple[str,tuple[datetime,datetime],str,tuple[datetime,datetime]]:
    now=datetime.now(tz).replace(tzinfo=None);today=now.replace(hour=0,minute=0,second=0,microsecond=0)
    if kind=="month":
        a=today.replace(day=1); ae=month_end(a); prev_end=a; prev=(a-timedelta(days=1)).replace(day=1)
        return a.strftime("%B %Y"),(a,ae),prev.strftime("%B %Y"),(prev,prev_end)
    if kind=="year":
        a=today.replace(month=1,day=1);ae=a.replace(year=a.year+1);p=a.replace(year=a.year-1)
        return str(a.year),(a,ae),str(p.year),(p,a)
    # календарные недели, понедельник-воскресенье
    a=today-timedelta(days=today.weekday());ae=a+timedelta(days=7);p=a-timedelta(days=7)
    return "Эта неделя",(a,ae),"Прошлая неделя",(p,a)


def change_text(current: float, previous: float) -> str:
    diff=current-previous
    if previous==0: return f"{money(diff)}" if diff else "без изменений"
    pct=diff/previous*100
    arrow="↗" if diff>0 else ("↘" if diff<0 else "→")
    return f"{arrow} {money(diff)} ({pct:+.1f}%)"


def build_router(settings: Settings, db: Database, ai: AIService, export_dir: Path) -> Router:
    router=Router(name="finance-v2")
    admin=AdminFilter(settings);tz=ZoneInfo(settings.timezone)
    receipts_dir=settings.database_path.parent/"receipts";receipts_dir.mkdir(parents=True,exist_ok=True)

    async def scope_for(user_id:int)->str: return await db.get_scope(user_id)

    async def send_main(message:Message, scope:str, text:str|None=None)->None:
        label=SCOPES[scope]
        await message.answer(text or f"<b>{label}</b>\nВыберите действие:",reply_markup=main_menu(scope))

    async def show_scope_picker(message:Message)->None:
        active=await scope_for(message.from_user.id)
        await message.answer(
            f"<b>ФД Финансы</b>\n\nТекущий раздел: {SCOPES[active]}\nВыберите раздел:",
            reply_markup=scope_menu(),
        )

    async def resolve_object_name(object_id:int|None)->str|None:
        if not object_id:return None
        obj=await db.get_object(object_id);return str(obj["name"]) if obj else None

    async def continue_to_comment(target:Message|CallbackQuery,state:FSMContext)->None:
        await state.set_state(ManualTransaction.comment)
        msg=target.message if isinstance(target,CallbackQuery) else target
        if msg: await msg.answer("Добавьте комментарий или выберите «Без комментария».",reply_markup=SKIP_COMMENT_MENU)

    async def save_transaction(target:Message|CallbackQuery,state:FSMContext,source:str)->int|None:
        data=await state.get_data();user=target.from_user
        if not user:return None
        tx_id=await db.add_transaction(
            tx_type=data["tx_type"],amount=float(data["amount"]),category=data["category"],
            finance_scope=data.get("finance_scope") or await scope_for(user.id),object_id=data.get("object_id"),
            object_name=data.get("object_name"),comment=data.get("comment"),created_at=datetime.now(tz).replace(tzinfo=None),
            created_by=user.id,source=source,vehicle_id=data.get("vehicle_id"),vehicle_expense_type=data.get("vehicle_expense_type"),
            fuel_type=data.get("fuel_type"),fuel_liters=data.get("fuel_liters"),odometer=data.get("odometer"),
        )
        scope=data.get("finance_scope") or await scope_for(user.id)
        preview=transaction_preview(data)
        if isinstance(target,CallbackQuery):
            if target.message:
                try: await target.message.edit_reply_markup(reply_markup=None)
                except Exception: pass
                await target.message.answer(f"✅ Операция #{tx_id} сохранена.\n\n{preview}")
            await target.answer("Сохранено")
        else: await target.answer(f"✅ Операция #{tx_id} сохранена.\n\n{preview}")
        if data["tx_type"]=="expense":
            await state.clear();await state.set_state(ReceiptUpload.photo);await state.update_data(receipt_tx_id=tx_id,finance_scope=scope)
            msg=target.message if isinstance(target,CallbackQuery) else target
            if msg: await msg.answer("Добавить фото чека к этому расходу?",reply_markup=receipt_keyboard(tx_id))
        else:
            await state.clear();msg=target.message if isinstance(target,CallbackQuery) else target
            if msg: await send_main(msg,scope)
        return tx_id

    async def show_object_card(target:Message|CallbackQuery, object_id:int)->None:
        obj,total=await db.object_stats(object_id)
        if not obj:
            if isinstance(target,CallbackQuery): await target.answer("Объект не найден",show_alert=True)
            return
        contract=float(obj["contract_amount"]);budget=float(obj["budget_amount"]);remaining=budget-total.expense
        forecast=contract-budget if contract or budget else 0
        spent_pct=(total.expense/budget*100) if budget>0 else 0
        text=(f"🏗 <b>{escape(str(obj['name']))}</b>\n"
              f"Статус: {'Архив' if obj['status']=='archived' else 'Активный'}\n\n"
              f"Цена заказа: <b>{money(contract)}</b>\nПолучено: <b>{money(total.income)}</b>\n"
              f"Бюджет расходов: <b>{money(budget)}</b>\nФакт расходов: <b>{money(total.expense)}</b>\n"
              f"Остаток бюджета: <b>{money(remaining)}</b>\nТекущий результат: <b>{money(total.profit)}</b>\n"
              f"Прогноз прибыли: <b>{money(forecast)}</b>")
        if budget>0:
            text += f"\nИспользовано бюджета: <b>{spent_pct:.1f}%</b>"
            if spent_pct>=90:text += "\n⚠️ Бюджет почти исчерпан."
        kb=object_card_keyboard(object_id,obj["status"]=="archived")
        if isinstance(target,CallbackQuery):
            if target.message: await target.message.edit_text(text,reply_markup=kb)
            await target.answer()
        else: await target.answer(text,reply_markup=kb)

    async def show_vehicle_card(target:Message|CallbackQuery, vehicle_id:int)->None:
        v=await db.get_vehicle(vehicle_id)
        if not v:
            if isinstance(target,CallbackQuery):await target.answer("Автомобиль не найден",show_alert=True)
            return
        _,ms,me=period_bounds("month",tz);_,ys,ye=period_bounds("year",tz)
        month=await db.summary("personal",ms,me,vehicle_id=vehicle_id);year=await db.summary("personal",ys,ye,vehicle_id=vehicle_id);all_=await db.summary("personal",vehicle_id=vehicle_id)
        service=await db.list_service(vehicle_id)
        text=(f"🚗 <b>{escape(str(v['name']))}</b>\nПробег: <b>{float(v['current_mileage']):,.0f} км</b>\n\n"
              f"Расходы за месяц: <b>{money(month.expense)}</b>\nЗа год: <b>{money(year.expense)}</b>\nЗа всё время: <b>{money(all_.expense)}</b>")
        if service:
            next_item=min(service,key=lambda x:float(x["next_mileage"]))
            left=float(next_item["next_mileage"])-float(v["current_mileage"])
            text += f"\n\n🛢 Ближайший сервис: {escape(str(next_item['title']))} — {'просрочено на' if left<0 else 'осталось'} <b>{abs(left):,.0f} км</b>"
        kb=vehicle_card_keyboard(vehicle_id)
        if isinstance(target,CallbackQuery):
            if target.message: await target.message.edit_text(text,reply_markup=kb)
            await target.answer()
        else: await target.answer(text,reply_markup=kb)

    async def show_debts_menu(target:Message|CallbackQuery) -> None:
        totals=await db.debt_totals();active=await db.list_debts(status="active")
        today=datetime.now(tz).date();overdue=0
        for d in active:
            if d.get("due_date"):
                try:
                    if datetime.strptime(str(d["due_date"]),"%Y-%m-%d").date()<today:overdue+=1
                except ValueError:pass
        net=float(totals["net"]);net_icon="🟢" if net>=0 else "🔴"
        text=(f"💸 <b>Долги</b>\n\nМне должны: <b>{money(float(totals['to_me']))}</b>\n"
              f"Я должен: <b>{money(float(totals['i_owe']))}</b>\n"
              f"{net_icon} Чистая позиция: <b>{money(net)}</b>\n"
              f"Активных долгов: {len(active)}")
        if overdue:text+=f"\n⚠️ Просрочено: <b>{overdue}</b>"
        if isinstance(target,CallbackQuery):
            if target.message: await target.message.edit_text(text,reply_markup=debts_menu_keyboard())
            await target.answer()
        else: await target.answer(text,reply_markup=debts_menu_keyboard())

    async def show_debt_card(target:Message|CallbackQuery,debt_id:int) -> None:
        d=await db.get_debt(debt_id)
        if not d:
            if isinstance(target,CallbackQuery):await target.answer("Долг не найден",show_alert=True)
            return
        direction=DEBT_DIRECTIONS.get(str(d["direction"]),str(d["direction"]))
        remaining=float(d["remaining"]);paid=float(d["paid_amount"]);original=float(d["original_amount"])
        status="✅ Закрыт" if d["status"]=="closed" else "🟡 Активен"
        due=debt_date_text(d.get("due_date"));overdue=False
        if d.get("due_date") and d["status"]=="active":
            try:overdue=datetime.strptime(str(d["due_date"]),"%Y-%m-%d").date()<datetime.now(tz).date()
            except ValueError:pass
        text=(f"💸 <b>{escape(direction)}</b>\n"
              f"Кто: <b>{escape(str(d['person']))}</b>\n"
              f"Сумма долга: <b>{money(original)}</b>\n"
              f"Погашено: <b>{money(paid)}</b>\n"
              f"Осталось: <b>{money(remaining)}</b>\n"
              f"Срок: <b>{escape(due)}</b>{' ⚠️' if overdue else ''}\n"
              f"Статус: {status}\n"
              f"Комментарий: {escape(str(d.get('comment') or 'Без комментария'))}")
        kb=debt_card_keyboard(debt_id,d["status"]=="closed")
        if isinstance(target,CallbackQuery):
            if target.message: await target.message.edit_text(text,reply_markup=kb)
            await target.answer()
        else: await target.answer(text,reply_markup=kb)

    async def prompt_debt_payment_record(target:Message|CallbackQuery,payment_id:int) -> None:
        pmt=await db.get_debt_payment(payment_id)
        if not pmt:return
        action="личный доход" if pmt["direction"]=="to_me" else "личный расход"
        text=(f"✅ Погашение <b>{money(float(pmt['amount']))}</b> записано.\n"
              f"Добавить эту сумму также как {action}?")
        msg=target.message if isinstance(target,CallbackQuery) else target
        if msg:await msg.answer(text,reply_markup=debt_payment_record_keyboard(payment_id))
        if isinstance(target,CallbackQuery):await target.answer()

    # ---------- Base / mode ----------
    @router.message(Command("myid"))
    async def myid(message:Message): await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")

    @router.message(admin,CommandStart())
    async def start(message:Message,state:FSMContext):
        await state.clear();await show_scope_picker(message)

    @router.message(admin,F.text.in_({"🏭 Рабочие финансы","👤 Личные финансы"}))
    async def choose_scope(message:Message,state:FSMContext):
        await state.clear();scope="work" if message.text.startswith("🏭") else "personal";await db.set_scope(message.from_user.id,scope)
        await send_main(message,scope,f"✅ Выбран раздел: <b>{SCOPES[scope]}</b>")

    @router.message(admin,F.text=="🔄 Сменить раздел")
    async def change_scope(message:Message,state:FSMContext): await state.clear();await show_scope_picker(message)

    @router.message(admin,Command("cancel"))
    @router.message(admin,F.text=="❌ Отмена")
    async def cancel(message:Message,state:FSMContext):
        await state.clear();scope=await scope_for(message.from_user.id);await send_main(message,scope,"Действие отменено.")

    @router.callback_query(admin,F.data=="flow:cancel")
    async def cancel_cb(callback:CallbackQuery,state:FSMContext):
        await state.clear();scope=await scope_for(callback.from_user.id)
        if callback.message:
            try: await callback.message.edit_reply_markup(reply_markup=None)
            except Exception: pass
            await send_main(callback.message,scope,"Действие отменено.")
        await callback.answer()

    # ---------- Manual transaction ----------
    async def manual_start(message:Message,state:FSMContext,tx_type:str):
        scope=await scope_for(message.from_user.id);await state.clear();await state.set_state(ManualTransaction.amount)
        await state.update_data(tx_type=tx_type,finance_scope=scope)
        await message.answer(f"{SCOPES[scope]}\nВведите сумму операции «{TYPE_LABELS[tx_type]}»:",reply_markup=CANCEL_MENU)

    @router.message(admin,F.text=="➕ Доход")
    async def add_income(message:Message,state:FSMContext): await manual_start(message,state,"income")
    @router.message(admin,F.text=="➖ Расход")
    async def add_expense(message:Message,state:FSMContext): await manual_start(message,state,"expense")

    @router.message(ManualTransaction.amount,admin)
    async def manual_amount(message:Message,state:FSMContext):
        try: amount=parse_amount(message.text or "")
        except ValueError as e: await message.answer(str(e));return
        data=await state.get_data();scope=data["finance_scope"];tx=data["tx_type"]
        await state.update_data(amount=amount);await state.set_state(ManualTransaction.category)
        await message.answer(f"Сумма: <b>{money(amount)}</b>\nВыберите категорию:",reply_markup=categories_keyboard(scope,tx))

    @router.callback_query(ManualTransaction.category,admin,F.data.startswith("category:"))
    async def manual_category(callback:CallbackQuery,state:FSMContext):
        try:
            _,scope,tx,idx=(callback.data or "").split(":",3);cat=CATEGORIES[scope][tx][int(idx)]
        except Exception: await callback.answer("Категория не найдена",show_alert=True);return
        await state.update_data(category=cat)
        if callback.message:
            try: await callback.message.edit_reply_markup(reply_markup=None)
            except Exception: pass
        if scope=="work":
            await state.set_state(ManualTransaction.object_choice);objs=await db.list_objects()
            if callback.message: await callback.message.answer("Выберите объект:",reply_markup=object_choice_keyboard(objs,"txobj"))
        else:
            await state.update_data(object_id=None,object_name=None);await continue_to_comment(callback,state)
        await callback.answer()

    @router.callback_query(ManualTransaction.object_choice,admin,F.data.startswith("txobj:"))
    async def manual_object_choice(callback:CallbackQuery,state:FSMContext):
        val=(callback.data or "").split(":",1)[1]
        if val=="new":
            await state.set_state(ManualTransaction.new_object)
            if callback.message: await callback.message.answer("Введите название нового объекта:",reply_markup=CANCEL_MENU)
        elif val=="none":
            await state.update_data(object_id=None,object_name=None);await continue_to_comment(callback,state)
        else:
            obj=await db.get_object(int(val))
            if not obj: await callback.answer("Объект не найден",show_alert=True);return
            await state.update_data(object_id=int(val),object_name=obj["name"]);await continue_to_comment(callback,state)
        await callback.answer()

    @router.message(ManualTransaction.new_object,admin)
    async def manual_new_object(message:Message,state:FSMContext):
        name=(message.text or "").strip()[:120]
        if not name: await message.answer("Введите название.");return
        try: oid=await db.add_object(name)
        except Exception:
            objs=await db.list_objects();match=next((o for o in objs if o["name"].casefold()==name.casefold()),None)
            if not match: await message.answer("Не удалось создать объект.");return
            oid=int(match["id"]);name=match["name"]
        await state.update_data(object_id=oid,object_name=name);await continue_to_comment(message,state)

    @router.message(ManualTransaction.comment,admin)
    async def manual_comment(message:Message,state:FSMContext):
        text=(message.text or "").strip();comment=None if text.casefold()=="без комментария" else text[:250]
        await state.update_data(comment=comment);data=await state.get_data();await state.set_state(ManualTransaction.confirm)
        await message.answer("Проверьте операцию:\n\n"+transaction_preview(data),reply_markup=confirmation_keyboard("transaction"))

    @router.callback_query(ManualTransaction.confirm,admin,F.data=="transaction:confirm")
    async def manual_confirm(callback:CallbackQuery,state:FSMContext): await save_transaction(callback,state,"manual")
    @router.callback_query(ManualTransaction.confirm,admin,F.data=="transaction:edit")
    async def manual_redo(callback:CallbackQuery,state:FSMContext):
        data=await state.get_data();tx=data["tx_type"];scope=data["finance_scope"];await state.clear();await state.set_state(ManualTransaction.amount);await state.update_data(tx_type=tx,finance_scope=scope)
        if callback.message: await callback.message.answer("Начните заново. Введите сумму:",reply_markup=CANCEL_MENU)
        await callback.answer()

    # ---------- Receipts ----------
    @router.callback_query(ReceiptUpload.photo,admin,F.data.startswith("receipt:add:"))
    async def receipt_add(callback:CallbackQuery,state:FSMContext):
        tx_id=int((callback.data or "0").rsplit(":",1)[1]);await state.update_data(receipt_tx_id=tx_id)
        if callback.message: await callback.message.answer("Отправьте фотографию чека одним фото.",reply_markup=CANCEL_MENU)
        await callback.answer()

    @router.callback_query(ReceiptUpload.photo,admin,F.data.startswith("receipt:skip:"))
    async def receipt_skip(callback:CallbackQuery,state:FSMContext):
        data=await state.get_data();scope=data.get("finance_scope") or await scope_for(callback.from_user.id);await state.clear()
        if callback.message: await send_main(callback.message,scope,"Чек пропущен.")
        await callback.answer()

    @router.message(ReceiptUpload.photo,admin,F.photo)
    async def receipt_photo(message:Message,state:FSMContext):
        data=await state.get_data();tx_id=int(data.get("receipt_tx_id",0))
        if not tx_id: await message.answer("Не найдена операция для чека.");return
        photo=message.photo[-1];now=datetime.now(tz);folder=receipts_dir/f"{now:%Y}"/f"{now:%m}";folder.mkdir(parents=True,exist_ok=True)
        path=folder/f"expense_{tx_id}_{now:%Y%m%d_%H%M%S}.jpg"
        await message.bot.download(photo,destination=path)
        await db.attach_receipt(tx_id,photo.file_id,str(path));scope=data.get("finance_scope") or await scope_for(message.from_user.id);await state.clear()
        await send_main(message,scope,f"✅ Чек сохранён и привязан к расходу #{tx_id}.")

    @router.message(ReceiptUpload.photo,admin)
    async def receipt_not_photo(message:Message): await message.answer("Отправьте фото чека или нажмите «❌ Отмена».")

    # ---------- Edit expense ----------
    @router.message(admin,F.text=="✏️ Изменить расход")
    @router.message(admin,Command("edit_expense"))
    async def edit_start(message:Message,state:FSMContext):
        scope=await scope_for(message.from_user.id);rows=await db.recent_expenses(scope,12);await state.clear()
        if not rows: await message.answer("Расходов в этом разделе пока нет.",reply_markup=main_menu(scope));return
        await message.answer("Выберите расход:",reply_markup=expense_selection_keyboard(rows))

    @router.callback_query(admin,F.data.startswith("edit:select:"))
    async def edit_select(callback:CallbackQuery,state:FSMContext):
        tx_id=int((callback.data or "0").rsplit(":",1)[1]);row=await db.get_transaction(tx_id)
        if not row or row["tx_type"]!="expense": await callback.answer("Расход не найден",show_alert=True);return
        await state.clear();await state.set_state(EditExpense.menu);await state.update_data(
            edit_tx_id=tx_id,tx_type="expense",amount=float(row["amount"]),category=row["category"],finance_scope=row.get("finance_scope","work"),
            object_id=row.get("object_id"),object_name=row.get("object_name"),comment=row.get("comment"),receipt_file_id=row.get("receipt_file_id")
        )
        if callback.message: await callback.message.edit_text(f"<b>Расход #{tx_id}</b>\n\n{transaction_preview(await state.get_data())}\n\nЧто изменить?",reply_markup=expense_edit_keyboard(row.get("finance_scope","work"),bool(row.get("receipt_file_id"))))
        await callback.answer()

    async def edit_preview(target:Message|CallbackQuery,state:FSMContext):
        d=await state.get_data();text=f"<b>Расход #{d['edit_tx_id']}</b>\n\n{transaction_preview(d)}\n\nЧто изменить?";kb=expense_edit_keyboard(d["finance_scope"],bool(d.get("receipt_file_id")))
        if isinstance(target,CallbackQuery):
            if target.message: await target.message.answer(text,reply_markup=kb)
            await target.answer()
        else: await target.answer(text,reply_markup=kb)

    @router.callback_query(EditExpense.menu,admin,F.data=="edit:field:amount")
    async def edit_amount_start(callback:CallbackQuery,state:FSMContext): await state.set_state(EditExpense.amount);await callback.message.answer("Новая сумма:",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(EditExpense.amount,admin)
    async def edit_amount(message:Message,state:FSMContext):
        try:v=parse_amount(message.text or "")
        except ValueError as e: await message.answer(str(e));return
        await state.update_data(amount=v);await state.set_state(EditExpense.menu);await edit_preview(message,state)

    @router.callback_query(EditExpense.menu,admin,F.data=="edit:field:category")
    async def edit_cat_start(callback:CallbackQuery,state:FSMContext):
        d=await state.get_data();await state.set_state(EditExpense.category);await callback.message.answer("Выберите категорию:",reply_markup=categories_keyboard(d["finance_scope"],"expense","editcat"));await callback.answer()
    @router.callback_query(EditExpense.category,admin,F.data.startswith("editcat:"))
    async def edit_cat(callback:CallbackQuery,state:FSMContext):
        _,scope,_,idx=(callback.data or "").split(":",3);await state.update_data(category=CATEGORIES[scope]["expense"][int(idx)]);await state.set_state(EditExpense.menu);await edit_preview(callback,state)

    @router.callback_query(EditExpense.menu,admin,F.data=="edit:field:object")
    async def edit_obj_start(callback:CallbackQuery,state:FSMContext):
        await state.set_state(EditExpense.object_choice);await callback.message.answer("Выберите объект:",reply_markup=object_choice_keyboard(await db.list_objects(),"editobj"));await callback.answer()
    @router.callback_query(EditExpense.object_choice,admin,F.data.startswith("editobj:"))
    async def edit_obj(callback:CallbackQuery,state:FSMContext):
        val=(callback.data or "").split(":",1)[1]
        if val=="none": await state.update_data(object_id=None,object_name=None)
        elif val=="new":
            await state.set_state(ObjectFlow.name);await state.update_data(return_to_edit=True);await callback.message.answer("Введите название нового объекта:",reply_markup=CANCEL_MENU);await callback.answer();return
        else:
            o=await db.get_object(int(val));await state.update_data(object_id=int(val),object_name=o["name"] if o else None)
        await state.set_state(EditExpense.menu);await edit_preview(callback,state)

    @router.callback_query(EditExpense.menu,admin,F.data=="edit:field:comment")
    async def edit_comment_start(callback:CallbackQuery,state:FSMContext): await state.set_state(EditExpense.comment);await callback.message.answer("Новый комментарий или «Без комментария»:",reply_markup=SKIP_COMMENT_MENU);await callback.answer()
    @router.message(EditExpense.comment,admin)
    async def edit_comment(message:Message,state:FSMContext):
        text=(message.text or "").strip();await state.update_data(comment=None if text.casefold()=="без комментария" else text[:250]);await state.set_state(EditExpense.menu);await edit_preview(message,state)

    @router.callback_query(EditExpense.menu,admin,F.data=="edit:receipt:view")
    async def edit_receipt_view(callback:CallbackQuery,state:FSMContext):
        d=await state.get_data();fid=d.get("receipt_file_id")
        if fid and callback.message: await callback.message.answer_photo(fid,caption=f"Чек к расходу #{d['edit_tx_id']}")
        await callback.answer()

    @router.callback_query(EditExpense.menu,admin,F.data=="edit:save")
    async def edit_save(callback:CallbackQuery,state:FSMContext):
        d=await state.get_data();ok=await db.update_expense(tx_id=int(d["edit_tx_id"]),amount=float(d["amount"]),category=d["category"],object_id=d.get("object_id"),object_name=d.get("object_name"),comment=d.get("comment"),updated_at=datetime.now(tz).replace(tzinfo=None),updated_by=callback.from_user.id)
        scope=d["finance_scope"];await state.clear()
        if callback.message: await send_main(callback.message,scope,"✅ Расход изменён." if ok else "Не удалось изменить расход.")
        await callback.answer()

    # ---------- Reports / comparison ----------
    @router.message(admin,F.text=="📊 Отчёты")
    async def reports(message:Message): await message.answer("Выберите период:",reply_markup=reports_keyboard())
    @router.callback_query(admin,F.data.startswith("report:"))
    async def report(callback:CallbackQuery):
        scope=await scope_for(callback.from_user.id);period=(callback.data or "report:all").split(":",1)[1];title,start,end=period_bounds(period,tz)
        total=await db.summary(scope,start,end);cats=await db.category_totals(scope,start,end);lines=[summary_text(f"{SCOPES[scope]} — {title}",total)]
        expenses=[x for x in cats if x["tx_type"]=="expense"][:6]
        if expenses:
            lines.append("\n<b>Основные расходы:</b>")
            for r in expenses: lines.append(f"• {escape(str(r['vehicle_expense_type'] or r['category']))}: {money(float(r['total']))}")
        if callback.message: await callback.message.answer("\n".join(lines))
        await callback.answer()

    @router.message(admin,F.text=="📈 Сравнение")
    async def compare_start(message:Message): await message.answer("Что сравнить?",reply_markup=compare_keyboard())
    @router.callback_query(admin,F.data.startswith("compare:"))
    async def compare(callback:CallbackQuery):
        scope=await scope_for(callback.from_user.id);kind=(callback.data or "compare:month").split(":",1)[1];a_name,(as_,ae),b_name,(bs,be)=compare_bounds(kind,tz)
        a=await db.summary(scope,as_,ae);b=await db.summary(scope,bs,be)
        text=(f"<b>{SCOPES[scope]} — сравнение</b>\n\n<b>{escape(a_name)}</b> ↔ {escape(b_name)}\n\n"
              f"Доход: <b>{money(a.income)}</b> · {change_text(a.income,b.income)}\n"
              f"Расход: <b>{money(a.expense)}</b> · {change_text(a.expense,b.expense)}\n"
              f"Результат: <b>{money(a.profit)}</b> · {change_text(a.profit,b.profit)}")
        if callback.message: await callback.message.answer(text)
        await callback.answer()

    @router.message(admin,F.text=="🧾 Последние операции")
    async def recent(message:Message):
        scope=await scope_for(message.from_user.id);await message.answer(transactions_text(await db.recent(scope,15),SCOPES[scope]))

    # ---------- Objects / budgets ----------
    @router.message(admin,F.text=="📁 Объекты")
    async def objects(message:Message):
        if await scope_for(message.from_user.id)!="work": await message.answer("Объекты доступны в рабочих финансах.");return
        await message.answer("<b>Объекты</b>",reply_markup=objects_menu(await db.list_objects()))
    @router.callback_query(admin,F.data.in_({"object:list:active","object:list:archived"}))
    async def object_list(callback:CallbackQuery):
        archived=(callback.data or "").endswith("archived");objs=await db.list_objects(include_archived=True);objs=[o for o in objs if (o["status"]=="archived")==archived]
        if callback.message: await callback.message.edit_text("<b>Архив объектов</b>" if archived else "<b>Активные объекты</b>",reply_markup=objects_menu(objs))
        await callback.answer()
    @router.callback_query(admin,F.data=="object:add")
    async def object_add(callback:CallbackQuery,state:FSMContext): await state.clear();await state.set_state(ObjectFlow.name);await callback.message.answer("Название объекта:",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(ObjectFlow.name,admin)
    async def object_name(message:Message,state:FSMContext):
        d=await state.get_data();name=(message.text or "").strip()[:120]
        if d.get("return_to_edit"):
            try: oid=await db.add_object(name)
            except Exception:
                objs=await db.list_objects();m=next((o for o in objs if o["name"].casefold()==name.casefold()),None);oid=int(m["id"]) if m else 0
            if not oid: await message.answer("Не удалось создать объект.");return
            edit_data=d.copy();edit_data.pop("return_to_edit",None);edit_data.update(object_id=oid,object_name=name)
            await state.clear();await state.set_state(EditExpense.menu);await state.update_data(**edit_data);await edit_preview(message,state);return
        await state.update_data(new_object_name=name);await state.set_state(ObjectFlow.contract);await message.answer("Цена заказа / договора. Если не хотите указывать — отправьте 0:",reply_markup=CANCEL_MENU)
    @router.message(ObjectFlow.contract,admin)
    async def object_contract(message:Message,state:FSMContext):
        try:v=parse_amount(message.text or "",allow_zero=True)
        except ValueError as e: await message.answer(str(e));return
        await state.update_data(new_contract=v);await state.set_state(ObjectFlow.budget);await message.answer("Плановый бюджет расходов. Можно 0:",reply_markup=CANCEL_MENU)
    @router.message(ObjectFlow.budget,admin)
    async def object_budget(message:Message,state:FSMContext):
        try:v=parse_amount(message.text or "",allow_zero=True)
        except ValueError as e: await message.answer(str(e));return
        d=await state.get_data()
        try: oid=await db.add_object(d["new_object_name"],float(d.get("new_contract",0)),v)
        except Exception: await state.clear();await send_main(message,"work","Объект с таким названием уже существует.");return
        await state.clear();await show_object_card(message,oid)
    @router.callback_query(admin,F.data.startswith("object:view:"))
    async def object_view(callback:CallbackQuery): await show_object_card(callback,int((callback.data or "0").rsplit(":",1)[1]))
    @router.callback_query(admin,F.data.startswith("object:rename:"))
    async def object_rename_start(callback:CallbackQuery,state:FSMContext): await state.clear();await state.set_state(ObjectFlow.rename);await state.update_data(object_id=int((callback.data or "0").rsplit(":",1)[1]));await callback.message.answer("Новое название:",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(ObjectFlow.rename,admin)
    async def object_rename(message:Message,state:FSMContext):
        d=await state.get_data();oid=int(d["object_id"]);await db.update_object(oid,name=(message.text or "").strip()[:120]);await state.clear();await show_object_card(message,oid)
    @router.callback_query(admin,F.data.startswith("object:contract:"))
    async def object_contract_start(callback:CallbackQuery,state:FSMContext): await state.clear();await state.set_state(ObjectFlow.edit_contract);await state.update_data(object_id=int((callback.data or "0").rsplit(":",1)[1]));await callback.message.answer("Новая цена заказа (можно 0):",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(ObjectFlow.edit_contract,admin)
    async def object_contract_edit(message:Message,state:FSMContext):
        try:v=parse_amount(message.text or "",True)
        except ValueError as e: await message.answer(str(e));return
        d=await state.get_data();oid=int(d["object_id"]);await db.update_object(oid,contract=v);await state.clear();await show_object_card(message,oid)
    @router.callback_query(admin,F.data.startswith("object:budget:"))
    async def object_budget_start(callback:CallbackQuery,state:FSMContext): await state.clear();await state.set_state(ObjectFlow.edit_budget);await state.update_data(object_id=int((callback.data or "0").rsplit(":",1)[1]));await callback.message.answer("Новый бюджет расходов (можно 0):",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(ObjectFlow.edit_budget,admin)
    async def object_budget_edit(message:Message,state:FSMContext):
        try:v=parse_amount(message.text or "",True)
        except ValueError as e: await message.answer(str(e));return
        d=await state.get_data();oid=int(d["object_id"]);await db.update_object(oid,budget=v);await state.clear();await show_object_card(message,oid)
    @router.callback_query(admin,F.data.startswith("object:archive:"))
    async def object_archive(callback:CallbackQuery): oid=int((callback.data or "0").rsplit(":",1)[1]);await db.update_object(oid,archived=True);await show_object_card(callback,oid)
    @router.callback_query(admin,F.data.startswith("object:restore:"))
    async def object_restore(callback:CallbackQuery): oid=int((callback.data or "0").rsplit(":",1)[1]);await db.update_object(oid,archived=False);await show_object_card(callback,oid)

    # ---------- Vehicles ----------
    @router.message(admin,F.text=="🚗 Автомобиль")
    async def vehicle_entry(message:Message):
        if await scope_for(message.from_user.id)!="personal": await message.answer("Автомобиль находится в личных финансах.");return
        vs=await db.list_vehicles()
        if len(vs)==1: await show_vehicle_card(message,int(vs[0]["id"]))
        else: await message.answer("Выберите автомобиль:",reply_markup=vehicles_keyboard(vs))
    @router.callback_query(admin,F.data=="vehicle:list")
    async def vehicle_list(callback:CallbackQuery):
        if callback.message: await callback.message.edit_text("Выберите автомобиль:",reply_markup=vehicles_keyboard(await db.list_vehicles()))
        await callback.answer()
    @router.callback_query(admin,F.data=="vehicle:add")
    async def vehicle_add(callback:CallbackQuery,state:FSMContext): await state.clear();await state.set_state(VehicleFlow.name);await callback.message.answer("Название автомобиля, например «Renault Logan»:",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(VehicleFlow.name,admin)
    async def vehicle_name(message:Message,state:FSMContext): await state.update_data(vehicle_name=(message.text or "").strip()[:100]);await state.set_state(VehicleFlow.mileage);await message.answer("Текущий пробег, км:",reply_markup=CANCEL_MENU)
    @router.message(VehicleFlow.mileage,admin)
    async def vehicle_mileage_new(message:Message,state:FSMContext):
        try:m=parse_number(message.text or "")
        except ValueError as e: await message.answer(str(e));return
        d=await state.get_data();vid=await db.add_vehicle(d["vehicle_name"],m,datetime.now(tz).replace(tzinfo=None));await state.clear();await show_vehicle_card(message,vid)
    @router.callback_query(admin,F.data.startswith("vehicle:view:"))
    async def vehicle_view(callback:CallbackQuery): await show_vehicle_card(callback,int((callback.data or "0").rsplit(":",1)[1]))
    @router.callback_query(admin,F.data.startswith("vehicle:rename:"))
    async def vehicle_rename_start(callback:CallbackQuery,state:FSMContext): await state.clear();await state.set_state(VehicleFlow.rename);await state.update_data(vehicle_id=int((callback.data or "0").rsplit(":",1)[1]));await callback.message.answer("Новое название автомобиля:",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(VehicleFlow.rename,admin)
    async def vehicle_rename(message:Message,state:FSMContext):
        d=await state.get_data();vid=int(d["vehicle_id"]);await db.update_vehicle(vid,name=(message.text or "").strip()[:100]);await state.clear();await show_vehicle_card(message,vid)

    async def auto_amount_begin(callback:CallbackQuery,state:FSMContext,vehicle_id:int,expense_type:str,fuel_type:str|None=None):
        v=await db.get_vehicle(vehicle_id);await state.clear();await state.set_state(AutoExpense.amount);await state.update_data(vehicle_id=vehicle_id,vehicle_name=v["name"] if v else "Автомобиль",vehicle_expense_type=expense_type,fuel_type=fuel_type,finance_scope="personal",tx_type="expense",category="Автомобиль")
        label=f"{expense_type} — {fuel_type}" if fuel_type else expense_type
        await callback.message.answer(f"{label}: введите сумму:",reply_markup=CANCEL_MENU);await callback.answer()
    @router.callback_query(admin,F.data.startswith("auto:fuel:"))
    async def auto_fuel(callback:CallbackQuery,state:FSMContext):
        vid=int((callback.data or "0").rsplit(":",1)[1]);await state.clear();await state.set_state(AutoExpense.fuel_type);await state.update_data(vehicle_id=vid)
        if callback.message:await callback.message.answer("Какое топливо заправили?",reply_markup=fuel_type_keyboard(vid))
        await callback.answer()
    @router.callback_query(AutoExpense.fuel_type,admin,F.data.startswith("auto:fueltype:"))
    async def auto_fuel_type(callback:CallbackQuery,state:FSMContext):
        _,_,vid_s,kind=(callback.data or "").split(":",3);fuel_type="Бензин" if kind=="petrol" else "Газ"
        await auto_amount_begin(callback,state,int(vid_s),"Топливо",fuel_type)
    @router.callback_query(admin,F.data.startswith("auto:expense:"))
    async def auto_expense_menu(callback:CallbackQuery):
        vid=int((callback.data or "0").rsplit(":",1)[1]);await callback.message.answer("Выберите тип расхода:",reply_markup=vehicle_expense_type_keyboard(vid));await callback.answer()
    @router.callback_query(admin,F.data.startswith("auto:type:"))
    async def auto_type(callback:CallbackQuery,state:FSMContext):
        _,_,vid_s,idx_s=(callback.data or "").split(":",3);typ=VEHICLE_EXPENSE_TYPES[int(idx_s)];await auto_amount_begin(callback,state,int(vid_s),typ)
    @router.message(AutoExpense.amount,admin)
    async def auto_amount(message:Message,state:FSMContext):
        try:v=parse_amount(message.text or "")
        except ValueError as e: await message.answer(str(e));return
        d=await state.get_data();await state.update_data(amount=v)
        if d["vehicle_expense_type"]=="Топливо": await state.set_state(AutoExpense.liters);await message.answer("Сколько литров заправлено?",reply_markup=CANCEL_MENU)
        else: await state.set_state(AutoExpense.mileage);await message.answer("Текущий пробег, км. Если не хотите обновлять — отправьте 0:",reply_markup=CANCEL_MENU)
    @router.message(AutoExpense.liters,admin)
    async def auto_liters(message:Message,state:FSMContext):
        try:v=parse_number(message.text or "",False)
        except ValueError as e: await message.answer(str(e));return
        await state.update_data(fuel_liters=v);await state.set_state(AutoExpense.mileage);await message.answer("Текущий пробег, км. Можно 0:",reply_markup=CANCEL_MENU)
    @router.message(AutoExpense.mileage,admin)
    async def auto_mileage(message:Message,state:FSMContext):
        try:v=parse_number(message.text or "")
        except ValueError as e: await message.answer(str(e));return
        await state.update_data(odometer=v if v>0 else None);await state.set_state(AutoExpense.comment);await message.answer("Комментарий или «Без комментария»:",reply_markup=SKIP_COMMENT_MENU)
    @router.message(AutoExpense.comment,admin)
    async def auto_comment(message:Message,state:FSMContext):
        text=(message.text or "").strip();await state.update_data(comment=None if text.casefold()=="без комментария" else text[:250]);d=await state.get_data();await state.set_state(AutoExpense.confirm);await message.answer("Проверьте:\n\n"+transaction_preview(d),reply_markup=confirmation_keyboard("auto"))
    @router.callback_query(AutoExpense.confirm,admin,F.data=="auto:confirm")
    async def auto_confirm(callback:CallbackQuery,state:FSMContext): await save_transaction(callback,state,"auto")
    @router.callback_query(AutoExpense.confirm,admin,F.data=="auto:edit")
    async def auto_redo(callback:CallbackQuery,state:FSMContext):
        d=await state.get_data();vid=int(d["vehicle_id"]);typ=d["vehicle_expense_type"];await auto_amount_begin(callback,state,vid,typ,d.get("fuel_type"))

    @router.callback_query(admin,F.data.startswith("auto:mileage:"))
    async def mileage_start(callback:CallbackQuery,state:FSMContext): await state.clear();await state.set_state(MileageFlow.value);await state.update_data(vehicle_id=int((callback.data or "0").rsplit(":",1)[1]));await callback.message.answer("Новый пробег, км:",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(MileageFlow.value,admin)
    async def mileage_save(message:Message,state:FSMContext):
        try:v=parse_number(message.text or "")
        except ValueError as e: await message.answer(str(e));return
        d=await state.get_data();vid=int(d["vehicle_id"]);await db.update_vehicle_mileage(vid,v,datetime.now(tz).replace(tzinfo=None));await state.clear();await show_vehicle_card(message,vid)

    @router.callback_query(admin,F.data.startswith("auto:stats:"))
    async def auto_stats_menu(callback:CallbackQuery):
        vid=int((callback.data or "0").rsplit(":",1)[1]);await callback.message.answer("Период статистики:",reply_markup=vehicle_stats_keyboard(vid));await callback.answer()
    @router.callback_query(admin,F.data.startswith("autostat:"))
    async def auto_stats(callback:CallbackQuery):
        _,vid_s,period=(callback.data or "").split(":",2);vid=int(vid_s);title,start,end=period_bounds(period,tz)
        v=await db.get_vehicle(vid);total=await db.summary("personal",start,end,vehicle_id=vid);cats=await db.category_totals("personal",start,end,vehicle_id=vid);distance=await db.vehicle_distance(vid,start,end);fuel=await db.vehicle_fuel_stats(vid,start,end)
        lines=[f"🚗 <b>{escape(str(v['name'] if v else 'Автомобиль'))} — {escape(title)}</b>",f"Всего расходов: <b>{money(total.expense)}</b>"]
        for r in [x for x in cats if x["tx_type"]=="expense"]: lines.append(f"• {escape(str(r['vehicle_expense_type'] or r['category']))}: {money(float(r['total']))}")
        if distance>0: lines.append(f"\nПроехано по зафиксированному пробегу: <b>{distance:,.0f} км</b>\nСтоимость: <b>{total.expense/distance:.2f} ₽/км</b>")
        if fuel["amount"]>0:
            lines.append(f"\n⛽ Топливо всего: <b>{money(float(fuel['amount']))}</b> · {float(fuel['liters']):.1f} л")
            for fuel_name in ("Бензин","Газ","Не указано"):
                fs=fuel.get("by_type",{}).get(fuel_name)
                if not fs:continue
                icon="⛽" if fuel_name=="Бензин" else ("🔵" if fuel_name=="Газ" else "▫️")
                lines.append(f"{icon} {fuel_name}: <b>{money(float(fs['amount']))}</b> · {float(fs['liters']):.1f} л · {float(fs['avg_price']):.2f} ₽/л")
            if distance>0: lines.append(f"Стоимость топлива: <b>{float(fuel['amount'])/distance:.2f} ₽/км</b>")
        if callback.message: await callback.message.answer("\n".join(lines),reply_markup=vehicle_stats_keyboard(vid))
        await callback.answer()

    @router.callback_query(admin,F.data.startswith("auto:history:"))
    async def auto_history(callback:CallbackQuery):
        vid=int((callback.data or "0").rsplit(":",1)[1]);v=await db.get_vehicle(vid);rows=await db.vehicle_transactions(vid,20)
        if callback.message: await callback.message.answer(transactions_text(rows,f"История — {v['name'] if v else 'Автомобиль'}"))
        await callback.answer()

    @router.callback_query(admin,F.data.startswith("auto:service:"))
    async def service_list(callback:CallbackQuery):
        vid=int((callback.data or "0").rsplit(":",1)[1]);v=await db.get_vehicle(vid);items=await db.list_service(vid);lines=["🛢 <b>Сервис по пробегу</b>"]
        if not items: lines.append("\nСервисных интервалов пока нет.")
        for x in items:
            left=float(x["next_mileage"])-float(v["current_mileage"] if v else 0);status=f"осталось {left:,.0f} км" if left>=0 else f"⚠️ просрочено на {abs(left):,.0f} км"
            lines.append(f"\n• <b>{escape(str(x['title']))}</b>\nСледующий: {float(x['next_mileage']):,.0f} км · {status}")
        if callback.message: await callback.message.answer("\n".join(lines),reply_markup=service_keyboard(vid))
        await callback.answer()
    @router.callback_query(admin,F.data.startswith("service:add:"))
    async def service_add_start(callback:CallbackQuery,state:FSMContext): await state.clear();await state.set_state(ServiceFlow.title);await state.update_data(vehicle_id=int((callback.data or "0").rsplit(":",1)[1]));await callback.message.answer("Что обслуживаем? Например «Замена масла»:",reply_markup=CANCEL_MENU);await callback.answer()
    @router.message(ServiceFlow.title,admin)
    async def service_title(message:Message,state:FSMContext): await state.update_data(title=(message.text or "").strip()[:100]);await state.set_state(ServiceFlow.last_mileage);await message.answer("На каком пробеге выполнено последнее обслуживание?",reply_markup=CANCEL_MENU)
    @router.message(ServiceFlow.last_mileage,admin)
    async def service_last(message:Message,state:FSMContext):
        try:v=parse_number(message.text or "")
        except ValueError as e: await message.answer(str(e));return
        await state.update_data(last_mileage=v);await state.set_state(ServiceFlow.interval);await message.answer("Интервал в километрах, например 10000:",reply_markup=CANCEL_MENU)
    @router.message(ServiceFlow.interval,admin)
    async def service_interval(message:Message,state:FSMContext):
        try:v=parse_number(message.text or "",False)
        except ValueError as e: await message.answer(str(e));return
        await state.update_data(interval=v);await state.set_state(ServiceFlow.note);await message.answer("Комментарий или «Без комментария»:",reply_markup=SKIP_COMMENT_MENU)
    @router.message(ServiceFlow.note,admin)
    async def service_note(message:Message,state:FSMContext):
        d=await state.get_data();text=(message.text or "").strip();note=None if text.casefold()=="без комментария" else text[:250];vid=int(d["vehicle_id"]);await db.add_service(vid,d["title"],float(d["last_mileage"]),float(d["interval"]),note,datetime.now(tz).replace(tzinfo=None));await state.clear();await show_vehicle_card(message,vid)

    # ---------- Personal debts ----------
    @router.message(admin,F.text=="💸 Долги")
    async def debts_entry(message:Message):
        if await scope_for(message.from_user.id)!="personal": await message.answer("Долги находятся в личных финансах.");return
        await show_debts_menu(message)

    @router.callback_query(admin,F.data=="debt:menu")
    async def debts_menu_cb(callback:CallbackQuery): await show_debts_menu(callback)

    @router.callback_query(admin,F.data.startswith("debt:list:"))
    async def debts_list(callback:CallbackQuery):
        mode=(callback.data or "debt:list:active").rsplit(":",1)[1]
        if mode in {"to_me","i_owe"}: rows=await db.list_debts(status="active",direction=mode);title=DEBT_DIRECTIONS[mode]
        elif mode=="closed": rows=await db.list_debts(status="closed");title="Закрытые долги"
        else: rows=await db.list_debts(status="active");title="Все активные долги"
        text=f"💸 <b>{escape(title)}</b>\n\n"+("Выберите долг:" if rows else "Записей пока нет.")
        if callback.message: await callback.message.edit_text(text,reply_markup=debt_list_keyboard(rows))
        await callback.answer()

    @router.callback_query(admin,F.data=="debt:add")
    async def debt_add_start(callback:CallbackQuery,state:FSMContext):
        await state.clear();await state.set_state(DebtFlow.direction)
        if callback.message:await callback.message.answer("Какой это долг?",reply_markup=debt_direction_keyboard())
        await callback.answer()

    @router.callback_query(DebtFlow.direction,admin,F.data.startswith("debt:newdir:"))
    async def debt_direction(callback:CallbackQuery,state:FSMContext):
        direction=(callback.data or "").rsplit(":",1)[1]
        if direction not in DEBT_DIRECTIONS:await callback.answer("Неизвестный тип долга",show_alert=True);return
        await state.update_data(debt_direction=direction);await state.set_state(DebtFlow.person)
        if callback.message:await callback.message.answer("Кто? Введите имя человека или название:",reply_markup=CANCEL_MENU)
        await callback.answer()

    @router.message(DebtFlow.person,admin)
    async def debt_person(message:Message,state:FSMContext):
        person=(message.text or "").strip()[:120]
        if not person:await message.answer("Введите имя или название.");return
        await state.update_data(debt_person=person);await state.set_state(DebtFlow.amount);await message.answer("Сумма долга:",reply_markup=CANCEL_MENU)

    @router.message(DebtFlow.amount,admin)
    async def debt_amount(message:Message,state:FSMContext):
        try:v=parse_amount(message.text or "")
        except ValueError as e:await message.answer(str(e));return
        await state.update_data(debt_amount=v);await state.set_state(DebtFlow.due_date);await message.answer("Срок возврата в формате ДД.ММ.ГГГГ или «Без срока»:",reply_markup=CANCEL_MENU)

    @router.message(DebtFlow.due_date,admin)
    async def debt_due(message:Message,state:FSMContext):
        try:due=parse_debt_date(message.text or "")
        except ValueError as e:await message.answer(str(e));return
        await state.update_data(debt_due_date=due);await state.set_state(DebtFlow.comment);await message.answer("Комментарий или «Без комментария»:",reply_markup=SKIP_COMMENT_MENU)

    @router.message(DebtFlow.comment,admin)
    async def debt_comment(message:Message,state:FSMContext):
        text=(message.text or "").strip();comment=None if text.casefold()=="без комментария" else text[:250]
        await state.update_data(debt_comment=comment);d=await state.get_data();direction=DEBT_DIRECTIONS[d["debt_direction"]]
        preview=(f"💸 <b>{escape(direction)}</b>\nКто: <b>{escape(str(d['debt_person']))}</b>\n"
                 f"Сумма: <b>{money(float(d['debt_amount']))}</b>\nСрок: <b>{escape(debt_date_text(d.get('debt_due_date')))}</b>\n"
                 f"Комментарий: {escape(str(comment or 'Без комментария'))}")
        await state.set_state(DebtFlow.confirm);await message.answer("Проверьте:\n\n"+preview,reply_markup=confirmation_keyboard("debtnew"))

    @router.callback_query(DebtFlow.confirm,admin,F.data=="debtnew:confirm")
    async def debt_confirm(callback:CallbackQuery,state:FSMContext):
        d=await state.get_data();debt_id=await db.add_debt(d["debt_direction"],d["debt_person"],float(d["debt_amount"]),d.get("debt_due_date"),d.get("debt_comment"),datetime.now(tz).replace(tzinfo=None))
        await state.clear()
        if callback.message:await callback.message.answer(f"✅ Долг #{debt_id} сохранён.")
        await show_debt_card(callback,debt_id)

    @router.callback_query(DebtFlow.confirm,admin,F.data=="debtnew:edit")
    async def debt_redo(callback:CallbackQuery,state:FSMContext):
        await state.clear();await state.set_state(DebtFlow.direction)
        if callback.message:await callback.message.answer("Заполним заново. Какой это долг?",reply_markup=debt_direction_keyboard())
        await callback.answer()

    @router.callback_query(admin,F.data.startswith("debt:view:"))
    async def debt_view(callback:CallbackQuery): await show_debt_card(callback,int((callback.data or "0").rsplit(":",1)[1]))

    @router.callback_query(admin,F.data.startswith("debt:pay:"))
    async def debt_pay_start(callback:CallbackQuery,state:FSMContext):
        debt_id=int((callback.data or "0").rsplit(":",1)[1]);d=await db.get_debt(debt_id)
        if not d or d["status"]=="closed":await callback.answer("Долг уже закрыт",show_alert=True);return
        await state.clear();await state.set_state(DebtFlow.payment);await state.update_data(debt_id=debt_id)
        if callback.message:await callback.message.answer(f"Остаток: <b>{money(float(d['remaining']))}</b>\nВведите сумму погашения:",reply_markup=CANCEL_MENU)
        await callback.answer()

    @router.message(DebtFlow.payment,admin)
    async def debt_payment_amount(message:Message,state:FSMContext):
        try:amount=parse_amount(message.text or "")
        except ValueError as e:await message.answer(str(e));return
        d=await state.get_data();debt_id=int(d["debt_id"]);debt=await db.get_debt(debt_id)
        if not debt:await state.clear();await message.answer("Долг не найден.");return
        if amount>float(debt["remaining"])+0.005:await message.answer(f"Сумма больше остатка {money(float(debt['remaining']))}.");return
        try:payment_id=await db.add_debt_payment(debt_id,amount,datetime.now(tz).replace(tzinfo=None))
        except ValueError as e:await message.answer(str(e));return
        await state.clear();await prompt_debt_payment_record(message,payment_id)

    @router.callback_query(admin,F.data.startswith("debt:close:"))
    async def debt_close(callback:CallbackQuery,state:FSMContext):
        debt_id=int((callback.data or "0").rsplit(":",1)[1]);d=await db.get_debt(debt_id)
        if not d:await callback.answer("Долг не найден",show_alert=True);return
        remaining=float(d["remaining"])
        if d["status"]=="closed" or remaining<=0.005:await show_debt_card(callback,debt_id);return
        if callback.message:await callback.message.answer(f"Закрыть долг полностью? Будет записано погашение остатка <b>{money(remaining)}</b>.",reply_markup=debt_close_confirm_keyboard(debt_id))
        await callback.answer()

    @router.callback_query(admin,F.data.startswith("debt:closeconfirm:"))
    async def debt_close_confirm(callback:CallbackQuery,state:FSMContext):
        debt_id=int((callback.data or "0").rsplit(":",1)[1]);d=await db.get_debt(debt_id)
        if not d:await callback.answer("Долг не найден",show_alert=True);return
        remaining=float(d["remaining"])
        if remaining<=0.005:await show_debt_card(callback,debt_id);return
        payment_id=await db.add_debt_payment(debt_id,remaining,datetime.now(tz).replace(tzinfo=None),"Закрытие долга")
        await prompt_debt_payment_record(callback,payment_id)

    @router.callback_query(admin,F.data.startswith("debt:record:"))
    async def debt_record_payment(callback:CallbackQuery):
        parts=(callback.data or "").split(":");payment_id=int(parts[2]);answer=parts[3]
        pmt=await db.get_debt_payment(payment_id)
        if not pmt:await callback.answer("Платёж не найден",show_alert=True);return
        if answer=="yes" and not pmt.get("transaction_id"):
            tx_type="income" if pmt["direction"]=="to_me" else "expense";category="Возврат долга" if tx_type=="income" else "Погашение долга"
            tx_id=await db.add_transaction(tx_type=tx_type,amount=float(pmt["amount"]),category=category,finance_scope="personal",object_id=None,object_name=None,
                comment=f"Долг: {pmt['person']}",created_at=datetime.now(tz).replace(tzinfo=None),created_by=callback.from_user.id,source="debt")
            await db.set_debt_payment_transaction(payment_id,tx_id)
            note=f"✅ Записано в личные финансы как {'доход' if tx_type=='income' else 'расход'} #{tx_id}."
        elif answer=="yes":note="Эта операция уже записана в личные финансы."
        else:note="Погашение учтено только во вкладке долгов."
        if callback.message:await callback.message.answer(note)
        await show_debt_card(callback,int(pmt["debt_id"]))

    @router.callback_query(admin,F.data.startswith("debt:history:"))
    async def debt_history(callback:CallbackQuery):
        debt_id=int((callback.data or "0").rsplit(":",1)[1]);d=await db.get_debt(debt_id);rows=await db.debt_payments(debt_id)
        if not d:await callback.answer("Долг не найден",show_alert=True);return
        lines=[f"📖 <b>История — {escape(str(d['person']))}</b>",f"Исходная сумма: <b>{money(float(d['original_amount']))}</b>"]
        if not rows:lines.append("\nПогашений пока нет.")
        for r in rows:
            linked=f" · операция #{r['transaction_id']}" if r.get("transaction_id") else ""
            lines.append(f"\n• <b>{money(float(r['amount']))}</b> · <code>{r['paid_at']}</code>{linked}")
        if callback.message:await callback.message.answer("\n".join(lines),reply_markup=debt_card_keyboard(debt_id,d["status"]=="closed"))
        await callback.answer()

    @router.callback_query(admin,F.data.startswith("debt:edit:"))
    async def debt_edit(callback:CallbackQuery):
        debt_id=int((callback.data or "0").rsplit(":",1)[1]);d=await db.get_debt(debt_id)
        if not d or d["status"]=="closed":await callback.answer("Закрытый долг нельзя изменить",show_alert=True);return
        if callback.message:await callback.message.answer("Что изменить?",reply_markup=debt_edit_keyboard(debt_id))
        await callback.answer()

    @router.callback_query(admin,F.data.startswith("debt:editfield:"))
    async def debt_edit_field(callback:CallbackQuery,state:FSMContext):
        _,_,debt_id_s,field=(callback.data or "").split(":",3);debt_id=int(debt_id_s);await state.clear();await state.update_data(debt_id=debt_id)
        prompts={"person":("Кто? Новое имя или название:",DebtFlow.edit_person,CANCEL_MENU),"amount":("Новая исходная сумма долга:",DebtFlow.edit_amount,CANCEL_MENU),"due":("Новый срок ДД.ММ.ГГГГ или «Без срока»:",DebtFlow.edit_due_date,CANCEL_MENU),"comment":("Новый комментарий или «Без комментария»:",DebtFlow.edit_comment,SKIP_COMMENT_MENU)}
        if field not in prompts:await callback.answer("Поле не найдено",show_alert=True);return
        prompt,st,kb=prompts[field];await state.set_state(st)
        if callback.message:await callback.message.answer(prompt,reply_markup=kb)
        await callback.answer()

    @router.message(DebtFlow.edit_person,admin)
    async def debt_edit_person(message:Message,state:FSMContext):
        person=(message.text or "").strip()[:120]
        if not person:await message.answer("Введите имя.");return
        d=await state.get_data();debt_id=int(d["debt_id"]);await db.update_debt(debt_id,person=person);await state.clear();await show_debt_card(message,debt_id)

    @router.message(DebtFlow.edit_amount,admin)
    async def debt_edit_amount(message:Message,state:FSMContext):
        try:amount=parse_amount(message.text or "")
        except ValueError as e:await message.answer(str(e));return
        d=await state.get_data();debt_id=int(d["debt_id"])
        try:await db.update_debt(debt_id,original_amount=amount)
        except ValueError as e:await message.answer(str(e));return
        await state.clear();await show_debt_card(message,debt_id)

    @router.message(DebtFlow.edit_due_date,admin)
    async def debt_edit_due(message:Message,state:FSMContext):
        try:due=parse_debt_date(message.text or "")
        except ValueError as e:await message.answer(str(e));return
        d=await state.get_data();debt_id=int(d["debt_id"]);await db.update_debt(debt_id,due_date=due);await state.clear();await show_debt_card(message,debt_id)

    @router.message(DebtFlow.edit_comment,admin)
    async def debt_edit_comment(message:Message,state:FSMContext):
        text=(message.text or "").strip();comment=None if text.casefold()=="без комментария" else text[:250]
        d=await state.get_data();debt_id=int(d["debt_id"]);await db.update_debt(debt_id,comment=comment);await state.clear();await show_debt_card(message,debt_id)

    # ---------- Smart input / Groq ----------
    @router.message(admin,F.text=="🧠 Умный ввод")
    async def smart_start(message:Message,state:FSMContext):
        scope=await scope_for(message.from_user.id);await state.clear();await state.set_state(SmartInput.text);await state.update_data(current_scope=scope)
        await message.answer("Опишите одну операцию обычным текстом. Groq определит сумму, тип и категорию.\nНапример: «Купил металл за 38500 для Навес Репное».",reply_markup=CANCEL_MENU)
    @router.message(SmartInput.text,admin)
    async def smart_parse(message:Message,state:FSMContext):
        d=await state.get_data();current=d["current_scope"];objs=await db.list_objects();names=[o["name"] for o in objs]
        try:p=await ai.parse_operation(message.text or "",current,names)
        except ValueError as e: await message.answer(f"Не удалось распознать: {escape(str(e))}");return
        data=p.as_dict();data["object_id"]=None
        if data["finance_scope"]=="work" and data.get("object_name"):
            q=str(data["object_name"]).casefold();match=next((o for o in objs if o["name"].casefold()==q or q in o["name"].casefold() or o["name"].casefold() in q),None)
            if match: data["object_id"]=int(match["id"]);data["object_name"]=match["name"]
        await state.update_data(**data);await state.set_state(SmartInput.confirm);await message.answer("Распознано:\n\n"+transaction_preview(data),reply_markup=confirmation_keyboard("smart"))
    @router.callback_query(SmartInput.confirm,admin,F.data=="smart:confirm")
    async def smart_confirm(callback:CallbackQuery,state:FSMContext): await save_transaction(callback,state,"groq" if ai.enabled else "fallback")
    @router.callback_query(SmartInput.confirm,admin,F.data=="smart:edit")
    async def smart_redo(callback:CallbackQuery,state:FSMContext):
        scope=await scope_for(callback.from_user.id);await state.clear();await state.set_state(SmartInput.text);await state.update_data(current_scope=scope);await callback.message.answer("Опишите операцию заново:",reply_markup=CANCEL_MENU);await callback.answer()

    @router.message(admin,F.text=="🤖 Спросить Groq")
    async def ask_start(message:Message,state:FSMContext):
        scope=await scope_for(message.from_user.id);await state.clear();await state.set_state(AskAI.question);await state.update_data(scope=scope);await message.answer(f"Задайте вопрос по разделу {SCOPES[scope]}. Например: «На что ушло больше всего денег?»",reply_markup=CANCEL_MENU)
    @router.message(AskAI.question,admin)
    async def ask(message:Message,state:FSMContext):
        d=await state.get_data();scope=d["scope"];total=await db.summary(scope);cats=await db.category_totals(scope);recent_rows=await db.recent(scope,50)
        lines=[f"Раздел: {scope}",f"Доходы: {total.income:.2f}",f"Расходы: {total.expense:.2f}",f"Результат: {total.profit:.2f}","Категории:"]
        for r in cats: lines.append(f"- {r['tx_type']} / {r['vehicle_expense_type'] or r['category']}: {float(r['total']):.2f}")
        if scope=="work":
            lines.append("Объекты:")
            for o in await db.list_objects(include_archived=True):
                _,s=await db.object_stats(int(o["id"]));lines.append(f"- {o['name']}: договор {o['contract_amount']}, бюджет {o['budget_amount']}, доход {s.income}, расход {s.expense}")
        else:
            lines.append("Автомобили:")
            for v in await db.list_vehicles(include_archived=True):
                s=await db.summary("personal",vehicle_id=int(v["id"]));fuel=await db.vehicle_fuel_stats(int(v["id"]));lines.append(f"- {v['name']}: пробег {v['current_mileage']}, расходы {s.expense}, топливо {fuel.get('by_type',{})}")
            dt=await db.debt_totals();lines.append(f"Долги: мне должны {dt['to_me']}, я должен {dt['i_owe']}, чистая позиция {dt['net']}")
            for drow in await db.list_debts(status="active"):
                lines.append(f"- долг {drow['direction']} {drow['person']}: исходно {drow['original_amount']}, осталось {drow['remaining']}, срок {drow.get('due_date') or 'нет'}")
        lines.append("Последние операции:")
        for r in recent_rows: lines.append(f"- {r['created_at']} {r['tx_type']} {r['amount']} {r['category']} {r.get('object_name') or ''} {r.get('vehicle_expense_type') or ''} {r.get('fuel_type') or ''} {r.get('comment') or ''}")
        try:answer=await ai.answer_finance_question((message.text or "").strip(),"\n".join(lines),scope)
        except Exception as e: answer=f"Ошибка Groq: {e}"
        await state.clear()
        for i in range(0,len(answer),3900): await message.answer(escape(answer[i:i+3900]),reply_markup=main_menu(scope))

    # ---------- Excel ----------
    @router.message(admin,F.text=="📤 Excel")
    async def excel(message:Message):
        rows=await db.all_transactions()
        if not rows: await message.answer("Операций пока нет.");return
        await message.answer("Формирую Excel…");path=await create_excel(rows,await db.list_objects(True),await db.list_vehicles(True),await db.all_debts(),await db.all_debt_payments(),export_dir)
        try: await message.answer_document(FSInputFile(path),caption=f"ФД Финансы. Операций: {len(rows)}")
        finally: path.unlink(missing_ok=True)

    @router.message()
    async def unknown(message:Message):
        if message.from_user.id not in settings.admin_ids: await message.answer(f"Доступ запрещён. Ваш Telegram ID: <code>{message.from_user.id}</code>");return
        scope=await scope_for(message.from_user.id);await send_main(message,scope,"Выберите действие в меню или используйте /start.")

    @router.callback_query()
    async def stale(callback:CallbackQuery):
        if callback.from_user.id not in settings.admin_ids: await callback.answer("Нет доступа",show_alert=True)
        else: await callback.answer("Кнопка устарела. Откройте меню заново.",show_alert=True)

    return router
