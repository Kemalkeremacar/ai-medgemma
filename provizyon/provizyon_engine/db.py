"""MSSQL veritabanı bağlantı yardımcısı."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import pyodbc

from . import settings


@contextmanager
def get_connection(database: str | None = None) -> Generator[pyodbc.Connection, None, None]:
    conn = pyodbc.connect(settings.get_mssql_conn_str(database))
    try:
        yield conn
    finally:
        conn.close()
