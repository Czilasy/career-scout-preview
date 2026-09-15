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
import { currentHistoryIntent } from "../composables/resultHistory";
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
import { liveTaskStep, type StepId } from "./useDiscoveryState";
import { useDiscoverySceneState } from "./useDiscoverySceneState";
import type { SceneIdentity } from "../types";

export function useDiscoveryResults(state: DiscoveryState, deps: ResultsNeeds) {
  const { activeCategory, activeStep, analysisReady, archiveHistoryLatest, currentRoundStatus, draftPlatform, exportBusy, feedbackBusyIds, groups, hideHistory, historyBackToLatest, historyMode, historyOpen, historyRound, interruptedRunId, isScrapedOnly, jdBusyIds, lifecycleDialogJob, lifecycleDialogOpen, locationDraft, pausedRunId, pipelineResult, pipelineResultRunId, platformBeforeHistory, platformState, profileFacts, profileId, profileSummary, recrawlBusy, recrawlSnapshot, recrawlTaskId, rejectedIds, resultEpoch, resultLoaded, resultPlatformFilter, resultRunIds, resultsPageSeen, resultsBootstrapPending, returningFromHistory, resumeAnalysisLandOnReturn, resumeAnalysisPhase, scrapeBusy, scrapeCompleted, scrapeSnapshot, scrapeTaskId, screenBusy, screenSnapshot, screenTaskId, showHistory, unfinishedWorkflowRestored, workflowEpoch } = state;
  const sceneStore = useDiscoverySceneState();

  function currentSceneIdentity(): SceneIdentity {
    return {
      profileId: profileId.value,
      // Spec041 返工：当前轮身份取现场存档的稳定轮次值，不随 task_id /
      // 结果 run id 变化（否则翻一次历史就把当前轮现场换了键）。
      runEpoch: sceneStore.roundEpoch.value
        || sceneStore.ensureRoundEpoch(profileId.value)
        || "draft",
      platform: platformState.result
        || screenSnapshot.value?.platform
        || scrapeSnapshot.value?.platform
        || platformState.draft,
    };
  }
  // 跨域依赖一律经 deps.X 调用时解析：本 composable 早于 wireDiscoveryDeps 构造，
  // 构造期解构会拿到 undefined（契约 discovery-deps.ts「只在调用时读取 deps 成员」）。
  // 首屏对齐：第一次拿到最近结果时，把新任务草稿平台（顶部滑块）带到结果
  // 平台，避免「品牌色是智联、滑块停在 BOSS」要用户手动切一次才一致。
  // 只做一次：之后切平台或再加载结果都不再改写草稿（契约首屏例外见
  // specs/001 .../contracts/platform-schema.md「最近结果加载」）。
  // setDraftPlatform 在草稿已是该平台时直接返回，不会清任何现场。
  let draftAlignedToResult = false;


function setPipelineResult(result: PipelineResult) {
  if (historyMode.value) return;
  pipelineResult.value = result;
  // 后端权威优先；即时 finish 响应或旧快照缺 platform 时按结果级平台回填。
  const platform = (result as PipelineResult & { platform?: string }).platform || "";
  if (platform) {
    if (platform === "boss" || platform === "zhilian") {
      // 结果主题必须跟随当前结果快照，不读取新任务草稿平台。
      platformState.setResultPlatform(platform);
      setThemePlatform(platform);
    }
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
  // Spec041 补丁：结果已由后端补齐，"正在恢复上次的结果…"骨架到此结束。
  // 启动恢复流程被活任务拦下（busy 门 / maybeAutoStartNewRound 提前返回）时，
  // 这个标记原本没有任何清除点，会一直挡住岗位列表（要刷新一次才恢复）。
  resultsBootstrapPending.value = false;
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
  const requestEpoch = workflowEpoch.value;
  const fetched = await fetchMergedLatestResult();
  if (requestEpoch !== workflowEpoch.value) return;
  if (!fetched) return;
  const { newer } = fetched;
  if (hasLiveTaskState() && newer.data.scrape_task_id && scrapeTaskId.value && newer.data.scrape_task_id !== scrapeTaskId.value) return;
  const live = hasLiveTaskState();
  await applyFetchedLatestResult(fetched, opts, live);
}


async function applyFetchedLatestResult(
  fetched: MergedLatestResult,
  opts?: { skipTerminalSnapshot?: boolean },
  live = hasLiveTaskState(),
) {
  const { merged, newer } = fetched;
  // 首屏对齐（契约 platform-schema.md「最近结果加载·首屏例外」）：只在这条
  // 「启动/刷新加载最近结果」路径、且无活动任务时，把草稿平台对齐到结果平台一次。
  // 必须早于 setPipelineResult：setDraftPlatform 按 B007 清空本轮抓取/筛选现场，
  // 若在其后调用会抹掉刚置位的「结果已加载 / 抓取已完成」事实，使 03 步不可达
  // （未结束任务的恢复场景）。不放进 setPipelineResult 本身——任务跑完的补拉也
  // 走那里，切草稿会清掉现场。
  // setDraftPlatform 会顺带按新平台重载 schema/城市（否则提交会带旧平台的
  // filter_schema_version 触发后端 409）；草稿已是该平台时它直接返回。
  if (!live && !historyMode.value && !draftAlignedToResult) {
    const platform = (merged as PipelineResult & { platform?: string }).platform || "";
    if (platform === "boss" || platform === "zhilian") {
      draftAlignedToResult = true;
      deps.setDraftPlatform(platform);
    }
  }
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
    integrity: newer.data.integrity || merged.integrity || null,
  };
  screenSnapshot.value = {
    status: snapshotStatus, stage: "done", progress: { message: "上次 AI 筛选已完成" }, logs: [],
    started_at: newer.data.started_at,
    finished_at: newer.data.finished_at,
    integrity: newer.data.integrity || merged.integrity || null,
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
  // 039（用户拍板·数字永久展示）：03 面板不得以“没有筛选单元”为由整行隐藏。
  // 纯抓取轮尚未筛选，真实结论就是「已完成 0 / N、未开始 N」（N = 本轮已抓岗位，
  // 即筛选工作单元）；抓完未筛选的轮次不是“无数字可显示”。
  screenSnapshot.value.total = stageTotal || sourceTotal;
  screenSnapshot.value.source_total = sourceTotal;
  if (currentRoundStatus.value === "scraped_only") {
    screenSnapshot.value.success_count = 0;
    screenSnapshot.value.fail_count = 0;
    screenSnapshot.value.unstarted_count = sourceTotal;
  }
  const uncertainCount = (merged.jobs || []).filter((job) => job.verdict !== "match" && job.verdict !== "not_match" && job.verdict !== "mismatch").length;
  screenSnapshot.value.pending_count = snapshotStatus === "completed_with_pending" ? uncertainCount : 0;
  // 039（用户拍板·单一来源）：面板计数只在这里补齐——按本轮真实任务快照
  //（/api/task-state，与实时面板同一后端口径）取完成/跳过/未开始/已抓与失败留痕。
  // 启动恢复、从历史/灵动岛跳回最新、以后新增的任何入口都经本函数，
  // 禁止再按入口各打一份补丁（缺补丁的入口会退回「已完成 0」）。
  if (!live && !opts?.skipTerminalSnapshot) {
    await syncRestoredRoundCounts(
      String(newer.data.scrape_task_id || ""),
      String(newer.data.source_run_id || ""),
    );
  }
}

// 039：按该轮真实任务快照补齐面板计数与失败留痕；取不到时保持合成值，不阻塞首屏。
async function syncRestoredRoundCounts(scrapeRunId: string, screenRunId: string) {
  const epoch = workflowEpoch.value;
  const targets: Array<[typeof scrapeSnapshot, string]> = [
    [scrapeSnapshot, scrapeRunId],
  ];
  // 纯抓取轮没有筛选任务。这里的第二个编号只是结果轮编号，不能拿它
  // 去读取一份旧的筛选快照，否则会把“已完成 0 / N”覆盖成旧的 2 / 2。
  if (currentRoundStatus.value !== "scraped_only") {
    targets.push([screenSnapshot, screenRunId && screenRunId !== scrapeRunId ? screenRunId : ""]);
  }
  for (const [target, runId] of targets) {
    if (!runId || !target.value) continue;
    let state: Partial<ApiTaskSnapshot>;
    try {
      // Spec041 返工：任务状态查询带当前画像，后端按归属校验。
      state = await apiRequest<Partial<ApiTaskSnapshot>>(
        `/api/task-state/${encodeURIComponent(runId)}`
        + (profileId.value ? `?profile_id=${encodeURIComponent(profileId.value)}` : ""));
    } catch {
      continue; // 取不到真实快照时保持合成值，不阻塞首屏
    }
    if (epoch !== workflowEpoch.value) return; // 期间切轮/开新一轮：不覆盖
    const snap = target.value;
    if (!snap) continue;
    const total = Number(state.total || 0);
    if (total > 0) snap.total = total;
    snap.success_count = Number(state.success_count || 0);
    snap.fail_count = Number(state.fail_count || 0);
    snap.unstarted_count = Number(state.unstarted_count || 0);
    snap.pending_count = Number(state.pending_count || 0);
    if (state.source_total != null) snap.source_total = Number(state.source_total || 0);
    if (state.scraped_count != null) snap.scraped_count = Number(state.scraped_count || 0);
    if (state.combo_issues) snap.combo_issues = state.combo_issues;
  }
}

// 最新结果加载：读取当前画像全局最新的一轮。当前任务创建入口一次只运行
// 一个平台，因此另一平台的“最近结果”属于旧轮，不能在这里聚合。


async function fetchMergedLatestResult(): Promise<MergedLatestResult | null> {
  try {
    const requestEpoch = workflowEpoch.value;
    const query = deps.props.profileId
      ? `?profile_id=${encodeURIComponent(deps.props.profileId)}`
      : "";
    const data = await apiRequest<{
      has_result?: boolean;
      source_run_id?: string;
      platform?: "boss" | "zhilian";
      result?: PipelineResult;
      status?: string;
      started_at?: number;
      finished_at?: number;
      execution_config?: Record<string, unknown> | null;
      scrape_task_id?: string;
      round_context?: Partial<RoundContext> | null;
      integrity?: PipelineResult["integrity"];
      // 043：该轮是否已消费过"一次性提醒"（启动恢复闸门依据）。
      notice_sent?: boolean;
    }>(`/api/latest-pipeline-result${query}`);
    if (requestEpoch !== workflowEpoch.value) return null;
    if (interruptedRunId.value || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return null;
    if (!data?.has_result || !data.result) return null;
    if (hasLiveTaskState() && scrapeTaskId.value && data.scrape_task_id
      && data.scrape_task_id !== scrapeTaskId.value) return null;
    const platform = data.platform
      || data.round_context?.platform
      || data.result.jobs?.find((job) => job.platform)?.platform
      || draftPlatform.value;
    if (platform !== "boss" && platform !== "zhilian") return null;
    const newer = { platform, data };
    const parts = [newer];

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

    const sum = (key: "total_scraped" | "total_matched" | "total_kept" | "total_dropped") =>
      parts.reduce((acc, part) => acc + Number((part.data.result as Record<string, unknown> | undefined)?.[key] || 0), 0);
    const merged: PipelineResult = {
      ...(newer.data.result as PipelineResult),
      platform: newer.platform,
      jobs: parts.flatMap((part) => (Array.isArray(part.data.result?.jobs) ? part.data.result!.jobs : [])),
      dropped: parts.flatMap((part) => (Array.isArray(part.data.result?.dropped) ? part.data.result!.dropped : [])),
      total_scraped: sum("total_scraped"),
      total_matched: sum("total_matched"),
      total_kept: sum("total_kept"),
      total_dropped: sum("total_dropped"),
      integrity: newer.data.integrity || (newer.data.result as PipelineResult | undefined)?.integrity || null,
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


/**
 * Spec041 返工：当前轮的平台身份（与历史轮平台严格分开）。
 * 结果平台优先（结果页展示的轮次身份），其次任务快照，最后才是草稿平台。
 */
function currentRoundPlatform(): Platform {
  const resultPlatform = (pipelineResult.value as { platform?: string } | null)?.platform;
  if (resultPlatform === "boss" || resultPlatform === "zhilian") return resultPlatform;
  return screenSnapshot.value?.platform
    || recrawlSnapshot.value?.platform
    || scrapeSnapshot.value?.platform
    || platformState.result
    || platformState.draft;
}


function enterHistoryRound(detail: HistoryRoundDetail) {
  // 当前轮现场先留在原身份键下，历史轮单独保存查看现场。
  sceneStore.getCurrent(currentSceneIdentity());
  // Spec041 返工（真实验收失败项二）：进入历史前记下"当前轮平台"，历史轮平台
  // 只用于浏览展示，绝不改写当前轮的草稿平台与结果平台——否则退出历史后
  // 当前轮会顶着一个错的平台（真实现场：智联历史 → 回到 BOSS 空结果页）。
  if (!historyRound.value) platformBeforeHistory.value = currentRoundPlatform();
  // 035（真机问题③，FR-012）：历史浏览默认只读——先挂历史轮标记（同一时刻只有一个
  // 历史轮激活），展示数据直接装载，不经 setPipelineResult 的当前轮置位路径；
  // 待确认重抓是唯一复用原轮次 source_run_id 的写回入口。
  historyRound.value = {
    runId: detail.source_run_id || "",
    platform: detail.platform,
    status: detail.status,
    jobCount: Number(detail.result?.total_kept || (detail.result?.jobs || []).length || 0),
    integrity: detail.integrity || detail.result?.integrity || null,
  };
  pipelineResult.value = {
    ...(detail.result || {}),
    integrity: detail.integrity || detail.result?.integrity || null,
  };
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
  // 只改"结果平台 + 品牌色"用于展示；草稿平台保持当前轮自己的值（不改写）。
  platformState.setResultPlatform(detail.platform);
  setThemePlatform(detail.platform);
  activeStep.value = "results";
  // B038：历史轮原始状态透传，scraped_only 轮进入"待筛选"展示模式。
  currentRoundStatus.value = detail.status;
  if (isScrapedOnly.value) activeCategory.value = "matched";
}


/**
 * 退出历史、回到当前轮现场，并返回"真实落点步骤"（供灵动岛导航使用）。
 * 返回 null 表示本次没有完成退出（并发点击 / 用户又点了别的轮次）：调用方
 * 不得据此再改步骤，避免制造空结果页。
 */
async function returnToLatest(): Promise<StepId | null> {
  if (returningFromHistory.value) return null;
  const resumePhase = resumeAnalysisPhase.value;
  // Spec041 返工：当前轮平台取"进入历史前记下的当前轮平台"；
  // 拿不到时按当前结果/任务/草稿的真实身份回退，绝不使用历史轮平台。
  const restorePlatform = platformBeforeHistory.value || currentRoundPlatform();
  if (historyRound.value?.runId) {
    sceneStore.saveHistory(
      historyRound.value.runId,
      {},
      {
        profileId: profileId.value,
        runEpoch: historyRound.value.runId,
        platform: historyRound.value.platform,
      },
    );
  }
  returningFromHistory.value = true;
  try {
    // 先拿到最新结果，再清理历史展示。请求期间继续保留历史轮次，避免
    // pipelineResult 被置空后渲染出一个数字全为 0 的临时 04 页面。
    // 035：未结束任务存在时不请求结果，直接回到任务真实进度页。
    const liveStep = liveTaskStep(state);
    const intent = currentHistoryIntent();
    const fetched = liveStep ? null : await fetchMergedLatestResult();
    // 等结果期间用户又点了一轮历史（或又发起一次回最新）：放弃本次，
    // 迟到的「最新」不允许覆盖用户后来选中的轮次。
    if (currentHistoryIntent() !== intent) return null;

    platformBeforeHistory.value = null;
    historyRound.value = null;
    historyBackToLatest();
    resultPlatformFilter.value = "all";
    pipelineResult.value = null;
    pipelineResultRunId.value = "";
    resultLoaded.value = false;
    platformState.setResultPlatform(null);
    resultRunIds.value = { boss: "", zhilian: "" };
    resultEpoch.value += 1;
    currentRoundStatus.value = "";
    // 没有可恢复结果时也要回到当前轮平台，不能把历史轮的品牌色留在新轮页面。
    setThemePlatform(restorePlatform);

    if (liveStep) {
      scrapeCompleted.value = liveStep === "screen";
      activeStep.value = liveStep;
      return liveStep;
    }
    // 分析中/失败时，拉到的可能还是旧轮结果；分析状态优先，不能误进第四页。
    if (fetched && (resumePhase === "idle" || resumePhase === "succeeded")) {
      await applyFetchedLatestResult(fetched);
      activeStep.value = "results";
      return "results";
    }

    // 没有任务和最新结果时，才按简历分析自己的真实进度落点。
    // 分析成功可能早已推进到后续完整流程，不能用旧的 succeeded 状态
    // 把已经完成的最新结果页覆盖成第二页。
    if (resumePhase !== "idle") {
      resumeAnalysisLandOnReturn.value?.();
      const step: StepId = resumePhase === "succeeded" ? "search" : "upload";
      activeStep.value = step;
      return step;
    }

    // 没有进行中的任务，也没有可恢复的最新结果：回到干净的 01，
    // 不把一个空的 04 当成“最新结果”（当前轮没有结果时落真实进度页）。
    analysisReady.value = false;
    scrapeCompleted.value = false;
    activeStep.value = "upload";
    return "upload";
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
      // 单条补抓也是结果页上的后台任务，不能因为开始轮询就把用户带离当前现场。
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

// ---------------------------------------------------------------------------
// 036 B088：顶栏胶囊结果数字提取由 useDiscoveryState 数据层提供（实现在
// 数据层，避免 results→state 反向运行时依赖）。本域 re-export 标记「结果
// 数字源自流水线结果」，消费方（roundStatusPayload 派生）统一经此处取用。
// ---------------------------------------------------------------------------
export { resultCountsFromPipeline } from "./useDiscoveryState";
