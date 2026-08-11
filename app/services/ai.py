from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore

from app.constants import CATEGORIES


@dataclass(slots=True)
class ParsedOperation:
    tx_type: str
    amount: float
    category: str
    finance_scope: str
    object_name: str | None
    comment: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tx_type": self.tx_type, "amount": self.amount, "category": self.category,
            "finance_scope": self.finance_scope, "object_name": self.object_name, "comment": self.comment,
        }


class AIService:
    def __init__(self, api_key: str | None, model: str, base_url: str) -> None:
        self.model=model; self.base_url=base_url.rstrip("/")
        self.client=AsyncOpenAI(api_key=api_key,base_url=self.base_url) if api_key and AsyncOpenAI else None

    @property
    def enabled(self) -> bool: return self.client is not None

    async def parse_operation(self, text: str, current_scope: str, object_names: list[str] | None=None) -> ParsedOperation:
        if not self.client: return self._fallback(text,current_scope)
        cats=CATEGORIES[current_scope]
        sys=(
            "Ты помощник по личным и рабочим финансам. Извлеки ровно одну операцию. "
            f"Текущий раздел: {current_scope}. Если пользователь явно говорит 'личный/личные' — finance_scope=personal; "
            "если явно говорит 'рабочий/работа/бизнес' — finance_scope=work; иначе используй текущий раздел. "
            f"Категории текущего раздела: доходы={list(cats['income'])}; расходы={list(cats['expense'])}. "
            f"Известные рабочие объекты: {object_names or []}. Для personal object_name всегда null. "
            "Не придумывай сумму. Верни только JSON: "
            '{"tx_type":"income|expense","amount":число,"category":"строка","finance_scope":"work|personal",'
            '"object_name":"строка или null","comment":"строка или null"}'
        )
        msgs=[{"role":"system","content":sys},{"role":"user","content":text}]
        try:
            try:
                r=await self.client.chat.completions.create(model=self.model,messages=msgs,temperature=0,response_format={"type":"json_object"})
            except Exception:
                r=await self.client.chat.completions.create(model=self.model,messages=msgs,temperature=0)
            data=self._extract(r.choices[0].message.content or "")
            return self._validate(data,current_scope)
        except Exception:
            return self._fallback(text,current_scope)

    async def answer_finance_question(self, question: str, context: str, scope: str) -> str:
        if not self.client:
            return "Groq не подключён. Добавьте AI_API_KEY в Railway и перезапустите бота."
        sys=(
            "Ты персональный финансовый аналитик пользователя. Отвечай по-русски, конкретно и без выдуманных сумм. "
            f"Активный раздел: {scope}. Используй только переданные данные. Если данных недостаточно — скажи об этом."
        )
        r=await self.client.chat.completions.create(model=self.model,messages=[{"role":"system","content":sys},{"role":"user","content":f"ДАННЫЕ:\n{context}\n\nВОПРОС:\n{question}"}],temperature=0.2)
        return (r.choices[0].message.content or "Не удалось сформировать ответ.").strip()

    @staticmethod
    def _extract(raw: str) -> dict[str,Any]:
        raw=raw.strip(); raw=re.sub(r"^```(?:json)?\s*|\s*```$","",raw,flags=re.I)
        try:
            d=json.loads(raw); return d if isinstance(d,dict) else {}
        except json.JSONDecodeError:
            m=re.search(r"\{.*\}",raw,re.S)
            if not m: raise ValueError("Нет JSON")
            d=json.loads(m.group()); return d if isinstance(d,dict) else {}

    def _validate(self,d:dict[str,Any],current_scope:str)->ParsedOperation:
        scope=str(d.get("finance_scope") or current_scope)
        if scope not in {"work","personal"}: scope=current_scope
        tx=str(d.get("tx_type") or "expense")
        if tx not in {"income","expense"}: tx="expense"
        amount=float(d.get("amount") or 0)
        if amount<=0: raise ValueError("Не определена сумма")
        allowed=CATEGORIES[scope][tx]
        raw=str(d.get("category") or "").casefold()
        category=next((c for c in allowed if c.casefold()==raw or c.casefold() in raw or raw in c.casefold()),allowed[-1])
        obj=None if scope=="personal" else self._clean(d.get("object_name"),120)
        return ParsedOperation(tx,amount,category,scope,obj,self._clean(d.get("comment"),250))

    @staticmethod
    def _clean(v:Any,n:int)->str|None:
        if v is None:return None
        s=str(v).strip();return s[:n] if s else None

    def _fallback(self,text:str,current_scope:str)->ParsedOperation:
        low=text.casefold(); scope="personal" if "личн" in low else ("work" if any(x in low for x in ("рабоч","бизнес","объект")) else current_scope)
        tx="income" if any(x in low for x in ("доход","получил","аванс","оплата","зарплата пришла","вернули")) else "expense"
        nums=re.findall(r"(?<!\d)(\d[\d\s]*(?:[.,]\d{1,2})?)(?!\d)",text)
        if not nums: raise ValueError("Не нашёл сумму")
        amount=float(nums[0].replace(" ","").replace(",","."))
        allowed=CATEGORIES[scope][tx];category=allowed[-1]
        maps={"металл":"Материалы","труб":"Материалы","бензин":"Транспорт" if scope=="work" else "Автомобиль","продукт":"Продукты","аренд":"Аренда","аванс":"Аванс"}
        for k,v in maps.items():
            if k in low and v in allowed: category=v;break
        obj=None
        if scope=="work":
            m=re.search(r"(?:для|объект(?:а|у)?|на объект)\s+([^,.]+)",text,re.I); obj=m.group(1).strip()[:120] if m else None
        return ParsedOperation(tx,amount,category,scope,obj,text.strip()[:250])
