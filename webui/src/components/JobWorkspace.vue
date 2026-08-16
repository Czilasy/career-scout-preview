<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  ArrowUpRight,
  BriefcaseBusiness,
  Check,
  MapPin,
  X,
} from "@lucide/vue";
import JobListToolbar from "./JobListToolbar.vue";
import { countActiveFilters, emptyFilterState, filterJobs, sortJobs } from "../listFilter";
import type { FilterState, SortKey } from "../listFilter";
import type { JobItem } from "../types";
import { cleanJobLocation } from "../location";

const props = withDefaults(defineProps<{
  jobs: JobItem[];
  batchSize?: number;
  emptyMessage: string;
  deferMobileDetail?: boolean;
  /** 结果页平台筛选档位；非空时列表头部渲染全部/智联/BOSS 滑块（纯展示层过滤由父组件做）。 */
  platformFilter?: "" | "all" | "boss" | "zhilian";
  /**
   * 结果加载代次（contracts/filter-sort.md §6 resultEpoch 信号）：
   * 父组件在 pipelineResult 被新 run 结果替换时递增；本组件 watch 它重置筛选/排序/分片。
   * 禁止用 props.jobs 内容变化判断重置——切分类/切平台同样改变 jobs（D3）。
   */
  resultEpoch?: number;
}>(), {
  batchSize: 30,
  deferMobileDetail: false,
  platformFilter: "",
  resultEpoch: 0,
});

const emit = defineEmits<{
  (e: "update:platformFilter", value: "all" | "boss" | "zhilian"): void;
}>();

const PLATFORM_FILTER_OPTIONS = [
  { id: "all" as const, label: "全部" },
  { id: "zhilian" as const, label: "智联" },
  { id: "boss" as const, label: "BOSS" },
];

function platformLabel(job: JobItem): string {
  if (job.platform === "zhilian") return "智联";
  if (job.platform === "boss") return "BOSS";
  return "";
}

const visibleCount = ref(props.batchSize);
const localSelectedId = ref("");
const detailOpen = ref(true);
const userSelectedDetail = ref(false);

// ---------------------------------------------------------------------------
// 列表筛选 / 排序（纯展示层派生，状态归本组件内部，UI 在 JobListToolbar）
// 数据流（contracts/filter-sort.md §1）：
//   props.jobs（父级已做平台+分类过滤）
//     → filterJobs(jobs, filterState)   // 条件筛选：组内 OR、组间 AND
//     → sortJobs(jobs, sortKey)         // 排序：稳定排序
//     → visibleJobs（slice(batchSize)，无限滚动不变）
// 状态生命周期（D3）：切分类/切平台保留；仅 resultEpoch 变化时重置。
// ---------------------------------------------------------------------------
const filterState = ref<FilterState>(emptyFilterState());
const sortKey = ref<SortKey>("default");
const filterCount = computed(() => countActiveFilters(filterState.value));

const workspaceJobs = computed(() => sortJobs(filterJobs(props.jobs, filterState.value), sortKey.value));

// 新结果加载（resultEpoch 递增）：重置筛选/排序/分片/选中。
// 禁止用 props.jobs 内容变化判断重置（切分类/切平台同样改变 jobs，违反 D3）。
watch(() => props.resultEpoch, () => {
  filterState.value = emptyFilterState();
  sortKey.value = "default";
  visibleCount.value = props.batchSize;
  // 新结果上的选中是新开始：未手动选择标记归零，保证后续筛选/排序的选中跟随
  // 走「未手动选择 → 过滤排序后第一项」分支（contracts §6）。
  userSelectedDetail.value = false;
  localSelectedId.value = workspaceJobs.value[0] ? jobKey(workspaceJobs.value[0]) : "";
});

// 筛选/排序变化（含确定提交）：重置分片；选中跟随——
// 未手动选择过岗位 → 更新为过滤排序后第一项（保证列表第一项与右侧详情一致）；
// 已手动选择 → 保持原选中项，被过滤掉则回退到第一项。
watch([filterState, sortKey], () => {
  visibleCount.value = props.batchSize;
  const firstId = workspaceJobs.value[0] ? jobKey(workspaceJobs.value[0]) : "";
  if (!userSelectedDetail.value) {
    localSelectedId.value = firstId;
  } else if (!workspaceJobs.value.some((job) => jobKey(job) === localSelectedId.value)) {
  }
}, { deep: true });

const visibleJobs = computed(() => workspaceJobs.value.slice(0, visibleCount.value));
const selectedJob = computed(() => {
  const selected = workspaceJobs.value.find((job) => jobKey(job) === localSelectedId.value);
  return selected || workspaceJobs.value[0] || null;
});
const hasMore = computed(() => visibleCount.value < workspaceJobs.value.length);

// 无限滚动：列表底部哨兵进入视口即自动展开下一批（数据全在内存，无二次请求）
const sentinel = ref<HTMLElement | null>(null);
// .job-list 是桌面端的内部滚动容器；哨兵放进它内部，observer 的 root 用它本身
const listEl = ref<HTMLElement | null>(null);
// 回到顶部：滑过一屏后出现，点击平滑滚回顶部
const showBackToTop = ref(false);
let observer: IntersectionObserver | undefined;

// 右侧详情面板的滚动容器 ref：切换岗位时用于把 scrollTop 归零
const detailEl = ref<HTMLElement | null>(null);

function onScroll() {
  const y = window.scrollY || document.documentElement.scrollTop || 0;
  showBackToTop.value = y > window.innerHeight;
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// 哨兵元素可能在挂载之后才出现（jobs 从空到填充），故 observer 的创建要响应式：
// 挂载时若哨兵已在则直接建；否则等哨兵进 DOM（watch 触发）再建。
function setupObserver() {
  if (observer || typeof IntersectionObserver === "undefined") return;
  const el = listEl.value;
  if (!el || !sentinel.value) return;
  // 桌面端 .job-list 是内部滚动容器、哨兵在其内部 → root 用 .job-list；
  // 移动端 .job-list 不滚动（整页滚动）→ root 退回视口(null)。
  const root = el.scrollHeight > el.clientHeight + 2 ? el : null;
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && hasMore.value) {
          visibleCount.value += props.batchSize;
        }
      }
    },
    { root, rootMargin: "240px" },
  );
  observer.observe(sentinel.value);
}

function teardownObserver() {
  if (observer) {
    observer.disconnect();
    observer = undefined;
  }
}

onMounted(() => {
  setupObserver();
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
});

onBeforeUnmount(() => {
  teardownObserver();
  window.removeEventListener("scroll", onScroll);
});

// 哨兵 ref 从无到有（或列表重渲染导致元素替换）时，(重新)建立 observer。
// 修复首屏（挂载时 jobs 为空、哨兵未渲染）无限滚动不生效的问题。
watch(sentinel, (el) => {
  if (el) setupObserver();
  else teardownObserver();
});

// 记录上一次岗位集合签名：只有集合真正变化（新结果加载 / 有岗位离开本 tab）
// 才重置滚动与选中；原地内容更新（如重抓只补了 JD、判定未变）保留滚动位置。
let prevJobKeySignature = "";
watch(() => props.jobs, (jobs) => {
  const signature = JSON.stringify(jobs.map(jobKey).sort());
  if (signature !== prevJobKeySignature) {
    visibleCount.value = props.batchSize;
    if (!jobs.some((job) => jobKey(job) === localSelectedId.value)) {
      localSelectedId.value = jobs[0] ? jobKey(jobs[0]) : "";
    }
    detailOpen.value = Boolean(jobs.length);
    prevJobKeySignature = signature;
  } else if (!jobs.some((job) => jobKey(job) === localSelectedId.value)) {
    // 集合未变但选中项已不在（理论边界）：回退到第一条
    localSelectedId.value = jobs[0] ? jobKey(jobs[0]) : "";
  }
}, { deep: false, immediate: true });

function jobKey(job: JobItem): string {
  // T511：双 ID 优先 — (platform, platform_job_id) 是岗位稳定键。
  // job_id 是内部 UUID，跨平台可能冲突；platform_job_id 在平台内稳定。
  // 草稿切换不改 task/result，结果 jobKey 不变（platform-schema.md 不变式 5）。
  if (job.platform && job.platform_job_id) {
    return `${job.platform}:${job.platform_job_id}`;
  }
  return String(job.job_id || job.id || job.canonical_url || job.title || "unknown");
}

function selectJob(job: JobItem) {
  localSelectedId.value = jobKey(job);
  detailOpen.value = true;
  userSelectedDetail.value = true;
  // 切换岗位后把右侧详情面板滚回顶部，避免停留在上一个岗位的滚动位置
  // （如已滚到底部收藏/不感兴趣按钮区）。容器元素不变，nextTick 后归零即可。
  nextTick(() => {
    if (typeof detailEl.value?.scrollTo === "function") {
      detailEl.value.scrollTo({ top: 0 });
    }
  });
}

function company(job: JobItem): string {
  return String(job.company || job.boss_name || "公司待确认");
}

// T511：按 job.platform 选 host 白名单（platform-schema.md L196-216）。
// 不按 URL 猜平台 — job.platform 是后端权威来源；缺失时退化为 BOSS 兼容旧结果。
function jobUrl(job: JobItem): string {
  const raw = String(job.canonical_url || job.job_link || job.source_url || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    const host = parsed.hostname.toLowerCase();
    if (parsed.username || parsed.password) return "";
    const port = parsed.port;
    if (port && port !== "443") return "";
    if (job.platform === "zhilian") {
      // 智联：host 恰为 zhaopin.com 或 www.zhaopin.com；
      // path 符合 jobdetail/<id>.htm；http 升级为 https；移除 query 和 fragment。
      const isZhilianHost = host === "zhaopin.com" || host === "www.zhaopin.com";
      if (!isZhilianHost) return "";
      if (!/^\/jobdetail\/[^/]+\.htm$/.test(parsed.pathname)) return "";
      const clean = new URL(parsed.toString());
      clean.protocol = "https:";
      clean.search = "";
      clean.hash = "";
      return clean.toString();
    }
    // 默认分支（boss 或缺失 platform）：保持现有 BOSS HTTPS-only 规则
    const isBossHost = host === "zhipin.com" || host.endsWith(".zhipin.com");
    return parsed.protocol === "https:" && isBossHost ? parsed.toString() : "";
  } catch {
    return "";
  }
}

// T511：智联结果常带的 extra 标签（如 company_nature_label）。
// 后端权威标签在 extra 里，前端只做透传展示；已知键统一映射为中文，
// 避免 company_size / industry 等原始键被兜底逻辑渲染成英文。
const EXTRA_LABEL_MAP: Record<string, string> = {
  company_nature_label: "公司性质",
  company_nature: "公司性质",
  company_size: "公司规模",
  scale_label: "公司规模",
  scale: "公司规模",
  company_scale: "公司规模",
  industry_label: "行业",
  industry: "行业",
  company_industry: "行业",
  stage_label: "融资阶段",
  stage: "融资阶段",
  company_stage: "融资阶段",
};
const extraLabels = computed(() => {
  const extra = selectedJob.value?.extra;
  if (!extra) return [];
  return Object.entries(extra)
    .filter(([, v]) => typeof v === "string" && v)
    .map(([k, v]) => ({
      key: k,
      label: EXTRA_LABEL_MAP[k] || k.replace(/_label$/, "").replace(/_/g, " "),
      value: v as string,
    }));
});

// B033：岗位靠谱判定 flags（高危红 / 中危黄）。高危强制 not_match 已由后端保证，
// 前端只按 level 渲染标记，不依赖 code 映射。
function flagLevel(job: JobItem): "high" | "medium" | "" {
  const flags = job.flags;
  if (!Array.isArray(flags) || !flags.length) return "";
  if (flags.some((f) => f?.level === "high")) return "high";
  if (flags.some((f) => f?.level === "medium")) return "medium";
  return "";
}
const highFlags = computed(() =>
  (selectedJob.value?.flags || []).filter((f) => f?.level === "high"));
const mediumFlags = computed(() =>
  (selectedJob.value?.flags || []).filter((f) => f?.level === "medium"));

function verdictLabel(job: JobItem): string {
  if (job.verdict === "match") return "匹配";
  if (job.verdict === "not_match") return "不匹配";
  if (job.verdict === "uncertain") return "待确认";
  return "";
}

// 平台筛选到无数据的平台时给出专属空态；「全部」档位沿用分类空态文案。
// 应用了筛选条件后列表为空 → 专属“无符合条件岗位”空态（含清除筛选按钮）。
const emptyTitle = computed(() => {
  if (filterCount.value) return "没有符合条件的岗位";
  if (props.platformFilter === "boss" || props.platformFilter === "zhilian") {
    return "该平台暂无数据";
  }
  return props.emptyMessage;
});
const emptyHint = computed(() => {
  if (filterCount.value) return "试试调整筛选条件。";
  if (props.platformFilter === "boss" || props.platformFilter === "zhilian") {
    return "切回「全部」可查看另一平台的岗位。";
  }
  return "完成当前步骤后，岗位会集中显示在这里。";
});
/** 筛选空态（过滤后为空且原列表非空）时显示「清除筛选」按钮；原列表本身为空不显示。 */
const showClearFilter = computed(() => filterCount.value > 0 && props.jobs.length > 0);

function clearFilters() {
  filterState.value = emptyFilterState();
}
</script>

<template>
  <div
    class="job-workspace"
    :class="{
      'defer-mobile-detail': deferMobileDetail,
      'detail-selected': userSelectedDetail,
    }"
  >
    <section class="job-list-pane" aria-label="岗位列表">
      <div class="job-list-heading">
        <div class="job-list-heading-left">
          <span>{{ jobs.length }} 个岗位</span>
        </div>
        <div
          v-if="platformFilter"
          class="result-platform-segment"
          role="tablist"
          aria-label="结果平台筛选"
          data-testid="result-platform-filter"
        >
          <button
            v-for="option in PLATFORM_FILTER_OPTIONS"
            :key="option.id"
            type="button"
            role="tab"
            :aria-selected="platformFilter === option.id"
            :class="['result-platform-segment-btn', { active: platformFilter === option.id }]"
            :data-testid="`result-platform-filter-${option.id}`"
            @click="emit('update:platformFilter', option.id)"
          >{{ option.label }}</button>
        </div>
        <div class="job-list-heading-right">
          <JobListToolbar
            v-if="jobs.length"
            :filter-state="filterState"
            :sort-key="sortKey"
            @apply-filter="(state) => { filterState = state; }"
            @reset-filter="clearFilters"
            @select-sort="(key) => { sortKey = key; }"
          />
          <slot name="heading-actions" />
        </div>
      </div>
      <div v-if="workspaceJobs.length" ref="listEl" class="job-list" role="list">
        <button
          v-for="job in visibleJobs"
          :key="jobKey(job)"
          class="job-row"
          :class="{ selected: selectedJob && jobKey(selectedJob) === jobKey(job) }"
          type="button"
          role="listitem"
          data-testid="job-row"
          @click="selectJob(job)"
        >
          <span class="job-row-main">
            <span class="job-row-titleline">
              <span
                v-if="flagLevel(job)"
                class="job-row-flag"
                :class="flagLevel(job)"
                aria-hidden="true"
              >⚠</span>
              <span class="job-row-title">{{ job.title || "未知岗位" }}</span>
              <span
                v-if="platformLabel(job)"
                class="job-row-platform"
                :data-platform="job.platform"
                data-testid="job-row-platform-badge"
              >{{ platformLabel(job) }}</span>
              <span v-if="job._applied" class="job-row-applied" data-testid="job-row-applied-badge">已投</span>
            </span>
            <span class="job-row-company">{{ company(job) }}</span>
          </span>
          <span class="job-row-meta">
            <strong>{{ job.salary || "薪资面议" }}</strong>
            <span>{{ cleanJobLocation(job.location) || "地点待确认" }}</span>
          </span>

        </button>
        <div ref="sentinel" class="load-sentinel" aria-hidden="true"></div>
      </div>
      <section v-else class="empty-panel" data-testid="job-workspace-empty">
        <BriefcaseBusiness :size="28" aria-hidden="true" />
        <h2>{{ emptyTitle }}</h2>
        <p>{{ emptyHint }}</p>
        <button
          v-if="showClearFilter"
          type="button"
          class="button secondary small"
          data-testid="clear-filter"
          @click="clearFilters"
        >清除筛选</button>
      </section>
    </section>

    <aside
      v-if="selectedJob && detailOpen"
      ref="detailEl"
      class="job-detail-pane"
      data-testid="job-detail"
      aria-label="岗位详情"
    >
      <header class="job-detail-header">
        <div>
          <h2>{{ selectedJob.title || "未知岗位" }}</h2>
          <p>{{ company(selectedJob) }}</p>
        </div>
        <div class="job-detail-header-side">
          <div class="verdict-line">
            <span v-if="verdictLabel(selectedJob)" class="status-pill" :data-verdict="selectedJob.verdict">
              <Check v-if="selectedJob.verdict === 'match'" :size="13" :stroke-width="2.4" aria-hidden="true" />
              {{ verdictLabel(selectedJob) }}
            </span>
            <span v-if="verdictLabel(selectedJob)" class="verdict-by">AI 判定</span>
            <span
              v-if="selectedJob.platform"
              class="status-pill platform-pill"
              :data-platform="selectedJob.platform"
              data-testid="job-platform-badge"
            >{{ selectedJob.platform === 'boss' ? 'BOSS' : '智联' }}</span>
            <span
              v-if="selectedJob._applied"
              class="status-pill applied-pill"
              data-testid="job-detail-applied-badge"
            >已投递</span>
          </div>
          <button class="icon-button mobile-detail-close" type="button" aria-label="关闭岗位详情" @click="detailOpen = false">
            <X :size="20" />
          </button>
        </div>
      </header>

      <div class="job-detail-facts">
        <span><strong>{{ selectedJob.salary || "薪资面议" }}</strong></span>
        <span><MapPin :size="16" aria-hidden="true" />{{ cleanJobLocation(selectedJob.location) || "地点待确认" }}</span>
        <span><BriefcaseBusiness :size="16" aria-hidden="true" />{{ company(selectedJob) }}</span>
        <span v-if="selectedJob.experience" data-testid="job-experience">{{ selectedJob.experience }}</span>
        <span v-if="selectedJob.degree" data-testid="job-degree">{{ selectedJob.degree }}</span>
      </div>

      <div v-if="extraLabels.length" class="job-extra-facts" data-testid="job-extra-facts">
        <span
          v-for="item in extraLabels"
          :key="item.key"
          class="job-extra-fact"
          :data-extra-key="item.key"
        ><span class="job-extra-fact-label">{{ item.label + ": " }}</span><span class="job-extra-fact-value">{{ item.value }}</span></span>
      </div>

      <div
        v-if="selectedJob.verdict_reason || selectedJob.reason"
        class="verdict-reason"
        :class="{ 'flag-danger': highFlags.length }"
      >
        <strong><span class="sec-mk" aria-hidden="true"></span>AI 判断说明</strong>
        <p>
          <span
            v-for="(f, i) in highFlags"
            :key="i"
            class="flag-reason"
            :data-testid="`flag-high-${i}`"
          ><span class="flag-icon" aria-hidden="true">⚠</span>{{ f.reason }}</span>
          {{ selectedJob.verdict_reason || selectedJob.reason }}
        </p>
      </div>

      <div
        v-if="(selectedJob.caveats && selectedJob.caveats.length) || mediumFlags.length"
        class="caveats-list"
        :class="{ 'flag-unsure': mediumFlags.length }"
      >
        <strong><span class="sec-mk" aria-hidden="true"></span>软性要求提醒（不影响匹配，自己判断）</strong>
        <ul>
          <li v-for="(c, i) in selectedJob.caveats" :key="i">{{ c }}</li>
          <li
            v-for="(f, i) in mediumFlags"
            :key="'flag-' + i"
            class="flag-reason"
            :data-testid="`flag-medium-${i}`"
          ><span class="flag-icon" aria-hidden="true">⚠</span>{{ f.reason }}</li>
        </ul>
      </div>

      <section class="jd-content">
        <h3><span class="sec-mk" aria-hidden="true"></span>职位描述</h3>
        <p>{{ selectedJob.jd || selectedJob.jd_excerpt || "尚未获取职位描述。" }}</p>
      </section>

      <div class="job-detail-actions">
        <slot name="actions" :job="selectedJob" />
        <a
          v-if="jobUrl(selectedJob)"
          class="button secondary"
          :href="jobUrl(selectedJob)"
          target="_blank"
          rel="noopener noreferrer"
        >
          查看原岗位 <ArrowUpRight :size="17" aria-hidden="true" />
        </a>
      </div>
      </aside>
      <button
        v-if="showBackToTop"
        class="back-to-top"
        type="button"
        aria-label="回到顶部"
        @click="scrollToTop"
      >
        ↑ 顶部
      </button>
  </div>
</template>
