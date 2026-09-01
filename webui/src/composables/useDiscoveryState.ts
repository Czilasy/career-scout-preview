// 021 B8 T027：DiscoveryView 数据层（自 DiscoveryView.vue script 原样搬运）。
import { computed, reactive, ref, watch, type Ref } from "vue";

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
import JobWorkspace from "../components/JobWorkspace.vue";
import OneClickScreenDialog, {
  type OneClickFilterGroup,
  crossPlatformDedupeEnabled,
} from "../components/OneClickScreenDialog.vue";
import type { PipelineResult, RoundStatusPayload } from "../discovery";

// 各预设档默认「每组翻页数」（与后端 webui/mode_configs.py MODE_DEFAULT_PAGES 同步）。
// 切换档位/开始新一轮时，预设档的翻页数回归该默认；手动修改保存后走自定义档。
export const MODE_DEFAULT_PAGES: Record<string, number> = {
  stable: 2,
  balanced: 5,
  extreme: 10,
};
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
import { historyStatusLabel } from "../discovery";
import { useLocationDraft } from "../composables/useLocationDraft";
import { useResultHistory } from "../composables/resultHistory";

// ---------------------------------------------------------------------------
// 036 B088：胶囊点击导航信号（App.vue 经 DynamicIsland 派发，本域消费）。
// 用模块级 ref + requestCapsuleNavigation 而非 defineExpose：
// DiscoveryView.vue 是超限红线文件禁止修改，胶囊点击需要从 App 侧驱动
// Discovery 内部 activeStep，故经此模块级信号解耦（App 只发目标，本域响应）。
// ---------------------------------------------------------------------------
export type CapsuleNavigationTarget = "home" | "task" | "results" | "attention";
const capsuleNavigationTarget = ref<CapsuleNavigationTarget | null>(null);

/** 请求顶栏胶囊导航（App.vue 在 DynamicIsland 点击时调用）。 */
export function requestCapsuleNavigation(target: CapsuleNavigationTarget): void {
  capsuleNavigationTarget.value = target;
}

export function useDiscoveryState(props: DiscoveryProps, emit: DiscoveryEmit) {


const WORKFLOW_STATE_VERSION = 1;


const workflowStateKey = computed(() => `career-scout-workflow:${props.profileId}`);


const workflowStateRestored = ref(false);


const unfinishedWorkflowRestored = ref(false);


const resultsPageSeen = ref(false);


const restoredWorkflowSnapshot = ref<Record<string, any> | null>(null);


const activeTaskRestored = ref(false);

// D7：未登录类错误码（BOSS/智联 preflight 与任务暂停的稳定错误码）。
const LOGIN_ERROR_CODES = new Set([
  "source_login_required", "login_expired", "boss_login_required",
]);
// 任务失败后显示的登录引导条；visible 时展示「打开账号 X 的 BOSS 窗口登录」。

// 任务失败后显示的登录引导条；visible 时展示「打开账号 X 的 BOSS 窗口登录」。
const loginGuide = ref<{ visible: boolean; platform: Platform; accountName: string }>({
  visible: false,
  platform: "boss",
  accountName: "",
});

// T503：平台三身份独立（platform-schema.md L142-159）
// platformState 是真相之源（非响应式闭包）；draftPlatform 是 Vue 镜像，仅供模板渲染。
// setDraftPlatform 同步更新两者，不用 watcher 相互覆盖（不变式 4）。
// 仅切换新任务草稿，不改 task/result（不变式 1）；T505 起按 platformState.draft 加载 schema/城市。
const platformState = createPlatformState(DEFAULT_PLATFORM);


const draftPlatform = ref<Platform>(platformState.draft);
// T505：schema / 城市目录加载器。带请求序号 + AbortController + 响应平台校验，
// 旧响应晚到不会覆盖当前平台（platform-schema.md L151-156）。

// T505：schema / 城市目录加载器。带请求序号 + AbortController + 响应平台校验，
// 旧响应晚到不会覆盖当前平台（platform-schema.md L151-156）。
const schemaLoader = createSchemaLoader();


const cityLoader = createCityCatalogLoader();


const schemaRef = ref<PlatformFilterSchema | null>(null);


const cityCatalogRef = ref<PlatformCityCatalog | null>(null);


const schemaBusy = ref(false);


const cityCatalogBusy = ref(false);
// T513：草稿平台 schema 标记 enabled_for_new_tasks=false 时，禁用新建任务入口。
// 平台注册表权威来自后端；前端只读 schema 投影，不猜原因（platform-schema.md L222）。
// 用 draftPlatform ref（响应式）；platformState.draft 是普通闭包 getter，不触发追踪。

// T513：草稿平台 schema 标记 enabled_for_new_tasks=false 时，禁用新建任务入口。
// 平台注册表权威来自后端；前端只读 schema 投影，不猜原因（platform-schema.md L222）。
// 用 draftPlatform ref（响应式）；platformState.draft 是普通闭包 getter，不触发追踪。
const draftPlatformDisabled = computed(() => Boolean(
  schemaRef.value && schemaRef.value.platform === draftPlatform.value && schemaRef.value.enabled_for_new_tasks === false,
));


const pendingPlatformSwitch = ref<Platform | null>(null);


const nationalScopeConfirm = ref<"scrape" | "one-click" | null>(null);


const steps = [
  { id: "upload", label: "上传简历" },
  { id: "search", label: "广泛抓取" },
  { id: "screen", label: "AI 筛选" },
  { id: "results", label: "查看结果" },
];


const stepCopy: Record<StepId, { eyebrow: string; title: string; description: string }> = {
  upload: {
    eyebrow: "01 · 建立本轮标准",
    title: "让 AI 先读懂你的简历",
    description: "支持 TXT、PDF、DOCX。分析后仍由你确认搜索范围和筛选条件。",
  },
  search: {
    eyebrow: "02 · 广泛发现",
    title: "确认关键词与城市",
    description: "这一步只按关键词和城市抓取，不提前缩窄岗位池。",
  },
  screen: {
    eyebrow: "03 · 两阶段判断",
    title: "确认七类筛选条件",
    description: "先按列表字段粗筛，再抓取 JD 与简历画像精筛。",
  },
  results: {
    eyebrow: "04 · 集中决策",
    title: "查看与整理岗位",
    description: "匹配、不匹配、待确认与粗筛剔除分开展示。",
  },
};


const activeStep = ref<StepId>("upload");


const analysisReady = ref(false);


const selectedFile = ref<File | null>(null);


const aiConsent = ref(false);


const dragActive = ref(false);


const uploadBusy = ref(false);


const resumeError = ref("");


const keywords = ref<Array<{ word: string; recommended: boolean }>>([]);


const selectedKeywords = ref<string[]>([]);


const customKeyword = ref("");


const cityText = ref("");


const locationDraft = useLocationDraft();


const customCity = ref("");


const fieldLabels = ref<Record<string, FieldLabel>>({});
// T506：筛选草稿分平台独立保存（platform-schema.md L139）。
// boss.stage 与 zhilian.company_nature 互不串用；公共字段（salary/experience/...）
// 也按平台隔离，避免切换平台时把 A 平台不支持的值带给 B 平台。

// T506：筛选草稿分平台独立保存（platform-schema.md L139）。
// boss.stage 与 zhilian.company_nature 互不串用；公共字段（salary/experience/...）
// 也按平台隔离，避免切换平台时把 A 平台不支持的值带给 B 平台。
const filterValues = ref<Record<Platform, Record<string, string[]>>>({
  boss: {},
  zhilian: {},
});


const profileSummary = ref("");
// B033：画像事实（隐藏层）随简历分析产生，随筛选任务透传后端落库，界面不展示。

// B033：画像事实（隐藏层）随简历分析产生，随筛选任务透传后端落库，界面不展示。
const profileFacts = ref<Record<string, unknown>>({});
// B009：保存最近一次简历分析的中文语义，切平台时按新 schema 重投影。

// B009：保存最近一次简历分析的中文语义，切平台时按新 schema 重投影。
const resumeAnalysis = ref<AnalyzeResponse | null>(null);


const appliedResumePlatforms = ref<Set<Platform>>(new Set());


const scrapeTaskId = ref("");


const scrapeBusy = ref(false);


const scrapeSnapshot = ref<TaskSnapshot | null>(null);


const screenBusy = ref(false);


const pausingScreen = ref(false);


const switchAccounts = ref<Array<{ id: string; name: string }>>([]);


const switchAccountId = ref("");


const screenSnapshot = ref<TaskSnapshot | null>(null);


const screenTaskId = ref("");
// 待确认项「全部重抓」状态

// 待确认项「全部重抓」状态
const recrawlBusy = ref(false);


const recrawlTaskId = ref("");


const recrawlSnapshot = ref<TaskSnapshot | null>(null);

const recrawlRetryCount = ref<0>(0);


const scrapeCompleted = ref(false);


const resultLoaded = ref(false);
// 结束保存后的内存关闭标记：与持久化 round_context 一起驱动“本轮已结束保存”态。

// 结束保存后的内存关闭标记：与持久化 round_context 一起驱动“本轮已结束保存”态。
const finishedPartial = ref(false);
// “全部”视图重抓的平台选择引导：展示各平台待确认数量，数量为 0 的平台禁用。

// “全部”视图重抓的平台选择引导：展示各平台待确认数量，数量为 0 的平台禁用。
const recrawlPlatformGuide = ref<{ boss: number; zhilian: number } | null>(null);


const exportBusy = ref(false);


const finishSaveBusy = ref(false);


const cancelBusy = ref(false);


const historyScreenBusy = ref(false);
// 刷新后接回任务时显示的恢复提示条；任务结束后清空
const restoredTaskHint = ref("");
// 025 反馈：恢复提示改为悬浮浮窗，8s 自动关闭，不再钉顶占位推挤页面
let restoreHintTimer: ReturnType<typeof setTimeout> | undefined;
watch(restoredTaskHint, (value) => {
  if (restoreHintTimer !== undefined) clearTimeout(restoreHintTimer);
  restoreHintTimer = undefined;
  if (value) {
    restoreHintTimer = setTimeout(() => {
      restoredTaskHint.value = "";
    }, 8000);
  }
});
// 切片7：从 DB 恢复的 paused 任务 run_id（无内存工作线程，不能 poll）

// 切片7：从 DB 恢复的 paused 任务 run_id（无内存工作线程，不能 poll）
const pausedRunId = ref("");
// 服务重启打断的 AI 筛选任务：恢复后优先展示续跑入口，不被旧历史结果覆盖

// 服务重启打断的 AI 筛选任务：恢复后优先展示续跑入口，不被旧历史结果覆盖
const interruptedRunId = ref("");


const pipelineResult = ref<PipelineResult | null>(null);


const pipelineResultRunId = ref("");
// 结果页平台筛选（全部/BOSS/智联）：纯展示层过滤，不改草稿/任务身份（不变式 1）。

// 结果页平台筛选（全部/BOSS/智联）：纯展示层过滤，不改草稿/任务身份（不变式 1）。
const resultPlatformFilter = ref<"all" | "boss" | "zhilian">("all");
// specs/004：结果加载代次。pipelineResult 被新 run 结果替换时递增，
// JobWorkspace 据此重置列表筛选/排序（切分类/切平台不重置，contracts §6 D3）。

// specs/004：结果加载代次。pipelineResult 被新 run 结果替换时递增，
// JobWorkspace 据此重置列表筛选/排序（切分类/切平台不重置，contracts §6 D3）。
const resultEpoch = ref(0);
// B074：重抓胶囊「暂不处理」隐藏态（会话内共享，组件卸载重建不丢）。
// 仅按 resultEpoch（新结果重载）复位，不按 count 归零复位——
// 平台/页签切换导致的待确认数抖动不会让胶囊重弹。
const recrawlCapsuleDismissed = ref(false);

function dismissRecrawlCapsule(): void {
  recrawlCapsuleDismissed.value = true;
}

watch(resultEpoch, () => {
  recrawlCapsuleDismissed.value = false;
});
// 合并载入时每个平台各自的结果来源 run：单平台视图下“全部重抓”/导出用对应 run。

// 合并载入时每个平台各自的结果来源 run：单平台视图下“全部重抓”/导出用对应 run。
const resultRunIds = ref<{ boss: string; zhilian: string }>({ boss: "", zhilian: "" });
// 历史轮次：抽屉状态由独立 composable 持有，历史模式状态留在本视图。

// 历史轮次：抽屉状态由独立 composable 持有，历史模式状态留在本视图。
const historyStore = useResultHistory();

const {
  open: historyOpen,
  items: historyItems,
  loading: historyLoading,
  error: historyError,
  deleting: historyDeleting,
  deleteTarget: historyDeleteTarget,
  detail: historyDetail,
  show: showHistory,
  hide: hideHistory,
  openRound: openHistoryRound,
  backToLatest: historyBackToLatest,
  confirmDelete: confirmHistoryDelete,
  cancelDelete: cancelHistoryDelete,
  deleteRound: deleteHistoryRound,
  archiveAllCurrentResults: archiveHistoryLatest,
} = historyStore;


const historyRound = ref<{ runId: string; platform: Platform; status: string; jobCount: number } | null>(null);
// 035：从历史「回到最新」的过渡标记：过渡期间切到 04 页不得触发「已结束」置位。
const returningFromHistory = ref(false);
// 035：后台任务跑完时用户在看历史的顶部冒泡提示状态。
const taskCompletedToast = ref<{ visible: boolean }>({ visible: false });


const platformBeforeHistory = ref<Platform | null>(null);


const historyMode = computed(() => Boolean(historyRound.value));
// B038：当前展示轮的次级状态。'' = 无轮 / 'scraped_only' = 已抓取未筛选 /
// 其它 = AI 筛选轮。驱动 04 页"待筛选"单列表模式，岗位 verdict 本身保持无判定。

// B038：当前展示轮的次级状态。'' = 无轮 / 'scraped_only' = 已抓取未筛选 /
// 其它 = AI 筛选轮。驱动 04 页"待筛选"单列表模式，岗位 verdict 本身保持无判定。
const currentRoundStatus = ref("");


const isScrapedOnly = computed(() => currentRoundStatus.value === "scraped_only");


const historyStatusText = computed(() => historyRound.value
  ? historyStatusLabel(historyRound.value.status, historyRound.value.jobCount)
  : "");


const historyProfileText = computed(() => String(historyDetail.value?.result?.profile_summary || ""));


const activeCategory = ref<ResultCategory>("matched");


const rejectedIds = ref(new Set<string>());


const feedbackBusyIds = ref(new Set<string>());


const jdBusyIds = ref(new Set<string>());


const advancedBusy = ref(false);


const executionSelection = ref<ExecutionSelection>("custom");


const scopePreview = ref<FrozenSearchScope | null>(null);


const scopePreviewBusy = ref(false);

const scopePreviewReqId = ref<0>(0);


const advancedSettings = ref<Record<string, number | string>>({
  pages: 3,
  inter_combo_delay: 10,
  detail_batch_size: 15,
  detail_interval: 2,
  detail_reset_every: 4,
  detail_batch_cooldown: 5,
  detail_tab_pool_size: 5,
  screen_batch_size: 50,
  screen_concurrency: 5,
  match_batch_size: 4,
  match_concurrency: 10,
});
// 字段合法范围（与 input 的 min/max 保持一致）。失焦/回车时才钳到边界，
// 输入过程中不干预，让用户自由编辑。

// 字段合法范围（与 input 的 min/max 保持一致）。失焦/回车时才钳到边界，
// 输入过程中不干预，让用户自由编辑。
const advancedRanges = ref<Record<string, [number, number]>>({
  // 024：pages 范围收紧 1~200（对齐后端 _MAX_PLANNED_PAGES 上限）
  pages: [1, 200],
  inter_combo_delay: [5, 120],
  detail_batch_size: [1, Number.MAX_SAFE_INTEGER],
  detail_interval: [2, 15],
  detail_reset_every: [2, 10],
  detail_batch_cooldown: [5, 60],
  detail_tab_pool_size: [1, 10],
  screen_batch_size: [1, 100],
  screen_concurrency: [1, 10],
  match_batch_size: [1, 20],
  match_concurrency: [1, 10],
});


const pagesValue = computed(() => Number(advancedSettings.value.pages || 3));


const executionModeLabels: Record<ExecutionSelection, string> = {
  stable: "稳定",
  balanced: "平衡",
  extreme: "极限",
  custom: "自定义",
};


const executionModeSummary = computed(() => {
  const delay = Number(advancedSettings.value.inter_combo_delay || 0);
  const batch = Number(advancedSettings.value.detail_batch_size || 0);
  const screen = Number(advancedSettings.value.screen_concurrency || 0);
  const match = Number(advancedSettings.value.match_concurrency || 0);
  return `当前模式：${executionModeLabels[executionSelection.value] || "自定义"} · 组合延迟 ${delay} 秒 · 详情每批 ${batch} 个 · 粗筛 ${screen} 路并发 · 精筛 ${match} 路并发`;
});


const screenPanelOpen = ref(false);


const oneClickOpen = ref(false);


const oneClickGroups = computed<OneClickFilterGroup[]>(() => filterGroups.value);


const hasOldResult = computed(() => resultLoaded.value && Boolean(pipelineResult.value));


const autoScreenArmed = ref(false);


const autoScreenFields = ref<Record<string, string[]>>({});


const autoScreenProfile = ref("");


const profileError = ref("");


const profileInputEl = ref<HTMLTextAreaElement | null>(null);


const profileConfirmed = ref(false);

// 任意 pipeline 任务占用中（运行/暂停/待恢复）都禁止再启动新任务。

// 任意 pipeline 任务占用中（运行/暂停/失败/中断待处理）都禁止再启动新任务。
// 035：与 hasLiveTaskState 口径对齐——failed/interrupted 快照也视为未结束，防止
// 「上传简历被挡、开始抓取不挡」等入口行为打架。
const pipelineBusy = computed(() => Boolean(
  scrapeBusy.value || screenBusy.value || recrawlBusy.value
  || pausedRunId.value || interruptedRunId.value
  || [scrapeSnapshot.value?.status, screenSnapshot.value?.status, recrawlSnapshot.value?.status]
    .some((s) => s && ["paused", "failed", "interrupted"].includes(String(s))),
));


const oneClickDisabled = computed(() => Boolean(draftPlatformDisabled.value || pipelineBusy.value));
// 步骤 2 两个面板（关键词配置 / 高级执行设置）共用同一受控状态：
// 默认收拢、手动展开/收起联动（一个 ref 天然同步两卡）；开始抓取后自动收拢。

// 步骤 2 两个面板（关键词配置 / 高级执行设置）：
// 宽屏双栏时联动开关（一个 ref 天然同步两卡）；窄屏单列时各自独立，
// 由 DiscoveryView 按 matchMedia(1050) 分别控制联动/独立。
const searchPanelsOpen = ref(false);
const advancedPanelsOpen = ref(false);

const pollTimer = ref<number | undefined>(undefined);


const scopeLocked = computed(() => Boolean(
  scrapeBusy.value || screenBusy.value || recrawlBusy.value || pausedRunId.value
  || activeStep.value === "screen" || activeStep.value === "results"
  || historyMode.value,
));


const enabledSteps = computed<StepId[]>(() => {
  if (historyMode.value) return ["results"];
  const enabled: StepId[] = ["upload"];
  if (analysisReady.value) enabled.push("search");
  if (scrapeCompleted.value) enabled.push("screen");
  // 结果页只在任务真正结束后开放；AI 筛选暂停中任务未结束，04 保持不可进。
  if (resultLoaded.value && screenSnapshot.value?.status !== "paused") enabled.push("results");
  return enabled;
});


const completedSteps = computed<StepId[]>(() => {
  const completed: StepId[] = [];
  if (analysisReady.value) completed.push("upload");
  if (scrapeCompleted.value) completed.push("search");
  if (resultLoaded.value) completed.push("screen");
  return completed;
});


const currentCopy = computed(() => stepCopy[activeStep.value]);


const cityList = computed(() => cityText.value
  .replaceAll("，", ",")
  .split(",")
  .map((city) => city.trim())
  .filter(Boolean));


const effectiveSearchCities = computed(() => cityList.value.length ? cityList.value : ["全国"]);
// T505：filterGroups 由当前已加载 schema 派生（platform-schema.md L147）。
// 旧 fieldLabels 不再驱动筛选 UI；T507 起由 analyzeResume 按当前 schema 投影建议。
// 每组自带一个“清空键”（BOSS 的 value="0"，智联的“不限/全部”）：
// 点它等于不选任何值（空数组，提交时该字段不下发），不再另加内置“不限”芯片造成重复。

// T505：filterGroups 由当前已加载 schema 派生（platform-schema.md L147）。
// 旧 fieldLabels 不再驱动筛选 UI；T507 起由 analyzeResume 按当前 schema 投影建议。
// 每组自带一个“清空键”（BOSS 的 value="0"，智联的“不限/全部”）：
// 点它等于不选任何值（空数组，提交时该字段不下发），不再另加内置“不限”芯片造成重复。
const FILTER_SENTINEL_LABELS = new Set(["不限", "全部"]);


const filterGroups = computed(() => {
  const schema = schemaRef.value;
  if (!schema) return [];
  return schema.fields
    .map((field) => {
      const sentinelOpt = field.options.find(
        (opt) => opt.value === "0" || FILTER_SENTINEL_LABELS.has(opt.label),
      );
      return {
        key: field.key,
        label: field.label,
        multiple: field.multiple,
        sentinel: sentinelOpt ? { label: sentinelOpt.label, code: sentinelOpt.value } : null,
        options: field.options
          .filter((opt) => !sentinelOpt || opt.value !== sentinelOpt.value)
          .map((opt) => [opt.label, opt.value] as [string, string]),
      };
    })
    .filter((group) => group.options.length || group.sentinel);
});


const searchSummary = computed(() => {
  const kw = selectedKeywords.value.length;
  const locCount = locationDraft.allLocations(draftPlatform.value, cityList.value).length;
  const ct = locCount || cityList.value.length || 1;
  const parts: string[] = [];
  parts.push(kw && ct ? `${kw}×${ct}=${kw * ct}组` : "未配置");
  parts.push(profileSummary.value.trim() ? "画像已填" : "画像未填");
  return parts.join(" · ");
});


const screenSummaryChips = computed(() => {
  const chips: { label: string; value: string }[] = [];
  const drafts = filterValues.value[draftPlatform.value];
  filterGroups.value.forEach((group) => {
    const values = drafts[group.key] || [];
    if (!values.length) return;
    // B011：未知值不显示数字编号，直接省略该胶囊内容。
    const labels = values
      .map((code) => group.options.find(([, optCode]) => optCode === code)?.[0])
      .filter((label): label is string => Boolean(label));
    if (!labels.length) return;
    chips.push({ label: group.label, value: labels.join(" / ") });
  });
  return chips;
});

// 分类基于当前平台过滤后的结果：页签计数跟随筛选联动。
// 过滤逻辑抽到 discovery.ts 纯函数（filterPipelineResultByPlatform），
// 切换筛选只影响展示层派生，不触碰 pipelineResult 本体。
const filteredPipelineResult = computed<PipelineResult>(() =>
  filterPipelineResultByPlatform(pipelineResult.value || {}, resultPlatformFilter.value),
);


const groups = computed(() => partitionPipelineResult(filteredPipelineResult.value));
// “全部”视图重抓引导按岗位自身平台统计待确认数量，不按当前草稿/结果 run 猜。

// “全部”视图重抓引导按岗位自身平台统计待确认数量，不按当前草稿/结果 run 猜。
const uncertainByPlatform = computed(() => {
  const jobs = groups.value.uncertain;
  return {
    boss: jobs.filter((job) => job.platform === "boss").length,
    zhilian: jobs.filter((job) => job.platform === "zhilian").length,
  };
});


const resultTabs = computed(() => {
  if (isScrapedOnly.value) {
    // B038：未筛选轮只展示单"待筛选"列表，不经过 verdict 分类。
    const total = (filteredPipelineResult.value.jobs || []).length;
    return [{ id: "matched" as const, label: "待筛选", count: total }];
  }
  return [
    { id: "matched" as const, label: "匹配", count: groups.value.matched.length },
    { id: "unmatched" as const, label: "不匹配", count: groups.value.unmatched.length },
    { id: "uncertain" as const, label: "待确认", count: groups.value.uncertain.length },
    { id: "dropped" as const, label: "已筛除", count: groups.value.dropped.length },
  ];
});


const currentJobs = computed(() => {
  if (isScrapedOnly.value) return filteredPipelineResult.value.jobs || [];
  return groups.value[activeCategory.value];
});


const currentEmptyMessage = computed(() => isScrapedOnly.value
  ? "没有待筛选的岗位"
  : ({
    matched: "没有明确匹配的岗位",
    unmatched: "没有明确不匹配的岗位",
    uncertain: "没有需要人工确认的岗位",
    dropped: "没有在粗筛阶段被移除的岗位",
  } as Record<ResultCategory, string>)[activeCategory.value]);


const COMPLETED_TASK_STATUSES = new Set([
  "done",
  "completed",
  "completed_with_pending",
  "partial",
]);


const SPEED_FIELDS = [
  "pages", "inter_combo_delay", "detail_batch_size", "detail_interval",
  "detail_reset_every", "detail_batch_cooldown",
  "detail_tab_pool_size", "screen_batch_size",
  "screen_concurrency", "match_batch_size", "match_concurrency",
] as const;

// 指数退避：7 次 / 64s 上限。前 5 次快速重试（4s→8s→16s→32s→64s），
// 后 2 次保持 64s，总等待约 4 分钟。达上限后主动放弃并提示用户。
const POLL_MAX_RETRIES = 7;


const POLL_BASE_DELAY = 4000;


const POLL_MAX_DELAY = 64000;

const pollRetryCount = ref<0>(0);

// ---------------------------------------------------------------------------
// 轨迹浮窗：大卡片收敛为“查看轨迹”小按钮，点击后居中弹窗展示全部内容
// ---------------------------------------------------------------------------
const lifecycleDialogOpen = ref(false);


const lifecycleDialogJob = ref<JobItem | null>(null);

// ---------------------------------------------------------------------------
// 顶栏本轮状态胶囊数据（纯派生，不发请求）：
// 四态按 spec FR-013 优先级判定；平台优先取任务自身平台（恢复任务快照携带），
// 缺省时用草稿平台。空闲常驻（idle），不再上抛 null（spec FR-012）。
// ---------------------------------------------------------------------------
const roundStatusPayload = computed<CapsuleStatusPayload | null>(() => {
  const platform: Platform = scrapeSnapshot.value?.platform
    || screenSnapshot.value?.platform
    || draftPlatform.value;

  // ---- attention（最高）：暂停 / 出错 / 中断 ----
  const snapshots = [scrapeSnapshot.value, screenSnapshot.value, recrawlSnapshot.value];
  const failed = snapshots.find((s) => s && String(s.status) === "failed");
  if (failed) {
    return {
      platform, phase: "scraping" as const, judged: 0, scope: platform,
      capsule: {
        state: "attention", platform,
        attention: { kind: "error", message: failed.error || "任务执行出错" },
      },
    };
  }
  const hasPaused = pausedRunId.value || interruptedRunId.value
    || snapshots.some((s) => s && (String(s.status) === "paused" || String(s.status) === "interrupted"));
  if (hasPaused) {
    return {
      platform, phase: "scraping" as const, judged: 0, scope: platform,
      capsule: {
        state: "attention", platform,
        attention: { kind: "paused", message: "任务已暂停，请处理后继续" },
      },
    };
  }

  // ---- running：抓取 / 筛选 / 补抓进行中 ----
  const scrapeLive = scrapeBusy.value || recrawlBusy.value
    || snapshots.some((s) => s && ["running", "queued"].includes(String(s.status)));
  const screenLive = screenBusy.value
    || Boolean(screenSnapshot.value && ["running", "queued"].includes(String(screenSnapshot.value.status)));
  if (scrapeLive || screenLive) {
    const phase = scrapeLive ? "scraping" as const : "screening" as const;
    const snapshot = scrapeLive
      ? (scrapeSnapshot.value || recrawlSnapshot.value)
      : screenSnapshot.value;
    const progress = taskProgressFromSnapshot(snapshot);
    return {
      platform, phase, judged: progress.done, scope: platform,
      capsule: { state: "running", platform, progress: { phase, ...progress } },
    };
  }

  // ---- completed：有结果（当前轮或历史轮）----
  if (historyRound.value) {
    if (isScrapedOnly.value) {
      const total = historyRound.value.jobCount;
      return {
        platform: historyRound.value.platform, phase: "scraped" as const, judged: total, scope: "history" as const,
        capsule: {
          state: "completed", platform: historyRound.value.platform,
          results: { matched: total, pending: 0 },
        },
      };
    }
    const counts = resultCountsFromPipeline(pipelineResult.value);
    const g = groups.value;
    const judged = g.matched.length + g.unmatched.length + g.uncertain.length + g.dropped.length;
    return {
      platform: historyRound.value.platform, phase: "judged" as const, judged, scope: "history" as const,
      capsule: {
        state: "completed", platform: historyRound.value.platform,
        results: counts,
      },
    };
  }
  if (resultLoaded.value && pipelineResult.value) {
    const scope = resultPlatformFilter.value === "all" ? "all" as const : resultPlatformFilter.value;
    if (isScrapedOnly.value) {
      const total = (filteredPipelineResult.value.jobs || []).length;
      return {
        platform, phase: "scraped" as const, judged: total, scope,
        capsule: {
          state: "completed", platform,
          results: { matched: total, pending: 0 },
        },
      };
    }
    const counts = resultCountsFromPipeline(pipelineResult.value);
    const g = groups.value;
    const judged = g.matched.length + g.unmatched.length + g.uncertain.length + g.dropped.length;
    return {
      platform, phase: "judged" as const, judged, scope,
      capsule: { state: "completed", platform, results: counts },
    };
  }

  // ---- idle（常驻，最低优先级）：无任务无结果 ----
  return {
    platform, phase: "scraped" as const, judged: 0, scope: platform,
    capsule: { state: "idle", platform },
  };
});

// ---------------------------------------------------------------------------
// 036 B088：消费胶囊点击导航信号（App.vue 经 requestCapsuleNavigation 派发）。
// home → 01 上传页；task → 任务真实进度页（liveTaskStep 优先）；results → 04；
// attention → 处理现场（liveTaskStep 优先）。
//
// 历史模式：不在此同步置空 historyRound/returningFromHistory——直接调
// historyBackToLatest()（state.detail=null），由 DiscoveryView 既有
// watch(historyDetail) 分支触发 returnToLatest() 完整清理（还原草稿平台、
// 重置结果页筛选、重载最新结果），避免跳过清理残留历史轮状态（SC-010）。
// returnToLatest 内部按 liveTaskStep 或结果页落点设置 activeStep。
// ---------------------------------------------------------------------------
watch(capsuleNavigationTarget, (target) => {
  if (!target) return;
  capsuleNavigationTarget.value = null;  // 消费本次请求
  if (historyMode.value) {
    historyBackToLatest();
    return;
  }
  const liveStep = deriveLiveTaskStep({
    scrapeBusy: scrapeBusy.value,
    scrapeSnapshot: scrapeSnapshot.value,
    screenBusy: screenBusy.value,
    screenSnapshot: screenSnapshot.value,
    recrawlBusy: recrawlBusy.value,
    recrawlSnapshot: recrawlSnapshot.value,
    pausedRunId: pausedRunId.value,
    interruptedRunId: interruptedRunId.value,
  });
  if (target === "home") {
    activeStep.value = "upload";
  } else if (target === "task") {
    activeStep.value = liveStep || "search";
  } else if (target === "results") {
    activeStep.value = "results";
  } else if (target === "attention") {
    activeStep.value = liveStep || "screen";
  }
});

return {
  WORKFLOW_STATE_VERSION,
  workflowStateKey,
  workflowStateRestored,
  unfinishedWorkflowRestored,
  resultsPageSeen,
  restoredWorkflowSnapshot,
  activeTaskRestored,
  LOGIN_ERROR_CODES,
  loginGuide,
  platformState,
  draftPlatform,
  schemaLoader,
  cityLoader,
  schemaRef,
  cityCatalogRef,
  schemaBusy,
  cityCatalogBusy,
  draftPlatformDisabled,
  pendingPlatformSwitch,
  nationalScopeConfirm,
  steps,
  stepCopy,
  activeStep,
  analysisReady,
  selectedFile,
  aiConsent,
  dragActive,
  uploadBusy,
  resumeError,
  keywords,
  selectedKeywords,
  customKeyword,
  cityText,
  locationDraft,
  customCity,
  fieldLabels,
  filterValues,
  profileSummary,
  profileFacts,
  resumeAnalysis,
  appliedResumePlatforms,
  scrapeTaskId,
  scrapeBusy,
  scrapeSnapshot,
  screenBusy,
  pausingScreen,
  switchAccounts,
  switchAccountId,
  screenSnapshot,
  screenTaskId,
  recrawlBusy,
  recrawlTaskId,
  recrawlSnapshot,
  recrawlRetryCount,
  scrapeCompleted,
  resultLoaded,
  finishedPartial,
  recrawlPlatformGuide,
  exportBusy,
  finishSaveBusy,
  cancelBusy,
  historyScreenBusy,
  restoredTaskHint,
  pausedRunId,
  interruptedRunId,
  pipelineResult,
  pipelineResultRunId,
  resultPlatformFilter,
  resultEpoch,
  recrawlCapsuleDismissed,
  dismissRecrawlCapsule,
  resultRunIds,
  historyStore,
  historyRound,
  platformBeforeHistory,
  historyMode,
  returningFromHistory,
  taskCompletedToast,
  currentRoundStatus,
  isScrapedOnly,
  historyStatusText,
  historyProfileText,
  activeCategory,
  rejectedIds,
  feedbackBusyIds,
  jdBusyIds,
  advancedBusy,
  executionSelection,
  scopePreview,
  scopePreviewBusy,
  scopePreviewReqId,
  advancedSettings,
  advancedRanges,
  pagesValue,
  executionModeLabels,
  executionModeSummary,
  screenPanelOpen,
  oneClickOpen,
  oneClickGroups,
  hasOldResult,
  autoScreenArmed,
  autoScreenFields,
  autoScreenProfile,
  profileError,
  profileInputEl,
  profileConfirmed,
  pipelineBusy,
  oneClickDisabled,
  searchPanelsOpen,
  advancedPanelsOpen,
  pollTimer,
  scopeLocked,
  enabledSteps,
  completedSteps,
  currentCopy,
  cityList,
  effectiveSearchCities,
  FILTER_SENTINEL_LABELS,
  filterGroups,
  searchSummary,
  screenSummaryChips,
  filteredPipelineResult,
  groups,
  uncertainByPlatform,
  resultTabs,
  currentJobs,
  currentEmptyMessage,
  COMPLETED_TASK_STATUSES,
  SPEED_FIELDS,
  POLL_MAX_RETRIES,
  POLL_BASE_DELAY,
  POLL_MAX_DELAY,
  pollRetryCount,
  lifecycleDialogOpen,
  lifecycleDialogJob,
  roundStatusPayload,
  historyOpen,
  historyItems,
  historyLoading,
  historyError,
  historyDeleting,
  historyDeleteTarget,
  historyDetail,
  showHistory,
  hideHistory,
  openHistoryRound,
  historyBackToLatest,
  confirmHistoryDelete,
  cancelHistoryDelete,
  deleteHistoryRound,
  archiveHistoryLatest,
};
}

export type DiscoveryState = ReturnType<typeof useDiscoveryState>;

/** 035：未结束任务真实存在判定（浏览历史不改变任务状态；供开新一轮入口守卫复用）。 */
export function hasLiveTaskState(state: DiscoveryState): boolean {
  if (state.pausedRunId.value || state.interruptedRunId.value) return true;
  const liveStatuses = new Set(["running", "queued", "paused", "failed", "interrupted"]);
  for (const snap of [
    state.screenSnapshot.value,
    state.scrapeSnapshot.value,
    state.recrawlSnapshot.value,
  ]) {
    if (snap && liveStatuses.has(String(snap.status))) return true;
  }
  return false;
}

/** 035：跨域共享派生的最小判定面（接受任意携带 status 的快照形状）。 */
export interface LiveTaskProbe {
  scrapeBusy?: boolean;
  scrapeSnapshot?: { status?: string | null } | null;
  screenBusy?: boolean;
  screenSnapshot?: { status?: string | null } | null;
  recrawlBusy?: boolean;
  recrawlSnapshot?: { status?: string | null } | null;
  pausedRunId?: string;
  interruptedRunId?: string;
}

const LIVE_TASK_STATUSES = new Set(["running", "queued", "paused", "failed", "interrupted"]);

function snapshotLive(snapshot?: { status?: string | null } | null): boolean {
  return Boolean(snapshot && LIVE_TASK_STATUSES.has(String(snapshot.status || "")));
}

/**
 * 035：未结束任务的「真实进度页」只读派生（US2/US3 共享）：
 * 抓取活（运行/排队/暂停/失败/中断）→ "search"（02，抓取任务的真实进度页）；
 * 筛选/重抓活（含 pausedRunId/interruptedRunId）→ "screen"（03）；无活任务 → ""。
 * 抓取+筛选同时活时以抓取为准（一键链路筛选接续时抓取快照已终态，自然落 03）。
 */
export function deriveLiveTaskStep(probe: LiveTaskProbe): StepId | "" {
  if (probe.scrapeBusy || snapshotLive(probe.scrapeSnapshot)) return "search";
  if (
    probe.screenBusy || probe.recrawlBusy
    || snapshotLive(probe.screenSnapshot)
    || snapshotLive(probe.recrawlSnapshot)
    || probe.pausedRunId || probe.interruptedRunId
  ) return "screen";
  return "";
}

/** 035：liveTaskStep(state)——持有 state 的域（search/results 等）直接取用。 */
export function liveTaskStep(state: DiscoveryState): StepId | "" {
  return deriveLiveTaskStep({
    scrapeBusy: state.scrapeBusy.value,
    scrapeSnapshot: state.scrapeSnapshot.value,
    screenBusy: state.screenBusy.value,
    screenSnapshot: state.screenSnapshot.value,
    recrawlBusy: state.recrawlBusy.value,
    recrawlSnapshot: state.recrawlSnapshot.value,
    pausedRunId: state.pausedRunId.value,
    interruptedRunId: state.interruptedRunId.value,
  });
}

// 031 B8：emit/props 形状固定为类型，替代原未类型化的 emit 参数
// 签名（data-model E6 基线最后一处未类型化签名）。成员与 DiscoveryView 的
// defineEmits/defineProps 逐项对应，跨域调用从此受 vue-tsc 检查。
export interface DiscoveryEmit {
  (event: "notify", notice: Notice): void;
  (event: "profile-created", profile: CandidateProfile): void;
  (event: "job-feedback-changed", payload: { profileId: string; jobId: string }): void;
  (event: "round-status", payload: RoundStatusPayload | null): void;
  (event: "open-browser-accounts"): void;
}

export interface DiscoveryProps {
  profileId: string;
}

export type StepId = "upload" | "search" | "screen" | "results";

export type ResultCategory = "matched" | "unmatched" | "uncertain" | "dropped";

export type FieldLabel = [string, unknown, string | Record<string, string>];

export interface AnalyzeResponse {
  ok: boolean;
  fields: Record<string, unknown>;
  labels: Record<string, FieldLabel>;
  platform?: Platform;
  filter_schema_version?: number;
  semantic?: Record<string, string[]>;
}

export interface TaskSnapshot {
  status: "running" | "done" | "failed" | "paused" | "cancelled" | string;
  progress?: Record<string, unknown>;
  logs?: string[];
  error?: string;
  result?: PipelineResult;
  started_at?: number;
  finished_at?: number;
  // 切片7：统一状态接口字段（FR-037/SC-006）
  stage?: string;
  success_count?: number;
  fail_count?: number;
  unstarted_count?: number;
  total?: number;
  kept_count?: number;
  dropped_count?: number;
  pending_count?: number;
  source_total?: number;
  pause_info?: { error_code?: string; error_reason?: string } | null;
  execution_config?: Record<string, unknown> | null;
  scraped_count?: number;
  // T510：任务自身平台，供 TaskProgress 展示真实平台徽章（http-api.md L201）
  platform?: Platform;
  /** 一键链路标记：抓取任务完成后前端自动接续 AI 筛选。 */
  auto_screen?: boolean;
}

export interface OneClickLaunch {
 autoScreen?: boolean;
 fields?: Record<string, string[]>;
 profile?: string;
}

export interface AiScreenLaunch {
 consumeAutoScreen?: boolean;
 fields?: Record<string, string[]>;
 profile?: string;
}

export // 双平台合并加载：拉两个平台的 /api/latest-pipeline-result 并合并。
// 刷新路径（loadLatestResult）与实时任务完成路径（pollTask）共用，
// 保证两条路径行为一致（R2：实时路径只 set 单平台结果导致切平台显示 0）。
interface MergedLatestResult {
  merged: PipelineResult;
  newer: {
    platform: "boss" | "zhilian";
    data: {
      source_run_id?: string;
      status?: string;
      started_at?: number;
      finished_at?: number;
      execution_config?: Record<string, unknown> | null;
      result?: PipelineResult | null;
      scrape_task_id?: string;
      round_context?: Partial<RoundContext> | null;
    };
  };
  /** 025 B078：各平台最新轮状态（供完成态判定；无该平台轮则缺省）。 */
  platformStatuses?: Partial<Record<"boss" | "zhilian", string>>;
}

// ---------------------------------------------------------------------------
// 036 B088 顶栏胶囊（灵动岛）状态类型：四态 + 派生优先级
// attention > running > completed > idle（spec FR-013）。
// ---------------------------------------------------------------------------

/** 胶囊四态（多态并存取优先级最高一件，不堆叠）。 */
export type DynamicIslandState =
  | { state: "idle"; platform: Platform }
  | {
      state: "running";
      platform: Platform;
      progress: { phase: "scraping" | "screening"; done: number; total?: number };
    }
  | {
      state: "completed";
      platform: Platform;
      results: { matched: number; pending: number };
    }
  | {
      state: "attention";
      platform: Platform;
      attention: { kind: "paused" | "error" | "pending"; message: string };
    };

/** round-status 上抛 payload：既有展示字段 + 胶囊状态（App 供 DynamicIsland 消费）。 */
export interface CapsuleStatusPayload extends RoundStatusPayload {
  capsule: DynamicIslandState;
}

// ---------------------------------------------------------------------------
// 036 B088：顶栏胶囊进度/结果数字提取（纯函数，数据层唯一实现）。
// tasks/results 域 re-export 本函数，避免数据层反向依赖动作层。
// 进度：progress.current/success_count 为已处理数，total/source_total 为总数；
// total 未知（缺省/0）时省略分母（spec 边界 B088-进度数字缺失）。
// 结果：matched = 明确匹配；pending = 待确认（verdict 非 match/not_match/mismatch）。
// ---------------------------------------------------------------------------

export function taskProgressFromSnapshot(
  snapshot: TaskSnapshot | null,
): { done: number; total?: number } {
  if (!snapshot) return { done: 0 };
  const progress = (snapshot.progress || {}) as Record<string, unknown>;
  const doneRaw = progress.current ?? snapshot.success_count ?? snapshot.scraped_count;
  const totalRaw = progress.total ?? snapshot.total ?? snapshot.source_total;
  const done = typeof doneRaw === "number" && Number.isFinite(doneRaw) ? doneRaw : 0;
  const total = typeof totalRaw === "number" && totalRaw > 0 ? totalRaw : undefined;
  return total === undefined ? { done } : { done, total };
}

export function resultCountsFromPipeline(
  result: PipelineResult | null,
): { matched: number; pending: number } {
  if (!result) return { matched: 0, pending: 0 };
  const jobs = Array.isArray(result.jobs) ? result.jobs : [];
  let matched = 0;
  let pending = 0;
  for (const job of jobs) {
    // 与结果页 partitionPipelineResult 同源：match→匹配；not_match→不匹配；
    // 其余（uncertain/mismatch/缺省）全部计入待确认，保证胶囊数字与结果页一致（SC-009）。
    if (job.verdict === "match") matched += 1;
    else if (job.verdict !== "not_match") pending += 1;
  }
  return { matched, pending };
}