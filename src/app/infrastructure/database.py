"""与 Agent checkpoint 完全分离的本地业务数据库事务入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


class BusinessDatabase:
    """为生成的 Repository 提供独立 SQLite 连接与提交/回滚边界。"""

    def __init__(self, path: str | Path) -> None:
        """记录业务数据库路径，不在模板导入时创建文件。"""

        self.path = Path(path).expanduser().resolve()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """每次业务操作使用独立事务，异常时回滚全部写入。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN")
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()
