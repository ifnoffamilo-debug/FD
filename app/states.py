from aiogram.fsm.state import State, StatesGroup


class ManualTransaction(StatesGroup):
    amount = State()
    category = State()
    object_name = State()
    comment = State()
    confirm = State()


class SmartInput(StatesGroup):
    text = State()
    confirm = State()


class AskAI(StatesGroup):
    question = State()


class ObjectReport(StatesGroup):
    object_name = State()


class EditExpense(StatesGroup):
    menu = State()
    amount = State()
    category = State()
    object_name = State()
    comment = State()
