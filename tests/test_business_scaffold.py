from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.router import business_router
from app.api.security import trusted_business_context
from app.application.context import ApplicationServiceContext
from app.domain.base import BusinessEntity
from app.infrastructure.database import BusinessDatabase
from app.infrastructure.migrations.runner import apply_business_migrations
from app.server.app import create_app
from app.settings import load_settings


def test_business_router_is_mounted_with_trusted_principal(tmp_path) -> None:
    """验证业务路由复用公开身份，并为匿名用户签发稳定会话。"""

    settings = replace(
        load_settings(),
        runtime_root=tmp_path,
        working_dir=tmp_path / ".agent-runtime",
        auth_enabled=False,
        anonymous_session_secret="test-session-secret-that-is-long-enough",
        cookie_secure=False,
        business_database_path=".business-data/business.sqlite",
    )
    original_route_count = len(business_router.routes)

    async def business_identity(
        context: ApplicationServiceContext = Depends(trusted_business_context),
    ) -> dict[str, str]:
        """通过业务服务上下文返回可信 Principal 和独立数据库路径。"""

        return {
            "userId": context.principal.user_id,
            "databasePath": str(context.database.path),
        }

    business_router.add_api_route(
        "/business/_contract/identity", business_identity, methods=["GET"]
    )
    try:
        with TestClient(create_app(settings)) as client:
            first = client.get("/business/_contract/identity")
            second = client.get("/business/_contract/identity")
        assert first.status_code == 200
        assert first.cookies.get(settings.anonymous_cookie_name)
        assert second.status_code == 200
        assert second.json()["userId"] == first.json()["userId"]
        assert first.json()["databasePath"] == str(
            settings.resolved_business_database_path
        )
    finally:
        del business_router.routes[original_route_count:]


def test_business_entity_rejects_undeclared_fields() -> None:
    """验证生成的领域实体默认拒绝契约外字段。"""

    class ExampleEntity(BusinessEntity):
        """提供严格字段校验的测试实体。"""

        name: str

    with pytest.raises(ValidationError):
        ExampleEntity(name="example", undeclared="value")


@pytest.mark.asyncio
async def test_business_database_is_separate_and_rolls_back(tmp_path) -> None:
    """验证业务数据与 Agent checkpoint 分离，异常时不会提交写入。"""

    settings = replace(
        load_settings(),
        runtime_root=tmp_path,
        working_dir=tmp_path / ".agent-runtime",
        business_database_path=".business-data/business.sqlite",
    )
    assert settings.resolved_business_database_path != settings.checkpoint_db_path
    database = BusinessDatabase(settings.resolved_business_database_path)
    async with database.transaction() as connection:
        await connection.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
        await connection.execute("INSERT INTO example (id) VALUES (1)")

    with pytest.raises(ValueError, match="rollback"):
        async with database.transaction() as connection:
            await connection.execute("INSERT INTO example (id) VALUES (2)")
            raise ValueError("rollback")

    async with database.transaction() as connection:
        cursor = await connection.execute("SELECT COUNT(*) FROM example")
        row = await cursor.fetchone()
    assert row == (1,)


def test_business_migrations_are_versioned_and_atomic(tmp_path) -> None:
    """验证业务迁移有序、重复执行幂等，且失败不会留下半成品表。"""

    import sqlite3

    sql_root = tmp_path / "sql"
    sql_root.mkdir()
    (sql_root / "001_create_orders.sql").write_text(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    database_path = tmp_path / "business.sqlite"
    assert apply_business_migrations(database_path, sql_root) == ["001_create_orders.sql"]
    assert apply_business_migrations(database_path, sql_root) == []
    (sql_root / "002_invalid.sql").write_text(
        "CREATE TABLE temporary_table (id INTEGER);\n"
        "INSERT INTO absent_table (id) VALUES (1);\n",
        encoding="utf-8",
    )
    with pytest.raises(sqlite3.OperationalError):
        apply_business_migrations(database_path, sql_root)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'temporary_table'"
        ).fetchone() is None
