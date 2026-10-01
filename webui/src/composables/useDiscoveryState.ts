// 021 B8 T027：DiscoveryView 数据层（自 DiscoveryView.vue script 原样搬运）。
import { computed, reactive, ref, watch, type Ref } from "vue";

import {
  buildSearchScriptParams,
  createCityCatalogLoader,
  createPlatformState,
  createSchemaLoader,
  DEFAULT_PLATFORM,
  filterPipelineResultByPlatform,
  isNationwideCityName,
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
  IntegritySnapshot,
  ComboIssue,
  DynamicIslandState,
  IslandNavTarget,
  PageScene,
  ResumeAnalysisPhase,
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
import { useSearchDraftSlots } from "../composables/useSearchDraftSlots";
import { setHistoryProfile, useResultHistory } from "../composables/resultHistory";
import { reachableStep } from "./useIslandNavigation";

// ---------------------------------------------------------------------------
// 036 B088：胶囊点击导航信号（App.vue 经 DynamicIsland 派发，本域消费）。
// 用模块级 ref + requestCapsuleNavigation 而非 defineExpose：
// DiscoveryView.vue 是超限红线文件禁止修改，胶囊点击需要从 App 侧驱动
// Discovery 内部 activeStep，故经此模块级信号解耦（App 只发目标，本域响应）。
// ---------------------------------------------------------------------------
export type CapsuleNavigationTarget = IslandNavTarget | "task" | "attention";
/** 导航信号（Spec041）：消费方 useDiscoveryIslandBridge 由视图在 setup 里构造。 */
export const capsuleNavigationTarget = ref<CapsuleNavigationTarget | null>(null);
export type { DynamicIslandState } from "../types";

/** 请求顶栏胶囊导航（App.vue 在 DynamicIsland 点击时调用）。 */
export function requestCapsuleNavigation(target: CapsuleNavigationTarget): void {
  capsuleNavigationTarget.value = target;
}

// ---------------------------------------------------------------------------
// 暂停族（用户主动暂停 / 服务重启打断）的判定与中文口径——模块级唯一实现。
// 两条线共用同一条 attention 通道（kind 仍为 paused，胶囊状态枚举与导航落点不变），
// 但性质不能混着说：把「已中断」写成「已暂停」就是谎报，与同一屏的阶段卡和顶栏胶囊
// 互相打脸。这里只把胶囊分支已经读到的那一份状态换成中文口径——判活与状态权威仍是
// 那一份，不在此另起一套判定；用词与阶段卡的 stageStatusLabel 对齐（paused=已暂停、
// interrupted=已中断），两种性质各自的出路见 pausedFamilyIslandMessage。
// 顶栏胶囊与展开面板的通知行标题共读这一份：通知池只接性质，不再自己按 kind 猜。
// 出路也共读这一份：可恢复的暂停说「处理后继续」，被服务重启打断的说「开始新一轮」
// （状态词表：中断没有活体 worker，界面上不存在轨道级「继续」这个入口）。
// ---------------------------------------------------------------------------
const PAUSED_FAMILY_STATUSES = ["paused", "interrupted"] as const;
export type PausedFamilyFact = typeof PAUSED_FAMILY_STATUSES[number];

const PAUSED_FAMILY_LABELS: Record<PausedFamilyFact, string> = {
  paused: "任务已暂停",
  interrupted: "任务已中断",
};

/** 快照带暂停族状态才取该状态；其它状态（含缺失）一律空串，交回调用方既有判定。 */
function pausedFamilyStatusOf(
  snapshot: { status?: unknown } | null | undefined,
): PausedFamilyFact | "" {
  const status = String(snapshot?.status ?? "");
  return (PAUSED_FAMILY_STATUSES as readonly string[]).includes(status)
    ? status as PausedFamilyFact
    : "";
}

/** 短说法：通知池展开行的标题（同一行的详情用下面的完整说法）。 */
export function pausedFamilyNoticeTitle(fact: PausedFamilyFact): string {
  return PAUSED_FAMILY_LABELS[fact];
}

/** 完整说法：顶栏胶囊那一行的提示。 */
function pausedFamilyIslandMessage(fact: PausedFamilyFact): string {
  // 状态词表（SPEC 046 D-06）：中断是「无活体 worker」，轨道级「继续」在服务重启后
  // 必然失败、界面上也没有这个入口，唯一出路是开新一轮；能接续的只有用户主动暂停。
  // 出口在这里说清楚，不在页面上补一个做不到的按钮。
  return fact === "interrupted"
    ? `${PAUSED_FAMILY_LABELS[fact]}，请开始新一轮`
    : `${PAUSED_FAMILY_LABELS[fact]}，请处理后继续`;
}

export function useDiscoveryState(props: DiscoveryProps, emit: DiscoveryEmit) {


const WORKFLOW_STATE_VERSION = 2;


const workflowStateKey = computed(() => `career-scout-workflow:${props.profileId}`);
const profileId = computed(() => props.profileId);


const workflowStateRestored = ref(false);


const unfinishedWorkflowRestored = ref(false);


const resultsPageSeen = ref(false);


const restoredWorkflowSnapshot = ref<Record<string, any> | null>(null);


const activeTaskRestored = ref(false);

// Spec041：步骤页内部现场由 useDiscoverySceneState 保存；这里保留轮次快照，
// 让既有 workflow sessionStorage 通道在刷新时一并带上现场。
const sceneSnapshot = ref<PageScene | null>(null);

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

// Discovery 页面导航的唯一投影入口。单平台使用 legacy 业务事实；全部平台
// 由 Flow 把可达阶段投影到这里。页面、灵动岛和后台恢复都只经过下面的守卫，
// 不再各自维护一份“能不能进”的集合。
const flowReachableSteps = ref<Set<StepId> | null>(null);
// SPEC 046 V2 FR-011 / 契约「同一 Flow 的解锁集合只增不减」：Flow 可达水位。
// 它只记「这一页在本 Flow 内已经放行过」，用于同一条 Flow 的投影暂时缺位或回退
// （重新水合、轮询间隙、离开「全部」再回来）时不回锁。它不是第四份流程外壳状态：
// 外壳事实（Flow / Track / 结果）仍然只读服务端投影一处，水位既不上报后端也不写进
// 任何存档，只在内存里累积，并随 Flow 身份或换轮/换画像重置。
// 写入通道只有两条，都在这道守卫里：投影到达（带 Flow 身份）、守卫自己放行的落点
// （含刷新从现场存档恢复出的落点）。
const flowReachWatermark = ref<Set<StepId>>(new Set<StepId>());
// 水位所属的 Flow 身份：与投影一起写入，只用来判断「是不是同一条 Flow」。
// 空串 = 还没拿到过带身份的投影（此时水位只由落点喂过，任何 Flow 都视为同一条）。
const flowReachWatermarkOwner = ref("");
// Flow 归属：投影在场即为真。null（离开「全部」、Flow 不再由本页持有）即交回 legacy 分支。
const flowNavigationOwned = ref(false);
const navigationManualHold = ref(false);
// B096：并行 Flow 的活动 Track 是页面级占用事实，不属于任一平台草稿。
// 由页面把 parallelFlow.hasActiveTrack 投影进来，所有新任务/范围守卫共用。
const flowActive = ref(false);
// SPEC 046 判活口径：flowActive 说的是「这一轮还没结束」（锁范围、锁提交、决定落点），
// 不等于「此刻有活体 worker 在跑」。已中断/已暂停的轮次前者为真、后者为假。
// 由页面把 parallelFlow.hasLiveWorker 投影进来，判活（hasLiveTaskState）只认这个。
// null = 页面还没投影过活体事实（未经协调器投影的调用方，例如单平台传统链路的单元测试）：
// 这时没有「此刻无活体」这条否定证据，只能保守退回活动线。
const flowLiveWorker = ref<boolean | null>(null);


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


const locationDraft = useLocationDraft(profileId);


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


const resumeAnalysisPhase = ref<ResumeAnalysisPhase>("idle");


const resumeAnalysisLandOnReturn = ref<(() => void) | null>(null);


const resumeAnalysisReset = ref<(() => void) | null>(null);


const resumeAnalysisRestore = ref<
  ((phase: ResumeAnalysisPhase, error?: string, taskId?: string) => void) | null
>(null);


const appliedResumePlatforms = ref<Set<Platform>>(new Set());


const scrapeTaskId = ref("");


const scrapeBusy = ref(false);


// 抓取任务当前公共动作（暂停/继续）自身的请求状态；与 scrapeBusy 分离，
// 运行中的任务仍必须允许点击“暂停”。
const scrapeActionBusy = ref("");


const scrapeSnapshot = ref<TaskSnapshot | null>(null);


const screenBusy = ref(false);


const pausingScreen = ref(false);


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
// 新一轮代次：重置开始即失效旧轮的异步请求，避免旧轮结果在清空后回写。
const workflowEpoch = ref(0);

/**
 * Invalidate every workflow-owned async continuation.
 *
 * The epoch is shared by task recovery and round transitions.  Unmounting a
 * view uses the same invalidation boundary so a response that settles after
 * the view is gone cannot continue the old workflow internally.
 */
function invalidateWorkflowEpoch(): number {
  workflowEpoch.value += 1;
  return workflowEpoch.value;
}
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
// 最新轮的结果来源 run；保留平台键以兼容岗位动作和导出调用方。
const resultRunIds = ref<{ boss: string; zhilian: string }>({ boss: "", zhilian: "" });
// 历史轮次：抽屉状态由独立 composable 持有，历史模式状态留在本视图。

// 历史轮次：抽屉状态由独立 composable 持有，历史模式状态留在本视图。
const historyStore = useResultHistory();

// Spec041：历史列表/详情/删除/归档都限定当前求职画像；这里只做首次绑定，
// 切画像由 DiscoveryView 的画像 watch 调 setProfile 换槽（避免无实例
// 环境下创建不回收的 watcher）。
setHistoryProfile(props.profileId);

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


const historyRound = ref<{ runId: string; platform: Platform; status: string; jobCount: number; integrity?: IntegritySnapshot | null } | null>(null);
// 035：从历史「回到最新」的过渡标记：过渡期间切到 04 页不得触发「已结束」置位。
const returningFromHistory = ref(false);
// 035：后台任务跑完时用户在看历史的顶部冒泡提示状态。
const taskCompletedToast = ref<{ visible: boolean }>({ visible: false });


const platformBeforeHistory = ref<Platform | null>(null);


const historyMode = computed(() => Boolean(historyRound.value));
// Spec041 返工：浏览历史轮时，顶部平台段展示"正在看的那一轮平台"，但当前轮的
// 草稿平台 / 结果平台不被历史轮覆盖（历史轮平台只用于展示，退出即消失）。
const viewPlatform = computed<Platform>(() => historyRound.value?.platform || draftPlatform.value);
// 灵动岛从历史页跳回最新时，使用结果域的完整恢复流程；在结果域接线前
// 保留旧的本地退回动作作为安全兜底。
// Spec041 返工：返回值 = 当前轮真实落点步骤（退出没完成时为空，调用方不得改步骤）。
const capsuleReturnToLatest = ref<
  (() => StepId | null | void | Promise<StepId | null | void>) | null
>(null);
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


// Spec041 返工：搜索范围预览按平台各存一份（真实验收失败项三）。
// 切平台只读本平台的预览；旧平台的在飞请求由 scopePreviewReqId 与"请求平台
// 复核"双重作废，绝不把旧平台响应写到新平台。
const scopePreviewByPlatform = ref<Record<Platform, FrozenSearchScope | null>>({
  boss: null,
  zhilian: null,
});

const scopePreview = computed<FrozenSearchScope | null>({
  get: () => scopePreviewByPlatform.value[draftPlatform.value] ?? null,
  set: (value) => {
    scopePreviewByPlatform.value[draftPlatform.value] = value ?? null;
  },
});


const scopePreviewBusy = ref(false);

const scopePreviewReqId = ref<0>(0);

/** 供范围预览请求按"发起时的平台"落槽，避免旧平台响应写进新平台。 */
function setScopePreviewFor(platform: Platform, value: FrozenSearchScope | null): void {
  scopePreviewByPlatform.value[platform] = value ?? null;
}

// ---------------------------------------------------------------------------
// Spec041 后续（用户拍板 2026-09-13）：第 2 页输入"一份、两平台共用、一直在"。
//
// 这 6 项（keywords / selectedKeywords / customKeyword / cityText / customCity /
// profileSummary）是第 2 页的输入，也是一轮工作的"参数"：回到第 2 页、切平台再点执行
// 就是复用它们。它们统一放在 useSearchDraftSlots（通用树干）：两个平台同一份，
// 浏览器本地存一份 + 数据库按画像存一份（打开应用时 hydratePage2Draft 回填）。
// 第 3 页筛选草稿（filterValues）与区县草稿（locationDraft）仍按平台各存各的——
// 区县码各平台不同，不能通用。
// ---------------------------------------------------------------------------
const searchDraft = useSearchDraftSlots(profileId);
const searchDraftPlatform = ref<Platform>(draftPlatform.value);
const searchDraftLoaded = ref(false);

function saveSearchDraftFor(platform: Platform): void {
  searchDraft.set(platform, {
    keywords: keywords.value.map((item) => ({ ...item })),
    selectedKeywords: [...selectedKeywords.value],
    customKeyword: customKeyword.value,
    cityText: cityText.value,
    customCity: customCity.value,
    profileSummary: profileSummary.value,
  });
  searchDraftPlatform.value = platform;
}

/**
 * 把这份共用输入装进工作副本。
 *
 * 画像文本只在工作副本为空时回填：它还会从轮次/任务快照恢复（那些来源更权威），
 * 不能用空值把刚恢复出来的画像擦掉；其余字段逐字覆盖，用户清空也算数。
 */
function loadSearchDraftFor(platform: Platform): boolean {
  const slot = searchDraft.slot(platform);
  keywords.value = slot.keywords.map((item) => ({ ...item }));
  selectedKeywords.value = [...slot.selectedKeywords];
  customKeyword.value = slot.customKeyword;
  cityText.value = slot.cityText;
  customCity.value = slot.customCity;
  if (!profileSummary.value.trim() && slot.profileSummary) {
    profileSummary.value = slot.profileSummary;
  }
  searchDraftPlatform.value = platform;
  searchDraftLoaded.value = true;
  return searchDraft.hasStoredSlots();
}

/** 清工作副本但不回写（切画像用：新画像的输入不能被旧画像清掉）。 */
function clearSearchDraftRefs(): void {
  searchDraftLoaded.value = false;
  keywords.value = [];
  selectedKeywords.value = [];
  customKeyword.value = "";
  cityText.value = "";
  customCity.value = "";
}

/** 确保工作副本已挂上这份共用输入（恢复流程收尾调用）。 */
function ensureSearchDraftLoaded(): void {
  if (searchDraftLoaded.value) return;
  loadSearchDraftFor(draftPlatform.value);
}

/**
 * Spec041 后续：简历分析给出的关键词建议与平台无关，直接写进这份共用输入，
 * 切平台不用重新录一遍；城市仍由用户自己选（AI 不代填）。
 */
function mirrorSearchDraftKeywords(): void {
  searchDraft.set(draftPlatform.value, {
    keywords: keywords.value.map((item) => ({ ...item })),
    selectedKeywords: [...selectedKeywords.value],
  });
}

/**
 * 切平台：第 2 页输入两个平台共用（同一份），所以这里不保存、不装载，
 * 只作废旧平台在飞的范围预览，避免旧响应写进新平台。
 */
function switchSearchDraftPlatform(next: Platform): void {
  searchDraftPlatform.value = next;
  // 旧平台的范围预览请求即刻作废（新平台若无关键词不会发新请求，也必须失效）。
  scopePreviewReqId.value += 1;
  scopePreviewBusy.value = false;
}

watch(draftPlatform, (next) => {
  switchSearchDraftPlatform(next);
});

// 工作副本一变就落到这份共用输入（本地立即写 + 数据库防抖写）。
watch(
  [keywords, selectedKeywords, customKeyword, cityText, customCity, profileSummary],
  () => {
    if (!searchDraftLoaded.value) return;
    saveSearchDraftFor(searchDraftPlatform.value);
  },
  { deep: true },
);


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

// 任意 pipeline 任务占用中（运行/暂停）都禁止再启动新任务；失败/中断只保留
// 用户可见错误快照，不再把新任务入口锁死。
const pipelineBusy = computed(() => Boolean(
  scrapeBusy.value || screenBusy.value || recrawlBusy.value
  || flowActive.value
  || pausedRunId.value
  || [scrapeSnapshot.value?.status, screenSnapshot.value?.status, recrawlSnapshot.value?.status]
    .some((s) => s && String(s) === "paused"),
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
  || flowActive.value
  || activeStep.value === "screen" || activeStep.value === "results"
  || historyMode.value,
));

// 锁定原因必须与事实一致：scopeLocked 还包含「已在第 3/4 步」「正在看历史轮」，
// 这些时候没有任何任务在跑，一律提示「任务进行中」就是谎报。锁定范围不变，
// 只把原因按真实情况分层说清楚。
const scopeLockReason = computed(() => {
  if (!scopeLocked.value) return "";
  if (scrapeBusy.value || screenBusy.value || recrawlBusy.value || flowActive.value) {
    return "任务进行中，平台已锁定";
  }
  if (pausedRunId.value) return "任务已暂停，平台已锁定";
  if (historyMode.value) return "正在查看历史轮次，平台已锁定";
  return "本轮搜索范围已确认，返回第 2 步前平台已锁定";
});


const legacyEnabledSteps = computed<StepId[]>(() => {
  if (historyMode.value) return ["results"];
  const enabled: StepId[] = ["upload"];
  if (analysisReady.value) enabled.push("search");
  // Spec041 返工补丁：第 3 步跟随"本轮有没有抓取/结果现场"。切平台会清掉本轮抓取身份、
  // 但正在展示的结果仍在（真实现场：第 4 页还在），只认 scrapeCompleted 会把第 3 步
  // 灰成"点了没反应"，出现"四页在、三页没了"的死角。
  if (scrapeCompleted.value || resultLoaded.value) enabled.push("screen");
  // 结果页只在任务真正结束后开放；AI 筛选暂停中任务未结束，04 保持不可进。
  if (resultLoaded.value && screenSnapshot.value?.status !== "paused") enabled.push("results");
  return enabled;
});

const STEP_ORDER: StepId[] = ["upload", "search", "screen", "results"];

// Flow 归属判据（可达性单一来源的开关）：Flow 投影在场，或本轮流程活动线在场
// （Flow 外壳已成立、第一份投影还没到达）。两者都不成立才是「确实没有 Flow 归属」
// 的旧形态——单平台 legacy 链路，只有那条路径允许读 legacyEnabledSteps 与活体任务探针。
const hasFlowOwnership = computed(() => flowNavigationOwned.value || flowActive.value);

/** 把放行过的步骤并进水位（同一条 Flow 只增不减）。 */
function rememberFlowReach(steps: Iterable<StepId>): void {
  const next = new Set(flowReachWatermark.value);
  for (const step of steps) {
    if (STEP_ORDER.includes(step)) next.add(step);
  }
  if (next.size !== flowReachWatermark.value.size) flowReachWatermark.value = next;
}

/** 清空 Flow 可达水位（换轮、换画像、归属结束、投影换到另一条 Flow）。 */
function clearFlowReachWatermark(): void {
  flowReachWatermark.value = new Set<StepId>();
  flowReachWatermarkOwner.value = "";
}

/** 页面先后顺序里，某一步及其之前的全部步骤（「用户已经站在这一页」的可达证据）。 */
function stepPrefixThrough(step: StepId): StepId[] {
  const rank = STEP_ORDER.indexOf(step);
  return rank < 0 ? [] : STEP_ORDER.slice(0, rank + 1);
}

const enabledSteps = computed<StepId[]>(() => {
  if (historyMode.value) return ["results"];
  if (hasFlowOwnership.value) {
    // 状态所有权：页面可达性唯一来源是 Flow 投影一处，不再并 legacy 现场、
    // 也不再叠加活体任务探针（那是第二套「能不能进」的口径）。
    // 同一条 Flow 的投影暂时缺位或回退时沿用已到达的水位（只增不减，FR-011）；
    // 用户当前所在的这一页永远算已解锁——刷新恢复出的落点先于投影到达，
    // 撤场重来的 null 会清水位，这一段空窗不能把用户脚下这一页锁掉。
    // 01 发起页与 02 本轮关键词页是 Flow 自己的最小开放面（投影基线也从 02 起步），
    // 不属于任何 legacy 现场。
    const reached = new Set<StepId>(flowReachWatermark.value);
    const projected = flowReachableSteps.value;
    if (projected) for (const step of projected) reached.add(step);
    for (const step of stepPrefixThrough(activeStep.value)) reached.add(step);
    return STEP_ORDER.filter((step) => step === "upload" || step === "search" || reached.has(step));
  }
  // 没有 Flow 归属的旧形态（单平台 legacy 路径）：沿用本轮抓取/结果现场与活体探针。
  const reachable = new Set<StepId>(legacyEnabledSteps.value);
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
  if (liveStep) reachable.add(liveStep);
  return STEP_ORDER.filter((step) => reachable.has(step));
});

function setFlowReachableSteps(steps: Iterable<string> | null, flowId = ""): void {
  if (steps === null) {
    // 投影撤场 = 这条 Flow 不再由本页持有：可达性交回 legacy 分支，水位一并清空。
    flowNavigationOwned.value = false;
    flowReachableSteps.value = null;
    clearFlowReachWatermark();
    return;
  }
  const next = new Set(Array.from(steps).filter((step): step is StepId => STEP_ORDER.includes(step as StepId)));
  const id = String(flowId || "");
  // 投影换到另一条 Flow：上一轮的入口不带给这一轮，水位按 Flow 身份重新起步。
  // 身份未知（空串，未经协调器投影的调用方）时不猜，沿用同一份水位。
  if (id && flowReachWatermarkOwner.value && id !== flowReachWatermarkOwner.value) clearFlowReachWatermark();
  flowNavigationOwned.value = true;
  flowReachableSteps.value = next;
  if (id) flowReachWatermarkOwner.value = id;
  rememberFlowReach(next);
}

function setNavigationManualHold(hold: boolean): void {
  navigationManualHold.value = hold;
}

function setFlowActive(active: boolean): void {
  flowActive.value = active;
}

function setFlowLiveWorker(active: boolean): void {
  flowLiveWorker.value = active;
}

type NavigationOwner = { activeStep?: Ref<StepId> };

function navigateStep(
  this: NavigationOwner | undefined,
  step: string,
  options: { source?: "user" | "flow" | "island" | "restore" | "system" | "reset"; allowUnreachable?: boolean } = {},
): StepId {
  const targetRef = this?.activeStep || activeStep;
  const requested = STEP_ORDER.includes(step as StepId) ? step as StepId : "upload";
  const source = options.source || "user";
  if (historyMode.value && requested !== "results") {
    emit("notify", { message: "历史轮次不可改写，请先回到最新", tone: "warning" });
    return targetRef.value;
  }
  // A user's explicit earlier click owns the current landing page.  All
  // background sources share this gate so legacy polling cannot jump over the
  // same hold that already protects Flow projection updates.  Explicit user
  // clicks and restore reconciliation remain allowed to establish a landing
  // page in the first place.
  if ((source === "flow" || source === "system") && navigationManualHold.value) return targetRef.value;
  // SPEC 046 V2 FR-011 / 契约第 3 节第 7 条：刷新先恢复本地 activeStep，再水合 Flow。
  // Flow 归属已成立时，存档里的落点就是「刷新前已解锁的那一页」，先记进水位再校正，
  // 免得投影还窄于现场时把恢复出来的页钳走（没有 Flow 归属的旧形态不享有这条待遇，
  // 由 legacy 事实判定落点是否有效）。
  if (source === "restore" && hasFlowOwnership.value) {
    rememberFlowReach(stepPrefixThrough(requested));
  }
  const landing = options.allowUnreachable || source === "system" || source === "reset"
    ? requested
    : reachableStep(requested, new Set(enabledSteps.value)) as StepId;
  if (source === "user" || source === "island") {
    const requestedRank = STEP_ORDER.indexOf(requested);
    const highestRank = Math.max(...enabledSteps.value.map((candidate) => STEP_ORDER.indexOf(candidate)));
    if (requestedRank < highestRank) navigationManualHold.value = true;
  }
  targetRef.value = landing;
  // 守卫放行的落点即「已解锁页面」：并进水位，投影后续缺位时不再把它锁回去。
  // 强制落点（system/reset/allowUnreachable）是绕过可达性判定的出口，不作为解锁证据。
  if (!options.allowUnreachable && source !== "system" && source !== "reset") {
    rememberFlowReach(stepPrefixThrough(landing));
  }
  return landing;
}

function reconcileActiveStep(this: NavigationOwner | undefined, step?: string): StepId {
  const targetRef = this?.activeStep || activeStep;
  return navigateStep.call(this, step || targetRef.value, { source: "restore" });
}

function resetNavigation(this: NavigationOwner | undefined): void {
  flowReachableSteps.value = null;
  // 换轮 / 换画像：上一轮的入口不带给新一轮，水位与归属一并清空。
  flowNavigationOwned.value = false;
  clearFlowReachWatermark();
  navigationManualHold.value = false;
  const targetRef = this?.activeStep || activeStep;
  targetRef.value = "upload";
}

function capsuleNavigationMeta(stuckAt: "scrape" | "screen" | "none" = "none") {
  return {
    enabledSteps: [...enabledSteps.value],
    stuckAt,
    historyMode: historyMode.value,
    bootstrapping: !workflowStateRestored.value,
  };
}


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
  .filter((city) => city.length > 0 && !isNationwideCityName(city)));


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
  // v-show 下 03 页始终渲染，平台草稿与筛选草稿可能处在过渡态：
  // 缺当前平台草稿时按空处理，不让派生计算把渲染打断。
  const drafts = filterValues.value[draftPlatform.value] || {};
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

// 结果是否已经到「可以直接渲染」的程度：一条结果可能早于外围的完成标记落地
// （恢复持久化页面、替换 Flow 投影时都会出现）。空态/加载中的横幅不得盖住已经
// 能渲染的列表、计数或 Flow 错误投影——这里读的就是 pipelineResult 本体那一份
// 事实，页面只绑定结果，不再自己拼判定。
const hasRenderableResult = computed(() => {
  const result = pipelineResult.value as (PipelineResult & {
    flow_tracks?: Array<Record<string, unknown>>;
  }) | null;
  if (resultLoaded.value) return true;
  if (!result) return false;
  const jobs = Array.isArray(result.jobs) ? result.jobs : [];
  const dropped = Array.isArray(result.dropped) ? result.dropped : [];
  const hasCount = [
    result.total_scraped,
    result.total_kept,
    result.total_matched,
    result.total_dropped,
  ].some((value) => Number(value || 0) > 0);
  const hasTrackProjection = (result.flow_tracks || []).some((track) => (
    (Array.isArray(track.jobs) && track.jobs.length > 0)
    || (Array.isArray(track.dropped) && track.dropped.length > 0)
    || Boolean(track.result_run_id)
    || ["failed", "unavailable", "interrupted"].includes(String(track.status || ""))
    || Boolean(String(track.message || track.reason || track.error || "").trim())
  ));
  return Boolean(jobs.length || dropped.length || hasCount || hasTrackProjection);
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

function resetForProfileSwitch(this: NavigationOwner | undefined): void {
  invalidateWorkflowEpoch();
  workflowStateRestored.value = false;
  if (pollTimer.value !== undefined) {
    window.clearTimeout(pollTimer.value);
    pollTimer.value = undefined;
  }
  resetNavigation.call(this);
  flowActive.value = false;
  flowLiveWorker.value = false;
  analysisReady.value = false;
  selectedFile.value = null;
  aiConsent.value = false;
  dragActive.value = false;
  uploadBusy.value = false;
  resumeError.value = "";
  // Spec041：旧画像的搜索草稿已在自己槽位里留好，这里只清工作副本，
  // 不回写（否则会把新画像的草稿清掉）。
  clearSearchDraftRefs();
  fieldLabels.value = {};
  filterValues.value = { boss: {}, zhilian: {} };
  profileSummary.value = "";
  profileFacts.value = {};
  resumeAnalysis.value = null;
  resumeAnalysisReset.value?.();
  resumeAnalysisPhase.value = "idle";
  appliedResumePlatforms.value = new Set();
  scrapeTaskId.value = "";
  scrapeBusy.value = false;
  scrapeActionBusy.value = "";
  scrapeSnapshot.value = null;
  screenTaskId.value = "";
  screenBusy.value = false;
  pausingScreen.value = false;
  screenSnapshot.value = null;
  recrawlTaskId.value = "";
  recrawlBusy.value = false;
  recrawlSnapshot.value = null;
  recrawlRetryCount.value = 0;
  scrapeCompleted.value = false;
  resultLoaded.value = false;
  finishedPartial.value = false;
  pipelineResult.value = null;
  pipelineResultRunId.value = "";
  resultPlatformFilter.value = "all";
  resultRunIds.value = { boss: "", zhilian: "" };
  resultEpoch.value += 1;
  historyRound.value = null;
  returningFromHistory.value = false;
  platformBeforeHistory.value = null;
  activeCategory.value = "matched";
  rejectedIds.value = new Set();
  currentRoundStatus.value = "";
  pausedRunId.value = "";
  interruptedRunId.value = "";
  restoredTaskHint.value = "";
  recrawlPlatformGuide.value = null;
  scopePreviewByPlatform.value = { boss: null, zhilian: null };
  scopePreviewBusy.value = false;
  scopePreviewReqId.value += 1;
  autoScreenArmed.value = false;
  autoScreenFields.value = {};
  autoScreenProfile.value = "";
  profileError.value = "";
  profileConfirmed.value = false;
  oneClickOpen.value = false;
  screenPanelOpen.value = false;
  searchPanelsOpen.value = false;
  advancedPanelsOpen.value = false;
  activeTaskRestored.value = false;
  unfinishedWorkflowRestored.value = false;
  restoredWorkflowSnapshot.value = null;
  resultsPageSeen.value = false;
  resultsBootstrapPending.value = false;
  sceneSnapshot.value = null;
}

// Spec041 返工：完成态现场恢复占位。刷新后（会话存档缺失、只剩已结束事实）
// 先落到结果页骨架并置位，等后端最新结果补齐；期间不显示"暂无结果"，
// 更不短暂回到 BOSS 空上传页。
const resultsBootstrapPending = ref(false);

// ---------------------------------------------------------------------------
// 暂停族（用户主动暂停 / 服务重启打断）的中文口径与性质判定，见本文件上方的
// pausedFamilyStatusOf / pausedFamilyNoticeTitle / pausedFamilyIslandMessage：
// 纯函数、模块级唯一实现，顶栏胶囊与展开面板的通知行标题共读这一份，不各写一套。
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// 顶栏本轮状态胶囊数据（纯派生，不发请求）：
// 四态按 spec FR-013 优先级判定；平台优先取任务自身平台（恢复任务快照携带），
// 缺省时用草稿平台。空闲常驻（idle），不再上抛 null（spec FR-012）。
// ---------------------------------------------------------------------------
const roundStatusPayload = computed<CapsuleStatusPayload | null>(() => {
  const platform: Platform = scrapeSnapshot.value?.platform
    || screenSnapshot.value?.platform
    || draftPlatform.value;

  // A terminal Flow projection is the public lifecycle owner for a parallel
  // round.  Legacy task recovery can leave a stale paused snapshot/ID behind
  // after latest-running-task has already gone empty; letting that snapshot
  // win here makes the island say “paused” and navigate back to Step 2 even
  // though the Flow failure is recoverable from Step 3/4.  Only take this
  // branch when the Flow has no active sibling, so a real worker still keeps
  // the strict live-task attention semantics below.
  const flowResult = pipelineResult.value as (PipelineResult & {
    flow_id?: string;
    flow_tracks?: Array<Record<string, unknown>>;
  }) | null;
  const flowTracks = Array.isArray(flowResult?.flow_tracks) ? flowResult.flow_tracks : [];
  const flowHasActiveSibling = flowTracks.some((track) =>
    ["running", "queued", "paused", "interrupted"].includes(String(track.status || "")));
  const flowFailure = flowTracks.find((track) => (
    ["failed", "unavailable", "interrupted"].includes(String(track.status || ""))
    || String(track.error_code || "") === "flow_result_incomplete"
    || track.unfinished_ai_screening === true
  ));
  const legacyTaskLive = scrapeBusy.value || screenBusy.value || recrawlBusy.value
    || [scrapeSnapshot.value, screenSnapshot.value, recrawlSnapshot.value].some((snapshot) =>
      snapshot && ["running", "queued"].includes(String(snapshot.status || "")));
  if (flowResult?.flow_id && flowFailure && !flowHasActiveSibling && !legacyTaskLive) {
    const message = String(
      flowFailure.message || flowFailure.reason || flowFailure.error
      || "AI 筛选未完成，请处理后继续",
    );
    return {
      platform, phase: "scraping" as const, judged: 0, scope: platform,
      ...capsuleNavigationMeta("screen"),
      capsule: {
        state: "attention", platform,
        attention: { kind: "error", message },
      },
      integrity: pipelineResult.value?.integrity,
    };
  }

  // 简历分析在后台进行时只占用上传按钮，胶囊单独展示分析中，不能冒充抓取任务。
  if (uploadBusy.value) {
    return {
      platform, phase: "scraped" as const, judged: 0, scope: platform,
      ...capsuleNavigationMeta("none"),
      capsule: { state: "analyzing", platform, progress: { done: 0 } },
    };
  }

  // ---- attention（最高）：暂停 / 出错 / 中断 ----
  const snapshots = [scrapeSnapshot.value, screenSnapshot.value, recrawlSnapshot.value];
  const failed = snapshots.find((s) => s && String(s.status) === "failed");
  if (failed) {
    const stuckAt = failed === screenSnapshot.value || failed === recrawlSnapshot.value ? "screen" : "scrape";
    return {
      platform, phase: "scraping" as const, judged: 0, scope: platform,
      ...capsuleNavigationMeta(stuckAt),
      capsule: {
        state: "attention", platform,
        attention: { kind: "error", message: failed.error || "任务执行出错" },
      },
      integrity: failed.integrity,
    };
  }
  const pausedFamilyScreenSide = pausedFamilyStatusOf(screenSnapshot.value)
    || pausedFamilyStatusOf(recrawlSnapshot.value);
  const pausedFamilyScrape = pausedFamilyStatusOf(scrapeSnapshot.value);
  const hasPaused = pausedRunId.value || interruptedRunId.value
    || Boolean(pausedFamilyScreenSide) || Boolean(pausedFamilyScrape);
  if (hasPaused) {
    const stuckAt = pausedFamilyScreenSide ? "screen" : "scrape";
    // 性质跟着落点指向的阶段说：三个快照都没带暂停族状态（只剩恢复出来的断点 runId）时，
    // 登记的是可恢复的暂停断点就说已暂停，否则就是被打断的断点，说已中断。
    const pausedFact = pausedFamilyScreenSide || pausedFamilyScrape
      || (pausedRunId.value ? "paused" : "interrupted");
    return {
      platform, phase: "scraping" as const, judged: 0, scope: platform,
      ...capsuleNavigationMeta(stuckAt),
      capsule: {
        state: "attention", platform,
        // pausedFact 一起上抛：展开面板的通知行标题读它，与岛上同一份性质。
        attention: { kind: "paused", pausedFact, message: pausedFamilyIslandMessage(pausedFact) },
      },
      integrity: snapshots.find((s) => s && s.integrity)?.integrity,
    };
  }

  // ---- running：抓取 / 筛选 / 补抓进行中 ----
  // 三条进行中链路必须分别判定并选各自的 snapshot：补抓虽复用抓取任务名，
  // 其 progress.stage 仍有 recrawl_fetch_jd / recrawl_ai，不能被硬编码成 scraping；
  // 否则旧 scrapeSnapshot 会抢在 recrawlSnapshot 前，胶囊既显示错阶段也不推进数字。
  const recrawlLive = recrawlBusy.value
    || Boolean(recrawlSnapshot.value && ["running", "queued"].includes(String(recrawlSnapshot.value.status)));
  const scrapeLive = scrapeBusy.value
    || Boolean(scrapeSnapshot.value && ["running", "queued"].includes(String(scrapeSnapshot.value.status)));
  const screenLive = screenBusy.value
    || Boolean(screenSnapshot.value && ["running", "queued"].includes(String(screenSnapshot.value.status)));
  if (recrawlLive || scrapeLive || screenLive) {
    const snapshot = recrawlLive
      ? recrawlSnapshot.value
      : scrapeLive
        ? scrapeSnapshot.value
        : screenSnapshot.value;
    // 037 复审：phase 不再只看"哪个任务在跑"二选一——screen 任务内部还分
    // 阶段（后端 emit 的 progress.stage：ensure_chrome/fetch_jd/screen_a/
    // screen_b/done，补抓链路为 recrawl_fetch_jd/recrawl_ai）。旧版一律落
    // "screening"，导致抓 JD（fetch_jd）也显示"AI精筛"（用户实测反馈）。
    // 现按 stage 细分：抓 JD → "jd"，其余仍为 "screening"。
    const phase = recrawlLive || screenLive ? screenStagePhase(snapshot) : "scraping" as const;
    const progress = taskProgressFromSnapshot(snapshot);
    return {
      platform, phase, judged: progress.done, scope: platform,
      ...capsuleNavigationMeta("none"),
      capsule: { state: "running", platform, progress: { phase, ...progress } },
      integrity: snapshot?.integrity,
    };
  }

  const resolvedIntegrity = historyRound.value?.integrity
    || screenSnapshot.value?.integrity
    || recrawlSnapshot.value?.integrity
    || scrapeSnapshot.value?.integrity
    || pipelineResult.value?.integrity;
  // 用户主动「结束并保存」收尾的轮次：白箱如实记 interrupted（任务确实被停止），
  // 但对用户来说这是正常收尾。这里按轮次口径展示（部分完成 / 已结束保存），
  // 不再报"任务因取消或停止而中断"，也不再让灵动岛据此把人送回 02/03。
  const integrityForDisplay = resolvedIntegrity
    && finishedPartial.value
    && resolvedIntegrity.conclusion === "interrupted"
    ? {
      ...resolvedIntegrity,
      conclusion: "partial" as const,
      label: "部分完成",
      primary_code: "user_finished",
      primary_reason: "已结束保存部分结果",
    }
    : resolvedIntegrity;
  if (integrityForDisplay && ["failed", "unverifiable", "interrupted"].includes(integrityForDisplay.conclusion)) {
    const interrupted = integrityForDisplay.conclusion === "interrupted";
    return {
      platform, phase: "scraping" as const, judged: 0, scope: platform,
      ...capsuleNavigationMeta("screen"),
      capsule: {
        state: "attention", platform,
        attention: interrupted ? {
          // 完整性结论的中断同样走 paused 通道：性质写实，通知行标题才不会说成已暂停。
          kind: "paused" as const,
          pausedFact: "interrupted" as const,
          message: integrityForDisplay.primary_reason || pausedFamilyNoticeTitle("interrupted"),
        } : {
          kind: "error" as const,
          message: integrityForDisplay.primary_reason || integrityForDisplay.label,
        },
      },
      integrity: integrityForDisplay,
    };
  }

  // ---- completed：有结果（当前轮或历史轮）----
  if (historyRound.value) {
    if (isScrapedOnly.value) {
      const total = historyRound.value.jobCount;
      return {
        platform: historyRound.value.platform, phase: "scraped" as const, judged: total, scope: "history" as const,
        ...capsuleNavigationMeta("none"),
        capsule: {
          state: "completed", platform: historyRound.value.platform,
          results: { matched: total, pending: 0 },
        },
        integrity: integrityForDisplay,
      };
    }
    const counts = resultCountsFromPipeline(pipelineResult.value);
    const g = groups.value;
    const judged = g.matched.length + g.unmatched.length + g.uncertain.length + g.dropped.length;
    return {
      platform: historyRound.value.platform, phase: "judged" as const, judged, scope: "history" as const,
      ...capsuleNavigationMeta("none"),
      capsule: {
        state: "completed", platform: historyRound.value.platform,
        results: counts,
      },
      integrity: integrityForDisplay,
    };
  }
  if (resultLoaded.value && pipelineResult.value) {
    const scope = resultPlatformFilter.value === "all" ? "all" as const : resultPlatformFilter.value;
    if (isScrapedOnly.value) {
      const total = (filteredPipelineResult.value.jobs || []).length;
      return {
        platform, phase: "scraped" as const, judged: total, scope,
        ...capsuleNavigationMeta("none"),
        capsule: {
          state: "completed", platform,
          results: { matched: total, pending: 0 },
        },
        integrity: integrityForDisplay,
      };
    }
    const counts = resultCountsFromPipeline(pipelineResult.value);
    const g = groups.value;
    const judged = g.matched.length + g.unmatched.length + g.uncertain.length + g.dropped.length;
    return {
      platform, phase: "judged" as const, judged, scope,
      ...capsuleNavigationMeta("none"),
      capsule: { state: "completed", platform, results: counts },
      integrity: integrityForDisplay,
    };
  }

  // ---- idle（常驻，最低优先级）：无任务无结果 ----
  return {
    platform, phase: "scraped" as const, judged: 0, scope: platform,
    ...capsuleNavigationMeta("none"),
    capsule: { state: "idle", platform },
  };
});

// Spec041：胶囊点击导航信号的消费（归一落点 / 历史退出 / 启动占位）已外迁到
// useDiscoveryIslandBridge.ts，由视图在 setup 内构造；本文件只保留信号本身。

return {
  WORKFLOW_STATE_VERSION,
  workflowStateKey,
  profileId,
  workflowStateRestored,
  unfinishedWorkflowRestored,
  resultsPageSeen,
  resultsBootstrapPending,
  viewPlatform,
  loadSearchDraftFor,
  saveSearchDraftFor,
  switchSearchDraftPlatform,
  ensureSearchDraftLoaded,
  mirrorSearchDraftKeywords,
  setScopePreviewFor,
  restoredWorkflowSnapshot,
  activeTaskRestored,
  resetForProfileSwitch,
  sceneSnapshot,
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
  flowReachableSteps,
  navigationManualHold,
  flowActive,
  flowLiveWorker,
  setFlowReachableSteps,
  setNavigationManualHold,
  setFlowActive,
  setFlowLiveWorker,
  navigateStep,
  reconcileActiveStep,
  resetNavigation,
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
  resumeAnalysisPhase,
  resumeAnalysisLandOnReturn,
  resumeAnalysisReset,
  resumeAnalysisRestore,
  appliedResumePlatforms,
  scrapeTaskId,
  scrapeBusy,
  scrapeActionBusy,
  scrapeSnapshot,
  screenBusy,
  pausingScreen,
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
  workflowEpoch,
  invalidateWorkflowEpoch,
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
  scopeLockReason,
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
  hasRenderableResult,
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
  capsuleReturnToLatest,
  confirmHistoryDelete,
  cancelHistoryDelete,
  deleteHistoryRound,
  archiveHistoryLatest,
};
}

export type DiscoveryState = ReturnType<typeof useDiscoveryState>;

/**
 * 判活（问题 A「此刻有没有活体 worker 在跑」）：决定迟到响应能不能覆盖现场、
 * 04 能不能认「已进结果页」并把本轮结果接进来。
 * 词表 A 侧唯一定义：只有「排队中 / 运行中」算活体——Flow 侧只认活体轨道投影，
 * 任务快照只认 running/queued。已暂停、已中断都没有活体在跑，属于问题 B
 * 「这一轮还没结束」，一律用 hasUnfinishedRound 回答；把它们并回判活，
 * 中断/暂停轮就会被当成「有人在干活」，04 的结果接回与「已看过」置位永开关死。
 */
export function hasLiveTaskState(state: DiscoveryState): boolean {
  // 页面还没投影活体事实（flowLiveWorker === null：单平台传统链路、未经协调器投影的
  // 调用方与单元现场）时退回活动线——那不是新语义，是没有活体事实可依据时唯一的
  // 保守口径：宁可当作有活，也不能凭空判成「本轮已结束」。
  const liveWorker = state.flowLiveWorker.value;
  if (liveWorker === null ? state.flowActive.value : liveWorker) return true;
  const liveStatuses = new Set(["running", "queued"]);
  for (const snap of [
    state.screenSnapshot.value,
    state.scrapeSnapshot.value,
    state.recrawlSnapshot.value,
  ]) {
    if (snap && liveStatuses.has(String(snap.status))) return true;
  }
  return false;
}

/**
 * 问题 B「这一轮还没结束」：流程活动线（含已中断、已暂停的轮）、真有活体任务，
 * 或本轮留着暂停/进行中快照等待处理与接回。
 *
 * 它决定的是「用户要不要被带回这一轮的真实进度页」——落点 liveTaskStep、
 * 灵动岛「回到最新」问这个。已中断的轮同样没结束：落点不能为空、不能造不可达步骤，
 * 否则上传入口守卫会把用户带进「开新一轮」，把刚中断的这一轮冲掉（SPEC 046 第五轮修过的两个缺陷）。
 * 词表 B 侧成员（paused）在 A 侧被剔除后由这里自持，两份谓词不得再互相借道。
 */
export function hasUnfinishedRound(state: DiscoveryState): boolean {
  if (state.flowActive.value || hasLiveTaskState(state)) return true;
  if (state.pausedRunId.value) return true;
  return [
    state.screenSnapshot.value,
    state.scrapeSnapshot.value,
    state.recrawlSnapshot.value,
  ].some((snapshot) => snapshotRoundUnfinished(snapshot));
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

// 落点侧（问题 B 的一部分）的快照清单：排队/运行/暂停都还有未收口的现场。
// 名字必须与内容一致——这是「本轮未结束」的清单，不是活体清单（暂停不在 A 侧）。
const UNFINISHED_ROUND_SNAPSHOT_STATUSES = new Set(["running", "queued", "paused"]);

function snapshotRoundUnfinished(snapshot?: { status?: string | null } | null): boolean {
  return Boolean(snapshot && UNFINISHED_ROUND_SNAPSHOT_STATUSES.has(String(snapshot.status || "")));
}

/**
 * 未结束任务的「真实进度页」只读派生：
 * 抓取段未收口（运行/排队/暂停）→ "search"（02，抓取任务的真实进度页）；
 * 筛选/重抓段未收口（含 pausedRunId）→ "screen"（03）；失败/中断快照只保留错误展示。
 */
export function deriveLiveTaskStep(probe: LiveTaskProbe): StepId | "" {
  if (probe.scrapeBusy || snapshotRoundUnfinished(probe.scrapeSnapshot)) return "search";
  if (
    probe.screenBusy || probe.recrawlBusy
    || snapshotRoundUnfinished(probe.screenSnapshot)
    || snapshotRoundUnfinished(probe.recrawlSnapshot)
    || probe.pausedRunId
  ) return "screen";
  return "";
}

/** 进行中阶段可作落点的清单，按页面先后排；取落点时从最深的一支开始。
 *  01 是发起页、04 是结果页，都不是进度页，因此不进这张表。 */
const LIVE_LANDING_STEPS: StepId[] = ["screen", "search"];

/** 035：liveTaskStep(state)——持有 state 的域（search/results 等）直接取用。 */
export function liveTaskStep(state: DiscoveryState): StepId | "" {
  const legacyStep = deriveLiveTaskStep({
    scrapeBusy: state.scrapeBusy.value,
    scrapeSnapshot: state.scrapeSnapshot.value,
    screenBusy: state.screenBusy.value,
    screenSnapshot: state.screenSnapshot.value,
    recrawlBusy: state.recrawlBusy.value,
    recrawlSnapshot: state.recrawlSnapshot.value,
    pausedRunId: state.pausedRunId.value,
    interruptedRunId: state.interruptedRunId.value,
  });
  if (legacyStep) return legacyStep;
  // 落点问的是问题 B「这一轮还没结束」，不是问题 A「此刻有活体 worker」：
  // 已中断/已暂停的轮次没有活体任务在跑，但同样没结束，落点不能为空、
  // 不能造不可达步骤（SPEC 046 第五轮的两个缺陷）。判活（04 接回结果、迟到响应）
  // 才用 hasLiveTaskState，两者不要混用。
  if (!hasUnfinishedRound(state)) return "";
  // 树干说有活（只由流程活动线成立）而本地进度探针给不出落点时，落点跟随 Flow
  // 投影到 state 的阶段集合，取其中**最深**的进行中阶段（screen 优先于 search）：
  // 并行流程「抓取已完成、筛选正在跑」必须落 03，落 02 只看得到抓取列表。
  // 判定面用投影事实本身，不用被 historyMode 收窄的 enabledSteps——「回到最新」
  // 在清掉历史轮之前求值，收窄集合会把有活任务的守卫判反。
  // 投影还没到达时按活动线的最小开放面落 02（此时可达集合就是 01+02）；
  // 投影里找不到进行中阶段就不给落点，绝不凭空造一个当前不可达的步骤（结果页不是进度页）。
  const projected = state.flowReachableSteps.value;
  const candidates = projected && projected.size
    ? projected
    : new Set<StepId>(["search"]);
  return LIVE_LANDING_STEPS.find((step) => candidates.has(step)) || "";
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
  // 043：未收尾流程的一次性提醒——上抛给 App 推入灵动岛通知池（一行字）。
  (event: "island-notice", payload: { id: string; title: string; detail?: string; target?: "results" | "task" }): void;
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
  integrity?: IntegritySnapshot | null;
  /** 039：软失败组合留痕（恢复态面板悬停失败数字时逐条展示）。 */
  combo_issues?: ComboIssue[] | null;
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

// 刷新路径与实时任务完成路径共用同一份“当前画像全局最新轮”契约。
export interface MergedLatestResult {
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
      integrity?: IntegritySnapshot | null;
    };
  };
  /** 025 B078：各平台最新轮状态（供完成态判定；无该平台轮则缺省）。 */
  platformStatuses?: Partial<Record<"boss" | "zhilian", string>>;
  /**
   * B096 返修：这一轮牵到的全部任务线（抓取线 + AI/结果线）。合并结果一个轮次
   * 挂两条平台线，恢复时的面板计数必须按全部任务线汇总；缺省表示单线结果，
   * 由加载方按 newer.data 自带的任务编号回退，口径仍然只有一份。
   */
  flowTaskLines?: FlowTaskLine[];
}

/** 一条平台任务线：抓取线编号 + AI/结果线编号（没有 AI 任务时后者为空）。 */
export interface FlowTaskLine {
  scrapeRunId: string;
  screenRunId: string;
}

/** round-status 上抛 payload：既有展示字段 + 胶囊状态（App 供 DynamicIsland 消费）。 */
export interface CapsuleStatusPayload extends RoundStatusPayload {
  capsule: DynamicIslandState;
  integrity?: IntegritySnapshot | null;
  /** Spec041：供胶囊派生落点使用的真实现场，不参与展示文案。 */
  enabledSteps?: string[];
  stuckAt?: "scrape" | "screen" | "none";
  historyMode?: boolean;
  bootstrapping?: boolean;
}

// ---------------------------------------------------------------------------
// 036 B088：顶栏胶囊进度/结果数字提取（纯函数，数据层唯一实现）。
// tasks/results 域 re-export 本函数，避免数据层反向依赖动作层。
// 进度：progress.current/success_count 为已处理数，total/source_total 为总数；
// total 未知（缺省/0）时省略分母（spec 边界 B088-进度数字缺失）。
// 结果：matched = 明确匹配；pending = 待确认（verdict 非 match/not_match/mismatch）。
// ---------------------------------------------------------------------------

// 037 复审：从 screen 任务 snapshot 的 progress.stage 细分阶段（纯函数）。
// 后端 AI 筛选链路 emit 的 stage：ensure_chrome（启动浏览器）/ fetch_jd（抓 JD）
// / screen_a（AI 粗筛）/ screen_b（AI 精筛）/ done；补抓链路为
// recrawl_fetch_jd / recrawl_ai。只有 JD 抓取细分出来（灵动岛显示"抓取 JD"），
// 其余一律 screening——避免抓 JD 与真·AI 精筛撞车显示同一文案（用户实测反馈）。
export function screenStagePhase(snapshot: TaskSnapshot | null): "jd" | "screening" {
  const progress = (snapshot?.progress || {}) as Record<string, unknown>;
  const stage = String(progress.stage ?? "");
  if (stage === "fetch_jd" || stage === "recrawl_fetch_jd") return "jd";
  return "screening";
}

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
