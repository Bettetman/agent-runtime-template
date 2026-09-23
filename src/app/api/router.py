"""后续业务 Endpoint 生成器可修改的唯一公开路由组合点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.security import trusted_business_context


# 生成器在此 include_router；基础设施统一验证可信 Principal。
business_router = APIRouter(dependencies=[Depends(trusted_business_context)])
