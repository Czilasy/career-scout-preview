// 021 B8 T027：DiscoveryView results 动作层（自 DiscoveryView.vue script 原样搬运，函数体零改动，跨域引用经 deps 调用时解析）。
// 031 B8：deps 形参类型 = discoveryDeps.ts 的 ResultsNeeds（跨域依赖契约）。
import type { Ref } from "vue";
import type { DiscoveryState } from "./useDiscoveryState";
import type { ResultsNeeds } from "./discoveryDeps";
import { ApiError, apiRequest, errorMessage, settingsApi, userFacingMessage } from "../api";
import type {
  AdvancedSettingsState,
  CandidateProfile,
  ExecutionSelection,
  ExecutionSettings,
  FrozenSearchScope,
  JobItem,
  LocationCondition,
  Notice,
  Platform,
  PlatformCityCatalog,
  PlatformFilterSchema,
  RoundContext,
  TaskSnapshot as ApiTaskSnapshot,
} from "../types";
import type { HistoryRoundDetail } from "../composables/resultHistory";
import JobWorkspace from "../components/JobWorkspace.vue";
import type { PipelineResult, RoundStatusPayload } from "../discovery";
import {
  Bookmark,
  Check,
  Download,
  FileText,
  Filter,
  History,
  LoaderCircle,
  Play,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
  UploadCloud,
  X,
} from "@lucide/vue";
import {
  buildSearchScriptParams,
  createCityCatalogLoader,
  createPlatformState,
  createSchemaLoader,
  DEFAULT_PLATFORM,
  filterPipelineResultByPlatform,
  normalizeScopePreview,
  partitionPipelineResult,
  projectResumeSuggestionToSchema,
  shouldConfirmNationalScope,
} from "../discovery";
import { setThemePlatform } from "../composables/useTheme";
import type { MergedLatestResult } from "./useDiscoveryState";
import { liveTaskStep } from "./useDiscoveryState";

export function useDiscoveryResults(state: DiscoveryState, deps: ResultsNeeds) {
  const { activeCategory, activeStep, analysisReady, archiveHistoryLatest, currentRoundStatus, draftPlatform, exportBusy, feedbackBusyIds, groups, hideHistory, historyBackToLatest, historyMode, historyOpen, historyRound, interruptedRunId, isScrapedOnly, jdBusyIds, lifecycleDialogJob, lifecycleDialogOpen, locationDraft, pausedRunId, pipelineResult, pipelineResultRunId, platformBeforeHistory, platformState, profileFacts, profileSummary, recrawlBusy, recrawlSnapshot, recrawlTaskId, rejectedIds, resultEpoch, resultLoaded, resultPlatformFilter, resultRunIds, resultsPageSeen, returningFromHistory, scrapeBusy, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenSnapshot, showHistory, unfinishedWorkflowRestored } = state;
  const { notify, pollRecrawl, pollTask, setDraftPlatform } = deps;


function setPipelineResult(result: PipelineResult) {
  if (historyMode.value) return;
  pipelineResult.value = result;
  // 后端权威优先；即时 finish 响应或旧快照缺 platform 时按结果级平台回填。
  const platform = (result as PipelineResult & { platform?: string }).platform || "";
  if (platform) {
    for (const list of [result.jobs, result.dropped]) {
      if (!Array.isArray(list)) continue;
      for (const job of list) {
        if (job && typeof job === "object" && !(job as JobItem).platform) {
          (job as JobItem).platform = platform as JobItem["platform"];
        }
      }
    }
  }
  // specs/004：新 run 结果替换完成 → 递增 resultEpoch，通知 JobWorkspace 重置筛选/排序。
  resultEpoch.value += 1;
  const sourceRunId = (result as Record<string, unknown>).source_run_id;
  if (typeof sourceRunId === "string") pipelineResultRunId.value = sourceRunId;
  analysisReady.value = true;
  scrapeCompleted.value = true;
  resultLoaded.value = true;
  const groups = partitionPipelineResult(result);
  let nextCategory: "matched" | "uncertain" | "unmatched" | "dropped" = "dropped";
  if (groups.matched.length) nextCategory = "matched";
  else if (groups.uncertain.length) nextCategory = "uncertain";
  else if (groups.unmatched.length) nextCategory = "unmatched";
  activeCategory.value = nextCategory;
}


function hasLiveTaskState(): boolean {
  if (pausedRunId.value || interruptedRunId.value) return true;
  const liveStatuses = new Set(["running", "queued", "paused", "failed", "interrupted"]);
  for (const snap of [screenSnapshot.value, scrapeSnapshot.value, recrawlSnapshot.value]) {
    if (snap && liveStatuses.has(String(snap.status))) return true;
  }
  return false;
}


async function loadLatestResult(opts?: { skipTerminalSnapshot?: boolean }) {
  // B068：刷新接回未完成轮次时，04 尚未出现，旧结果不能覆盖 02/03 的当前状态。
  if (unfinishedWorkflowRestored.value && !resultsPageSeen.value) return;
  // 暂停/中断任务未结束，不得把暂停时保存的安全网快照当作结果加载，
  // 否则 resultLoaded 被误置 true、04 结果页对用户开放造成「任务还在跑」误解。
  if (interruptedRunId.value || pausedRunId.value || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return;
  const fetched = await fetchMergedLatestResult();
  if (!fetched) return;
  const { merged, newer } = fetched;
  if (hasLiveTaskState() && newer.data.scrape_task_id && scrapeTaskId.value && newer.data.scrape_task_id !== scrapeTaskId.value) return;
  const live = hasLiveTaskState();
  pipelineResultRunId.value = newer.data.source_run_id || "";
  setPipelineResult(merged);
  // B038：最新轮可能是"已抓取，未筛选"，原样透传驱动展示模式。
  currentRoundStatus.value = newer.data.status === "scraped_only" ? "scraped_only" : "screened";
  if (isScrapedOnly.value) activeCategory.value = "matched";
  if (!live) {
    if (newer.data.scrape_task_id) scrapeTaskId.value = newer.data.scrape_task_id;
    const ps = (newer.data.result as Record<string, unknown>).profile_summary;
    if (typeof ps === "string" && ps.trim()) profileSummary.value = ps;
    const pfacts = (newer.data.result as Record<string, unknown>).profile_facts;
    if (pfacts && typeof pfacts === "object") profileFacts.value = pfacts as Record<string, unknown>;
    if (newer.data.round_context) deps.roundFlow.restoreRoundContext(newer.data.round_context);
  }
  if (pausedRunId.value) return;
  // 重抓任务恢复：结果已加载供 04 查看，但 03 页应显示重抓自身进度，
  // 不伪造"上次已完成"快照。
  if (opts?.skipTerminalSnapshot) return;
  const snapshotStatus = (newer.data.status === "completed_with_pending" || newer.data.status === "partial")
    ? "completed_with_pending"
    : "completed";
  scrapeSnapshot.value = {
    status: snapshotStatus, stage: "done", progress: { message: "上次抓取已完成" }, logs: [],
    started_at: newer.data.started_at,
    finished_at: newer.data.finished_at,
  };
  screenSnapshot.value = {
    status: snapshotStatus, stage: "done", progress: { message: "上次 AI 筛选已完成" }, logs: [],
    started_at: newer.data.started_at,
    finished_at: newer.data.finished_at,
  };
  const execConfig = newer.data.execution_config || {};
  scrapeSnapshot.value.execution_config = execConfig;
  screenSnapshot.value.execution_config = execConfig;
  screenSnapshot.value.kept_count = Number(merged.total_kept || 0);
  screenSnapshot.value.dropped_count = Number(merged.total_dropped || 0);
  const sourceTotal = Number(merged.total_scraped || 0);
  const stageTotal = snapshotStatus === "completed_with_pending"
    ? Number(merged.total_kept || 0)
    : sourceTotal;
  scrapeSnapshot.value.total = stageTotal || sourceTotal;
  scrapeSnapshot.value.source_total = sourceTotal;
  screenSnapshot.value.total = stageTotal || sourceTotal;
  screenSnapshot.value.source_total = sourceTotal;
  const uncertainCount = (merged.jobs || []).filter((job) => job.verdict !== "match" && job.verdict !== "not_match" && job.verdict !== "mismatch").length;
  screenSnapshot.value.pending_count = snapshotStatus === "completed_with_pending" ? uncertainCount : 0;
}

// 双平台合并加载：拉两个平台的 /api/latest-pipeline-result 并合并。
// 刷新路径（loadLatestResult）与实时任务完成路径（deps.pollTask）共用，
// 保证两条路径行为一致（R2：实时路径只 set 单平台结果导致切平台显示 0）。


async function fetchMergedLatestResult(): Promise<MergedLatestResult | null> {
  try {
    // 分别拉两个平台各自的最近结果，合并展示（后端 T409 按平台查询）。
    const base = deps.props.profileId ? `&profile_id=${encodeURIComponent(deps.props.profileId)}` : "";
    const fetchOne = (platform: "boss" | "zhilian") => apiRequest<{
      has_result?: boolean;
      source_run_id?: string;
      result?: PipelineResult;
      status?: string;
      started_at?: number;
      finished_at?: number;
      execution_config?: Record<string, unknown> | null;
      scrape_task_id?: string;
      round_context?: Partial<RoundContext> | null;
    }>(`/api/latest-pipeline-result?platform=${platform}${base}`).catch(() => null);
    const [bossData, zhilianData] = await Promise.all([fetchOne("boss"), fetchOne("zhilian")]);
    if (interruptedRunId.value || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return null;

    const parts = [
      bossData?.has_result && bossData.result ? { platform: "boss" as const, data: bossData } : null,
      zhilianData?.has_result && zhilianData.result ? { platform: "zhilian" as const, data: zhilianData } : null,
    ].filter((part): part is { platform: "boss" | "zhilian"; data: NonNullable<typeof bossData> } => Boolean(part))
      .filter((part) => {
        if (!hasLiveTaskState()) return true;
        const activeScrapeTaskId = scrapeTaskId.value;
        return !activeScrapeTaskId || !part.data.scrape_task_id || part.data.scrape_task_id === activeScrapeTaskId;
      });
    if (!parts.length) return null;

    // 每个岗位标记来源 run（单岗位补抓/单 JD 动作需要定位来源）。
    for (const part of parts) {
      resultRunIds.value[part.platform] = part.data.source_run_id || "";
      if (!hasLiveTaskState()) {
        deps.roundFlow.registerRoundContext(part.platform, part.data.round_context);
      }
      const runId = part.data.source_run_id || "";
      for (const list of [part.data.result?.jobs, part.data.result?.dropped]) {
        if (!Array.isArray(list)) continue;
        for (const job of list) {
          if (job && typeof job === "object") {
            (job as JobItem)._result_run_id = runId;
            // 兼容旧快照缺 platform 字段：按查询平台回填（后端权威优先）。
            if (!(job as JobItem).platform) (job as JobItem).platform = part.platform;
          }
        }
      }
    }
    // 以更新时间较新的一份为主干（profile_summary / 状态投影 / 默认 run）。
    let newer = parts[0];
    if (parts.length > 1 && Number(parts[1].data.started_at || 0) > Number(parts[0].data.started_at || 0)) {
      newer = parts[1];
    }

    const sum = (key: "total_scraped" | "total_matched" | "total_kept" | "total_dropped") =>
      parts.reduce((acc, part) => acc + Number((part.data.result as Record<string, unknown> | undefined)?.[key] || 0), 0);
    const merged: PipelineResult = {
      ...(newer.data.result as PipelineResult),
      jobs: parts.flatMap((part) => (Array.isArray(part.data.result?.jobs) ? part.data.result!.jobs : [])),
      dropped: parts.flatMap((part) => (Array.isArray(part.data.result?.dropped) ? part.data.result!.dropped : [])),
      total_scraped: sum("total_scraped"),
      total_matched: sum("total_matched"),
      total_kept: sum("total_kept"),
      total_dropped: sum("total_dropped"),
    };
    // 019：跨平台重复簇——剔除行 extra.cross_platform_dup_of 反查合并 jobs 中
    // 的对端保留条目，命中者挂运行时簇数据（复用 _result_run_id 惯例）；
    // 未命中（对端条目随轮次顶替不可见）静默跳过，退化为剔除台账条目。
    const jobsByPlatformKey = new Map<string, JobItem>();
    for (const job of merged.jobs || []) {
      if (job?.platform && job.platform_job_id) {
        jobsByPlatformKey.set(`${job.platform}:${job.platform_job_id}`, job);
      }
    }
    for (const drop of merged.dropped || []) {
      const dupOf = (drop as JobItem).extra?.cross_platform_dup_of;
      if (!dupOf || typeof dupOf !== "object") continue;
      const head = jobsByPlatformKey.get(
        `${String((dupOf as Record<string, unknown>).platform ?? "")}:${String((dupOf as Record<string, unknown>).platform_job_id ?? "")}`,
      );
      if (!head) continue;
      (head._also_on_copies || (head._also_on_copies = [])).push({
        platform: ((drop as JobItem).platform || head.platform) as NonNullable<JobItem["platform"]>,
        salary: drop.salary || "",
        source_url: String((drop as JobItem).canonical_url || drop.source_url || ""),
        platform_job_id: drop.platform_job_id,
      });
    }
    // 025 B078：暴露各平台最新轮状态（完成态判定用；无该平台轮则缺省）
    const platformStatuses: Partial<Record<"boss" | "zhilian", string>> = {};
    for (const part of parts) {
      platformStatuses[part.platform] = String(part.data.status ?? "");
    }
    return { merged, newer, platformStatuses };
  } catch (error) {
    deps.notify(errorMessage(error, "上次结果暂时无法恢复"), "warning");
    return null;
  }
}


async function clearLatestResult() {
  try {
    await archiveHistoryLatest();
    return true;
  } catch (error) {
    deps.notify(userFacingMessage(error, "归档旧结果失败，已停止开始新一轮"), "error");
    return false;
  }
}


function openHistoryDrawer() {
  showHistory();
}


function toggleHistoryDrawer() {
  if (historyOpen.value) hideHistory();
  else showHistory();
}


function closeHistoryDrawer() {
  if (historyOpen.value) hideHistory();
}


function enterHistoryRound(detail: HistoryRoundDetail) {
  // 首次进入历史时记住进入前的草稿平台，返回最新时还原。
  if (!historyRound.value) platformBeforeHistory.value = platformState.draft;
  // 035（真机问题③，FR-012）：历史浏览只读——先挂历史轮标记（同一时刻只有一个
  // 历史轮激活），展示数据直接装载，不经 setPipelineResult 的当前轮置位路径：
  // scrapeCompleted/resultLoaded/analysisReady 不因历史轮被置位。
  historyRound.value = {
    runId: detail.source_run_id || "",
    platform: detail.platform,
    status: detail.status,
    jobCount: Number(detail.result?.total_kept || (detail.result?.jobs || []).length || 0),
  };
  pipelineResult.value = detail.result || {};
  resultEpoch.value += 1;
  const historyGroups = partitionPipelineResult(detail.result || {});
  activeCategory.value = historyGroups.matched.length ? "matched"
    : historyGroups.uncertain.length ? "uncertain"
    : historyGroups.unmatched.length ? "unmatched"
    : "dropped";
  pipelineResultRunId.value = detail.source_run_id || "";
  resultRunIds.value[detail.platform] = detail.source_run_id || "";
  resultPlatformFilter.value = detail.platform;
  // 历史轮次与顶部平台开关/品牌色绑定：BOSS 历史进 BOSS 模式，智联历史进智联模式。
  platformState.setDraftPlatform(detail.platform);
  draftPlatform.value = detail.platform;
  setThemePlatform(detail.platform);
  activeStep.value = "results";
  // B038：历史轮原始状态透传，scraped_only 轮进入"待筛选"展示模式。
  currentRoundStatus.value = detail.status;
  if (isScrapedOnly.value) activeCategory.value = "matched";
}


async function returnToLatest() {
  const restorePlatform = platformBeforeHistory.value;
  platformBeforeHistory.value = null;
  historyRound.value = null;
  historyBackToLatest();
  resultPlatformFilter.value = "all";
  pipelineResult.value = null;
  pipelineResultRunId.value = "";
  resultLoaded.value = false;
  resultRunIds.value = { boss: "", zhilian: "" };
  resultEpoch.value += 1;
  currentRoundStatus.value = "";
  if (restorePlatform) {
    platformState.setDraftPlatform(restorePlatform);
    draftPlatform.value = restorePlatform;
    setThemePlatform(restorePlatform);
  }
  // 035：未结束任务存在时回最新＝回到该任务的真实进度页（抓取→02、筛选/重抓→03），
  // 不得落到空结果页、不得触发「已结束」；并按任务真实状态重算 scrapeCompleted
  //（防御层，FR-012：抓取活 = 未完成；筛选/重抓活 = 抓取已完成）。
  const liveStep = liveTaskStep(state);
  if (liveStep) {
    scrapeCompleted.value = liveStep === "screen";
    activeStep.value = liveStep;
    return;
  }
  // 任务已结束：回结果页展示本轮成果；过渡期间防御性拦截重复置位。
  returningFromHistory.value = true;
  activeStep.value = "results";
  try {
    await loadLatestResult();
  } finally {
    returningFromHistory.value = false;
  }
}

// B038：历史未筛选轮补筛——退出历史模式，挂载父抓取任务与画像后
// 复用现有"开始 AI 筛选"全流程；后端把结果升级回同一轮次。


function onResultPlatformFilterChange(value: "all" | "boss" | "zhilian") {
  if (historyMode.value) return;
  resultPlatformFilter.value = value;
}


async function exportResultCsv() {
  if (exportBusy.value) return;
  exportBusy.value = true;
  try {
    // 优先按当前结果的 run_id 导出，与结果页展示完全同源
    const query = pipelineResultRunId.value
      ? `?run_id=${encodeURIComponent(pipelineResultRunId.value)}`
      : "";
    const response = await fetch(`/api/pipeline-result/export.csv${query}`, {
      credentials: "same-origin",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
      throw new ApiError(response.status, payload);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const matched = /filename=([^;]+)/.exec(disposition);
    const filename = matched?.[1]?.trim() || "career_scout_jobs.csv";
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    deps.notify("已导出匹配/不匹配分组 CSV", "success");
  } catch (error) {
    deps.notify(errorMessage(error, "导出 CSV 失败"), "error");
  } finally {
    exportBusy.value = false;
  }
}


function restoreLocationsFromContext(ctx?: Partial<RoundContext> | null) {
  if (!ctx?.locations?.length) return;
  const grouped = new Map<string, LocationCondition[]>();
  for (const loc of ctx.locations) {
    if (!loc?.city_name) continue;
    const list = grouped.get(loc.city_name) ?? [];
    list.push(loc);
    grouped.set(loc.city_name, list);
  }
  for (const [city, conditions] of grouped) {
    locationDraft.setLocations(ctx.platform ?? draftPlatform.value, city, conditions);
  }
}


function jobId(job: JobItem): string {
  // T511/T714：pipeline 待确认岗位稳定键是 platform_job_id（store._pending_result_row
  // 把 platform_job_id 映射成 job_id 返回）。智联岗位 job_id 经常为 null（未落库），
  // 旧实现 fallback 到 canonical_url，导致 /api/pipeline/jobs/<id>/jd 等接口 404。
  // 这里优先 platform_job_id（后端按 platform_job_id 查 pending 表），
  // BOSS 历史结果 platform_job_id 缺失时退回 job_id/id/canonical_url 兼容旧行为。
  // 与 JobWorkspace.jobKey（带 platform 前缀，用于 Vue v-for 跨平台唯一）用途不同。
  if (job.platform_job_id) {
    return String(job.platform_job_id);
  }
  return String(
    job.job_id
      || job.id
      || job.canonical_url
      || job.source_url
      || job.job_link
      || ""
  );
}


function withBusy(setRef: typeof feedbackBusyIds, id: string, active: boolean) {
  const next = new Set(setRef.value);
  if (active) next.add(id);
  else next.delete(id);
  setRef.value = next;
}


async function ensureFeedbackProfile(): Promise<string> {
  if (deps.props.profileId) return deps.props.profileId;
  const profile = await apiRequest<CandidateProfile>("/api/profiles", {
    method: "POST",
    json: { name: "岗位发现", confirmed_fields: {} },
  });
  deps.emit("profile-created", profile);
  return profile.id;
}


function feedbackPayload(job: JobItem, profileId: string) {
  return {
    profile_id: profileId,
    job: {
      job_id: job.job_id || job.id,
      platform: job.platform,
      platform_job_id: job.platform_job_id,
      title: job.title,
      salary: job.salary,
      location: job.location,
      company: job.company || job.boss_name,
      jd: job.jd,
      job_link: job.job_link || job.source_url || job.canonical_url,
    },
  };
}


async function toggleInterest(job: JobItem) {
  const id = jobId(job);
  if (!id || feedbackBusyIds.value.has(id)) return;
  withBusy(feedbackBusyIds, id, true);
  try {
    const profileId = await ensureFeedbackProfile();
    const marked = job._marked === "interested";
    await apiRequest(marked
      ? "/api/pipeline/jobs/interest/cancel"
      : "/api/pipeline/jobs/interest", {
      method: "POST",
      json: feedbackPayload(job, profileId),
    });
    job._marked = marked ? null : "interested";
    if (!marked) {
      const next = new Set(rejectedIds.value);
      next.delete(id);
      rejectedIds.value = next;
    }
    deps.notify(marked ? "已取消收藏" : "已收藏", marked ? "info" : "success");
  } catch (error) {
    deps.notify(errorMessage(error, "收藏状态更新失败"), "error");
  } finally {
    withBusy(feedbackBusyIds, id, false);
  }
}


async function toggleRejected(job: JobItem) {
  const id = jobId(job);
  if (!id || feedbackBusyIds.value.has(id)) return;
  if (job._marked === "interested") await toggleInterest(job);
  withBusy(feedbackBusyIds, id, true);
  try {
    const profileId = await ensureFeedbackProfile();
    const currentlyRejected = rejectedIds.value.has(id) || job._marked === "rejected";
    await apiRequest(currentlyRejected
      ? "/api/pipeline/jobs/reject/cancel"
      : "/api/pipeline/jobs/reject", {
      method: "POST",
      json: feedbackPayload(job, profileId),
    });
    const next = new Set(rejectedIds.value);
    if (currentlyRejected) {
      next.delete(id);
      job._marked = null;
      deps.notify("已撤销不感兴趣", "info");
    } else {
      next.add(id);
      job._marked = "rejected";
      deps.notify("已标记不感兴趣", "info");
    }
    rejectedIds.value = next;
  } catch (error) {
    deps.notify(errorMessage(error, "不感兴趣状态更新失败"), "error");
  } finally {
    withBusy(feedbackBusyIds, id, false);
  }
}


async function retryJd(job: JobItem) {
  if (historyMode.value) return;
  const id = jobId(job);
  if (!id || jdBusyIds.value.has(id)) return;
  withBusy(jdBusyIds, id, true);
  try {
    const data = await apiRequest<{
      task_id?: string;
      jd?: string;
      verdict?: string;
      verdict_reason?: string;
      caveats?: string[];
      flags?: JobItem["flags"];
    }>(
      `/api/pipeline/jobs/${encodeURIComponent(id)}/jd`, {
      method: "POST",
      json: {
        // 单岗位动作优先用岗位自身来源 run（合并视图下跨平台也准确）。
        source_run_id: job._result_run_id || pipelineResultRunId.value,
        source_url: job.source_url || job.job_link || job.canonical_url,
        profile_summary: profileSummary.value,
        profile_facts: profileFacts.value,
      },
    });
    if (data.task_id) {
      recrawlBusy.value = true;
      recrawlTaskId.value = data.task_id;
      // 单条补抓也是重抓任务：进度回 03 页展示。
      activeStep.value = "screen";
      recrawlSnapshot.value = {
        status: "running",
        progress: { message: "正在补抓这条岗位…" },
        logs: [],
        error: "",
      };
      await deps.pollRecrawl(data.task_id);
      return;
    }
    job.jd = data.jd || "";
    if (data.verdict) {
      job.verdict = data.verdict as JobItem["verdict"];
      job.verdict_reason = data.verdict_reason || "";
      job.caveats = data.caveats || [];
      job.flags = data.flags || [];
      deps.notify(`JD 已补抓，AI 判定：${data.verdict === "match" ? "匹配" : "不匹配"}`, "success");
    } else {
      deps.notify("JD 已补抓（AI 未判定，可点全部重抓触发精筛）", "success");
    }
  } catch (error) {
    deps.notify(errorMessage(error, "JD 补抓失败"), "error");
  } finally {
    withBusy(jdBusyIds, id, false);
  }
}

// 待确认项「全部重抓」：缺 JD 的补 CDP 抓取，有 JD 的用画像重跑 AI 精筛。
// 复用现有轮询机制显示进度（已完成 X / 共 N），结果原地合并进当前结果，保留当前 tab。


function lifecycleJob(job: JobItem): JobItem {
  if (job.platform && job.platform_job_id && job.canonical_url) {
    return { ...job, id: undefined, job_id: undefined };
  }
  return job;
}


function onJobFeedbackChanged(payload: { profileId: string; jobId: string }) {
  deps.emit("job-feedback-changed", payload);
}

// ---------------------------------------------------------------------------
// 轨迹浮窗：大卡片收敛为“查看轨迹”小按钮，点击后居中弹窗展示全部内容
// ---------------------------------------------------------------------------


function openLifecycleDialog(job: JobItem) {
  lifecycleDialogJob.value = lifecycleJob(job);
  lifecycleDialogOpen.value = true;
}


function closeLifecycleDialog() {
  lifecycleDialogOpen.value = false;
}


function handleLifecycleDialogKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") closeLifecycleDialog();
}

return {
  setPipelineResult,
  hasLiveTaskState,
  loadLatestResult,
  fetchMergedLatestResult,
  clearLatestResult,
  openHistoryDrawer,
  toggleHistoryDrawer,
  closeHistoryDrawer,
  enterHistoryRound,
  returnToLatest,
  onResultPlatformFilterChange,
  exportResultCsv,
  restoreLocationsFromContext,
  jobId,
  withBusy,
  ensureFeedbackProfile,
  feedbackPayload,
  toggleInterest,
  toggleRejected,
  retryJd,
  lifecycleJob,
  onJobFeedbackChanged,
  openLifecycleDialog,
  closeLifecycleDialog,
  handleLifecycleDialogKeydown,
};
}