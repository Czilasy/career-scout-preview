"""通用搜索配置包的数据访问域（Spec 044 B100）。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。

职责边界：本域只做参数化 SQL 与 JSON 编解码。快照的结构、版本和完整性
校验属于 webui/search_packages.py 服务域；读取时 JSON 无法解析一律抛
SearchPackageCorruptError，绝不静默补齐、返回半包或替用户改内容。
"""

from __future__ import annotations

import json

from webui.store_helpers import _now, _uuid


class SearchPackageCorruptError(RuntimeError):
    """配置包快照在数据库里已经损坏（JSON 无法解析），拒绝当作可用包返回。"""


class StoreSearchPackagesMixin:
    """search_packages 表的 CRUD；一行 = 一套平台无关的搜索身份完整快照。"""

    def list_search_packages(self) -> list:
        """全部配置包摘要，按最近更新在前；不解析快照内容。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at, updated_at FROM search_packages "
                "ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_search_package(self, package_id) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM search_packages WHERE id = ?", (str(package_id),)
            ).fetchone()
        if row is None:
            raise KeyError(package_id)
        return self._search_package_row(row)

    def create_search_package(
        self,
        *,
        name,
        payload_version,
        keywords,
        city,
        profile_summary,
        profile_facts,
    ) -> dict:
        package_id = _uuid()
        timestamp = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO search_packages ("
                " id, name, payload_version, keywords_json, city_json,"
                " profile_summary, profile_facts_json, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    package_id,
                    str(name),
                    int(payload_version),
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(city, ensure_ascii=False),
                    str(profile_summary),
                    json.dumps(profile_facts, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_search_package(package_id)

    def rename_search_package(self, package_id, name) -> dict:
        """只改名称与更新时间，其它内容字节语义不变。"""
        pid = str(package_id)
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE search_packages SET name = ?, updated_at = ? WHERE id = ?",
                (str(name), _now(), pid),
            )
            if cursor.rowcount == 0:
                raise KeyError(package_id)
        return self.get_search_package(pid)

    def delete_search_package(self, package_id) -> None:
        """只删除目标行；画像、任务与搜索结果不级联。"""
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM search_packages WHERE id = ?", (str(package_id),)
            )
            if cursor.rowcount == 0:
                raise KeyError(package_id)

    @staticmethod
    def _search_package_row(row) -> dict:
        try:
            keywords = json.loads(row["keywords_json"])
            city = json.loads(row["city_json"])
            profile_facts = json.loads(row["profile_facts_json"])
        except (TypeError, ValueError) as exc:
            raise SearchPackageCorruptError(
                "search package payload is not valid JSON"
            ) from exc
        return {
            "id": row["id"],
            "name": row["name"],
            "payload_version": row["payload_version"],
            "keywords": keywords,
            "city": city,
            "profile_summary": row["profile_summary"],
            "profile_facts": profile_facts,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
