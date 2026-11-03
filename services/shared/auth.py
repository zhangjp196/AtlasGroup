"""本地单用户身份（独立部署：无登录 / 无权限）。

平台 JWT 鉴权已移除。本服务为单租户独立应用，所有请求固定归属本地匿名用户，
接口不再要求登录态。

若确需多用户鉴权，可在本模块替换为真实实现（解析 Authorization），并在路由上
用 FastAPI 依赖挂载。
"""
from __future__ import annotations

# 本地匿名用户 ID（数据统一归属该用户）
LOCAL_USER_ID = "local"
