"""顺序执行生成的业务 SQLite 迁移，拒绝已执行迁移被改写。"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from app.settings import load_settings


def apply_business_migrations(database_path: Path, sql_root: Path) -> list[str]:
    """在独立业务数据库中按文件名执行未应用的 SQL，并验证历史摘要。"""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(sql_root.glob("*.sql")) if sql_root.is_dir() else []
    applied: list[str] = []
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, sha256 TEXT NOT NULL)"
        )
        known = {
            row[0]: row[1]
            for row in connection.execute("SELECT name, sha256 FROM schema_migrations")
        }
        connection.execute("BEGIN")
        for path in files:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"业务迁移文件无效：{path.name}。")
            sql = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            previous = known.get(path.name)
            if previous is not None:
                if previous != digest:
                    raise ValueError(f"已应用业务迁移被修改：{path.name}。")
                continue
            if not sql.strip():
                raise ValueError(f"业务迁移文件为空：{path.name}。")
            for statement in _sqlite_statements(sql):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (name, sha256) VALUES (?, ?)",
                (path.name, digest),
            )
            applied.append(path.name)
    return applied


def _sqlite_statements(sql: str) -> list[str]:
    """按 SQLite 完整语句边界切分脚本，保留整个迁移事务的原子性。"""

    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer)
            buffer = ""
    if buffer.strip():
        raise ValueError("业务迁移 SQL 存在未闭合语句。")
    return statements


def main() -> None:
    """使用 Runtime 当前配置运行业务迁移并输出已应用的文件名。"""

    settings = load_settings()
    sql_root = Path(__file__).resolve().parent / "sql"
    for name in apply_business_migrations(settings.resolved_business_database_path, sql_root):
        print(name)


if __name__ == "__main__":
    main()
