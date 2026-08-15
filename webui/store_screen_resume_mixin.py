"""Screening resume/continue store access.

分层：app.py → screen_flow.py → 本 mixin → store.py。
本模块只做数据访问，不含路由/编排逻辑。
"""

from __future__ import annotations


class StoreScreenResumeMixin:
    """AI 筛选续跑相关的 screening_runs 数据访问，挂载到 TaskStore。"""

    def load_screening_jd_map(self, run_id):
        """从 screening_results 读取非 dropped 行的 JD 内容。

        键优先取 ``platform_job_id``，缺失时回退 ``job_id``；供 JD 断点
        文件缺失时从结果表回退，避免续跑重复抓取 JD。
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT platform_job_id, job_id, jd FROM screening_results "
                "WHERE run_id = ? AND is_dropped = 0 "
                "AND jd IS NOT NULL AND TRIM(jd) != ''",
                (str(run_id),),
            ).fetchall()
        out = {}
        for row in rows:
            jd = str(row["jd"] or "").strip()
            if not jd:
                continue
            key = str(row["platform_job_id"] or "")
            if not key:
                key = str(row["job_id"] or "")
            if key:
                out[key] = jd
        return out

    def latest_screen_runs_for_source(self, source_task_id, statuses):
        """按 ``statuses`` 顺序返回每个状态各自最新的筛选 run。

        每个状态独立按 ``updated_at DESC`` 取最近一行，返回顺序即调用方
        传入的状态顺序，供续跑候选优先级使用。
        """
        source_task_id = str(source_task_id or "")
        if not source_task_id:
            return []
        rows = []
        with self._connection() as conn:
            for status in statuses or ():
                row = conn.execute(
                    "SELECT * FROM screening_runs "
                    "WHERE json_extract(execution_params_json, '$.scrape_task_id') = ? "
                    "AND record_kind != 'result_snapshot' "
                    "AND status = ? "
                    "ORDER BY updated_at DESC, rowid DESC LIMIT 1",
                    (source_task_id, str(status)),
                ).fetchone()
                if row is not None:
                    rows.append(row)
        return [self._screening_run_row(row) for row in rows]
