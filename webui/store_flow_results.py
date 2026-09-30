"""Flow-scoped result projections and archive operations."""

from __future__ import annotations

from webui.store_helpers import _build_pipeline_result_rows, _decode_json, _now


class StoreFlowResultsMixin:
    def get_flow_results(self, flow_id, *, profile_id=None) -> dict:
            """Return result projections strictly scoped to one Flow and Track.

            A Track with a persisted scrape run but no AI result snapshot remains
            visible as an unfinished result.  The payload deliberately does not
            fall back to a platform's latest result, which would leak another
            Flow into the current page.
            """
            flow = self.get_flow(flow_id, profile_id=profile_id)
            tracks = []
            for track in flow.get("tracks", []):
                projected = self._flow_track_result(track, flow)
                tracks.append(projected)
            jobs = []
            seen = set()
            for track in tracks:
                for job in track["jobs"]:
                    key = str(job.get("platform_job_id") or job.get("job_id") or "")
                    if key and (track["platform"], key) in seen:
                        continue
                    if key:
                        seen.add((track["platform"], key))
                    jobs.append(job)
            return {
                "flow_id": flow["id"],
                "profile_id": flow["profile_id"],
                "selection": flow["selection"],
                "status": flow["status"],
                "tracks": tracks,
                "jobs": jobs,
                "screened_count": sum(item["screened_count"] for item in tracks),
            }

    def archive_flow_results(self, flow_id, profile_id) -> list[str]:
            """Archive only result snapshots bound to one Flow."""
            flow = self.get_flow(flow_id, profile_id=profile_id)
            result_ids = [
                str(track["result_run_id"])
                for track in flow.get("tracks", [])
                if track.get("result_run_id")
            ]
            if not result_ids:
                return []
            placeholders = ",".join("?" for _ in result_ids)
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT id FROM screening_runs WHERE id IN (" + placeholders + ") "
                    "AND profile_id = ? AND record_kind = 'result_snapshot' "
                    "AND archived_at IS NULL",
                    (*result_ids, str(profile_id)),
                ).fetchall()
                archived = [str(row["id"]) for row in rows]
                if archived:
                    now = _now()
                    archived_placeholders = ",".join("?" for _ in archived)
                    conn.execute(
                        "UPDATE screening_runs SET archived_at = ?, updated_at = ? "
                        "WHERE id IN (" + archived_placeholders + ")",
                        (now, now, *archived),
                    )
            return archived

    def _flow_track_result(self, track: dict, flow: dict) -> dict:
            """Build one platform result without crossing Flow boundaries."""
            platform = str(track["platform"])
            # Virtual legacy history rows have no durable Flow identity.  They
            # remain read-only compatibility projections; real Flow bindings use
            # the strict exact-flow checks below.
            strict_flow_id = None if flow.get("legacy") else flow["id"]
            jobs: list[dict] = []
            dropped: list[dict] = []
            ai_screened = False
            result_run_id = track.get("result_run_id")
            if result_run_id:
                with self._connection() as conn:
                    self._assert_flow_run(
                        conn, "screening_runs", result_run_id,
                        flow["profile_id"], platform,
                        check_platform=True, flow_id=strict_flow_id,
                        track_id=track["id"],
                    )
                    run = conn.execute(
                        "SELECT * FROM screening_runs WHERE id = ? AND profile_id = ? "
                        "AND record_kind = 'result_snapshot'",
                        (str(result_run_id), str(flow["profile_id"])),
                    ).fetchone()
                    if run is not None and str(run["platform"] or platform) == platform:
                        rows = conn.execute(
                            "SELECT * FROM screening_results WHERE run_id = ? ORDER BY rowid",
                            (str(result_run_id),),
                        ).fetchall()
                        jobs, dropped = _build_pipeline_result_rows(rows)
                        ai_screened = True

            if not jobs and not dropped and not ai_screened:
                source_run_ids = []
                if track.get("scrape_run_id"):
                    source_run_ids.append(track["scrape_run_id"])
                if track.get("screen_run_id"):
                    source_run_ids.append(track["screen_run_id"])
                    with self._connection() as conn:
                        source_row = conn.execute(
                            "SELECT execution_params_json FROM screening_runs WHERE id = ?",
                            (str(track["screen_run_id"]),),
                        ).fetchone()
                    params = _decode_json(
                        source_row["execution_params_json"] if source_row else None, {}
                    )
                    for key in ("scrape_task_id", "source_run_id"):
                        value = params.get(key) if isinstance(params, dict) else None
                        if value:
                            source_run_ids.append(value)
                for source_run_id in source_run_ids:
                    try:
                        with self._connection() as conn:
                            self._assert_flow_run(
                                conn, "screening_runs", source_run_id,
                                flow["profile_id"], platform,
                                check_platform=True, flow_id=strict_flow_id,
                                track_id=track["id"],
                            )
                        jobs = [
                            dict(job)
                            for job in self.load_scrape_run_jobs(source_run_id)
                            if isinstance(job, dict)
                        ]
                    except (KeyError, ValueError):
                        jobs = []
                    if jobs:
                        break

            jobs = self._dedupe_flow_jobs(jobs, platform)
            dropped = self._dedupe_flow_jobs(dropped, platform, seen={
                str(job.get("platform_job_id") or job.get("job_id") or "")
                for job in jobs
            })
            screened_count = 0
            if ai_screened:
                screened_count = sum(
                    1 for job in jobs
                    if str(job.get("verdict") or "") in {"match", "not_match", "mismatch"}
                )
            unfinished = bool(
                not ai_screened
                and jobs
                and str(track.get("status") or "") in {
                    "failed", "stopped", "cancelled", "done", "succeeded"
                }
            )
            return {
                **dict(track),
                "jobs": jobs,
                "dropped": dropped,
                "ai_screened": ai_screened,
                "unfinished_ai_screening": unfinished,
                "screened_count": screened_count,
                "message": "未完成 AI 筛选" if unfinished else "",
            }

    @staticmethod
    def _dedupe_flow_jobs(jobs, platform, *, seen=None):
            seen = set(seen or ())
            result = []
            for job in jobs or []:
                if not isinstance(job, dict):
                    continue
                item = dict(job)
                # The durable Flow Track is the platform authority.  A legacy
                # scrape/screen payload can carry a stale platform field; let
                # it never leak across the Track boundary in the public Flow
                # results projection.
                item["platform"] = platform
                key = str(item.get("platform_job_id") or item.get("job_id") or "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                result.append(item)
            return result
