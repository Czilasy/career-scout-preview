<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ArrowUpRight, BriefcaseBusiness, ChevronRight, MapPin, X } from "@lucide/vue";
import type { JobItem } from "../types";

const props = withDefaults(defineProps<{
  jobs: JobItem[];
  batchSize?: number;
  emptyMessage: string;
}>(), {
  batchSize: 30,
});

const visibleCount = ref(props.batchSize);
const localSelectedId = ref("");
const detailOpen = ref(true);

const visibleJobs = computed(() => props.jobs.slice(0, visibleCount.value));
const selectedJob = computed(() => {
  const selected = props.jobs.find((job) => jobKey(job) === localSelectedId.value);
  return selected || props.jobs[0] || null;
});
const hasMore = computed(() => visibleCount.value < props.jobs.length);

watch(() => props.jobs, (jobs) => {
  visibleCount.value = props.batchSize;
  if (!jobs.some((job) => jobKey(job) === localSelectedId.value)) {
    localSelectedId.value = jobs[0] ? jobKey(jobs[0]) : "";
  }
  detailOpen.value = Boolean(jobs.length);
}, { deep: false, immediate: true });

function jobKey(job: JobItem): string {
  return String(job.job_id || job.id || job.canonical_url || job.title || "unknown");
}

function selectJob(job: JobItem) {
  localSelectedId.value = jobKey(job);
  detailOpen.value = true;
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
  <div v-if="jobs.length" class="job-workspace">
    <section class="job-list-pane" aria-label="岗位列表">
      <div class="job-list-heading">
        <span>{{ jobs.length }} 个岗位</span>
        <span>已加载 {{ visibleJobs.length }}</span>
      </div>
      <div class="job-list" role="list">
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
          <ChevronRight :size="18" aria-hidden="true" />
        </button>
      </div>
      <button
        v-if="hasMore"
        class="button secondary load-more"
        type="button"
        data-testid="load-more"
        @click="visibleCount += batchSize"
      >
        加载更多（剩余 {{ jobs.length - visibleJobs.length }}）
      </button>
    </section>

    <aside
      v-if="selectedJob && detailOpen"
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
  </div>

  <section v-else class="empty-panel">
    <BriefcaseBusiness :size="28" aria-hidden="true" />
    <h2>{{ emptyMessage }}</h2>
    <p>完成当前步骤后，岗位会集中显示在这里。</p>
  </section>
</template>
