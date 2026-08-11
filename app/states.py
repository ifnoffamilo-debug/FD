from aiogram.fsm.state import State, StatesGroup


class ManualTransaction(StatesGroup):
    amount = State(); category = State(); object_choice = State(); new_object = State(); comment = State(); confirm = State()


class SmartInput(StatesGroup):
    text = State(); confirm = State()


class AskAI(StatesGroup):
    question = State()


class EditExpense(StatesGroup):
    menu = State(); amount = State(); category = State(); object_choice = State(); comment = State()


class ReceiptUpload(StatesGroup):
    photo = State()


class ObjectFlow(StatesGroup):
    name = State(); contract = State(); budget = State(); rename = State(); edit_contract = State(); edit_budget = State()


class VehicleFlow(StatesGroup):
    name = State(); mileage = State(); rename = State()


class AutoExpense(StatesGroup):
    amount = State(); liters = State(); mileage = State(); comment = State(); confirm = State()


class MileageFlow(StatesGroup):
    value = State()


class ServiceFlow(StatesGroup):
    title = State(); last_mileage = State(); interval = State(); note = State()
