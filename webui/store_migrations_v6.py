"""Schema migration 035/036（未收尾流程的一次性提醒记号与持久水位，Spec 043）与 037（通用搜索配置包，Spec 044）。"""

from __future__ import annotations

from webui.store_helpers import _now


class StoreMigrationsV6Mixin:
    """043：screening_runs 增加"提醒已发出"标记列；新增持久提醒水位表。
    044：新增平台无关的通用搜索配置包表。"""

    def _migration_035(self):
        """流程行增加一次性提醒记号列。

        043 未收尾流程提醒：行级记号挂在流程行上（前端闸门读取）；
        持久水位见 036（删除已提醒行后更旧者仍沉默）。老库补列；
        表不存在（冻结测试库）时跳过，读取方按缺列容错。
        """
        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(screening_runs)")}
            if columns and "notice_sent_at" not in columns:
                conn.execute(
                    "ALTER TABLE screening_runs ADD COLUMN notice_sent_at TEXT"
                )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (35, ?, 'screening runs notice sent marker')",
                (_now(),),
            )

    def _migration_036(self):
        """持久提醒水位表：水位不随流程行删除而消失（FR-004 不接力）。

        按画像分行（无归属记 __global__），读取时与全局行取最大；
        删除已提醒的流程行后，更旧的未收尾依旧沉默。
        """
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_notice_state ("
                " profile_key TEXT PRIMARY KEY,"
                " watermark TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (36, ?, 'run notice watermark state')",
                (_now(),),
            )

    def _migration_037(self):
        """通用搜索配置包表（Spec 044 B100）。

        一行 = 一套平台无关的搜索身份完整快照：名称、版本号、关键词、城市、
        画像文本与画像事实。表中没有平台标识、画像外键或第三页筛选字段；
        列表默认按最近更新在前。
        """
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search_packages ("
                " id TEXT PRIMARY KEY,"
                " name TEXT NOT NULL,"
                " payload_version INTEGER NOT NULL,"
                " keywords_json TEXT NOT NULL,"
                " city_json TEXT NOT NULL,"
                " profile_summary TEXT NOT NULL,"
                " profile_facts_json TEXT NOT NULL,"
                " created_at TEXT NOT NULL,"
                " updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_search_packages_updated "
                "ON search_packages(updated_at DESC, created_at DESC)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (37, ?, 'search packages')",
                (_now(),),
            )
