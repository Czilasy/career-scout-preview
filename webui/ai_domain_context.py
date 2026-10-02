"""B094 领域上下文读取与独立 JD 资料通道。

职责：用既有公开绑定服务定位这条 run 自己的来源 Track，把用户已有的行业
选项派生成领域上下文，并只对「判定复用」做规则版本守卫；客观 JD 资料走
与判定互相独立的只读通道，不因旧判定不兼容、行业条件或画像摘要不同而失效。

引用方向：``runners/ai_screen_task`` → 本模块 → ``flow_task_coordinator`` /
``TaskStore`` 公开读取 / ``screen_flow.load_resume_jd`` → ``ai_domain_policy``。
本模块不写终态、不改绑定服务、不写 store。
"""

from __future__ import annotations

from dataclasses import dataclass

from webui import ai_domain_policy as policy
from webui.flow_task_coordinator import (
    FlowTaskOperationError,
    MissingPlatformIdentityError,
)
from webui.screen_flow import load_resume_jd, load_resume_verdicts_with_fallback, read_domain_selection

#: 进行中记录不参与资料复用（其断点尚未定稿）。
ACTIVE_STATUSES = frozenset({"queued", "running"})


class DomainContextError(RuntimeError):
    """来源身份冲突或快照结构损坏；不得降级成「不限」后继续筛选。"""

    error_code = "filter_snapshot_incompatible"


class ScreeningPolicyIncompatibleError(RuntimeError):
    """旧判定口径与当前规则不一致，必须重新筛选；已抓资料仍保留。"""

    error_code = policy.INCOMPATIBLE_ERROR_CODE


@dataclass(frozen=True)
class DomainContext:
    platform: str
    selection: policy.DomainSelection
    flow_id: str | None = None
    track_id: str | None = None
    profile_id: str | None = None
    scrape_task_id: str = ""

    @property
    def has_selection(self) -> bool:
        return self.selection.has_selection

    @property
    def labels(self) -> tuple[str, ...]:
        return self.selection.labels

    def metadata(self) -> dict:
        return policy.run_metadata(self.selection)

    def criteria_state(self) -> dict:
        """交给粗筛/精筛的内部领域口径（不进 API 入参，也不进 frozen_filters）。"""
        return {
            policy.CRITERIA_DOMAIN_KEY: {
                "labels": list(self.selection.labels),
                "source_kind": self.selection.source_kind,
                "version": policy.POLICY_VERSION,
            }
        }

    def verdicts_are_reusable(self, execution_params) -> bool:
        return policy.is_verdict_state_compatible(
            execution_params, selection=self.selection,
        )


def build_screening_context(ctx, *, scrape_task_id: str, platform: str,
                           screening_fields, profile_id=None) -> DomainContext:
    """派生这条 run 的领域上下文；与自动候选查找共用只读语义入口。"""
    platform = str(platform or "").strip().lower()
    try:
        selection, binding = read_domain_selection(
            ctx.store, scrape_task_id, screening_fields,
            platform=platform, profile_id=profile_id,
        )
    except (FlowTaskOperationError, MissingPlatformIdentityError,
            policy.DomainSnapshotError, KeyError, ValueError) as exc:
        raise DomainContextError(str(exc)) from exc
    bound = binding or {}
    return DomainContext(
        platform=platform, selection=selection,
        flow_id=str(bound.get("flow_id") or "") or None,
        track_id=str(bound.get("track_id") or "") or None,
        profile_id=str(bound.get("profile_id") or "") or profile_id,
        scrape_task_id=str(scrape_task_id or ""),
    )


def no_domain_context(platform: str = "") -> DomainContext:
    """无领域选择时的空上下文（保留既有恢复与输入行为）。"""
    return DomainContext(
        platform=str(platform or "").strip().lower(),
        selection=policy.empty_selection(),
    )


def ensure_resume_compatible(ctx, resume_from_run_id: str,
                             context: DomainContext | None) -> None:
    """显式续跑前检查旧记录判定口径；不兼容必须在覆盖元数据之前阻断。"""
    run_id = str(resume_from_run_id or "").strip()
    if not run_id or context is None or not context.platform:
        return
    if run_id == str(context.scrape_task_id or ""):
        return
    run = ctx.store.get_screening_run(run_id)
    if run is None:
        return
    if context.verdicts_are_reusable(run.get("execution_params") or {}):
        return
    raise ScreeningPolicyIncompatibleError(
        f"screening verdicts of run {run_id} predate the current domain rules"
    )


def prepare_domain_run(ctx, *, scrape_task_id, platform, screening_fields,
                       resume_from_run_id, profile_id=None) -> DomainContext:
    """建立本轮领域上下文，并在覆盖元数据与读取断点之前校验旧判定口径。"""
    context = build_screening_context(
        ctx, scrape_task_id=scrape_task_id, platform=platform,
        screening_fields=screening_fields, profile_id=profile_id,
    )
    ensure_resume_compatible(ctx, resume_from_run_id, context)
    return context


def load_resume_progress(ctx, *, task_id, resume_from_run_id, platform,
                         scrape_task_id, screening_fields, profile_summary,
                         profile_facts, selection=None) -> tuple[dict, dict]:
    """读取兼容续跑的判定与 JD 断点；两条通道各自过自己的守卫。

    判定要领域版本与语义兼容才复用；JD 是客观资料，只受来源身份约束。
    """
    run_id = str(resume_from_run_id or "").strip()
    if not run_id:
        return {}, {}
    verdicts = load_resume_verdicts_with_fallback(
        ctx.store, run_id, platform, scrape_task_id, screening_fields,
        profile_summary, profile_facts=profile_facts, domain_selection=selection,
    )
    old_jd_path = ctx.jd_checkpoint_path(ctx.app.config["RESULT_DIR"], run_id)
    jd_map = load_resume_jd(ctx.store, old_jd_path, run_id)
    # 继承读取保持只读；新 run 落盘前删除旧断点会使暂停/重启丢资料。
    return verdicts, jd_map


def _candidate_is_same_identity(run, *, platform: str, profile_id) -> bool:
    params = run.get("execution_params") or {}
    run_platform = str(
        params.get("platform") or run.get("platform") or ""
    ).strip().lower()
    if run_platform != str(platform or "").strip().lower():
        return False
    if str(run.get("status") or "").strip() in ACTIVE_STATUSES:
        return False
    run_profile = str(run.get("profile_id") or "").strip()
    target_profile = str(profile_id or "").strip()
    # 无画像记录只在当前也无画像时复用，不猜测跨画像归属。
    return run_profile == target_profile


def load_source_jd_materials(store, *, scrape_task_id: str, current_run_id: str,
                            platform: str, profile_id, job_ids,
                            jd_path_for) -> dict[str, str]:
    """按来源/平台/画像/岗位身份只读复用同源 JD，与判定兼容与否无关。

    优先级：调用方已持有的当前 JD 与兼容续跑 JD 由调用方先行写入；本函数
    只按创建顺序新到旧补齐缺口，同候选断点文件原文优先于结果表。
    """
    wanted = {str(job_id).strip() for job_id in (job_ids or []) if str(job_id).strip()}
    scrape_task_id = str(scrape_task_id or "").strip()
    if not wanted or not scrape_task_id:
        return {}
    candidates = store.latest_screen_runs_for_source(scrape_task_id) or []
    materials: dict[str, str] = {}
    for run in reversed(list(candidates)):
        if len(materials) == len(wanted):
            break
        run_id = str(run.get("id") or "").strip()
        if not run_id or run_id == str(current_run_id or "").strip():
            continue
        if not _candidate_is_same_identity(
            run, platform=platform, profile_id=profile_id,
        ):
            continue
        loaded = load_resume_jd(
            store, str(jd_path_for(run_id)), run_id, include_dropped=True,
        )
        for job_id, jd in loaded.items():
            text = str(jd or "").strip()
            key = str(job_id)
            if text and key in wanted and key not in materials:
                materials[key] = text
    return materials


def extend_jd_materials(ctx, existing, context, *, current_run_id, job_ids) -> dict:
    """详情抓取前只读补齐同源 JD：判定不兼容不影响资料，也不删除旧文件。"""
    merged = dict(existing or {})
    if context is None or not context.has_selection or not context.scrape_task_id:
        return merged
    pending = [
        str(job_id) for job_id in (job_ids or [])
        if str(job_id or "").strip() and str(job_id) not in merged
    ]
    if not pending:
        return merged
    result_dir = ctx.app.config["RESULT_DIR"]
    found = load_source_jd_materials(
        ctx.store,
        scrape_task_id=context.scrape_task_id,
        current_run_id=current_run_id,
        platform=context.platform,
        profile_id=context.profile_id,
        job_ids=pending,
        jd_path_for=lambda run_id: ctx.jd_checkpoint_path(result_dir, run_id),
    )
    for job_id, jd in found.items():
        merged.setdefault(str(job_id), jd)
    return merged
