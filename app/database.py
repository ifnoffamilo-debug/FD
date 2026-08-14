from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class Summary:
    income: float
    expense: float
    count: int

    @property
    def profit(self) -> float:
        return self.income - self.expense


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    async def _run(self, fn: Callable[[], T]) -> T:
        return await asyncio.to_thread(fn)

    @staticmethod
    def _ts(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    async def initialize(self) -> None:
        def op() -> None:
            with self._connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tx_type TEXT NOT NULL CHECK(tx_type IN ('income','expense')),
                        amount REAL NOT NULL CHECK(amount > 0),
                        category TEXT NOT NULL,
                        object_name TEXT,
                        comment TEXT,
                        created_at TEXT NOT NULL,
                        created_by INTEGER NOT NULL,
                        source TEXT NOT NULL DEFAULT 'manual',
                        updated_at TEXT,
                        updated_by INTEGER,
                        deleted_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        entity_id INTEGER,
                        details TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS user_settings (
                        user_id INTEGER PRIMARY KEY,
                        active_scope TEXT NOT NULL DEFAULT 'work' CHECK(active_scope IN ('work','personal'))
                    );
                    CREATE TABLE IF NOT EXISTS objects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        contract_amount REAL NOT NULL DEFAULT 0,
                        budget_amount REAL NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS vehicles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        current_mileage REAL NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS vehicle_mileage (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                        mileage REAL NOT NULL CHECK(mileage >= 0),
                        recorded_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS vehicle_service (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        last_mileage REAL NOT NULL,
                        interval_km REAL NOT NULL,
                        next_mileage REAL NOT NULL,
                        note TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS debts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        direction TEXT NOT NULL CHECK(direction IN ('to_me','i_owe')),
                        person TEXT NOT NULL,
                        original_amount REAL NOT NULL CHECK(original_amount > 0),
                        due_date TEXT,
                        comment TEXT,
                        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','closed')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT,
                        closed_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS debt_payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        debt_id INTEGER NOT NULL REFERENCES debts(id) ON DELETE CASCADE,
                        amount REAL NOT NULL CHECK(amount > 0),
                        paid_at TEXT NOT NULL,
                        note TEXT,
                        transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL
                    );
                    """
                )
                cols = {r["name"] for r in db.execute("PRAGMA table_info(transactions)")}
                additions = {
                    "finance_scope": "TEXT NOT NULL DEFAULT 'work'",
                    "object_id": "INTEGER",
                    "vehicle_id": "INTEGER",
                    "vehicle_expense_type": "TEXT",
                    "fuel_type": "TEXT",
                    "fuel_liters": "REAL",
                    "odometer": "REAL",
                    "receipt_file_id": "TEXT",
                    "receipt_path": "TEXT",
                    "updated_at": "TEXT",
                    "updated_by": "INTEGER",
                    "deleted_at": "TEXT",
                }
                for name, ddl in additions.items():
                    if name not in cols:
                        db.execute(f"ALTER TABLE transactions ADD COLUMN {name} {ddl}")
                db.execute("UPDATE transactions SET finance_scope='work' WHERE finance_scope IS NULL OR finance_scope='' ")
                db.execute("CREATE INDEX IF NOT EXISTS idx_tx_scope_date ON transactions(finance_scope, created_at)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_tx_object_id ON transactions(object_id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_tx_vehicle_id ON transactions(vehicle_id)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_debts_status ON debts(status, direction)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_debt_payments_debt ON debt_payments(debt_id, paid_at)")

                # Старые текстовые объекты превращаем в справочник объектов без потери данных.
                names = db.execute(
                    "SELECT DISTINCT TRIM(object_name) AS n FROM transactions WHERE object_name IS NOT NULL AND TRIM(object_name)<>''"
                ).fetchall()
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for row in names:
                    db.execute("INSERT OR IGNORE INTO objects(name, created_at) VALUES (?,?)", (row["n"], now))
                db.execute(
                    """
                    UPDATE transactions
                    SET object_id=(SELECT id FROM objects WHERE objects.name=transactions.object_name COLLATE NOCASE)
                    WHERE object_id IS NULL AND object_name IS NOT NULL AND TRIM(object_name)<>''
                    """
                )

        await self._run(op)

    async def get_scope(self, user_id: int) -> str:
        def op() -> str:
            with self._connect() as db:
                row = db.execute("SELECT active_scope FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
                return str(row["active_scope"]) if row else "work"
        return await self._run(op)

    async def set_scope(self, user_id: int, scope: str) -> None:
        def op() -> None:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO user_settings(user_id, active_scope) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET active_scope=excluded.active_scope",
                    (user_id, scope),
                )
        await self._run(op)

    async def add_transaction(self, *, tx_type: str, amount: float, category: str,
                              finance_scope: str, object_id: int | None, object_name: str | None,
                              comment: str | None, created_at: datetime, created_by: int,
                              source: str, vehicle_id: int | None = None,
                              vehicle_expense_type: str | None = None, fuel_type: str | None = None,
                              fuel_liters: float | None = None, odometer: float | None = None) -> int:
        ts = self._ts(created_at)
        def op() -> int:
            with self._connect() as db:
                cur = db.execute(
                    """
                    INSERT INTO transactions(tx_type,amount,category,finance_scope,object_id,object_name,comment,
                        created_at,created_by,source,vehicle_id,vehicle_expense_type,fuel_type,fuel_liters,odometer)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (tx_type, amount, category, finance_scope, object_id, object_name, comment,
                     ts, created_by, source, vehicle_id, vehicle_expense_type, fuel_type, fuel_liters, odometer),
                )
                tx_id = int(cur.lastrowid)
                db.execute("INSERT INTO audit_log(user_id,action,entity_id,details,created_at) VALUES(?,?,?,?,?)",
                           (created_by, "create_transaction", tx_id, f"{finance_scope}:{tx_type}:{amount}:{category}", ts))
                if vehicle_id and odometer is not None:
                    db.execute("INSERT INTO vehicle_mileage(vehicle_id,mileage,recorded_at) VALUES(?,?,?)", (vehicle_id, odometer, ts))
                    db.execute("UPDATE vehicles SET current_mileage=MAX(current_mileage,?), updated_at=? WHERE id=?", (odometer, ts, vehicle_id))
                return tx_id
        return await self._run(op)

    async def attach_receipt(self, tx_id: int, file_id: str, path: str) -> None:
        def op() -> None:
            with self._connect() as db:
                db.execute("UPDATE transactions SET receipt_file_id=?, receipt_path=? WHERE id=? AND deleted_at IS NULL", (file_id, path, tx_id))
        await self._run(op)

    async def get_transaction(self, tx_id: int) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            with self._connect() as db:
                row = db.execute(
                    """SELECT t.*, o.name AS resolved_object_name, v.name AS vehicle_name
                       FROM transactions t LEFT JOIN objects o ON o.id=t.object_id LEFT JOIN vehicles v ON v.id=t.vehicle_id
                       WHERE t.id=? AND t.deleted_at IS NULL""", (tx_id,)
                ).fetchone()
                if not row: return None
                d = dict(row); d["object_name"] = d.get("resolved_object_name") or d.get("object_name")
                return d
        return await self._run(op)

    async def recent(self, scope: str, limit: int = 15) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            with self._connect() as db:
                rows = db.execute(
                    """SELECT t.*, COALESCE(o.name,t.object_name) AS resolved_object_name, v.name AS vehicle_name
                       FROM transactions t LEFT JOIN objects o ON o.id=t.object_id LEFT JOIN vehicles v ON v.id=t.vehicle_id
                       WHERE t.deleted_at IS NULL AND t.finance_scope=? ORDER BY t.created_at DESC,t.id DESC LIMIT ?""",
                    (scope, limit),
                ).fetchall()
                out=[]
                for r in rows:
                    d=dict(r); d["object_name"]=d.get("resolved_object_name"); out.append(d)
                return out
        return await self._run(op)

    async def recent_expenses(self, scope: str, limit: int = 12) -> list[dict[str, Any]]:
        rows = await self.recent(scope, limit=100)
        return [r for r in rows if r["tx_type"] == "expense"][:limit]

    async def update_expense(self, *, tx_id: int, amount: float, category: str,
                             object_id: int | None, object_name: str | None, comment: str | None,
                             updated_at: datetime, updated_by: int) -> bool:
        ts = self._ts(updated_at)
        def op() -> bool:
            with self._connect() as db:
                old = db.execute("SELECT * FROM transactions WHERE id=? AND deleted_at IS NULL AND tx_type='expense'", (tx_id,)).fetchone()
                if not old: return False
                cur = db.execute(
                    """UPDATE transactions SET amount=?,category=?,object_id=?,object_name=?,comment=?,updated_at=?,updated_by=?
                       WHERE id=? AND deleted_at IS NULL AND tx_type='expense'""",
                    (amount,category,object_id,object_name,comment,ts,updated_by,tx_id),
                )
                details=json.dumps({"before":dict(old),"after":{"amount":amount,"category":category,"object_id":object_id,"object_name":object_name,"comment":comment}},ensure_ascii=False,default=str)
                db.execute("INSERT INTO audit_log(user_id,action,entity_id,details,created_at) VALUES(?,?,?,?,?)", (updated_by,"update_expense",tx_id,details,ts))
                return cur.rowcount == 1
        return await self._run(op)

    def _scope_where(self, scope: str, start: datetime | None, end: datetime | None) -> tuple[str,list[Any]]:
        clauses=["deleted_at IS NULL","finance_scope=?"]; params: list[Any] = [scope]
        if start: clauses.append("created_at>=?"); params.append(self._ts(start))
        if end: clauses.append("created_at<?"); params.append(self._ts(end))
        return " AND ".join(clauses), params

    async def summary(self, scope: str, start: datetime | None=None, end: datetime | None=None,
                      object_id: int | None=None, vehicle_id: int | None=None) -> Summary:
        where, params = self._scope_where(scope,start,end)
        if object_id is not None: where += " AND object_id=?"; params.append(object_id)
        if vehicle_id is not None: where += " AND vehicle_id=?"; params.append(vehicle_id)
        def op() -> Summary:
            with self._connect() as db:
                r=db.execute(f"""SELECT COALESCE(SUM(CASE WHEN tx_type='income' THEN amount END),0) income,
                    COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount END),0) expense, COUNT(*) count
                    FROM transactions WHERE {where}""",params).fetchone()
                return Summary(float(r["income"]),float(r["expense"]),int(r["count"]))
        return await self._run(op)

    async def category_totals(self, scope: str, start: datetime | None=None, end: datetime | None=None,
                              vehicle_id: int | None=None) -> list[dict[str,Any]]:
        where, params=self._scope_where(scope,start,end)
        if vehicle_id is not None: where += " AND vehicle_id=?"; params.append(vehicle_id)
        def op() -> list[dict[str,Any]]:
            with self._connect() as db:
                return [dict(r) for r in db.execute(f"""SELECT tx_type,category,COALESCE(vehicle_expense_type,'') vehicle_expense_type,
                    SUM(amount) total,COUNT(*) count FROM transactions WHERE {where}
                    GROUP BY tx_type,category,vehicle_expense_type ORDER BY tx_type,total DESC""",params).fetchall()]
        return await self._run(op)

    async def all_transactions(self, scope: str | None=None) -> list[dict[str,Any]]:
        def op() -> list[dict[str,Any]]:
            with self._connect() as db:
                q="""SELECT t.*,COALESCE(o.name,t.object_name) resolved_object_name,v.name vehicle_name FROM transactions t
                    LEFT JOIN objects o ON o.id=t.object_id LEFT JOIN vehicles v ON v.id=t.vehicle_id WHERE t.deleted_at IS NULL"""
                params=[]
                if scope: q += " AND t.finance_scope=?"; params.append(scope)
                q += " ORDER BY t.created_at DESC,t.id DESC"
                out=[]
                for r in db.execute(q,params).fetchall():
                    d=dict(r); d["object_name"]=d.get("resolved_object_name"); out.append(d)
                return out
        return await self._run(op)

    # ---------------- Objects ----------------
    async def list_objects(self, include_archived: bool=False) -> list[dict[str,Any]]:
        def op() -> list[dict[str,Any]]:
            with self._connect() as db:
                where="" if include_archived else "WHERE status='active'"
                return [dict(r) for r in db.execute(f"SELECT * FROM objects {where} ORDER BY status,name COLLATE NOCASE").fetchall()]
        return await self._run(op)

    async def add_object(self, name: str, contract: float=0, budget: float=0) -> int:
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        def op() -> int:
            with self._connect() as db:
                cur=db.execute("INSERT INTO objects(name,contract_amount,budget_amount,created_at) VALUES(?,?,?,?)",(name,contract,budget,now))
                return int(cur.lastrowid)
        return await self._run(op)

    async def get_object(self, object_id: int) -> dict[str,Any] | None:
        def op():
            with self._connect() as db:
                r=db.execute("SELECT * FROM objects WHERE id=?",(object_id,)).fetchone(); return dict(r) if r else None
        return await self._run(op)

    async def update_object(self, object_id: int, *, name: str | None=None, contract: float | None=None,
                            budget: float | None=None, archived: bool | None=None) -> None:
        fields=[]; params=[]
        if name is not None: fields.append("name=?"); params.append(name)
        if contract is not None: fields.append("contract_amount=?"); params.append(contract)
        if budget is not None: fields.append("budget_amount=?"); params.append(budget)
        if archived is not None: fields.append("status=?"); params.append("archived" if archived else "active")
        if not fields: return
        fields.append("updated_at=?"); params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S")); params.append(object_id)
        def op():
            with self._connect() as db: db.execute(f"UPDATE objects SET {','.join(fields)} WHERE id=?",params)
        await self._run(op)

    async def object_stats(self, object_id: int) -> tuple[dict[str,Any] | None, Summary]:
        obj=await self.get_object(object_id)
        return obj, await self.summary("work", object_id=object_id)

    async def object_transactions(self, object_id: int, limit: int=15) -> list[dict[str,Any]]:
        def op():
            with self._connect() as db:
                return [dict(r) for r in db.execute("SELECT * FROM transactions WHERE deleted_at IS NULL AND object_id=? ORDER BY created_at DESC,id DESC LIMIT ?",(object_id,limit)).fetchall()]
        return await self._run(op)

    # ---------------- Vehicles ----------------
    async def list_vehicles(self, include_archived: bool=False) -> list[dict[str,Any]]:
        def op():
            with self._connect() as db:
                where="" if include_archived else "WHERE status='active'"
                return [dict(r) for r in db.execute(f"SELECT * FROM vehicles {where} ORDER BY status,id").fetchall()]
        return await self._run(op)

    async def add_vehicle(self, name: str, mileage: float, when: datetime) -> int:
        ts=self._ts(when)
        def op():
            with self._connect() as db:
                cur=db.execute("INSERT INTO vehicles(name,current_mileage,created_at) VALUES(?,?,?)",(name,mileage,ts)); vid=int(cur.lastrowid)
                db.execute("INSERT INTO vehicle_mileage(vehicle_id,mileage,recorded_at) VALUES(?,?,?)",(vid,mileage,ts)); return vid
        return await self._run(op)

    async def get_vehicle(self, vehicle_id: int) -> dict[str,Any] | None:
        def op():
            with self._connect() as db:
                r=db.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone(); return dict(r) if r else None
        return await self._run(op)

    async def update_vehicle_mileage(self, vehicle_id: int, mileage: float, when: datetime) -> None:
        ts=self._ts(when)
        def op():
            with self._connect() as db:
                db.execute("INSERT INTO vehicle_mileage(vehicle_id,mileage,recorded_at) VALUES(?,?,?)",(vehicle_id,mileage,ts))
                db.execute("UPDATE vehicles SET current_mileage=?,updated_at=? WHERE id=?",(mileage,ts,vehicle_id))
        await self._run(op)

    async def update_vehicle(self, vehicle_id: int, *, name: str | None=None, archived: bool | None=None) -> None:
        fields=[];params=[]
        if name is not None: fields.append("name=?");params.append(name)
        if archived is not None: fields.append("status=?");params.append("archived" if archived else "active")
        if not fields:return
        fields.append("updated_at=?");params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"));params.append(vehicle_id)
        def op():
            with self._connect() as db: db.execute(f"UPDATE vehicles SET {','.join(fields)} WHERE id=?",params)
        await self._run(op)

    async def vehicle_transactions(self, vehicle_id: int, limit: int=20) -> list[dict[str,Any]]:
        def op():
            with self._connect() as db:
                return [dict(r) for r in db.execute("SELECT * FROM transactions WHERE deleted_at IS NULL AND vehicle_id=? ORDER BY created_at DESC,id DESC LIMIT ?",(vehicle_id,limit)).fetchall()]
        return await self._run(op)

    async def vehicle_distance(self, vehicle_id: int, start: datetime | None=None, end: datetime | None=None) -> float:
        clauses=["vehicle_id=?"];params: list[Any] = [vehicle_id]
        if start: clauses.append("recorded_at>=?");params.append(self._ts(start))
        if end: clauses.append("recorded_at<?");params.append(self._ts(end))
        def op():
            with self._connect() as db:
                r=db.execute(f"SELECT MIN(mileage) mn,MAX(mileage) mx FROM vehicle_mileage WHERE {' AND '.join(clauses)}",params).fetchone()
                if r["mn"] is None or r["mx"] is None:return 0.0
                return max(0.0,float(r["mx"])-float(r["mn"]))
        return await self._run(op)

    async def vehicle_fuel_stats(self, vehicle_id: int, start: datetime | None=None, end: datetime | None=None) -> dict[str,Any]:
        where,params=self._scope_where("personal",start,end); where += " AND vehicle_id=? AND vehicle_expense_type='Топливо'";params.append(vehicle_id)
        def op():
            with self._connect() as db:
                rows=db.execute(f"""SELECT COALESCE(NULLIF(fuel_type,''),'Не указано') fuel_type,
                    COALESCE(SUM(amount),0) amount,COALESCE(SUM(fuel_liters),0) liters,COUNT(*) count
                    FROM transactions WHERE {where} GROUP BY COALESCE(NULLIF(fuel_type,''),'Не указано')""",params).fetchall()
                result={"amount":0.0,"liters":0.0,"avg_price":0.0,"by_type":{}}
                for r in rows:
                    amount=float(r["amount"]);liters=float(r["liters"]);name=str(r["fuel_type"])
                    result["by_type"][name]={"amount":amount,"liters":liters,"avg_price":amount/liters if liters else 0.0,"count":int(r["count"])}
                    result["amount"]+=amount;result["liters"]+=liters
                result["avg_price"]=result["amount"]/result["liters"] if result["liters"] else 0.0
                return result
        return await self._run(op)

    # ---------------- Debts ----------------
    async def add_debt(self, direction: str, person: str, amount: float, due_date: str | None,
                       comment: str | None, when: datetime) -> int:
        ts=self._ts(when)
        def op() -> int:
            with self._connect() as db:
                cur=db.execute("""INSERT INTO debts(direction,person,original_amount,due_date,comment,created_at)
                    VALUES(?,?,?,?,?,?)""",(direction,person,amount,due_date,comment,ts))
                return int(cur.lastrowid)
        return await self._run(op)

    async def get_debt(self, debt_id: int) -> dict[str,Any] | None:
        def op():
            with self._connect() as db:
                r=db.execute("""SELECT d.*,COALESCE(SUM(p.amount),0) paid_amount
                    FROM debts d LEFT JOIN debt_payments p ON p.debt_id=d.id
                    WHERE d.id=? GROUP BY d.id""",(debt_id,)).fetchone()
                if not r:return None
                d=dict(r);d["remaining"]=max(0.0,float(d["original_amount"])-float(d["paid_amount"]));return d
        return await self._run(op)

    async def list_debts(self, status: str | None="active", direction: str | None=None) -> list[dict[str,Any]]:
        def op():
            with self._connect() as db:
                clauses=[];params=[]
                if status:clauses.append("d.status=?");params.append(status)
                if direction:clauses.append("d.direction=?");params.append(direction)
                where=("WHERE "+" AND ".join(clauses)) if clauses else ""
                rows=db.execute(f"""SELECT d.*,COALESCE(SUM(p.amount),0) paid_amount
                    FROM debts d LEFT JOIN debt_payments p ON p.debt_id=d.id {where}
                    GROUP BY d.id ORDER BY CASE WHEN d.due_date IS NULL THEN 1 ELSE 0 END,d.due_date,d.created_at DESC""",params).fetchall()
                out=[]
                for r in rows:
                    d=dict(r);d["remaining"]=max(0.0,float(d["original_amount"])-float(d["paid_amount"]));out.append(d)
                return out
        return await self._run(op)

    async def debt_totals(self) -> dict[str,float]:
        rows=await self.list_debts(status="active")
        to_me=sum(float(r["remaining"]) for r in rows if r["direction"]=="to_me")
        i_owe=sum(float(r["remaining"]) for r in rows if r["direction"]=="i_owe")
        return {"to_me":to_me,"i_owe":i_owe,"net":to_me-i_owe,"count":float(len(rows))}

    async def add_debt_payment(self, debt_id: int, amount: float, when: datetime, note: str | None=None) -> int:
        ts=self._ts(when)
        def op() -> int:
            with self._connect() as db:
                debt=db.execute("SELECT * FROM debts WHERE id=?",(debt_id,)).fetchone()
                if not debt:raise ValueError("Долг не найден")
                paid=float(db.execute("SELECT COALESCE(SUM(amount),0) s FROM debt_payments WHERE debt_id=?",(debt_id,)).fetchone()["s"])
                remaining=max(0.0,float(debt["original_amount"])-paid)
                if amount<=0:raise ValueError("Сумма должна быть больше нуля")
                if amount>remaining+0.005:raise ValueError("Сумма погашения больше остатка долга")
                cur=db.execute("INSERT INTO debt_payments(debt_id,amount,paid_at,note) VALUES(?,?,?,?)",(debt_id,amount,ts,note))
                if remaining-amount<=0.005:
                    db.execute("UPDATE debts SET status='closed',closed_at=?,updated_at=? WHERE id=?",(ts,ts,debt_id))
                else:
                    db.execute("UPDATE debts SET updated_at=? WHERE id=?",(ts,debt_id))
                return int(cur.lastrowid)
        return await self._run(op)

    async def get_debt_payment(self, payment_id: int) -> dict[str,Any] | None:
        def op():
            with self._connect() as db:
                r=db.execute("""SELECT p.*,d.direction,d.person,d.original_amount,d.status
                    FROM debt_payments p JOIN debts d ON d.id=p.debt_id WHERE p.id=?""",(payment_id,)).fetchone()
                return dict(r) if r else None
        return await self._run(op)

    async def set_debt_payment_transaction(self, payment_id: int, transaction_id: int) -> None:
        def op():
            with self._connect() as db:db.execute("UPDATE debt_payments SET transaction_id=? WHERE id=?",(transaction_id,payment_id))
        await self._run(op)

    async def debt_payments(self, debt_id: int) -> list[dict[str,Any]]:
        def op():
            with self._connect() as db:
                return [dict(r) for r in db.execute("SELECT * FROM debt_payments WHERE debt_id=? ORDER BY paid_at DESC,id DESC",(debt_id,)).fetchall()]
        return await self._run(op)

    async def update_debt(self, debt_id: int, *, person: str | None=None, original_amount: float | None=None,
                          due_date: str | None | object=..., comment: str | None | object=...) -> None:
        fields=[];params=[]
        if person is not None:fields.append("person=?");params.append(person)
        if original_amount is not None:
            payments=await self.debt_payments(debt_id);paid=sum(float(x["amount"]) for x in payments)
            if original_amount+0.005<paid:raise ValueError("Новая сумма меньше уже погашенной")
            fields.append("original_amount=?");params.append(original_amount)
        if due_date is not ...:fields.append("due_date=?");params.append(due_date)
        if comment is not ...:fields.append("comment=?");params.append(comment)
        if not fields:return
        fields.append("updated_at=?");params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"));params.append(debt_id)
        def op():
            with self._connect() as db:db.execute(f"UPDATE debts SET {','.join(fields)} WHERE id=?",params)
        await self._run(op)

    async def all_debts(self) -> list[dict[str,Any]]:
        return await self.list_debts(status=None)

    async def all_debt_payments(self) -> list[dict[str,Any]]:
        def op():
            with self._connect() as db:
                return [dict(r) for r in db.execute("""SELECT p.*,d.person,d.direction FROM debt_payments p
                    JOIN debts d ON d.id=p.debt_id ORDER BY p.paid_at DESC,p.id DESC""").fetchall()]
        return await self._run(op)

    async def add_service(self, vehicle_id: int, title: str, last: float, interval: float, note: str | None, when: datetime) -> int:
        ts=self._ts(when)
        def op():
            with self._connect() as db:
                cur=db.execute("INSERT INTO vehicle_service(vehicle_id,title,last_mileage,interval_km,next_mileage,note,created_at) VALUES(?,?,?,?,?,?,?)",
                    (vehicle_id,title,last,interval,last+interval,note,ts));return int(cur.lastrowid)
        return await self._run(op)

    async def list_service(self, vehicle_id: int) -> list[dict[str,Any]]:
        def op():
            with self._connect() as db:return [dict(r) for r in db.execute("SELECT * FROM vehicle_service WHERE vehicle_id=? ORDER BY next_mileage",(vehicle_id,)).fetchall()]
        return await self._run(op)
