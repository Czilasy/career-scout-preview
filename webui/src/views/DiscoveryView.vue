<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  Bookmark,
  Check,
  FileText,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  UploadCloud,
} from "@lucide/vue";
import JobWorkspace from "../components/JobWorkspace.vue";
import StepNavigator from "../components/StepNavigator.vue";
import TaskProgress from "../components/TaskProgress.vue";
import { apiRequest, errorMessage } from "../api";
import { buildSearchScriptParams, partitionPipelineResult } from "../discovery";
import type { PipelineResult } from "../discovery";
import type { CandidateProfile, JobItem, Notice } from "../types";

type StepId = "upload" | "search" | "screen" | "results";
type ResultCategory = "matched" | "unmatched" | "uncertain" | "dropped";
type FieldLabel = [string, unknown, string | Record<string, string>];

interface AnalyzeResponse {
  ok: boolean;
  fields: Record<string, unknown>;
  labels: Record<string, FieldLabel>;
}

interface TaskSnapshot {
  status: "running" | "done" | "failed" | string;
  progress?: Record<string, unknown>;
  logs?: string[];
  error?: string;
  result?: PipelineResult;
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
const scrapeCompleted = ref(false);
const resultLoaded = ref(false);
const selectedFile = ref<File | null>(null);
const aiConsent = ref(false);
const dragActive = ref(false);
const uploadBusy = ref(false);
const keywords = ref<Array<{ word: string; recommended: boolean }>>([]);
const selectedKeywords = ref<string[]>([]);
const customKeyword = ref("");
const cityText = ref("");
const fieldLabels = ref<Record<string, FieldLabel>>({});
const filterValues = ref<Record<string, string[]>>({});
const profileSummary = ref("");
const scrapeTaskId = ref("");
const scrapeBusy = ref(false);
const scrapeSnapshot = ref<TaskSnapshot | null>(null);
const screenBusy = ref(false);
const screenSnapshot = ref<TaskSnapshot | null>(null);
const pipelineResult = ref<PipelineResult | null>(null);
const activeCategory = ref<ResultCategory>("matched");
const rejectedIds = ref(new Set<string>());
const feedbackBusyIds = ref(new Set<string>());
const jdBusyIds = ref(new Set<string>());
const advancedBusy = ref(false);
const advancedSettings = ref<Record<string, number>>({
  pages: 3,
  inter_combo_delay: 30,
  detail_batch_size: 5,
  screen_batch_size: 50,
  screen_concurrency: 1,
  match_batch_size: 4,
});
let pollTimer: number | undefined;

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
const searchEstimate = computed(() => {
  const groups = selectedKeywords.value.length * cityList.value.length;
  return groups
    ? `${selectedKeywords.value.length} 个关键词 × ${cityList.value.length} 个城市 = ${groups} 组搜索`
    : "请选择至少一个关键词和一个城市";
});
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
  void Promise.allSettled([loadAdvancedSettings(), loadLatestResult(), loadFilterLabels()]);
});

async function loadFilterLabels() {
  if (Object.keys(fieldLabels.value).length) return;
  try {
    const data = await apiRequest<{ labels?: Record<string, unknown> }>("/api/filter-labels");
    if (data.labels) fieldLabels.value = data.labels as typeof fieldLabels.value;
  } catch { /* non-critical */ }
}

watch(() => props.profileId, () => {
  if (!scrapeBusy.value && !screenBusy.value) void loadLatestResult();
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

function toggleFilter(key: string, code: string) {
  const values = filterValues.value[key] || [];
  filterValues.value[key] = values.includes(code)
    ? values.filter((value) => value !== code)
    : [...values, code];
}

async function loadAdvancedSettings() {
  try {
    const data = await apiRequest<{ settings?: Record<string, number> }>("/api/advanced-settings");
    advancedSettings.value = { ...advancedSettings.value, ...(data.settings || {}) };
  } catch (error) {
    notify(errorMessage(error, "高级设置加载失败"), "warning");
  }
}

async function saveAdvancedSettings() {
  advancedBusy.value = true;
  try {
    const data = await apiRequest<{ settings?: Record<string, number> }>("/api/advanced-settings", {
      method: "POST",
      json: { settings: advancedSettings.value },
    });
    advancedSettings.value = { ...advancedSettings.value, ...(data.settings || {}) };
    notify("高级设置已保存", "success");
  } catch (error) {
    notify(errorMessage(error, "高级设置保存失败"), "error");
  } finally {
    advancedBusy.value = false;
  }
}

async function startScrape() {
  const scriptParams = buildSearchScriptParams(selectedKeywords.value, cityList.value);
  if (!scriptParams.keyword || !scriptParams.city.length) {
    notify("请确认至少一个关键词和一个城市", "warning");
    return;
  }
  scrapeBusy.value = true;
  scrapeCompleted.value = false;
  scrapeSnapshot.value = { status: "running", progress: { message: "正在创建抓取任务…" }, logs: [] };
  try {
    const data = await apiRequest<{ task_id: string }>("/api/execute-search", {
      method: "POST",
      json: { script_params: scriptParams },
    });
    scrapeTaskId.value = data.task_id;
    await pollTask(data.task_id, "scrape");
  } catch (error) {
    scrapeBusy.value = false;
    scrapeSnapshot.value = { status: "failed", error: errorMessage(error, "抓取启动失败") };
    notify(errorMessage(error, "抓取启动失败"), "error");
  }
}

async function startAiScreen() {
  if (!scrapeCompleted.value || !scrapeTaskId.value) {
    notify("请先完成本轮抓取，再开始 AI 筛选", "warning");
    return;
  }
  screenBusy.value = true;
  screenSnapshot.value = { status: "running", progress: { message: "正在创建 AI 筛选任务…" }, logs: [] };
  try {
    const data = await apiRequest<{ task_id: string }>("/api/ai-screen", {
      method: "POST",
      json: {
        screening_fields: filterValues.value,
        profile_summary: profileSummary.value,
        scrape_task_id: scrapeTaskId.value,
      },
    });
    await pollTask(data.task_id, "screen");
  } catch (error) {
    screenBusy.value = false;
    screenSnapshot.value = { status: "failed", error: errorMessage(error, "AI 筛选启动失败") };
    notify(errorMessage(error, "AI 筛选启动失败"), "error");
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
    const data = await apiRequest<TaskSnapshot>(`/api/search-progress/${encodeURIComponent(taskId)}`);
    if (kind === "scrape") scrapeSnapshot.value = data;
    else screenSnapshot.value = data;

    if (data.status === "done") {
      pollRetryCount = 0;
      if (kind === "scrape") {
        scrapeBusy.value = false;
        scrapeCompleted.value = true;
        notify("抓取完成，请继续确认 AI 筛选条件", "success");
      } else {
        screenBusy.value = false;
        setPipelineResult(data.result || {});
        activeStep.value = "results";
        notify("AI 筛选完成", "success");
      }
      return;
    }
    if (data.status === "failed") {
      pollRetryCount = 0;
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
        error: "状态刷新连续失败，请检查网络后重试",
      };
      if (kind === "scrape") scrapeSnapshot.value = failed;
      else screenSnapshot.value = failed;
      notify("状态刷新连续失败，请检查网络后重试", "error");
      return;
    }
    const delay = Math.min(POLL_BASE_DELAY * 2 ** (pollRetryCount - 1), POLL_MAX_DELAY);
    const retrying: TaskSnapshot = {
      status: "running",
      progress: { message: `状态刷新失败，正在重试（${pollRetryCount}/${POLL_MAX_RETRIES}）…` },
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
  analysisReady.value = true;
  scrapeCompleted.value = true;
  resultLoaded.value = true;
  const groups = partitionPipelineResult(result);
  activeCategory.value = groups.matched.length ? "matched"
    : groups.uncertain.length ? "uncertain"
      : groups.unmatched.length ? "unmatched" : "dropped";
}

async function loadLatestResult() {
  try {
    const query = props.profileId ? `?profile_id=${encodeURIComponent(props.profileId)}` : "";
    const data = await apiRequest<{ has_result?: boolean; result?: PipelineResult }>(`/api/latest-pipeline-result${query}`);
    if (data.has_result && data.result) setPipelineResult(data.result);
  } catch (error) {
    notify(errorMessage(error, "上次结果暂时无法恢复"), "warning");
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
  screenSnapshot.value = null;
  pipelineResult.value = null;
  rejectedIds.value = new Set();
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
    const data = await apiRequest<{ jd: string }>(`/api/pipeline/jobs/${encodeURIComponent(id)}/jd`, {
      method: "POST",
      json: { source_url: job.source_url || job.job_link || job.canonical_url },
    });
    job.jd = data.jd;
    notify("JD 已补抓；原 AI 判定保持不变", "success");
  } catch (error) {
    notify(errorMessage(error, "JD 补抓失败"), "error");
  } finally {
    withBusy(jdBusyIds, id, false);
  }
}
</script>

<template>
  <main
    class="view-shell"
    :class="{ 'results-view': activeStep === 'results' }"
    data-testid="discovery-view"
  >
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
        <section class="content-card workflow-card">
          <div class="section-heading">
            <div><span class="card-kicker">搜索关键词</span><h2>哪些词用于广泛抓取？</h2></div>
            <span class="selection-summary">{{ selectedKeywords.length }} 项已选</span>
          </div>
          <div class="chip-grid" aria-label="搜索关键词">
            <button
              v-for="keyword in keywords"
              :key="keyword.word"
              class="choice-chip"
              :class="{ selected: selectedKeywords.includes(keyword.word), recommended: keyword.recommended }"
              type="button"
              data-testid="keyword-chip"
              :aria-pressed="selectedKeywords.includes(keyword.word)"
              @click="toggleKeyword(keyword.word)"
            >
              {{ keyword.word }}<small v-if="keyword.recommended">推荐</small>
            </button>
          </div>
          <div class="inline-input-row">
            <label class="field-label grow">
              <span>自定义关键词</span>
              <input v-model="customKeyword" type="text" placeholder="输入后按回车添加" @keydown.enter.prevent="addCustomKeyword">
            </label>
            <button class="button secondary align-end" type="button" @click="addCustomKeyword">添加</button>
          </div>
          <label class="field-label">
            <span>城市（多个城市用逗号分隔）</span>
            <input v-model="cityText" type="text" placeholder="上海，杭州">
          </label>
          <div class="estimate-line"><Search :size="17" aria-hidden="true" />{{ searchEstimate }}</div>
        </section>

        <details class="content-card advanced-panel">
          <summary><SlidersHorizontal :size="17" aria-hidden="true" />高级执行设置</summary>
          <div class="advanced-grid">
            <label class="field-label"><span>每组合翻页数 <i class="tip" title="每个关键词×城市组合抓多少页，页数越多岗位越多但耗时更长">?</i></span><input v-model.number="advancedSettings.pages" type="number" min="1" max="30"></label>
            <label class="field-label"><span>组合间延迟（秒） <i class="tip" title="两个搜索组合之间等待多久，太短容易触发反爬">?</i></span><input v-model.number="advancedSettings.inter_combo_delay" type="number" min="5" max="120"></label>
            <label class="field-label"><span>详情批次大小 <i class="tip" title="每批同时打开几个岗位详情页抓JD，越大越快但浏览器压力越大">?</i></span><input v-model.number="advancedSettings.detail_batch_size" type="number" min="1" max="10"></label>
            <label class="field-label"><span>粗筛每批数量 <i class="tip" title="一次发给AI多少条岗位做粗筛，越大单次等待越久">?</i></span><input v-model.number="advancedSettings.screen_batch_size" type="number" min="10" max="100"></label>
            <label class="field-label"><span>粗筛并发数 <i class="tip" title="同时发几个AI请求，免费端点建议保持1否则429限流">?</i></span><input v-model.number="advancedSettings.screen_concurrency" type="number" min="1" max="5"></label>
            <label class="field-label"><span>精筛每批数量 <i class="tip" title="JD精筛时一次发几条给AI对比，越大单次等待越久">?</i></span><input v-model.number="advancedSettings.match_batch_size" type="number" min="1" max="10"></label>
          </div>
          <button class="button secondary" type="button" :disabled="advancedBusy" @click="saveAdvancedSettings">
            {{ advancedBusy ? "保存中…" : "保存高级设置" }}
          </button>
        </details>

        <TaskProgress :snapshot="scrapeSnapshot" />
        <div class="workflow-actions">
          <button class="button primary" type="button" data-testid="start-scrape" :disabled="scrapeBusy" @click="startScrape">
            <Search :size="18" aria-hidden="true" />{{ scrapeBusy ? "抓取中…" : "开始抓取" }}
          </button>
          <button v-if="scrapeCompleted" class="button secondary" type="button" data-testid="continue-to-screen" @click="activeStep = 'screen'">
            继续确认筛选条件
          </button>
        </div>
      </section>

      <section v-else-if="activeStep === 'screen'" class="workflow-stack">
        <section class="content-card workflow-card">
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
        </section>

        <TaskProgress :snapshot="screenSnapshot" />
        <div class="workflow-actions">
          <button class="button primary" type="button" data-testid="start-ai-screen" :disabled="screenBusy || !scrapeCompleted" @click="startAiScreen">
            <Sparkles :size="18" aria-hidden="true" />{{ screenBusy ? "筛选中…" : "开始 AI 筛选" }}
          </button>
          <span class="action-hint">Stage A 粗筛 → 抓取 JD → Stage B 精筛</span>
        </div>
      </section>

      <section v-else class="results-stage">
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

        <JobWorkspace :jobs="currentJobs" :empty-message="currentEmptyMessage">
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
