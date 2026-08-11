from __future__ import annotations

SCOPES = {"work": "🏭 Рабочие финансы", "personal": "👤 Личные финансы"}
TYPE_LABELS = {"income": "Доход", "expense": "Расход"}

WORK_INCOME_CATEGORIES = (
    "Аванс", "Оплата за работу", "Доплата", "Продажа материала", "Прочий доход",
)
WORK_EXPENSE_CATEGORIES = (
    "Материалы", "Инструмент", "Зарплата", "Транспорт", "Аренда", "Реклама",
    "Хозтовары", "Налоги", "Прочий расход",
)
PERSONAL_INCOME_CATEGORIES = (
    "Зарплата", "Перевод", "Возврат", "Продажа", "Прочий доход",
)
PERSONAL_EXPENSE_CATEGORIES = (
    "Продукты", "Дом", "Автомобиль", "Покупки", "Развлечения", "Одежда",
    "Подписки и связь", "Путешествия", "Налоги", "Прочий расход",
)

CATEGORIES = {
    "work": {"income": WORK_INCOME_CATEGORIES, "expense": WORK_EXPENSE_CATEGORIES},
    "personal": {"income": PERSONAL_INCOME_CATEGORIES, "expense": PERSONAL_EXPENSE_CATEGORIES},
}

VEHICLE_EXPENSE_TYPES = (
    "Топливо", "Ремонт", "ТО", "Запчасти", "Шины", "Масла и жидкости",
    "Мойка", "Страховка", "Налоги", "Парковка", "Другое",
)
