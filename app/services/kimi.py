from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

try:
    from openai import AsyncOpenAI
except ImportError:  # Позволяет ручному учёту работать без SDK Kimi
    AsyncOpenAI = None  # type: ignore[assignment,misc]

from app.constants import EXPENSE_CATEGORIES, INCOME_CATEGORIES


@dataclass(slots=True)
class ParsedOperation:
    tx_type: str
    amount: float
    category: str
    object_name: str | None
    comment: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tx_type": self.tx_type,
            "amount": self.amount,
            "category": self.category,
            "object_name": self.object_name,
            "comment": self.comment,
        }


class KimiService:
    def __init__(self, api_key: str | None, model: str, base_url: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        if api_key and AsyncOpenAI is None:
            raise RuntimeError("Для подключения ИИ установите зависимости: pip install -r requirements.txt")
        self.client = (
            AsyncOpenAI(api_key=api_key, base_url=self.base_url)
            if api_key and AsyncOpenAI is not None
            else None
        )

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def parse_operation(self, text: str) -> ParsedOperation:
        if not self.client:
            return self._fallback_parse(text)

        system = (
            "Ты финансовый помощник производственной мастерской. "
            "Извлеки ровно одну финансовую операцию. Не придумывай сумму. "
            f"Категории доходов: {', '.join(INCOME_CATEGORIES)}. "
            f"Категории расходов: {', '.join(EXPENSE_CATEGORIES)}. "
            "Выбери ближайшую категорию. Объект — заказ, стройка или место, к которому относится операция. "
            "Верни только JSON без markdown и пояснений со строго такими полями: "
            '{"tx_type":"income|expense","amount":число,"category":"строка",'
            '"object_name":"строка или null","comment":"строка или null"}.'
        )
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                temperature=0,
            )
            raw = (response.choices[0].message.content or "").strip()
            data = self._extract_json(raw)
            return self._validate(data)
        except Exception:
            return self._fallback_parse(text)

    async def answer_finance_question(self, question: str, context: str) -> str:
        if not self.client:
            return (
                "Kimi пока не подключён. Добавьте AI_API_KEY в файл .env "
                "и перезапустите бота."
            )
        system = (
            "Ты финансовый аналитик мастерской «Фабрика Деталей». "
            "Отвечай по-русски, кратко и конкретно. Используй только переданные данные. "
            "Не выдумывай операции и суммы. Если данных недостаточно, прямо скажи об этом."
        )
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"ДАННЫЕ:\n{context}\n\nВОПРОС:\n{question}"},
            ],
            temperature=0.2,
        )
        return (response.choices[0].message.content or "Не удалось сформировать ответ.").strip()

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            value = json.loads(cleaned)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("ИИ не вернул JSON")
        value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise ValueError("ИИ вернул неверную структуру")
        return value

    def _validate(self, data: dict[str, Any]) -> ParsedOperation:
        tx_type = str(data.get("tx_type", "")).strip()
        if tx_type not in {"income", "expense"}:
            raise ValueError("Не определён тип операции")
        amount = float(data.get("amount", 0))
        if amount <= 0:
            raise ValueError("Не определена сумма")
        allowed = INCOME_CATEGORIES if tx_type == "income" else EXPENSE_CATEGORIES
        category = self._normalize_category(str(data.get("category", "")), allowed)
        object_name = self._clean_optional(data.get("object_name"))
        comment = self._clean_optional(data.get("comment"))
        return ParsedOperation(tx_type, amount, category, object_name, comment)

    @staticmethod
    def _clean_optional(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:250] if text else None

    @staticmethod
    def _normalize_category(value: str, allowed: tuple[str, ...]) -> str:
        lowered = value.casefold().strip()
        for category in allowed:
            if category.casefold() == lowered:
                return category
        for category in allowed:
            if category.casefold() in lowered or lowered in category.casefold():
                return category
        return allowed[-1]

    def _fallback_parse(self, text: str) -> ParsedOperation:
        lowered = text.casefold()
        income_words = ("доход", "получил", "получили", "аванс", "оплата", "заплатил клиент", "продал")
        tx_type = "income" if any(word in lowered for word in income_words) else "expense"

        candidates = re.findall(r"(?<!\d)(\d[\d\s]*(?:[.,]\d{1,2})?)(?!\d)", text)
        if not candidates:
            raise ValueError("Не нашёл сумму. Напишите, например: Купил металл за 38500 рублей")
        normalized = candidates[0].replace(" ", "").replace(",", ".")
        amount = float(normalized)
        if amount <= 0:
            raise ValueError("Сумма должна быть больше нуля")

        if tx_type == "income":
            mapping = {
                "аванс": "Аванс",
                "доплат": "Доплата",
                "материал": "Продажа материала",
                "оплат": "Оплата за работу",
            }
            allowed = INCOME_CATEGORIES
        else:
            mapping = {
                "труб": "Материалы",
                "металл": "Материалы",
                "краск": "Материалы",
                "инструмент": "Инструмент",
                "зарплат": "Зарплата",
                "ивану": "Зарплата",
                "бензин": "Транспорт",
                "достав": "Транспорт",
                "аренд": "Аренда",
                "реклам": "Реклама",
                "налог": "Налоги",
            }
            allowed = EXPENSE_CATEGORIES
        category = allowed[-1]
        for keyword, mapped in mapping.items():
            if keyword in lowered:
                category = mapped
                break

        object_name = None
        object_match = re.search(r"(?:для|объект(?:а|у)?|на объект)\s+([^,.]+)", text, re.IGNORECASE)
        if object_match:
            object_name = object_match.group(1).strip()[:120]

        return ParsedOperation(tx_type, amount, category, object_name, text.strip()[:250])
