"""Versioned schema migrations for webui.TaskStore.

021 B7 T024 后本文件为兼容门面：迁移按版本段归组至
store_migrations_v1..v4，re-export 全部既有符号，旧 import 面不变（宪法 VI）。
"""

from __future__ import annotations

from webui.store_migrations_v1 import (
    _DDL_INTEGER_DEFAULT_0,
    _DDL_TEXT_DEFAULT_BOSS,
    _DDL_TEXT_DEFAULT_EMPTY,
    _DDL_TEXT_DEFAULT_OBJECT,
    _DDL_TEXT_NOT_NULL,
    _SQL_SCREENING_RUN_COLUMNS,
)
from webui.store_migrations_v4 import StoreMigrationsV4Mixin
from webui.store_migrations_v3 import StoreMigrationsV3Mixin
from webui.store_migrations_v2 import StoreMigrationsV2Mixin
from webui.store_migrations_v1 import StoreMigrationsV1Mixin


class MigrationBackupError(RuntimeError):
    """Raised when pre-migration bootstrap backup or verification fails.

    TaskStore construction must abort when this is raised; the source database
    must not receive any v27 partial writes.
    """


class StoreMigrationsMixin(
    StoreMigrationsV4Mixin,
    StoreMigrationsV3Mixin,
    StoreMigrationsV2Mixin,
    StoreMigrationsV1Mixin,
):
    """迁移 mixin 组装（021 B7 T024）：调度在 v1，版本段 001-032 物理归组。"""
