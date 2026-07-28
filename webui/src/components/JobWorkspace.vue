<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ArrowUpRight, BriefcaseBusiness, MapPin, X } from "@lucide/vue";
import type { JobItem } from "../types";

const props = withDefaults(defineProps<{
  jobs: JobItem[];
  batchSize?: number;
  emptyMessage: string;
  deferMobileDetail?: boolean;
}>(), {
  batchSize: 30,
  deferMobileDetail: false,
});

const visibleCount = ref(props.batchSize);
const localSelectedId = ref("");
const detailOpen = ref(true);
const userSelectedDetail = ref(false);

const visibleJobs = computed(() => props.jobs.slice(0, visibleCount.value));
const selectedJob = computed(() => {
  const selected = props.jobs.find((job) => jobKey(job) === localSelectedId.value);
  return selected || props.jobs[0] || null;
});
const hasMore = computed(() => visibleCount.value < props.jobs.length);

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

function jobUrl(job: JobItem): string {
  const raw = String(job.canonical_url || job.job_link || job.source_url || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    const host = parsed.hostname.toLowerCase();
    const isBossHost = host === "zhipin.com"
      || host.endsWith(".zhipin.com");
    return parsed.protocol === "https:" && isBossHost ? parsed.toString() : "";
  } catch {
    return "";
  }
}

function verdictLabel(job: JobItem): string {
  if (job.verdict === "match") return "匹配";
  if (job.verdict === "not_match") return "不匹配";
  if (job.verdict === "uncertain") return "待确认";
  return "";
}
</script>

<template>
  <div
    v-if="jobs.length"
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
          <span>已加载 {{ visibleJobs.length }}</span>
        </div>
        <slot name="heading-actions" />
      </div>
      <div ref="listEl" class="job-list" role="list">
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
            <span class="job-row-title">{{ job.title || "未知岗位" }}</span>
            <span class="job-row-company">{{ company(job) }}</span>
          </span>
          <span class="job-row-meta">
            <strong>{{ job.salary || "薪资面议" }}</strong>
            <span>{{ job.location || "地点待确认" }}</span>
          </span>

        </button>
        <div ref="sentinel" class="load-sentinel" aria-hidden="true"></div>
      </div>
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
          <span v-if="verdictLabel(selectedJob)" class="status-pill" :data-verdict="selectedJob.verdict">
            {{ verdictLabel(selectedJob) }}
          </span>
          <h2>{{ selectedJob.title || "未知岗位" }}</h2>
          <p>{{ company(selectedJob) }}</p>
        </div>
        <button class="icon-button mobile-detail-close" type="button" aria-label="关闭岗位详情" @click="detailOpen = false">
          <X :size="20" />
        </button>
      </header>

      <div class="job-detail-facts">
        <span><strong>{{ selectedJob.salary || "薪资面议" }}</strong></span>
        <span><MapPin :size="16" aria-hidden="true" />{{ selectedJob.location || "地点待确认" }}</span>
        <span><BriefcaseBusiness :size="16" aria-hidden="true" />{{ company(selectedJob) }}</span>
      </div>

      <div v-if="selectedJob.verdict_reason || selectedJob.reason" class="verdict-reason">
        <strong>判断说明</strong>
        <p>{{ selectedJob.verdict_reason || selectedJob.reason }}</p>
      </div>

      <div v-if="selectedJob.caveats && selectedJob.caveats.length" class="caveats-list">
        <strong>软性要求提醒（不影响匹配，自己判断）</strong>
        <ul>
          <li v-for="(c, i) in selectedJob.caveats" :key="i">{{ c }}</li>
        </ul>
      </div>

      <section class="jd-content">
        <h3>职位描述</h3>
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

  <section v-else class="empty-panel">
    <BriefcaseBusiness :size="28" aria-hidden="true" />
    <h2>{{ emptyMessage }}</h2>
    <p>完成当前步骤后，岗位会集中显示在这里。</p>
  </section>
</template>
