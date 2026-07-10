from __future__ import annotations

from threading import Lock, RLock

from sqlalchemy import text


_REGISTRY_GUARD = Lock()
_SQLITE_LOCKS: dict[tuple[int, int], RLock] = {}


class AccountOperationLock:
    def __init__(self, engine):
        self._engine = engine

    def acquire(self, account_id: int):
        dialect = self._engine.dialect.name
        if dialect == "postgresql":
            return _PostgresqlAccountLock(self._engine, account_id)
        if dialect == "sqlite":
            return _SqliteAccountLock(self._engine, account_id)
        raise RuntimeError(f"不支持账号操作锁的数据库: {dialect}")


class _PostgresqlAccountLock:
    def __init__(self, engine, account_id: int):
        self._engine = engine
        self._account_id = account_id
        self._connection = None

    def __enter__(self):
        self._connection = self._engine.connect()
        try:
            self._connection.execute(
                text("SELECT pg_advisory_lock(:account_id)"),
                {"account_id": self._account_id},
            )
        except BaseException:
            self._connection.close()
            self._connection = None
            raise
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        try:
            self._connection.execute(
                text("SELECT pg_advisory_unlock(:account_id)"),
                {"account_id": self._account_id},
            )
        finally:
            self._connection.close()
            self._connection = None
        return False


class _SqliteAccountLock:
    def __init__(self, engine, account_id: int):
        key = (id(engine), account_id)
        with _REGISTRY_GUARD:
            self._lock = _SQLITE_LOCKS.setdefault(key, RLock())

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self._lock.release()
        return False
