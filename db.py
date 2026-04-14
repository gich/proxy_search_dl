"""Доступ к БД SQL Anywhere через pyodbc.

Пока CONNECTION_STRING и QUERY — заглушки. Заполнить перед деплоем.
Если USE_MOCK=True (по умолчанию), возвращаются моковые строки — удобно
для локальной разработки UI без реальной БД.
"""
from __future__ import annotations

import json
from datetime import datetime

USE_MOCK = True

# TODO: заполнить реальной строкой подключения.
# Пример: "DRIVER={SQL Anywhere 17};UID=...;PWD=...;ServerName=...;DBN=..."
CONNECTION_STRING = ""

# TODO: заполнить реальным SQL. Обязательно с параметром "?".
# Должен вернуть колонки в порядке: date, type, body_json
QUERY = "SELECT created_at, type, body_json FROM ??? WHERE ??? = ?"


def _mock_rows(param: str) -> list[dict]:
    samples = [
        {"orderId": param, "status": "new", "items": [{"sku": "A1", "qty": 2}]},
        {"orderId": param, "status": "paid", "payment": {"method": "card", "amount": 1234.56}},
        {"orderId": param, "note": "сломанный джсон ниже"},
    ]
    rows = [
        {"date": datetime(2026, 4, 14, 10, 15, 0), "type": "order.created",
         "body": json.dumps(samples[0], ensure_ascii=False)},
        {"date": datetime(2026, 4, 14, 10, 16, 30), "type": "order.paid",
         "body": json.dumps(samples[1], ensure_ascii=False)},
        {"date": datetime(2026, 4, 14, 10, 17, 5), "type": "order.error",
         "body": '{"broken": true, '},  # намеренно битый
    ]
    return rows


def fetch_rows(param: str) -> list[dict]:
    """Вернуть список словарей {date, type, body}. body — сырая строка JSON."""
    if USE_MOCK:
        return _mock_rows(param)

    import pyodbc

    with pyodbc.connect(CONNECTION_STRING) as cn:
        cur = cn.cursor()
        cur.execute(QUERY, param)
        return [
            {"date": r[0], "type": r[1], "body": r[2]}
            for r in cur.fetchall()
        ]
