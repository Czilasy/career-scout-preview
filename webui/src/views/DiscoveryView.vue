<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  Bookmark,
  Check,
  Download,
  FileText,
  Filter,
  History,
  LoaderCircle,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
  UploadCloud,
  X,
} from "@lucide/vue";
import CollapsibleCard from "../components/CollapsibleCard.vue";
import ExecutionModeSelector from "../components/ExecutionModeSelector.vue";
import JobLifecycleActions from "../components/JobLifecycleActions.vue";
import JobWorkspace from "../components/JobWorkspace.vue";
import StepNavigator from "../components/StepNavigator.vue";
import TaskProgress from "../components/TaskProgress.vue";
import { ApiError, apiRequest, errorMessage, settingsApi } from "../api";
import { setThemePlatform } from "../composables/useTheme";
import {
  buildSearchScriptParams,
  createCityCatalogLoader,
  createPlatformState,
  createSchemaLoader,
  DEFAULT_PLATFORM,
  filterPipelineResultByPlatform,
  normalizeScopePreview,
  partitionPipelineResult,
} from "../discovery";
import type { PipelineResult } from "../discovery";
import type {
  AdvancedSettingsState,
  CandidateProfile,
  ExecutionSelection,
  ExecutionSettings,
  FrozenSearchScope,
  JobItem,
  Notice,
  Platform,
  PlatformCityCatalog,
  PlatformFilterSchema,
} from "../types";

type StepId = "upload" | "search" | "screen" | "results";
type ResultCategory = "matched" | "unmatched" | "uncertain" | "dropped";
type FieldLabel = [string, unknown, string | Record<string, string>];

interface AnalyzeResponse {
  ok: boolean;
  fields: Record<string, unknown>;
  labels: Record<string, FieldLabel>;
}

interface TaskSnapshot {
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
  // T510：任务自身平台，供 TaskProgress 展示真实平台徽章（http-api.md L201）
  platform?: Platform;
}


const props = defineProps<{ profileId: string }>();
const emit = defineEmits<{
  notify: [notice: Notice];
  "profile-created": [profile: CandidateProfile];
  // Task 009：详情生命周期 action 成功后上抛，App 刷新当前 profile 提醒。
  "job-feedback-changed": [payload: { profileId: string; jobId: string }];
  // 顶栏本轮状态胶囊：纯展示数据，空闲时上抛 null（不新增任何请求）。
  "round-status": [payload: { platform: Platform; phase: "scraping" | "screening" | "judged"; judged: number } | null];
  // D7：岗位发现流程检测未登录，引导用户去账号面板打开浏览器窗口登录。
  "open-browser-accounts": [];
}>();

// D7：未登录类错误码（BOSS/智联 preflight 与任务暂停的稳定错误码）。
const LOGIN_ERROR_CODES = new Set([
  "source_login_required", "login_expired", "boss_login_required",
]);
// 任务失败后显示的登录引导条；visible 时展示「打开账号 X 的 BOSS 窗口登录」。
const loginGuide = ref<{ visible: boolean; platform: Platform; accountName: string }>({
  visible: false,
  platform: "boss",
  accountName: "",
});

function isLoginErrorCode(code: unknown): boolean {
  return typeof code === "string" && LOGIN_ERROR_CODES.has(code);
}

async function showLoginGuide(platform: Platform) {
  loginGuide.value = { visible: true, platform, accountName: "" };
  try {
    const data = await apiRequest<{
      accounts?: { id: string; name: string }[];
      active_account?: string;
    }>("/api/browser-accounts");
    const active = data.active_account || "a";
    const account = (data.accounts || []).find((item) => item.id === active);
    // 内置 ~/.career-scout/chrome-profile 账号在 UI 上固定叫「默认账号」（D7）。
    loginGuide.value.accountName = account
      ? (account.id === "a" ? "默认账号" : account.name)
      : active;
  } catch {
    // 账号名只是引导文案辅助，拉不到就显示通用文案。
  }
}

// T503：平台三身份独立（platform-schema.md L142-159）
// platformState 是真相之源（非响应式闭包）；draftPlatform 是 Vue 镜像，仅供模板渲染。
// setDraftPlatform 同步更新两者，不用 watcher 相互覆盖（不变式 4）。
// 仅切换新任务草稿，不改 task/result（不变式 1）；T505 起按 platformState.draft 加载 schema/城市。
const platformState = createPlatformState(DEFAULT_PLATFORM);
const draftPlatform = ref<Platform>(platformState.draft);
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
const draftPlatformDisabled = computed(() => Boolean(
  schemaRef.value && schemaRef.value.platform === draftPlatform.value && schemaRef.value.enabled_for_new_tasks === false,
));
function setDraftPlatform(platform: Platform) {
  if (platformState.draft === platform) return;
  platformState.setDraftPlatform(platform);
  draftPlatform.value = platform;
  // 同步主题品牌色到新平台（boss 青 / 智联蓝）。
  setThemePlatform(platform);
  // 切换草稿平台后按新平台重新加载 schema / 城市；旧请求被 loader 内部取消丢弃。
  void loadFilterLabels();
  void loadCityCatalog();
}

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
    title: "确认六类筛选条件",
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
const keywords = ref<Array<{ word: string; recommended: boolean }>>([]);
const selectedKeywords = ref<string[]>([]);
const customKeyword = ref("");
const cityText = ref("");
const customCity = ref("");
const fieldLabels = ref<Record<string, FieldLabel>>({});
// T506：筛选草稿分平台独立保存（platform-schema.md L139）。
// boss.stage 与 zhilian.company_nature 互不串用；公共字段（salary/experience/...）
// 也按平台隔离，避免切换平台时把 A 平台不支持的值带给 B 平台。
const filterValues = ref<Record<Platform, Record<string, string[]>>>({
  boss: {},
  zhilian: {},
});
const profileSummary = ref("");
const scrapeTaskId = ref("");
const scrapeBusy = ref(false);
const scrapeSnapshot = ref<TaskSnapshot | null>(null);
const screenBusy = ref(false);
const screenSnapshot = ref<TaskSnapshot | null>(null);
const screenTaskId = ref("");
// 待确认项「全部重抓」状态
const recrawlBusy = ref(false);
const recrawlTaskId = ref("");
const recrawlSnapshot = ref<TaskSnapshot | null>(null);
let recrawlRetryCount = 0;
const scrapeCompleted = ref(false);
const resultLoaded = ref(false);
const exportBusy = ref(false);
// 刷新后接回任务时显示的恢复提示条；任务结束后清空
const restoredTaskHint = ref("");
// 切片7：从 DB 恢复的 paused 任务 run_id（无内存工作线程，不能 poll）
const pausedRunId = ref("");
// 服务重启打断的 AI 筛选任务：恢复后优先展示续跑入口，不被旧历史结果覆盖
const interruptedRunId = ref("");
const pipelineResult = ref<PipelineResult | null>(null);
const pipelineResultRunId = ref("");
// 结果页平台筛选（全部/BOSS/智联）：纯展示层过滤，不改草稿/任务身份（不变式 1）。
const resultPlatformFilter = ref<"all" | "boss" | "zhilian">("all");
// specs/004：结果加载代次。pipelineResult 被新 run 结果替换时递增，
// JobWorkspace 据此重置列表筛选/排序（切分类/切平台不重置，contracts §6 D3）。
const resultEpoch = ref(0);
// 合并载入时每个平台各自的结果来源 run：单平台视图下“全部重抓”/导出用对应 run。
const resultRunIds = ref<{ boss: string; zhilian: string }>({ boss: "", zhilian: "" });
const activeCategory = ref<ResultCategory>("matched");
const rejectedIds = ref(new Set<string>());
const feedbackBusyIds = ref(new Set<string>());
const jdBusyIds = ref(new Set<string>());
const advancedBusy = ref(false);
const executionSelection = ref<ExecutionSelection>("custom");
const scopePreview = ref<FrozenSearchScope | null>(null);
const scopePreviewBusy = ref(false);
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
const advancedRanges = ref<Record<string, [number, number]>>({
  pages: [1, 9999],
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
function mergeManualRanges(raw: AdvancedSettingsState["manual_ranges"] | undefined) {
  if (!raw) return;
  for (const [field, value] of Object.entries(raw)) {
    const range = Array.isArray(value) ? value : [value.min, value.max];
    if (range.length === 2 && range.every((item) => Number.isFinite(item)) && range[0] <= range[1]) {
      advancedRanges.value[field] = [Number(range[0]), Number(range[1])];
    }
  }
}
function advancedRange(field: string): [number, number] {
  return advancedRanges.value[field] || [0, Number.MAX_SAFE_INTEGER];
}
function clampAdvanced(field: string) {
  if (field !== "pages") executionSelection.value = "custom";
  const raw = advancedSettings.value[field];
  if (typeof raw !== "number" || Number.isNaN(raw)) return;
  const range = advancedRanges.value[field];
  if (!range) return;
  const [min, max] = range;
  let next = raw;
  if (next < min) next = min;
  else if (next > max) next = max;
  if (next !== raw) advancedSettings.value[field] = next;
}
const screenPanelOpen = ref(true);
// 步骤 2 两个面板（关键词配置 / 高级执行设置）共用同一受控状态：
// 默认收拢、手动展开/收起联动（一个 ref 天然同步两卡）；开始抓取后自动收拢。
const searchPanelsOpen = ref(false);
let pollTimer: number | undefined;

const scopeLocked = computed(() => Boolean(
  scrapeBusy.value || screenBusy.value || recrawlBusy.value || pausedRunId.value,
));

const enabledSteps = computed<StepId[]>(() => {
  const enabled: StepId[] = ["upload"];
  if (analysisReady.value) enabled.push("search");
  if (scrapeCompleted.value) enabled.push("screen");
  if (resultLoaded.value) enabled.push("results");
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
  const ct = cityList.value.length ? cityList.value.length : 1;
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
    const labels = values.map((code) => {
      const option = group.options.find(([, optCode]) => optCode === code);
      return option ? option[0] : code;
    });
    chips.push({ label: group.label, value: labels.join(" / ") });
  });
  return chips;
});
watch(
  [selectedKeywords, cityText, () => advancedSettings.value.pages],
  () => { if (!scopeLocked.value) void refreshScopePreview(); },
  { deep: true },
);
// 分类基于当前平台过滤后的结果：页签计数跟随筛选联动。
// 过滤逻辑抽到 discovery.ts 纯函数（filterPipelineResultByPlatform），
// 切换筛选只影响展示层派生，不触碰 pipelineResult 本体。
const filteredPipelineResult = computed<PipelineResult>(() =>
  filterPipelineResultByPlatform(pipelineResult.value || {}, resultPlatformFilter.value),
);
const groups = computed(() => partitionPipelineResult(filteredPipelineResult.value));
const resultTabs = computed(() => [
  { id: "matched" as const, label: "匹配", count: groups.value.matched.length },
  { id: "unmatched" as const, label: "不匹配", count: groups.value.unmatched.length },
  { id: "uncertain" as const, label: "待确认", count: groups.value.uncertain.length },
  { id: "dropped" as const, label: "已筛除", count: groups.value.dropped.length },
]);
const currentJobs = computed(() => groups.value[activeCategory.value]);
const currentEmptyMessage = computed(() => ({
  matched: "没有明确匹配的岗位",
  unmatched: "没有明确不匹配的岗位",
  uncertain: "没有需要人工确认的岗位",
  dropped: "没有在粗筛阶段被移除的岗位",
})[activeCategory.value]);

onMounted(() => {
  void loadAdvancedSettings();
  void loadFilterLabels();
  void loadCityCatalog();
  void restoreRunningTask().finally(() => {
    if (!pausedRunId.value && !scrapeBusy.value && !screenBusy.value && !recrawlBusy.value) {
      void loadLatestResult();
    }
  });
});

async function restoreRunningTask() {
  try {
    const data = await apiRequest<{
      has_task?: boolean;
      task_id?: string;
      kind?: string;
      status?: string;
      // T509：任务自身平台（http-api.md L201，所有 has_task=true 响应含 platform）
      platform?: Platform;
      progress?: Record<string, unknown>;
      logs?: string[];
      error?: string;
      stage?: string;
      pause_info?: { error_code?: string; error_reason?: string } | null;
      execution_config?: Record<string, unknown> | null;
      backend_version?: string;
      current_version?: string;
      version_match?: boolean;
      scrape_task_id?: string;
      scrape_completed?: boolean;
      source_run_id?: string;
      started_at?: number;
      finished_at?: number;
      frozen_filters?: Record<string, unknown>;
      profile_summary?: string;
    }>("/api/latest-running-task");
    if (!data.has_task || !data.task_id) return;
    // T509：先设置任务自身平台，再加载对应 schema/城市（platform-schema.md L157）。
    // 不改草稿平台（不变式 2：setTaskPlatform 不改 draft/result）。
    const taskPlatform = data.platform;
    if (taskPlatform) {
      platformState.setTaskPlatform(taskPlatform);
      // 任务平台变化时同步主题品牌色（如恢复一个智联任务时切到蓝色品牌色）。
      setThemePlatform(taskPlatform);
      void loadFilterLabels(taskPlatform);
      void loadCityCatalog(taskPlatform);
    }
    // frozen_filters 写入任务平台对应的草稿槽；缺平台时退化到草稿平台（兼容旧 mock）
    const filterPlatform: Platform = taskPlatform ?? draftPlatform.value;
    if (data.kind === "recrawl") {
      await loadLatestResult();
    }
    const snapshot: TaskSnapshot = {
      status: data.status || "running",
      progress: data.progress || {},
      logs: data.logs || [],
      error: data.error || "",
      stage: data.stage,
      pause_info: data.pause_info,
      started_at: data.started_at,
      finished_at: data.finished_at,
      // T510：快照携带任务平台，供 TaskProgress 展示真实平台徽章
      platform: taskPlatform,
    };
    const kind: "scrape" | "screen" | "recrawl" = data.kind === "scrape"
      ? "scrape"
      : data.kind === "recrawl" ? "recrawl" : "screen";
    if (data.status === "interrupted") {
      // 服务重启打断的任务：工作线程已死不能 poll；提示用户重开（后端会自动接着上次进度）
      interruptedRunId.value = data.task_id;
      if (data.kind === "scrape") {
        scrapeTaskId.value = data.task_id;
        analysisReady.value = true;
        activeStep.value = "search";
        restoredTaskHint.value = "上次抓取因服务重启被中断；已抓数据已保存，可结束保存结果或重新开始抓取";
        return;
      }
      if (data.kind === "recrawl") {
        recrawlTaskId.value = data.task_id;
        resultLoaded.value = true;
        activeCategory.value = "uncertain";
        activeStep.value = "results";
        restoredTaskHint.value = "上次补抓因服务重启被中断；可结束保存已有结果";
        return;
      }
      restoredTaskHint.value = "上次 AI 筛选因服务重启被中断；重新开始 AI 筛选会接着上次进度，不重复消耗";
      scrapeTaskId.value = data.scrape_task_id || "";
      scrapeCompleted.value = Boolean(data.scrape_completed);
      screenTaskId.value = data.task_id;
      analysisReady.value = true;
      screenPanelOpen.value = false;
      activeStep.value = "screen";
      const savedFilters = data.frozen_filters || {};
      // T509：写入任务平台对应的草稿槽（platform-schema.md L157），
      // 不再用草稿平台槽 — 否则 zhilian 任务恢复后 filters 落到 boss 槽会被 boss schema 拒绝。
      const drafts = filterValues.value[filterPlatform];
      for (const key of Object.keys(drafts)) delete drafts[key];
      Object.assign(
        drafts,
        Object.fromEntries(
          Object.entries(savedFilters)
            .filter((entry): entry is [string, string[]] => Array.isArray(entry[1]))
            .map(([key, value]) => [key, value as string[]]),
        ),
      );
      profileSummary.value = data.profile_summary || "";
      return;
    }
    // 切片7：paused 状态从 DB 恢复（无内存工作线程，不能 poll）
    if (data.status === "paused") {
      pausedRunId.value = data.task_id;
      analysisReady.value = true;
      if (kind === "scrape") {
        activeStep.value = "search";
      } else if (kind === "screen") {
        scrapeTaskId.value = data.scrape_task_id || "";
        scrapeCompleted.value = Boolean(data.scrape_completed);
        screenTaskId.value = data.task_id;
        screenPanelOpen.value = false;
        activeStep.value = "screen";
        // T509：paused screen 任务也投影冻结筛选快照（platform-schema.md L157）
        const savedFilters = data.frozen_filters || {};
        const drafts = filterValues.value[filterPlatform];
        for (const key of Object.keys(drafts)) delete drafts[key];
        Object.assign(
          drafts,
          Object.fromEntries(
            Object.entries(savedFilters)
              .filter((entry): entry is [string, string[]] => Array.isArray(entry[1]))
              .map(([key, value]) => [key, value as string[]]),
          ),
        );
        profileSummary.value = data.profile_summary || "";
      } else {
        recrawlTaskId.value = data.task_id;
        resultLoaded.value = true;
        activeCategory.value = "uncertain";
        activeStep.value = "results";
      }
      // 拉 /api/task-state 拿完整计数画面（success/fail/unstarted/total）
      await enrichPausedSnapshot(data.task_id, snapshot, kind);
      const reason = data.pause_info?.error_reason || "任务已暂停";
      restoredTaskHint.value = `检测到暂停中的任务（${reason}），处理后点继续`;
      return;
    }
    if (kind === "scrape") {
      scrapeTaskId.value = data.task_id;
      scrapeBusy.value = true;
      scrapeSnapshot.value = snapshot;
      restoredTaskHint.value = "检测到抓取任务仍在后台运行，已自动接回";
      void pollTask(data.task_id, "scrape");
    } else if (kind === "screen") {
      screenTaskId.value = data.task_id;
      screenBusy.value = true;
      screenSnapshot.value = snapshot;
      restoredTaskHint.value = "检测到 AI 筛选任务仍在后台运行，已自动接回";
      void pollTask(data.task_id, "screen");
    } else {
      recrawlTaskId.value = data.task_id;
      recrawlBusy.value = true;
      recrawlSnapshot.value = snapshot;
      resultLoaded.value = true;
      activeCategory.value = "uncertain";
      activeStep.value = "results";
      restoredTaskHint.value = "检测到重抓任务仍在后台运行，已自动接回";
      void pollRecrawl(data.task_id);
    }
  } catch { /* non-critical: 接不回就当没有 */ }
}

const COMPLETED_TASK_STATUSES = new Set([
  "done",
  "completed",
  "completed_with_pending",
]);

function isCompletedTaskStatus(status?: string) {
  return Boolean(status && COMPLETED_TASK_STATUSES.has(status));
}

// 切片7：paused 任务从 /api/task-state 拉完整计数（FR-037）
async function enrichPausedSnapshot(
  runId: string,
  snapshot: TaskSnapshot,
  kind: "scrape" | "screen" | "recrawl",
) {
  try {
    const data = await apiRequest<{
      status?: string;
      stage?: string;
      progress?: number | Record<string, unknown>;
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
      result?: { updates?: Record<string, unknown> } | null;
    }>(`/api/task-state/${encodeURIComponent(runId)}`);
    snapshot.success_count = data.success_count;
    snapshot.fail_count = data.fail_count;
    snapshot.unstarted_count = data.unstarted_count;
    snapshot.total = data.total;
    snapshot.kept_count = data.kept_count;
    snapshot.dropped_count = data.dropped_count;
    snapshot.pending_count = data.pending_count;
    snapshot.source_total = data.source_total;
    snapshot.stage = data.stage || snapshot.stage;
    if (typeof data.progress === "number") {
      snapshot.progress = {
        ...(snapshot.progress || {}),
        overall_percent: data.progress,
      };
    } else if (data.progress) {
      snapshot.progress = { ...data.progress };
    }
    if (data.pause_info) snapshot.pause_info = data.pause_info;
    if (data.execution_config) snapshot.execution_config = data.execution_config;
    if (data.result?.updates) mergeRecrawlUpdates(data.result.updates);
  } catch { /* 退化到 progress 字段 */ }
  if (kind === "scrape") {
    scrapeTaskId.value = runId;
    scrapeSnapshot.value = { ...snapshot };
  } else if (kind === "screen") {
    screenTaskId.value = runId;
    screenSnapshot.value = { ...snapshot };
  } else {
    recrawlTaskId.value = runId;
    recrawlSnapshot.value = { ...snapshot };
  }
}

// T505/T509：按指定平台加载 schema（/api/filter-labels?platform=）。
// schemaLoader 内部用单调 reqId + AbortController + 响应平台校验，
// 保证旧平台响应晚到不覆盖当前平台（platform-schema.md L151-156）。
// T509：默认参数 = 草稿平台（新任务表单/简历建议路径）；restoreRunningTask 显式传入任务平台
// 以满足 platform-schema.md L157「先从任务响应设置任务平台，再加载对应 schema/城市」。
async function loadFilterLabels(platform: Platform = platformState.draft) {
  if (schemaLoader.loadedPlatform === platform && schemaRef.value) return;
  schemaBusy.value = true;
  try {
    const accepted = await schemaLoader.load(platform, (p, signal) =>
      apiRequest<PlatformFilterSchema>(
        `/api/filter-labels?platform=${encodeURIComponent(p)}`,
        { signal },
      ),
    );
    if (accepted && schemaLoader.data) {
      schemaRef.value = schemaLoader.data;
    }
  } catch { /* non-critical：loader 已记录 error */ }
  finally {
    if (schemaLoader.pendingPlatform === null) schemaBusy.value = false;
  }
}

// T505/T509：按指定平台加载城市目录（/api/options?platform=）。
// 与 loadFilterLabels 共用同一份序号 + 取消 + 校验逻辑（createAsyncResourceLoader）。
async function loadCityCatalog(platform: Platform = platformState.draft) {
  if (cityLoader.loadedPlatform === platform && cityCatalogRef.value) return;
  cityCatalogBusy.value = true;
  try {
    const accepted = await cityLoader.load(platform, (p, signal) =>
      apiRequest<PlatformCityCatalog>(
        `/api/options?platform=${encodeURIComponent(p)}`,
        { signal },
      ),
    );
    if (accepted && cityLoader.data) {
      cityCatalogRef.value = cityLoader.data;
    }
  } catch { /* non-critical：loader 已记录 error */ }
  finally {
    if (cityLoader.pendingPlatform === null) cityCatalogBusy.value = false;
  }
}

watch(() => props.profileId, () => {
  if (!pausedRunId.value && !scrapeBusy.value && !screenBusy.value && !recrawlBusy.value) {
    void loadLatestResult();
  }
});

onBeforeUnmount(() => {
  if (pollTimer) window.clearTimeout(pollTimer);
  document.removeEventListener("keydown", handleLifecycleDialogKeydown);
});

function notify(message: string, tone: Notice["tone"] = "info") {
  emit("notify", { message, tone });
}

function selectStep(step: string) {
  if (enabledSteps.value.includes(step as StepId)) activeStep.value = step as StepId;
}

function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement;
  selectedFile.value = input.files?.[0] || null;
}

function handleDrop(event: DragEvent) {
  dragActive.value = false;
  selectedFile.value = event.dataTransfer?.files?.[0] || null;
}

function initializeFromAnalysis(data: AnalyzeResponse) {
  const fields = data.fields || {};
  // T507：不替换权威标签 fieldLabels（platform-schema.md L147）。
  // filterGroups 由 schemaLoader 加载的 schema 驱动，不用 analyze 响应的 labels 覆盖。
  // data.labels 仍保留给 fallback 或后续调试，但不写入 fieldLabels。
  const rawKeywords = Array.isArray(fields.keyword) ? fields.keyword : [];
  keywords.value = rawKeywords
    .map((item) => typeof item === "string"
      ? { word: item, recommended: false }
      : {
        word: String((item as Record<string, unknown>).word || ""),
        recommended: Boolean((item as Record<string, unknown>).recommended),
      })
    .filter((item) => item.word);
  const recommended = keywords.value.filter((item) => item.recommended).map((item) => item.word);
  selectedKeywords.value = recommended.length ? recommended : keywords.value.map((item) => item.word);
  cityText.value = Array.isArray(fields.city)
    ? fields.city.map(String).join(", ")
    : String(fields.city || "");
  // T507：按当前已加载 schema 投影筛选建议（platform-schema.md L147）。
  // 只接受 schema 允许的字段；boss.stage 与 zhilian.company_nature 因 schema 不同不会串用。
  // 若 schema 未加载（如刚切平台尚未响应），保留空草稿，不投影。
  const drafts = filterValues.value[draftPlatform.value];
  for (const key of Object.keys(drafts)) delete drafts[key];
  const schema = schemaRef.value;
  if (schema) {
    for (const field of schema.fields) {
      const value = fields[field.key];
      const codes = (Array.isArray(value) ? value : value ? [value] : [])
        .map(String)
        .filter((item) => item !== "0");
      if (codes.length) drafts[field.key] = codes;
    }
  }
  profileSummary.value = String(fields.profile_summary || "");
}

async function analyzeResume() {
  if (!selectedFile.value) {
    notify("请先选择简历文件", "warning");
    return;
  }
  if (!aiConsent.value) {
    notify("请勾选 AI 解析同意后再继续", "warning");
    return;
  }
  uploadBusy.value = true;
  try {
    const form = new FormData();
    form.append("file", selectedFile.value);
    const data = await apiRequest<AnalyzeResponse>("/api/analyze-resume", {
      method: "POST",
      body: form,
    });
    await clearLatestResult();
    scrapeTaskId.value = "";
    screenTaskId.value = "";
    recrawlTaskId.value = "";
    scrapeSnapshot.value = null;
    screenSnapshot.value = null;
    recrawlSnapshot.value = null;
    pipelineResultRunId.value = "";
    resultPlatformFilter.value = "all";
    resultRunIds.value = { boss: "", zhilian: "" };
    pausedRunId.value = "";
    interruptedRunId.value = "";
    restoredTaskHint.value = "";
    scopePreview.value = null;
    scopePreviewBusy.value = false;
    activeCategory.value = "matched";
    initializeFromAnalysis(data);
    analysisReady.value = true;
    scrapeCompleted.value = false;
    resultLoaded.value = false;
    pipelineResult.value = null;
    rejectedIds.value = new Set();
    activeStep.value = "search";
    notify("简历分析完成，请确认关键词与城市", "success");
  } catch (error) {
    notify(errorMessage(error, "简历分析失败"), "error");
  } finally {
    uploadBusy.value = false;
  }
}

function toggleKeyword(word: string) {
  selectedKeywords.value = selectedKeywords.value.includes(word)
    ? selectedKeywords.value.filter((item) => item !== word)
    : [...selectedKeywords.value, word];
}

function addCustomKeyword() {
  const word = customKeyword.value.trim().replace(/[，,]+$/, "");
  if (!word) return;
  if (!keywords.value.some((item) => item.word === word)) {
    keywords.value.push({ word, recommended: false });
  }
  if (!selectedKeywords.value.includes(word)) selectedKeywords.value.push(word);
  customKeyword.value = "";
}

function confirmCities() {
  const cities = cityList.value;
  if (!cities.length) {
    notify("请输入至少一个城市", "warning");
    return;
  }
  notify(`已确认 ${cities.length} 个城市：${cities.join("、")}`, "success");
}

function addCustomCity() {
  const city = customCity.value.trim().replace(/[，,]+$/, "");
  if (!city) return;
  if (cityList.value.includes(city)) {
    customCity.value = "";
    return;
  }
  const current = cityText.value.trim().replace(/[，,]+$/, "");
  cityText.value = current ? `${current},${city}` : city;
  customCity.value = "";
}

function removeCity(city: string) {
  cityText.value = cityList.value.filter((c) => c !== city).join(",");
}

function toggleFilter(key: string, code: string) {
  const drafts = filterValues.value[draftPlatform.value];
  const values = drafts[key] || [];
  drafts[key] = values.includes(code)
    ? values.filter((value) => value !== code)
    : [...values, code];
}

async function loadAdvancedSettings() {
  try {
    const data = await apiRequest<Partial<AdvancedSettingsState> & { settings?: Record<string, number | string> }>("/api/advanced-settings");
    advancedSettings.value = { ...advancedSettings.value, ...(data.settings || {}) };
    if (data.selection) executionSelection.value = data.selection;
    mergeManualRanges(data.manual_ranges);
  } catch (error) {
    notify(errorMessage(error, "高级设置加载失败"), "warning");
  }
}

const SPEED_FIELDS = [
  "pages", "inter_combo_delay", "detail_batch_size", "detail_interval",
  "detail_reset_every", "detail_batch_cooldown",
  "detail_tab_pool_size", "screen_batch_size",
  "screen_concurrency", "match_batch_size", "match_concurrency",
] as const;

function currentExecutionSettings(): ExecutionSettings {
  return Object.fromEntries(SPEED_FIELDS.map((field) => [field, Number(advancedSettings.value[field])])) as unknown as ExecutionSettings;
}

async function refreshScopePreview(): Promise<FrozenSearchScope | null> {
  if (!selectedKeywords.value.length) {
    scopePreview.value = null;
    return null;
  }
  scopePreviewBusy.value = true;
  try {
    const data = await settingsApi.previewScope({
      platform: draftPlatform.value,
      keywords: selectedKeywords.value,
      scope_kind: cityList.value.length ? "cities" : "nationwide",
      cities: cityList.value.length ? cityList.value : [],
      pages_per_combination: pagesValue.value,
    });
    scopePreview.value = normalizeScopePreview(data);
    return scopePreview.value;
  } catch (error) {
    scopePreview.value = null;
    notify(errorMessage(error, "搜索范围校验失败"), "warning");
    return null;
  } finally {
    scopePreviewBusy.value = false;
  }
}

async function selectExecutionMode(selection: ExecutionSelection) {
  const preview = scopePreview.value || await refreshScopePreview();
  if (!preview) return;
  advancedBusy.value = true;
  try {
    const data = await settingsApi.selectMode(selection, preview.scope_digest);
    const returned = (data as unknown as { settings?: ExecutionSettings; config?: ExecutionSettings }).settings
      || (data as unknown as { config?: ExecutionSettings }).config;
    if (!returned) throw new Error("模式响应缺少完整执行配置");
    advancedSettings.value = { ...advancedSettings.value, ...returned };
    executionSelection.value = selection;
  } catch (error) {
    notify(errorMessage(error, "执行模式切换失败"), "error");
  } finally {
    advancedBusy.value = false;
  }
}

async function saveAdvancedSettings() {
  advancedBusy.value = true;
  try {
    const data = await settingsApi.saveCustom(currentExecutionSettings());
    advancedSettings.value = { ...advancedSettings.value, ...(data.settings || {}) };
    executionSelection.value = "custom";
    notify("高级设置已保存", "success");
  } catch (error) {
    notify(errorMessage(error, "高级设置保存失败"), "error");
  } finally {
    advancedBusy.value = false;
  }
}

async function startScrape() {
  const scriptParams = buildSearchScriptParams(selectedKeywords.value, effectiveSearchCities.value);
  if (!scriptParams.keyword || !scriptParams.city.length) {
    notify("请确认至少一个关键词和一个城市", "warning");
    return;
  }
  const preview = scopePreview.value || await refreshScopePreview();
  if (!preview) return;
  // 开始抓取后自动收拢两个配置面板（用户可随时手动展开查看）。
  searchPanelsOpen.value = false;
  scrapeBusy.value = true;
  scrapeCompleted.value = false;
  resultLoaded.value = false;
  pipelineResult.value = null;
  interruptedRunId.value = "";
  scrapeSnapshot.value = { status: "running", progress: { message: "正在创建抓取任务…" }, logs: [] };
  try {
    const data = await apiRequest<{ task_id: string }>("/api/execute-search", {
      method: "POST",
      json: { platform: draftPlatform.value, script_params: scriptParams, scope_digest: preview.scope_digest },
    });
    scrapeTaskId.value = data.task_id;
    await pollTask(data.task_id, "scrape");
  } catch (error) {
    scrapeBusy.value = false;
    scrapeSnapshot.value = { status: "failed", error: errorMessage(error, "抓取启动失败") };
    notify(errorMessage(error, "抓取启动失败"), "error");
    // D7：未登录被拒时给出账号级登录引导并跳转账号面板。
    if (error instanceof ApiError && isLoginErrorCode(error.payload.error_code)) {
      void showLoginGuide(draftPlatform.value);
    }
  }
}

async function cancelScrape() {
  if (!scrapeTaskId.value) return;
  // 先停轮询，避免取消后还去拿旧状态
  if (pollTimer) { window.clearTimeout(pollTimer); pollTimer = undefined; }
  try {
    await apiRequest(`/api/task/cancel/${encodeURIComponent(scrapeTaskId.value)}`, {
      method: "POST",
    });
    // 后端会立刻关浏览器并标 cancelled；这里直接复位，不等下一次轮询
    scrapeBusy.value = false;
    restoredTaskHint.value = "";
    scrapeSnapshot.value = { status: "cancelled", progress: { message: "已停止抓取" }, logs: [], error: "" };
    interruptedRunId.value = "";
    notify("已停止抓取", "warning");
  } catch (error) {
    // 取消接口失败时不要卡死：恢复轮询让前端看真实状态
    notify(errorMessage(error, "停止失败，请重试"), "error");
    await pollTask(scrapeTaskId.value, "scrape");
  }
}

async function continueScrape() {
  if (!scrapeTaskId.value || scrapeBusy.value) return;
  scrapeBusy.value = true;
  scrapeCompleted.value = false;
  pausedRunId.value = ""; // 切片7：清掉 DB paused 标记，进入内存工作模式
  interruptedRunId.value = "";
  restoredTaskHint.value = "";
  scrapeSnapshot.value = { status: "running", progress: { message: "正在从断点继续…" }, logs: [] };
  try {
    const data = await apiRequest<{ task_id: string; skipped: number; old_jobs: number }>(
      `/api/task/continue/${encodeURIComponent(scrapeTaskId.value)}`,
      { method: "POST" },
    );
    scrapeTaskId.value = data.task_id;
    pollRetryCount = 0;
    await pollTask(data.task_id, "scrape");
  } catch (error) {
    scrapeBusy.value = false;
    scrapeSnapshot.value = { status: "failed", progress: {}, logs: [], error: errorMessage(error, "断点续抓启动失败") };
  }
}

async function startAiScreen() {
  if (!scrapeCompleted.value || !scrapeTaskId.value) {
    notify("请先完成本轮抓取，再开始 AI 筛选", "warning");
    return;
  }
  screenPanelOpen.value = false;
  screenBusy.value = true;
  pausedRunId.value = ""; // 切片7：清掉 DB paused 标记，进入内存工作模式
  interruptedRunId.value = "";
  restoredTaskHint.value = "";
  screenSnapshot.value = { status: "running", progress: { message: "正在创建 AI 筛选任务…" }, logs: [] };
  try {
    const data = await apiRequest<{ task_id: string; resuming?: boolean }>("/api/ai-screen", {
      method: "POST",
      json: {
        // T506/T508：只提交当前草稿平台的筛选草稿 + schema 版本。
        // 不发 platform（父 run 已冻结平台，后端从父 run 读）；不发 BOSS 的 stage 给智联 run。
        screening_fields: filterValues.value[draftPlatform.value],
        filter_schema_version: schemaRef.value?.schema_version ?? null,
        profile_summary: profileSummary.value,
        scrape_task_id: scrapeTaskId.value,
      },
    });
    screenTaskId.value = data.task_id;
    if (data.resuming) {
      screenSnapshot.value = { status: "running", progress: { message: "检测到上次未完成的筛选，接着上次进度继续…" }, logs: [] };
      notify("检测到上次未完成的筛选，已自动续跑", "info");
    }
    await pollTask(data.task_id, "screen");
  } catch (error) {
    screenBusy.value = false;
    screenSnapshot.value = { status: "failed", error: errorMessage(error, "AI 筛选启动失败") };
    notify(errorMessage(error, "AI 筛选启动失败"), "error");
  }
}

async function continueAiScreen() {
  const runId = pausedRunId.value || screenTaskId.value;
  if (!runId || screenBusy.value) return;
  screenBusy.value = true;
  restoredTaskHint.value = "";
  interruptedRunId.value = "";
  screenSnapshot.value = {
    status: "running", progress: { message: "正在从 AI 断点继续…" }, logs: [],
  };
  try {
    const data = await apiRequest<{ task_id: string }>(
      `/api/task/continue/${encodeURIComponent(runId)}`,
      { method: "POST" },
    );
    pausedRunId.value = "";
    screenTaskId.value = data.task_id;
    pollRetryCount = 0;
    await pollTask(data.task_id, "screen");
  } catch (error) {
    screenBusy.value = false;
    screenSnapshot.value = {
      status: "paused", progress: {}, logs: [],
      error: errorMessage(error, "AI 断点继续失败"),
    };
  }
}

async function cancelAiScreen() {
  if (!screenTaskId.value) return;
  // 先停轮询，避免取消后还去拿旧状态
  if (pollTimer) { window.clearTimeout(pollTimer); pollTimer = undefined; }
  try {
    await apiRequest(`/api/task/cancel/${encodeURIComponent(screenTaskId.value)}`, {
      method: "POST",
    });
    // 后端会标 cancelled；这里直接复位，不等下一次轮询
    screenBusy.value = false;
    restoredTaskHint.value = "";
    interruptedRunId.value = "";
    screenSnapshot.value = { status: "cancelled", progress: { message: "已停止筛选" }, logs: [], error: "" };
    notify("已停止筛选", "warning");
  } catch (error) {
    // 取消接口失败时不要卡死：恢复轮询让前端看真实状态
    notify(errorMessage(error, "停止失败，请重试"), "error");
    await pollTask(screenTaskId.value, "screen");
  }
}

// 切片7：统一取消 paused 任务（FR-024）。
async function cancelPausedTask(runId: string) {
  if (!runId) return;
  try {
    await apiRequest(`/api/task/cancel/${encodeURIComponent(runId)}`, {
      method: "POST",
    });
    scrapeBusy.value = false;
    screenBusy.value = false;
    restoredTaskHint.value = "";
    pausedRunId.value = "";
    interruptedRunId.value = "";
    if (scrapeSnapshot.value) scrapeSnapshot.value = { status: "cancelled", progress: { message: "已取消任务" }, logs: [], error: "" };
    if (screenSnapshot.value) screenSnapshot.value = { status: "cancelled", progress: { message: "已取消任务" }, logs: [], error: "" };
    notify("已取消任务，已有结果保留", "warning");
  } catch (error) {
    notify(errorMessage(error, "取消失败，请重试"), "error");
  }
}

async function finishPausedTask(runId: string) {
  if (!runId) return;
  try {
    const data = await apiRequest<{
      result?: PipelineResult;
      snapshot_run_id?: string;
    }>(`/api/task/finish/${encodeURIComponent(runId)}`, { method: "POST" });
    scrapeBusy.value = false;
    screenBusy.value = false;
    recrawlBusy.value = false;
    restoredTaskHint.value = "";
    pausedRunId.value = "";
    interruptedRunId.value = "";
    const finished: TaskSnapshot = {
      status: "completed_with_pending", stage: "done",
      progress: { message: "已结束并保存部分结果" }, logs: [], error: "",
    };
    if (scrapeSnapshot.value) scrapeSnapshot.value = finished;
    if (screenSnapshot.value) screenSnapshot.value = finished;
    if (recrawlSnapshot.value) recrawlSnapshot.value = null;
    if (data.result) {
      setPipelineResult(data.result);
      activeStep.value = "results";
    }
    notify("任务已结束，已完成结果已保存", "success");
  } catch (error) {
    notify(errorMessage(error, "结束任务失败"), "error");
  }
}

// 指数退避：7 次 / 64s 上限。前 5 次快速重试（4s→8s→16s→32s→64s），
// 后 2 次保持 64s，总等待约 4 分钟。达上限后主动放弃并提示用户。
const POLL_MAX_RETRIES = 7;
const POLL_BASE_DELAY = 4000;
const POLL_MAX_DELAY = 64000;

let pollRetryCount = 0;

async function pollTask(taskId: string, kind: "scrape" | "screen") {
  try {
    const data = await apiRequest<TaskSnapshot>(`/api/task-state/${encodeURIComponent(taskId)}`);
    if (kind === "scrape") scrapeSnapshot.value = data;
    else screenSnapshot.value = data;

    if (isCompletedTaskStatus(data.status)) {
      pollRetryCount = 0;
      restoredTaskHint.value = "";
      if (kind === "scrape") {
        scrapeBusy.value = false;
        scrapeCompleted.value = true;
        notify(
          data.status === "completed_with_pending"
            ? "抓取完成，但有待确认，请继续检查筛选条件"
            : "抓取完成，请继续确认 AI 筛选条件",
          data.status === "completed_with_pending" ? "warning" : "success",
        );
      } else {
        screenBusy.value = false;
        // 实时路径与刷新路径统一：任务完成后拉双平台合并结果（R2），
        // 避免只 set 单平台结果导致结果页切平台显示 0。
        const fetched = await fetchMergedLatestResult();
        if (fetched) setPipelineResult(fetched.merged);
        activeStep.value = "results";
        notify(
          data.status === "completed_with_pending"
            ? "AI 筛选完成，但有岗位待确认"
            : "AI 筛选完成",
          data.status === "completed_with_pending" ? "warning" : "success",
        );
      }
      return;
    }
    if (data.status === "cancelled") {
      pollRetryCount = 0;
      restoredTaskHint.value = "";
      if (kind === "scrape") scrapeBusy.value = false;
      else screenBusy.value = false;
      // 不弹 error 通知：cancelScrape 已经弹过了；这里是轮询兜底（如刷新后接回的取消态）
      return;
    }
    if (data.status === "paused") {
      pollRetryCount = 0;
      if (kind === "scrape") scrapeBusy.value = false;
      else screenBusy.value = false;
      notify(data.error || "任务已暂停，请处理后点继续", "warning");
      return;
    }
    if (data.status === "failed") {
      pollRetryCount = 0;
      restoredTaskHint.value = "";
      if (kind === "scrape") scrapeBusy.value = false;
      else screenBusy.value = false;
      notify(data.error || "任务执行失败", "error");
      // D7：任务因未登录失败时给出账号级登录引导。
      if (kind === "scrape" && isLoginErrorCode(data.pause_info?.error_code)) {
        void showLoginGuide(data.platform || draftPlatform.value);
      }
      return;
    }
    pollTimer = window.setTimeout(() => void pollTask(taskId, kind), 1800);
  } catch (error) {
    pollRetryCount += 1;
    if (pollRetryCount > POLL_MAX_RETRIES) {
      // 达上限，主动放弃
      pollRetryCount = 0;
      if (kind === "scrape") scrapeBusy.value = false;
      else screenBusy.value = false;
      const failed: TaskSnapshot = {
        status: "failed",
        progress: { message: "任务执行失败" },
        logs: [],
        error: "进度获取连续失败，请检查网络后重试",
      };
      if (kind === "scrape") scrapeSnapshot.value = failed;
      else screenSnapshot.value = failed;
      notify("进度获取连续失败，请检查网络后重试", "error");
      return;
    }
    const delay = Math.min(POLL_BASE_DELAY * 2 ** (pollRetryCount - 1), POLL_MAX_DELAY);
    const retrying: TaskSnapshot = {
      status: "running",
      // 文案改温和：大多数情况是后端正忙没及时回，不是真失败
      progress: { message: `正在获取进度（${pollRetryCount}/${POLL_MAX_RETRIES}）…` },
      logs: [],
      error: "",
    };
    if (kind === "scrape") scrapeSnapshot.value = retrying;
    else screenSnapshot.value = retrying;
    pollTimer = window.setTimeout(() => void pollTask(taskId, kind), delay);
  }
}

function setPipelineResult(result: PipelineResult) {
  pipelineResult.value = result;
  // specs/004：新 run 结果替换完成 → 递增 resultEpoch，通知 JobWorkspace 重置筛选/排序。
  resultEpoch.value += 1;
  const sourceRunId = (result as Record<string, unknown>).source_run_id;
  if (typeof sourceRunId === "string") pipelineResultRunId.value = sourceRunId;
  analysisReady.value = true;
  scrapeCompleted.value = true;
  resultLoaded.value = true;
  const groups = partitionPipelineResult(result);
  activeCategory.value = groups.matched.length ? "matched"
    : groups.uncertain.length ? "uncertain"
      : groups.unmatched.length ? "unmatched" : "dropped";
}

async function loadLatestResult() {
  if (pausedRunId.value || interruptedRunId.value || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return;
  const fetched = await fetchMergedLatestResult();
  if (!fetched) return;
  const { merged, newer } = fetched;
  pipelineResultRunId.value = newer.data.source_run_id || "";
  setPipelineResult(merged);
  const ps = (newer.data.result as Record<string, unknown>).profile_summary;
  if (typeof ps === "string" && ps.trim()) profileSummary.value = ps;
  const snapshotStatus = newer.data.status === "completed_with_pending" ? "completed_with_pending" : "completed";
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
    };
  };
}

async function fetchMergedLatestResult(): Promise<MergedLatestResult | null> {
  try {
    // 分别拉两个平台各自的最近结果，合并展示（后端 T409 按平台查询）。
    const base = props.profileId ? `&profile_id=${encodeURIComponent(props.profileId)}` : "";
    const fetchOne = (platform: "boss" | "zhilian") => apiRequest<{
      has_result?: boolean;
      source_run_id?: string;
      result?: PipelineResult;
      status?: string;
      started_at?: number;
      finished_at?: number;
      execution_config?: Record<string, unknown> | null;
    }>(`/api/latest-pipeline-result?platform=${platform}${base}`).catch(() => null);
    const [bossData, zhilianData] = await Promise.all([fetchOne("boss"), fetchOne("zhilian")]);
    if (pausedRunId.value || interruptedRunId.value || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return null;

    const parts = [
      bossData?.has_result && bossData.result ? { platform: "boss" as const, data: bossData } : null,
      zhilianData?.has_result && zhilianData.result ? { platform: "zhilian" as const, data: zhilianData } : null,
    ].filter(Boolean) as { platform: "boss" | "zhilian"; data: NonNullable<typeof bossData> }[];
    if (!parts.length) return null;

    // 每个岗位标记来源 run（单岗位补抓/单 JD 动作需要定位来源）。
    for (const part of parts) {
      resultRunIds.value[part.platform] = part.data.source_run_id || "";
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
    const newer = parts.length === 1
      ? parts[0]
      : (Number(parts[0].data.started_at || 0) >= Number(parts[1].data.started_at || 0) ? parts[0] : parts[1]);

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
    return { merged, newer };
  } catch (error) {
    notify(errorMessage(error, "上次结果暂时无法恢复"), "warning");
    return null;
  }
}

async function clearLatestResult() {
  try {
    await apiRequest<{ ok?: boolean; cleared?: boolean }>("/api/reset-latest-result", {
      method: "POST",
    });
  } catch {
    // 清理失败不阻断新流程；前端状态已经先清空
  }
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
    notify("已导出匹配/不匹配分组 CSV", "success");
  } catch (error) {
    notify(errorMessage(error, "导出 CSV 失败"), "error");
  } finally {
    exportBusy.value = false;
  }
}

function resetWorkflow() {
  if (pollTimer) window.clearTimeout(pollTimer);
  activeStep.value = "upload";
  analysisReady.value = false;
  scrapeCompleted.value = false;
  resultLoaded.value = false;
  selectedFile.value = null;
  aiConsent.value = false;
  scrapeTaskId.value = "";
  scrapeSnapshot.value = null;
  screenTaskId.value = "";
  screenSnapshot.value = null;
  recrawlTaskId.value = "";
  recrawlSnapshot.value = null;
  pipelineResult.value = null;
  pipelineResultRunId.value = "";
  resultPlatformFilter.value = "all";
  resultRunIds.value = { boss: "", zhilian: "" };
  activeCategory.value = "matched";
  rejectedIds.value = new Set();
  pausedRunId.value = "";
  interruptedRunId.value = "";
  restoredTaskHint.value = "";
  scopePreview.value = null;
  scopePreviewBusy.value = false;
  keywords.value = [];
  selectedKeywords.value = [];
  customKeyword.value = "";
  cityText.value = "";
  customCity.value = "";
  // T506：重置两个平台的筛选草稿
  filterValues.value = { boss: {}, zhilian: {} };
  profileSummary.value = "";
  scrapeBusy.value = false;
  screenBusy.value = false;
  recrawlBusy.value = false;
  recrawlRetryCount = 0;
  screenPanelOpen.value = true;
  void clearLatestResult();
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
  return String(job.job_id || job.id || job.canonical_url || "");
}

function withBusy(setRef: typeof feedbackBusyIds, id: string, active: boolean) {
  const next = new Set(setRef.value);
  if (active) next.add(id);
  else next.delete(id);
  setRef.value = next;
}

async function ensureFeedbackProfile(): Promise<string> {
  if (props.profileId) return props.profileId;
  const profile = await apiRequest<CandidateProfile>("/api/profiles", {
    method: "POST",
    json: { name: "岗位发现", confirmed_fields: {} },
  });
  emit("profile-created", profile);
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
    notify(marked ? "已取消收藏" : "已收藏", marked ? "info" : "success");
  } catch (error) {
    notify(errorMessage(error, "收藏状态更新失败"), "error");
  } finally {
    withBusy(feedbackBusyIds, id, false);
  }
}

async function toggleRejected(job: JobItem) {
  const id = jobId(job);
  if (!id || feedbackBusyIds.value.has(id)) return;
  if (job._marked === "interested") await toggleInterest(job);
  const next = new Set(rejectedIds.value);
  if (next.has(id)) {
    next.delete(id);
    notify("已撤销不感兴趣", "info");
  } else {
    next.add(id);
    job._marked = null;
    notify("已标记不感兴趣（仅本轮有效）", "info");
  }
  rejectedIds.value = next;
}

async function retryJd(job: JobItem) {
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
    }>(
      `/api/pipeline/jobs/${encodeURIComponent(id)}/jd`, {
      method: "POST",
      json: {
        // 单岗位动作优先用岗位自身来源 run（合并视图下跨平台也准确）。
        source_run_id: job._result_run_id || pipelineResultRunId.value,
        source_url: job.source_url || job.job_link || job.canonical_url,
        profile_summary: profileSummary.value,
      },
    });
    if (data.task_id) {
      recrawlBusy.value = true;
      recrawlTaskId.value = data.task_id;
      recrawlSnapshot.value = {
        status: "running",
        progress: { message: "正在补抓这条岗位…" },
        logs: [],
        error: "",
      };
      await pollRecrawl(data.task_id);
      return;
    }
    job.jd = data.jd || "";
    if (data.verdict) {
      job.verdict = data.verdict as JobItem["verdict"];
      job.verdict_reason = data.verdict_reason || "";
      job.caveats = data.caveats || [];
      notify(`JD 已补抓，AI 判定：${data.verdict === "match" ? "匹配" : "不匹配"}`, "success");
    } else {
      notify("JD 已补抓（AI 未判定，可点全部重抓触发精筛）", "success");
    }
  } catch (error) {
    notify(errorMessage(error, "JD 补抓失败"), "error");
  } finally {
    withBusy(jdBusyIds, id, false);
  }
}

// 待确认项「全部重抓」：缺 JD 的补 CDP 抓取，有 JD 的用画像重跑 AI 精筛。
// 复用现有轮询机制显示进度（已完成 X / 共 N），结果原地合并进当前结果，保留当前 tab。
async function recrawlUncertain() {
  const ids = groups.value.uncertain.map((job) => jobId(job)).filter(Boolean);
  if (!ids.length) {
    notify("没有待确认的岗位", "info");
    return;
  }
  if (recrawlBusy.value) return;
  recrawlBusy.value = true;
  recrawlSnapshot.value = {
    status: "running",
    progress: { message: `准备重抓 ${ids.length} 个待确认岗位…` },
    logs: [],
    error: "",
  };
  interruptedRunId.value = "";
  try {
    const data = await apiRequest<{ task_id: string }>("/api/pipeline/recrawl", {
      method: "POST",
      json: {
        // “全部重抓”只在单平台视图可见，用当前视图平台的来源 run。
        source_run_id: (
          resultPlatformFilter.value !== "all"
            && resultRunIds.value[resultPlatformFilter.value]
        ) || pipelineResultRunId.value,
        job_ids: ids,
        profile_summary: profileSummary.value,
      },
    });
    recrawlTaskId.value = data.task_id;
    await pollRecrawl(data.task_id);
  } catch (error) {
    recrawlBusy.value = false;
    recrawlSnapshot.value = {
      status: "failed", progress: {}, logs: [], error: errorMessage(error, "重抓启动失败"),
    };
    notify(errorMessage(error, "重抓启动失败"), "error");
  }
}

async function continueRecrawl() {
  if (!recrawlTaskId.value || recrawlBusy.value) return;
  const taskId = recrawlTaskId.value;
  recrawlBusy.value = true;
  restoredTaskHint.value = "";
  interruptedRunId.value = "";
  recrawlSnapshot.value = {
    status: "running", progress: { message: "正在从重抓断点继续…" }, logs: [],
  };
  try {
    const data = await apiRequest<{ task_id?: string }>(
      `/api/task/continue/${encodeURIComponent(taskId)}`,
      { method: "POST" },
    );
    pausedRunId.value = "";
    recrawlTaskId.value = data.task_id || taskId;
    recrawlRetryCount = 0;
    await pollRecrawl(recrawlTaskId.value);
  } catch (error) {
    recrawlBusy.value = false;
    recrawlSnapshot.value = {
      status: "paused", progress: {}, logs: [],
      error: errorMessage(error, "重抓断点继续失败"),
    };
  }
}

async function pollRecrawl(taskId: string) {
  try {
    const data = await apiRequest<TaskSnapshot>(`/api/task-state/${encodeURIComponent(taskId)}`);
    recrawlSnapshot.value = data;
    const liveUpdates = (data.result as unknown as { updates?: Record<string, unknown> } | undefined)?.updates;
    if (liveUpdates) mergeRecrawlUpdates(liveUpdates as Record<string, unknown>);
    if (isCompletedTaskStatus(data.status)) {
      recrawlRetryCount = 0;
      recrawlBusy.value = false;
      const updates = (data.result as unknown as { updates?: Record<string, unknown> } | undefined)?.updates;
      if (updates) mergeRecrawlUpdates(updates as Record<string, unknown>);
      notify(
        data.status === "completed_with_pending"
          ? "重抓完成，但仍有岗位待确认"
          : "待确认岗位已重抓完成",
        data.status === "completed_with_pending" ? "warning" : "success",
      );
      window.setTimeout(() => { recrawlSnapshot.value = null; }, 3000);
      return;
    }
    if (data.status === "cancelled") {
      recrawlRetryCount = 0;
      recrawlBusy.value = false;
      notify("已停止重抓", "warning");
      window.setTimeout(() => { recrawlSnapshot.value = null; }, 3000);
      return;
    }
    if (data.status === "paused") {
      recrawlRetryCount = 0;
      recrawlBusy.value = false;
      notify(data.error || "重抓已暂停，请处理后点继续", "warning");
      return;
    }
    if (data.status === "failed") {
      recrawlRetryCount = 0;
      recrawlBusy.value = false;
      notify(data.error || "重抓失败", "error");
      window.setTimeout(() => { recrawlSnapshot.value = null; }, 5000);
      return;
    }
    pollTimer = window.setTimeout(() => void pollRecrawl(taskId), 1800);
  } catch (error) {
    recrawlRetryCount += 1;
    if (recrawlRetryCount > POLL_MAX_RETRIES) {
      recrawlRetryCount = 0;
      recrawlBusy.value = false;
      recrawlSnapshot.value = {
        status: "failed",
        progress: { message: "重抓进度获取连续失败" },
        logs: [],
        error: "重抓进度获取连续失败，请检查后重试",
      };
      notify("重抓进度获取连续失败，请检查后重试", "error");
      return;
    }
    const delay = Math.min(POLL_BASE_DELAY * 2 ** (recrawlRetryCount - 1), POLL_MAX_DELAY);
    pollTimer = window.setTimeout(() => void pollRecrawl(taskId), delay);
  }
}

// 把后端回写的 {jd/verdict/verdict_reason/caveats} 原地合并到当前结果，
// 已解决的项会随 groups 重算自动离开待确认 tab，未解决项原地保留。
function mergeRecrawlUpdates(updates: Record<string, unknown>) {
  const result = pipelineResult.value as (PipelineResult & { jobs?: JobItem[] }) | null;
  if (!result || !Array.isArray(result.jobs)) return;
  for (const job of result.jobs) {
    const id = jobId(job);
    const upd = updates[id];
    if (!upd || typeof upd !== "object") continue;
    const map = upd as Record<string, unknown>;
    if (typeof map.jd !== "undefined") job.jd = String(map.jd ?? "");
    if (typeof map.verdict !== "undefined") job.verdict = map.verdict as JobItem["verdict"];
    if (typeof map.verdict_reason !== "undefined") job.verdict_reason = String(map.verdict_reason ?? "");
    if (Array.isArray(map.caveats)) job.caveats = map.caveats as string[];
  }
}

// ---------------------------------------------------------------------------
// Task 009：详情生命周期控件接入（JobWorkspace actions slot）
// ---------------------------------------------------------------------------

/**
 * 传给 JobLifecycleActions 的岗位身份投影：
 * 权威三元组（job 自身冻结 platform + platform_job_id + canonical_url）完整时
 * 优先按三元组解析，避免把 pipeline pending 映射的平台原始 ID 误当内部 job_id。
 * 三元组不完整时保留原 job（由组件内部阻断写操作），绝不用当前 UI 平台补值。
 */
function lifecycleJob(job: JobItem): JobItem {
  if (job.platform && job.platform_job_id && job.canonical_url) {
    return { ...job, id: undefined, job_id: undefined };
  }
  return job;
}

function onJobFeedbackChanged(payload: { profileId: string; jobId: string }) {
  emit("job-feedback-changed", payload);
}

// ---------------------------------------------------------------------------
// 轨迹浮窗：大卡片收敛为“查看轨迹”小按钮，点击后居中弹窗展示全部内容
// ---------------------------------------------------------------------------

const lifecycleDialogOpen = ref(false);
const lifecycleDialogJob = ref<JobItem | null>(null);

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

watch(lifecycleDialogOpen, (open) => {
  if (open) document.addEventListener("keydown", handleLifecycleDialogKeydown);
  else document.removeEventListener("keydown", handleLifecycleDialogKeydown);
});

// ---------------------------------------------------------------------------
// 顶栏本轮状态胶囊数据（纯派生，不发请求）：
// 运行中（抓取/筛选/补抓）显示进行中态；有结果时显示已判定数；其余空闲态上抛 null。
// 平台优先取任务自身平台（恢复任务快照携带），缺省时用草稿平台。
// ---------------------------------------------------------------------------
const roundStatusPayload = computed(() => {
  const platform: Platform = scrapeSnapshot.value?.platform
    || screenSnapshot.value?.platform
    || draftPlatform.value;
  if (scrapeBusy.value || recrawlBusy.value) return { platform, phase: "scraping" as const, judged: 0 };
  if (screenBusy.value) return { platform, phase: "screening" as const, judged: 0 };
  if (resultLoaded.value && pipelineResult.value) {
    const g = groups.value;
    const judged = g.matched.length + g.unmatched.length + g.uncertain.length + g.dropped.length;
    return { platform, phase: "judged" as const, judged };
  }
  return null;
});
watch(roundStatusPayload, (payload) => {
  emit("round-status", payload);
}, { immediate: true });
</script>

<template>
  <main
    class="view-shell"
    :class="{ 'results-view': activeStep === 'results' }"
    data-testid="discovery-view"
  >
    <div v-if="restoredTaskHint" class="restore-banner" role="status">
      <LoaderCircle :size="16" class="spin" aria-hidden="true" />
      <span>{{ restoredTaskHint }}</span>
      <button type="button" class="restore-close" aria-label="关闭提示" @click="restoredTaskHint = ''">×</button>
    </div>
    <div
      class="platform-segment"
      role="tablist"
      aria-label="新任务目标平台"
      :data-testid="`platform-current-${draftPlatform}`"
      :data-loaded-schema-platform="schemaRef?.platform || ''"
      :data-loaded-city-platform="cityCatalogRef?.platform || ''"
    >
      <button
        v-for="platform in (['boss', 'zhilian'] as const)"
        :key="platform"
        type="button"
        role="tab"
        :aria-selected="draftPlatform === platform"
        :class="['platform-segment-btn', { active: draftPlatform === platform }]"
        :data-testid="`platform-segment-${platform}`"
        :disabled="scopeLocked"
        :title="scopeLocked ? '任务进行中，平台已锁定' : undefined"
        @click="setDraftPlatform(platform)"
      >{{ platform === 'boss' ? 'BOSS' : '智联' }}</button>
    </div>
    <StepNavigator
      :steps="steps"
      :active-step="activeStep"
      :enabled-steps="enabledSteps"
      :completed-steps="completedSteps"
      @select="selectStep"
    />

    <section class="view-stage">
      <header class="stage-header">
        <div>
          <span class="eyebrow">{{ currentCopy.eyebrow }}</span>
          <h1>{{ currentCopy.title }}</h1>
          <p>{{ currentCopy.description }}</p>
        </div>
        <div v-if="activeStep === 'results'" class="stage-actions">
          <button
            class="button secondary"
            type="button"
            data-testid="export-result-csv"
            :disabled="exportBusy || !resultLoaded"
            @click="exportResultCsv"
          >
            <Download :size="17" aria-hidden="true" />{{ exportBusy ? "导出中…" : "导出 CSV" }}
          </button>
          <button class="button secondary" type="button" @click="resetWorkflow">
            <RotateCcw :size="17" aria-hidden="true" />开始新一轮
          </button>
        </div>
      </header>

      <section v-if="activeStep === 'upload'" class="content-card workflow-card upload-layout">
        <div class="workflow-copy">
          <span class="card-kicker">简历只会发往你配置的 AI 服务</span>
          <h2>上传后生成建议，不替你做最终决定</h2>
          <p>分析会得到关键词、城市和六类筛选条件。每一项都可以在后续步骤调整。</p>
          <ul class="feature-list">
            <li><Check :size="17" aria-hidden="true" />抓取前确认关键词和城市</li>
            <li><Check :size="17" aria-hidden="true" />筛选前确认六类业务条件</li>
            <li><Check :size="17" aria-hidden="true" />AI 失败进入待确认，不伪装成匹配</li>
          </ul>
        </div>

        <div class="upload-form">
          <label
            class="file-drop"
            :class="{ active: dragActive, chosen: selectedFile }"
            @dragover.prevent="dragActive = true"
            @dragleave.prevent="dragActive = false"
            @drop.prevent="handleDrop"
          >
            <input
              type="file"
              accept=".txt,.pdf,.docx"
              data-testid="resume-input"
              @change="chooseFile"
            >
            <UploadCloud :size="30" aria-hidden="true" />
            <strong>{{ selectedFile ? selectedFile.name : "选择或拖入简历" }}</strong>
            <span>TXT / PDF / DOCX，最大尺寸由本地后端校验</span>
          </label>
          <label class="consent-line">
            <input v-model="aiConsent" type="checkbox" data-testid="resume-consent">
            <span>我知悉简历文本会发送到已配置的 AI 服务用于本次分析。</span>
          </label>
          <button
            class="button primary wide-button"
            type="button"
            data-testid="analyze-resume"
            :disabled="uploadBusy"
            @click="analyzeResume"
          >
            <Sparkles :size="18" aria-hidden="true" />{{ uploadBusy ? "分析中…" : "上传并分析" }}
          </button>
          <button
            class="button ghost wide-button"
            type="button"
            @click="analysisReady = true; activeStep = 'search'"
          >
            跳过简历，直接手动搜索
          </button>
        </div>
      </section>

      <section v-else-if="activeStep === 'search'" class="workflow-stack search-layout">
        <CollapsibleCard title="哪些词用于广泛抓取？" v-model="searchPanelsOpen" :class="{ locked: scopeLocked }">
          <template #prefix>
            <Search :size="17" aria-hidden="true" />
          </template>
          <template #summary>
            <span v-if="scopeLocked" class="lock-chip" role="status">{{ scrapeBusy || recrawlBusy ? '抓取中 · 范围已锁定' : screenBusy ? '筛选中 · 范围已锁定' : '范围已锁定' }}</span>
            <span class="selection-summary">{{ searchSummary }}</span>
          </template>
          <div class="search-columns">
            <div class="search-col">
              <p class="search-col-title">关键词 × 城市</p>
              <div class="chip-grid" aria-label="搜索关键词和城市">
                <button
                  v-for="keyword in keywords"
                  :key="keyword.word"
                  class="choice-chip"
                  :class="{ selected: selectedKeywords.includes(keyword.word), recommended: keyword.recommended }"
                  type="button"
                  data-testid="keyword-chip"
                  :disabled="scopeLocked"
                  :aria-pressed="selectedKeywords.includes(keyword.word)"
                  @click="toggleKeyword(keyword.word)"
                >
                  {{ keyword.word }}<small v-if="keyword.recommended">推荐</small>
                </button>
                <span v-for="city in cityList" :key="city" class="city-chip">
                  {{ city }}
                  <button type="button" class="city-chip-remove" aria-label="删除城市" :disabled="scopeLocked" @click="removeCity(city)">×</button>
                </span>
              </div>
              <div class="search-input-grid">
              <div class="inline-input-row">
                <label class="field-label grow">
                  <span>关键词</span>
                  <input v-model="customKeyword" data-testid="custom-keyword" type="text" placeholder="回车添加" :disabled="scopeLocked" @keydown.enter.prevent="addCustomKeyword">
                </label>
                <button class="button secondary align-end" data-testid="add-keyword" type="button" :disabled="scopeLocked" @click="addCustomKeyword">添加</button>
              </div>
              <div class="inline-input-row">
                <label class="field-label grow">
                  <span>城市</span>
                  <input v-model="customCity" data-testid="custom-city" type="text" placeholder="不输入则不指定城市" :disabled="scopeLocked" @keydown.enter.prevent="addCustomCity">
                </label>
                <button class="button secondary align-end" data-testid="add-city" type="button" :disabled="scopeLocked" @click="addCustomCity">添加</button>
              </div>
              </div>
            </div>
          </div>
          <label class="field-label">
            <span>求职画像（用于 AI 精筛）<small v-if="!profileSummary">　未填写将跳过精筛</small></span>
            <textarea v-model="profileSummary" rows="4" :disabled="scopeLocked" class="profile-summary-input" placeholder="上传简历后自动生成；也可手动填写，如：3年Python后端，熟悉FastAPI/Redis，期望AI应用开发方向"></textarea>
          </label>
        </CollapsibleCard>

        <CollapsibleCard class="advanced-panel" title="高级执行设置" v-model="searchPanelsOpen">
          <template #prefix>
            <SlidersHorizontal :size="17" aria-hidden="true" />
          </template>
          <template #actions>
            <button class="button secondary adv-save-btn" type="button" :disabled="advancedBusy" @click="saveAdvancedSettings">
              {{ advancedBusy ? "保存中…" : "保存高级设置" }}
            </button>
          </template>
          <div class="adv-groups">
          <ExecutionModeSelector
            :model-value="executionSelection"
            :busy="advancedBusy"
            :disabled="!scopePreview"
            @update:model-value="selectExecutionMode"
          />
          <div class="adv-fields">
          <div class="adv-group">
            <p class="adv-group-title">列表抓取</p>
            <div class="advanced-grid">
              <label class="field-label"><span>每组合翻页数 <i class="tip" :data-tip="pagesValue > 10 ? '范围由任务总页数 1~200 的后端校验决定。BOSS 最多返回 10 页，超出可能无新数据' : '范围由任务总页数 1~200 的后端校验决定'">?</i></span><input v-model.number="advancedSettings.pages" data-testid="pages-per-combination" type="number" min="1" :disabled="scopeLocked" @change="clampAdvanced('pages')"></label>
              <label class="field-label"><span>组合间延迟（秒） <i class="tip" data-tip="范围由当前模式版本提供。两个搜索组合之间等待多久，实际会±5秒随机抖动">?</i></span><input v-model.number="advancedSettings.inter_combo_delay" type="number" :min="advancedRange('inter_combo_delay')[0]" :max="advancedRange('inter_combo_delay')[1]" @change="clampAdvanced('inter_combo_delay')"></label>
            </div>
          </div>
          <!-- 详情抓取 -->
          <div class="adv-group">
            <p class="adv-group-title">详情抓取（JD）</p>
            <div class="advanced-grid">
              <label class="field-label"><span>每批抓取数量 <i class="tip" data-tip="范围由当前模式版本提供。每批交给浏览器抓JD的岗位数">?</i></span><input v-model.number="advancedSettings.detail_batch_size" data-testid="detail-batch-size" type="number" :min="advancedRange('detail_batch_size')[0]" :max="advancedRange('detail_batch_size')[1]" @change="clampAdvanced('detail_batch_size')"></label>
              <label class="field-label"><span>岗位间隔（秒） <i class="tip" data-tip="范围由当前模式版本提供。抓完一个岗位详情后等待再抓下一个">?</i></span><input v-model.number="advancedSettings.detail_interval" type="number" :min="advancedRange('detail_interval')[0]" :max="advancedRange('detail_interval')[1]" @change="clampAdvanced('detail_interval')"></label>
              <label class="field-label"><span>重置频率 <i class="tip" data-tip="范围由当前模式版本提供。每抓多少个详情后重置会话计数器">?</i></span><input v-model.number="advancedSettings.detail_reset_every" type="number" :min="advancedRange('detail_reset_every')[0]" :max="advancedRange('detail_reset_every')[1]" @change="clampAdvanced('detail_reset_every')"></label>
              <label class="field-label"><span>批次冷却（秒） <i class="tip" data-tip="范围由当前模式版本提供。两批详情抓取之间的休息时间">?</i></span><input v-model.number="advancedSettings.detail_batch_cooldown" type="number" :min="advancedRange('detail_batch_cooldown')[0]" :max="advancedRange('detail_batch_cooldown')[1]" @change="clampAdvanced('detail_batch_cooldown')"></label>
              <label class="field-label"><span>并发 Tab 数 <i class="tip" data-tip="范围由当前模式版本提供。同时常驻多少个浏览器 tab 抓 JD（1-10）">?</i></span><input v-model.number="advancedSettings.detail_tab_pool_size" data-testid="detail-tab-pool-size" type="number" :min="advancedRange('detail_tab_pool_size')[0]" :max="advancedRange('detail_tab_pool_size')[1]" @change="clampAdvanced('detail_tab_pool_size')"></label>
            </div>
          </div>
          <!-- AI 筛选 -->
          <div class="adv-group">
            <p class="adv-group-title">AI 筛选</p>
            <div class="advanced-grid">
              <label class="field-label"><span>粗筛每批数量 <i class="tip" data-tip="范围由当前模式版本提供。粗筛每次发送的岗位摘要数">?</i></span><input v-model.number="advancedSettings.screen_batch_size" type="number" :min="advancedRange('screen_batch_size')[0]" :max="advancedRange('screen_batch_size')[1]" @change="clampAdvanced('screen_batch_size')"></label>
              <label class="field-label"><span>粗筛并发数 <i class="tip" data-tip="范围由当前模式版本提供。粗筛同时发送的 AI 请求数">?</i></span><input v-model.number="advancedSettings.screen_concurrency" type="number" :min="advancedRange('screen_concurrency')[0]" :max="advancedRange('screen_concurrency')[1]" @change="clampAdvanced('screen_concurrency')"></label>
              <label class="field-label"><span>精筛每批数量 <i class="tip" data-tip="范围由当前模式版本提供。精筛每次发送的完整 JD 数">?</i></span><input v-model.number="advancedSettings.match_batch_size" type="number" :min="advancedRange('match_batch_size')[0]" :max="advancedRange('match_batch_size')[1]" @change="clampAdvanced('match_batch_size')"></label>
              <label class="field-label"><span>精筛并发数 <i class="tip" data-tip="范围由当前模式版本提供。精筛同时发送的 AI 请求数">?</i></span><input v-model.number="advancedSettings.match_concurrency" type="number" :min="advancedRange('match_concurrency')[0]" :max="advancedRange('match_concurrency')[1]" @change="clampAdvanced('match_concurrency')"></label>
            </div>
          </div>
          </div>
          </div>
        </CollapsibleCard>

        <TaskProgress :snapshot="scrapeSnapshot" kind="scrape" />
        <div
          v-if="loginGuide.visible"
          class="login-guide"
          data-testid="login-guide"
          role="status"
        >
          <p>
            {{ loginGuide.platform === 'boss' ? 'BOSS' : '智联' }} 尚未登录：请打开账号
            <strong>{{ loginGuide.accountName || '当前账号' }}</strong> 的
            {{ loginGuide.platform === 'boss' ? 'BOSS' : '智联' }} 窗口登录后，再重新开始任务。
          </p>
          <button
            type="button"
            class="button secondary small"
            data-testid="open-accounts-from-guide"
            @click="emit('open-browser-accounts')"
          >
            打开账号面板
          </button>
        </div>
        <div class="workflow-actions">
          <p v-if="draftPlatformDisabled" class="platform-disabled-notice" data-testid="platform-disabled-notice" role="status">
            当前平台（{{ draftPlatform === 'boss' ? 'BOSS' : '智联' }}）已禁用新建任务，请切换到可用平台。
          </p>
          <button class="button primary" type="button" data-testid="start-scrape" :disabled="draftPlatformDisabled" @click="scrapeBusy ? cancelScrape() : startScrape()">
            <Search v-if="!scrapeBusy" :size="18" aria-hidden="true" />
            <Square v-else :size="18" aria-hidden="true" />{{ scrapeBusy ? "停止抓取" : "开始抓取" }}
          </button>
          <button v-if="scrapeSnapshot && (scrapeSnapshot.status === 'failed' || scrapeSnapshot.status === 'paused') && scrapeTaskId"
                  class="button secondary" type="button" data-testid="continue-scrape"
                  :disabled="scrapeBusy" @click="continueScrape()">
            从断点继续
          </button>
          <button v-if="scrapeSnapshot && scrapeSnapshot.status === 'paused' && pausedRunId"
                  class="button danger" type="button" data-testid="cancel-paused-scrape"
                  @click="cancelPausedTask(pausedRunId)">
            取消任务
          </button>
          <button v-if="scrapeSnapshot && scrapeSnapshot.status === 'paused' && (pausedRunId || scrapeTaskId)"
                  class="button danger" type="button" data-testid="finish-paused-scrape"
                  @click="finishPausedTask(pausedRunId || scrapeTaskId)">
            结束并保存结果
          </button>
          <button v-if="interruptedRunId" class="button danger" type="button" data-testid="finish-interrupted-scrape" @click="finishPausedTask(interruptedRunId)">
            结束并保存结果
          </button>
          <button v-if="scrapeCompleted" class="button secondary" type="button" data-testid="continue-to-screen" @click="activeStep = 'screen'">
            继续确认筛选条件
          </button>
        </div>
      </section>

      <section v-else-if="activeStep === 'screen'" class="workflow-stack">
        <CollapsibleCard title="确认 6 类筛选条件" v-model="screenPanelOpen">
          <template #prefix>
            <Filter :size="17" aria-hidden="true" />
          </template>
          <template #summary>
            <span v-if="screenSummaryChips.length" class="summary-chips">
              <span v-for="chip in screenSummaryChips" :key="chip.label" class="summary-chip">{{ chip.label }}: {{ chip.value }}</span>
            </span>
            <span v-else class="selection-summary">未设置筛选条件</span>
          </template>
          <template #actions>
            <div class="workflow-actions screen-card-actions">
              <button class="button primary" type="button" data-testid="start-ai-screen" :disabled="draftPlatformDisabled || (!screenBusy && !scrapeCompleted)" @click="screenBusy ? cancelAiScreen() : startAiScreen()">
                <Square v-if="screenBusy" :size="15" aria-hidden="true" />
                <Sparkles v-else :size="15" aria-hidden="true" />{{ screenBusy ? "停止筛选" : "开始 AI 筛选" }}
              </button>
              <button v-if="interruptedRunId" class="button danger" type="button" data-testid="finish-interrupted-screen" @click="finishPausedTask(interruptedRunId)">
                结束并保存结果
              </button>
              <button v-if="screenSnapshot && screenSnapshot.status === 'paused'"
                      class="button secondary" type="button" data-testid="resume-ai-screen"
                      :disabled="screenBusy" @click="continueAiScreen()">
                继续
              </button>
              <button v-if="screenSnapshot && screenSnapshot.status === 'paused' && pausedRunId"
                      class="button danger" type="button" data-testid="cancel-paused-screen"
                      @click="cancelPausedTask(pausedRunId)">
                取消任务
              </button>
              <button v-if="screenSnapshot && screenSnapshot.status === 'paused' && (pausedRunId || screenTaskId)"
                      class="button danger" type="button" data-testid="finish-paused-screen"
                      @click="finishPausedTask(pausedRunId || screenTaskId)">
                结束并保存结果
              </button>
            </div>
          </template>
          <div class="filter-groups">
            <fieldset v-for="group in filterGroups" :key="group.key" class="filter-group">
              <legend>{{ group.label }}</legend>
              <div class="chip-grid compact">
                <button
                  v-if="group.sentinel"
                  class="choice-chip"
                  :class="{ selected: !(filterValues[draftPlatform][group.key] || []).length }"
                  type="button"
                  :disabled="screenBusy"
                  :aria-pressed="!(filterValues[draftPlatform][group.key] || []).length"
                  @click="filterValues[draftPlatform][group.key] = []"
                >{{ group.sentinel.label }}</button>
                <button
                  v-for="([label, code]) in group.options"
                  :key="code"
                  class="choice-chip"
                  :class="{ selected: (filterValues[draftPlatform][group.key] || []).includes(code) }"
                  type="button"
                  :disabled="screenBusy"
                  :aria-pressed="(filterValues[draftPlatform][group.key] || []).includes(code)"
                  @click="toggleFilter(group.key, code)"
                >{{ label }}</button>
              </div>
            </fieldset>
          </div>
        </CollapsibleCard>

        <TaskProgress :snapshot="screenSnapshot" kind="screen" />
      </section>

      <section
        v-else
        class="results-stage"
        :class="{
          'has-recrawl-banner': activeCategory === 'uncertain' && recrawlSnapshot,
        }"
      >
        <div class="command-band">
          <div class="result-tabs" role="tablist" aria-label="AI 筛选结果分类">
            <button
              v-for="tab in resultTabs"
              :key="tab.id"
              type="button"
              role="tab"
              :aria-selected="activeCategory === tab.id"
              :class="['vtab', `vtab--${tab.id}`, { active: activeCategory === tab.id }]"
              @click="activeCategory = tab.id"
            ><span class="vtab-dot" aria-hidden="true"></span>{{ tab.label }}<span class="vtab-count">{{ tab.count }}</span></button>
          </div>
          <span class="command-note" aria-hidden="true">判定依据：你的简历关键词 · 两阶段判断</span>
        </div>

        <div v-if="activeCategory === 'uncertain' && (recrawlSnapshot || interruptedRunId)" class="recrawl-banner">
          <TaskProgress :snapshot="recrawlSnapshot" kind="screen" />
          <button v-if="recrawlSnapshot && recrawlSnapshot.status === 'paused'"
                  class="button primary" type="button" data-testid="resume-recrawl"
                  :disabled="recrawlBusy" @click="continueRecrawl()">
            继续
          </button>
          <button v-if="recrawlSnapshot && recrawlSnapshot.status === 'paused'"
                  class="button danger" type="button" data-testid="finish-paused-recrawl"
                  :disabled="recrawlBusy" @click="finishPausedTask(recrawlTaskId || pausedRunId)">
            结束并保存结果
          </button>
          <button v-if="interruptedRunId" class="button danger" type="button" data-testid="finish-interrupted-recrawl" @click="finishPausedTask(interruptedRunId)">
            结束并保存结果
          </button>
        </div>

        <JobWorkspace
          :jobs="currentJobs"
          :empty-message="currentEmptyMessage"
          :defer-mobile-detail="Boolean(recrawlSnapshot && recrawlSnapshot.status === 'paused')"
          :platform-filter="resultPlatformFilter"
          :result-epoch="resultEpoch"
          @update:platform-filter="(value) => { resultPlatformFilter = value; }"
        >
          <template #heading-actions>
            <div v-if="activeCategory === 'uncertain' && resultPlatformFilter !== 'all'" class="recrawl-inline">
              <button
                class="button secondary small"
                type="button"
                data-testid="recrawl-uncertain"
                :disabled="recrawlBusy || !groups.uncertain.length"
                @click="recrawlUncertain"
              >
                <RotateCcw v-if="!recrawlBusy" :size="14" aria-hidden="true" />
                <LoaderCircle v-else class="spin" :size="14" aria-hidden="true" />
                {{ recrawlBusy ? "重抓中…" : `全部重抓（${groups.uncertain.length}）` }}
              </button>
            </div>
          </template>
          <template #actions="{ job }">
            <template v-if="activeCategory !== 'dropped'">
              <button class="button primary" type="button" :disabled="feedbackBusyIds.has(jobId(job))" @click="toggleInterest(job)">
                <Bookmark :size="17" aria-hidden="true" />{{ job._marked === "interested" ? "已收藏" : "收藏" }}
              </button>
              <button class="button danger" type="button" :disabled="feedbackBusyIds.has(jobId(job))" @click="toggleRejected(job)">
                {{ rejectedIds.has(jobId(job)) ? "撤销不感兴趣" : "不感兴趣" }}
              </button>
              <button v-if="!job.jd" class="button secondary" type="button" :disabled="jdBusyIds.has(jobId(job))" @click="retryJd(job)">
                <FileText :size="17" aria-hidden="true" />{{ jdBusyIds.has(jobId(job)) ? "补抓中…" : "补抓 JD" }}
              </button>
              <button
                class="button secondary"
                type="button"
                data-testid="open-lifecycle-dialog"
                @click="openLifecycleDialog(job)"
              >
                <History :size="17" aria-hidden="true" />查看轨迹
              </button>
            </template>
          </template>
        </JobWorkspace>
      </section>
    </section>

    <!-- 岗位轨迹浮窗：居中弹窗，内容为原生命周期卡片全部能力 -->
    <Transition name="dialog">
      <div
        v-if="lifecycleDialogOpen && lifecycleDialogJob"
        class="dialog-backdrop lifecycle-dialog-backdrop"
        data-testid="lifecycle-dialog"
        @click.self="closeLifecycleDialog"
      >
      <section class="dialog-panel lifecycle-dialog" role="dialog" aria-modal="true" aria-label="岗位轨迹">
        <header class="lifecycle-dialog-header">
          <h2>岗位轨迹</h2>
          <button
            class="icon-button"
            type="button"
            aria-label="关闭岗位轨迹浮窗"
            data-testid="lifecycle-dialog-close"
            @click="closeLifecycleDialog"
          >
            <X :size="18" aria-hidden="true" />
          </button>
        </header>
        <JobLifecycleActions
          :profile-id="profileId"
          :job="lifecycleDialogJob"
          @job-feedback-changed="onJobFeedbackChanged"
        />
      </section>
      </div>
    </Transition>
  </main>
</template>
