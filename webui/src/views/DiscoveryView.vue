<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  Bookmark,
  Check,
  FileText,
  Filter,
  LoaderCircle,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
  UploadCloud,
} from "@lucide/vue";
import CollapsibleCard from "../components/CollapsibleCard.vue";
import ExecutionModeSelector from "../components/ExecutionModeSelector.vue";
import JobWorkspace from "../components/JobWorkspace.vue";
import StepNavigator from "../components/StepNavigator.vue";
import TaskProgress from "../components/TaskProgress.vue";
import { apiRequest, errorMessage, settingsApi } from "../api";
import { buildSearchScriptParams, normalizeScopePreview, partitionPipelineResult } from "../discovery";
import type { PipelineResult } from "../discovery";
import type {
  AdvancedSettingsState,
  CandidateProfile,
  ExecutionSelection,
  ExecutionSettings,
  FrozenSearchScope,
  JobItem,
  Notice,
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
}


const props = defineProps<{ profileId: string }>();
const emit = defineEmits<{
  notify: [notice: Notice];
  "profile-created": [profile: CandidateProfile];
}>();

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
const filterValues = ref<Record<string, string[]>>({});
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
// 刷新后接回任务时显示的恢复提示条；任务结束后清空
const restoredTaskHint = ref("");
// 切片7：从 DB 恢复的 paused 任务 run_id（无内存工作线程，不能 poll）
const pausedRunId = ref("");
const pipelineResult = ref<PipelineResult | null>(null);
const pipelineResultRunId = ref("");
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
  browser_account: "a",
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
const searchPanelOpen = ref(true);
const screenPanelOpen = ref(true);
const advancedPanelOpen = ref(false);
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
const filterGroups = computed(() => {
  return ["salary", "experience", "degree", "industry", "scale", "stage"]
    .map((key) => {
      const meta = fieldLabels.value[key];
      const mapping = meta?.[2];
      return {
        key,
        label: meta?.[0] || key,
        options: typeof mapping === "object" && mapping
          ? Object.entries(mapping).filter(([, code]) => code !== "0")
          : [],
      };
    })
    .filter((group) => group.options.length);
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
  filterGroups.value.forEach((group) => {
    const values = filterValues.value[group.key] || [];
    if (!values.length) return;
    const labels = values.map((code) => {
      const option = group.options.find(([, optCode]) => optCode === code);
      return option ? option[0] : code;
    });
    chips.push({ label: group.label, value: labels.join(" / ") });
  });
  return chips;
});
watch(advancedPanelOpen, (open) => {
  if (open) searchPanelOpen.value = false;
});
watch(
  [selectedKeywords, cityText, () => advancedSettings.value.pages],
  () => { if (!scopeLocked.value) void refreshScopePreview(); },
  { deep: true },
);
const groups = computed(() => partitionPipelineResult(pipelineResult.value || {}));
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
    }>("/api/latest-running-task");
    if (!data.has_task || !data.task_id) return;
    const snapshot: TaskSnapshot = {
      status: data.status || "running",
      progress: data.progress || {},
      logs: data.logs || [],
      error: data.error || "",
      stage: data.stage,
      pause_info: data.pause_info,
      started_at: data.started_at,
      finished_at: data.finished_at,
    };
    const kind: "scrape" | "screen" | "recrawl" = data.kind === "scrape"
      ? "scrape"
      : data.kind === "recrawl" ? "recrawl" : "screen";
    if (data.status === "interrupted") {
      // 服务重启打断的任务：工作线程已死不能 poll；提示用户重开（后端会自动接着上次进度）
      restoredTaskHint.value = "上次 AI 筛选因服务重启被中断；重新开始 AI 筛选会接着上次进度，不重复消耗";
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

async function loadFilterLabels() {
  if (Object.keys(fieldLabels.value).length) return;
  try {
    const data = await apiRequest<{ labels?: Record<string, unknown> }>("/api/filter-labels");
    if (data.labels) fieldLabels.value = data.labels as typeof fieldLabels.value;
  } catch { /* non-critical */ }
}

watch(() => props.profileId, () => {
  if (!pausedRunId.value && !scrapeBusy.value && !screenBusy.value && !recrawlBusy.value) {
    void loadLatestResult();
  }
});

onBeforeUnmount(() => {
  if (pollTimer) window.clearTimeout(pollTimer);
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
  fieldLabels.value = data.labels || {};
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
  filterValues.value = {};
  for (const key of ["salary", "experience", "degree", "industry", "scale", "stage"]) {
    const value = fields[key];
    filterValues.value[key] = (Array.isArray(value) ? value : value ? [value] : [])
      .map(String)
      .filter((item) => item !== "0");
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
    pausedRunId.value = "";
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
  const values = filterValues.value[key] || [];
  filterValues.value[key] = values.includes(code)
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
  "inter_combo_delay", "detail_batch_size", "detail_interval",
  "detail_reset_every", "detail_batch_cooldown",
  "detail_tab_pool_size", "screen_batch_size",
  "screen_concurrency", "match_batch_size", "match_concurrency",
] as const;

function currentExecutionSettings(): ExecutionSettings {
  const settings = Object.fromEntries(SPEED_FIELDS.map((field) => [field, Number(advancedSettings.value[field])])) as unknown as ExecutionSettings;
  const account = String(advancedSettings.value.browser_account || "a");
  return { ...settings, browser_account: account === "b" ? "b" : "a" };
}

async function refreshScopePreview(): Promise<FrozenSearchScope | null> {
  if (!selectedKeywords.value.length) {
    scopePreview.value = null;
    return null;
  }
  scopePreviewBusy.value = true;
  try {
    const data = await settingsApi.previewScope({
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
  searchPanelOpen.value = false;
  advancedPanelOpen.value = false;
  scrapeBusy.value = true;
  scrapeCompleted.value = false;
  resultLoaded.value = false;
  pipelineResult.value = null;
  scrapeSnapshot.value = { status: "running", progress: { message: "正在创建抓取任务…" }, logs: [] };
  try {
    const data = await apiRequest<{ task_id: string }>("/api/execute-search", {
      method: "POST",
      json: { script_params: scriptParams, scope_digest: preview.scope_digest },
    });
    scrapeTaskId.value = data.task_id;
    await pollTask(data.task_id, "scrape");
  } catch (error) {
    scrapeBusy.value = false;
    scrapeSnapshot.value = { status: "failed", error: errorMessage(error, "抓取启动失败") };
    notify(errorMessage(error, "抓取启动失败"), "error");
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
  restoredTaskHint.value = "";
  screenSnapshot.value = { status: "running", progress: { message: "正在创建 AI 筛选任务…" }, logs: [] };
  try {
    const data = await apiRequest<{ task_id: string; resuming?: boolean }>("/api/ai-screen", {
      method: "POST",
      json: {
        screening_fields: filterValues.value,
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
        setPipelineResult(data.result || {});
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
  if (pausedRunId.value || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return;
  try {
    const query = props.profileId ? `?profile_id=${encodeURIComponent(props.profileId)}` : "";
    const data = await apiRequest<{
      has_result?: boolean;
      source_run_id?: string;
      result?: PipelineResult;
      status?: string;
      started_at?: number;
      finished_at?: number;
      execution_config?: Record<string, unknown> | null;
    }>(`/api/latest-pipeline-result${query}`);
    if (pausedRunId.value || scrapeBusy.value || screenBusy.value || recrawlBusy.value) return;
    if (data.has_result && data.result) {
      pipelineResultRunId.value = data.source_run_id || "";
      setPipelineResult(data.result);
      const ps = (data.result as Record<string, unknown>).profile_summary;
      if (typeof ps === "string" && ps.trim()) profileSummary.value = ps;
      const snapshotStatus = data.status === "completed_with_pending" ? "completed_with_pending" : "completed";
      scrapeSnapshot.value = {
        status: snapshotStatus, stage: "done", progress: { message: "上次抓取已完成" }, logs: [],
        started_at: data.started_at,
        finished_at: data.finished_at,
      };
      screenSnapshot.value = {
        status: snapshotStatus, stage: "done", progress: { message: "上次 AI 筛选已完成" }, logs: [],
        started_at: data.started_at,
        finished_at: data.finished_at,
      };
      const execConfig = data.execution_config || {};
      scrapeSnapshot.value.execution_config = execConfig;
      screenSnapshot.value.execution_config = execConfig;
      screenSnapshot.value.kept_count = Number(data.result.total_kept || 0);
      screenSnapshot.value.dropped_count = Number(data.result.total_dropped || 0);
      const resultTotals = data.result as { total_scraped?: number };
      const sourceTotal = Number(resultTotals.total_scraped || 0);
      const stageTotal = snapshotStatus === "completed_with_pending"
        ? Number(data.result.total_kept || 0)
        : sourceTotal;
      scrapeSnapshot.value.total = stageTotal || sourceTotal;
      scrapeSnapshot.value.source_total = sourceTotal;
      screenSnapshot.value.total = stageTotal || sourceTotal;
      screenSnapshot.value.source_total = sourceTotal;
      const uncertainCount = (data.result.jobs || []).filter((job) => job.verdict !== "match" && job.verdict !== "not_match" && job.verdict !== "mismatch").length;
      screenSnapshot.value.pending_count = snapshotStatus === "completed_with_pending" ? uncertainCount : 0;
    }
  } catch (error) {
    notify(errorMessage(error, "上次结果暂时无法恢复"), "warning");
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
  activeCategory.value = "matched";
  rejectedIds.value = new Set();
  pausedRunId.value = "";
  restoredTaskHint.value = "";
  scopePreview.value = null;
  scopePreviewBusy.value = false;
  keywords.value = [];
  selectedKeywords.value = [];
  customKeyword.value = "";
  cityText.value = "";
  customCity.value = "";
  filterValues.value = {};
  profileSummary.value = "";
  scrapeBusy.value = false;
  screenBusy.value = false;
  recrawlBusy.value = false;
  recrawlRetryCount = 0;
  screenPanelOpen.value = true;
  void clearLatestResult();
}

function jobId(job: JobItem): string {
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
      title: job.title,
      salary: job.salary,
      location: job.location,
      company: job.company || job.boss_name,
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
        source_run_id: pipelineResultRunId.value,
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
  try {
    const data = await apiRequest<{ task_id: string }>("/api/pipeline/recrawl", {
      method: "POST",
      json: {
        source_run_id: pipelineResultRunId.value,
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
        <button v-if="activeStep === 'results'" class="button secondary" type="button" @click="resetWorkflow">
          <RotateCcw :size="17" aria-hidden="true" />开始新一轮
        </button>
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

      <section v-else-if="activeStep === 'search'" class="workflow-stack">
        <CollapsibleCard title="哪些词用于广泛抓取？" v-model="searchPanelOpen">
          <template #prefix>
            <Search :size="17" aria-hidden="true" />
          </template>
          <template #summary>
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
            <textarea v-model="profileSummary" rows="2" :disabled="scopeLocked" placeholder="上传简历后自动生成；也可手动填写，如：3年Python后端，熟悉FastAPI/Redis，期望AI应用开发方向"></textarea>
          </label>
        </CollapsibleCard>

        <CollapsibleCard class="advanced-panel" title="高级执行设置" v-model="advancedPanelOpen">
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
          <!-- 浏览器账号 -->
          <div class="adv-group account-group">
            <p class="adv-group-title">浏览器账号</p>
            <div class="account-segments" role="radiogroup" aria-label="浏览器账号">
              <button type="button" role="radio" data-account="a"
                      :aria-checked="advancedSettings.browser_account === 'a'"
                      :class="{ active: advancedSettings.browser_account === 'a' }"
                      :disabled="scopeLocked || advancedBusy"
                      @click="advancedSettings.browser_account = 'a'">账号 A</button>
              <button type="button" role="radio" data-account="b"
                      :aria-checked="advancedSettings.browser_account === 'b'"
                      :class="{ active: advancedSettings.browser_account === 'b' }"
                      :disabled="scopeLocked || advancedBusy"
                      @click="advancedSettings.browser_account = 'b'">账号 B</button>
            </div>
          </div>
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
        <div class="workflow-actions">
          <button class="button primary" type="button" data-testid="start-scrape" @click="scrapeBusy ? cancelScrape() : startScrape()">
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
          <div class="filter-groups">
            <fieldset v-for="group in filterGroups" :key="group.key" class="filter-group">
              <legend>{{ group.label }}</legend>
              <div class="chip-grid compact">
                <button
                  class="choice-chip"
                  :class="{ selected: !(filterValues[group.key] || []).length }"
                  type="button"
                  :disabled="screenBusy"
                  :aria-pressed="!(filterValues[group.key] || []).length"
                  @click="filterValues[group.key] = []"
                >不限</button>
                <button
                  v-for="([label, code]) in group.options"
                  :key="code"
                  class="choice-chip"
                  :class="{ selected: (filterValues[group.key] || []).includes(code) }"
                  type="button"
                  :disabled="screenBusy"
                  :aria-pressed="(filterValues[group.key] || []).includes(code)"
                  @click="toggleFilter(group.key, code)"
                >{{ label }}</button>
              </div>
            </fieldset>
          </div>
        </CollapsibleCard>

        <TaskProgress :snapshot="screenSnapshot" kind="screen" />
        <div class="workflow-actions">
          <button class="button primary" type="button" data-testid="start-ai-screen" :disabled="!screenBusy && !scrapeCompleted" @click="screenBusy ? cancelAiScreen() : startAiScreen()">
            <Square v-if="screenBusy" :size="18" aria-hidden="true" />
            <Sparkles v-else :size="18" aria-hidden="true" />{{ screenBusy ? "停止筛选" : "开始 AI 筛选" }}
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
      </section>

      <section
        v-else
        class="results-stage"
        :class="{
          'has-recrawl-banner': activeCategory === 'uncertain' && recrawlSnapshot,
        }"
      >
        <div class="result-tabs" role="tablist" aria-label="AI 筛选结果分类">
          <button
            v-for="tab in resultTabs"
            :key="tab.id"
            type="button"
            role="tab"
            :aria-selected="activeCategory === tab.id"
            :class="{ active: activeCategory === tab.id }"
            @click="activeCategory = tab.id"
          >{{ tab.label }}<span>{{ tab.count }}</span></button>
        </div>

        <div v-if="activeCategory === 'uncertain' && recrawlSnapshot" class="recrawl-banner">
          <TaskProgress :snapshot="recrawlSnapshot" kind="screen" />
          <button v-if="recrawlSnapshot.status === 'paused'"
                  class="button primary" type="button" data-testid="resume-recrawl"
                  :disabled="recrawlBusy" @click="continueRecrawl()">
            继续
          </button>
          <button v-if="recrawlSnapshot.status === 'paused'"
                  class="button danger" type="button" data-testid="finish-paused-recrawl"
                  :disabled="recrawlBusy" @click="finishPausedTask(recrawlTaskId || pausedRunId)">
            结束并保存结果
          </button>
        </div>

        <JobWorkspace
          :jobs="currentJobs"
          :empty-message="currentEmptyMessage"
          :defer-mobile-detail="Boolean(recrawlSnapshot && recrawlSnapshot.status === 'paused')"
        >
          <template #heading-actions>
            <div v-if="activeCategory === 'uncertain'" class="recrawl-inline">
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
            </template>
          </template>
        </JobWorkspace>
      </section>
    </section>
  </main>
</template>
