from __future__ import annotations

INCOME_CATEGORIES = (
    "Аванс",
    "Оплата за работу",
    "Доплата",
    "Продажа материала",
    "Прочий доход",
)

EXPENSE_CATEGORIES = (
    "Материалы",
    "Инструмент",
    "Зарплата",
    "Транспорт",
    "Аренда",
    "Реклама",
    "Хозтовары",
    "Налоги",
    "Прочий расход",
)

TYPE_LABELS = {"income": "Доход", "expense": "Расход"}

ALL_CATEGORIES = {
    "income": INCOME_CATEGORIES,
    "expense": EXPENSE_CATEGORIES,
}
