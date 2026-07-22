<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  Bookmark,
  FileSearch,
  Filter,
  PauseCircle,
  Play,
  RotateCcw,
  Sparkles,
  Trash2,
} from "@lucide/vue";
import BaseDialog from "../components/BaseDialog.vue";
import JobWorkspace from "../components/JobWorkspace.vue";
import { apiRequest, errorMessage } from "../api";
import { buildFilterSummary, isTerminalScreeningStatus } from "../screening";
import type { ScreeningOptions } from "../screening";
import type { CandidateProfile, JobItem, Notice } from "../types";

type Zone = "match" | "mismatch" | "pending" | "interested" | "trash";

interface ScreeningRun {
  run_id: string;
  status: string;
  source_count?: number;
  processed_count?: number;
  match_count?: number;
  mismatch_count?: number;
  pending_count?: number;
  parse_failure_count?: number;
  error_code?: string | null;
}

const props = defineProps<{ profileId: string }>();
const emit = defineEmits<{
  notify: [notice: Notice];
  "profile-created": [profile: CandidateProfile];
}>();

const filterKeys = ["city", "salary", "experience", "degree", "scale", "stage", "industry"];
const filterLabels: Record<string, string> = {
  city: "城市",
  salary: "薪资",
  experience: "经验",
  degree: "学历",
  scale: "公司规模",
  stage: "融资阶段",
  industry: "行业",
};
const zoneLabels: Record<Zone, string> = {
  match: "符合",
  mismatch: "不符合",
  pending: "待核验",
  interested: "感兴趣",
  trash: "垃圾桶",
};

const options = ref<ScreeningOptions>({});
const filters = ref<Record<string, string>>(Object.fromEntries(filterKeys.map((key) => [key, ""])));
const filtersOpen = ref(true);
const keyword = ref("");
const pages = ref(1);
const maxDetails = ref(3);
const resumeFile = ref<File | null>(null);
const resumeId = ref("");
const skipResume = ref(false);
const suggestBusy = ref(false);
const aiUnavailable = ref(false);
const runId = ref("");
const run = ref<ScreeningRun | null>(null);
const runBusy = ref(false);
const activeZone = ref<Zone>("match");
const zoneJobs = ref<Record<Zone, JobItem[]>>({
  match: [], mismatch: [], pending: [], interested: [], trash: [],
});
const actionBusyIds = ref(new Set<string>());
const confirmCleanupOpen = ref(false);
const cleanupMessage = ref("");
const cleanupBusy = ref(false);
let pollTimer: number | undefined;

const filterSummary = computed(() => buildFilterSummary(
  Object.fromEntries(Object.entries(filters.value).filter(([, value]) => Boolean(value))),
  options.value,
  keyword.value,
  pages.value,
  maxDetails.value,
));
const zones = computed(() => (["match", "mismatch", "pending", "interested", "trash"] as Zone[])
  .map((id) => ({ id, label: zoneLabels[id], count: zoneJobs.value[id].length })));
const currentJobs = computed(() => zoneJobs.value[activeZone.value]);
const currentEmptyMessage = computed(() => ({
  match: "执行筛选后，符合条件的岗位会显示在这里",
  mismatch: "当前没有不符合条件的岗位",
  pending: "所有岗位都已完成核验",
  interested: "还没有长期保存的感兴趣岗位",
  trash: "垃圾桶为空",
})[activeZone.value]);
const canResume = computed(() => ["failed", "interrupted"].includes(run.value?.status || ""));
const running = computed(() => ["queued", "running"].includes(run.value?.status || "") || runBusy.value);

onMounted(() => {
  void Promise.allSettled([loadOptions(), loadPersistentZones(), restoreRun()]);
});

watch(() => props.profileId, () => {
  void loadPersistentZones();
  void restoreRun();
});

onBeforeUnmount(() => {
  if (pollTimer) window.clearTimeout(pollTimer);
});

function notify(message: string, tone: Notice["tone"] = "info") {
  emit("notify", { message, tone });
}

function jobId(job: JobItem): string {
  return String(job.job_id || job.id || job.canonical_url || "");
}

function setActionBusy(id: string, busy: boolean) {
  const next = new Set(actionBusyIds.value);
  if (busy) next.add(id);
  else next.delete(id);
  actionBusyIds.value = next;
}

function chooseResume(event: Event) {
  const input = event.target as HTMLInputElement;
  resumeFile.value = input.files?.[0] || null;
}

async function loadOptions() {
  try {
    const data = await apiRequest<{ options?: ScreeningOptions }>("/api/screening/filter-options");
    options.value = data.options || {};
  } catch (error) {
    notify(errorMessage(error, "筛选项加载失败"), "error");
  }
}

async function suggestFilters() {
  if (skipResume.value) {
    notify("已选择跳过简历，请直接手动填写筛选条件", "info");
    return;
  }
  if (!resumeFile.value && !resumeId.value) {
    notify("请先选择简历文件", "warning");
    return;
  }
  suggestBusy.value = true;
  try {
    if (resumeFile.value) {
      const form = new FormData();
      form.append("file", resumeFile.value);
      if (props.profileId) form.append("profile_id", props.profileId);
      const uploaded = await apiRequest<{ resume_id: string; profile_id?: string }>("/api/screening/resume", {
        method: "POST",
        body: form,
      });
      resumeId.value = uploaded.resume_id;
      if (!props.profileId && uploaded.profile_id) {
        const profiles = await apiRequest<{ profiles?: CandidateProfile[] }>("/api/profiles");
        const profile = profiles.profiles?.find((item) => item.id === uploaded.profile_id);
        if (profile) emit("profile-created", profile);
      }
    }
    const data = await apiRequest<{
      status?: string;
      suggestions?: Record<string, string>;
    }>("/api/screening/resume/suggest", {
      method: "POST",
      json: { resume_id: resumeId.value },
    });
    if (data.status === "ai_unavailable") {
      aiUnavailable.value = true;
      notify("AI 暂不可用，可继续人工填写筛选条件", "warning");
      return;
    }
    for (const key of filterKeys) {
      if (data.suggestions?.[key] !== undefined) filters.value[key] = data.suggestions[key];
    }
    notify("AI 建议已填入，请确认后再执行", "success");
  } catch (error) {
    aiUnavailable.value = true;
    notify(errorMessage(error, "AI 建议失败，可继续手动填写"), "warning");
  } finally {
    suggestBusy.value = false;
  }
}

function currentFilters(): Record<string, string> {
  return Object.fromEntries(Object.entries(filters.value).filter(([, value]) => Boolean(value)));
}

async function startRun() {
  if (!keyword.value.trim()) {
    notify("请输入搜索关键词", "warning");
    return;
  }
  runBusy.value = true;
  try {
    const payload: Record<string, unknown> = {
      keyword: keyword.value.trim(),
      filters: currentFilters(),
      pages: pages.value,
      max_details: maxDetails.value,
    };
    if (props.profileId) payload.profile_id = props.profileId;
    if (resumeId.value) payload.resume_id = resumeId.value;
    const created = await apiRequest<ScreeningRun>("/api/screening/runs", {
      method: "POST",
      json: payload,
    });
    run.value = created;
    runId.value = created.run_id;
    saveRun();
    filtersOpen.value = false;
    zoneJobs.value = { match: [], mismatch: [], pending: [], interested: zoneJobs.value.interested, trash: zoneJobs.value.trash };
    notify("筛选任务已启动", "success");
    await pollRun(created.run_id);
  } catch (error) {
    runBusy.value = false;
    notify(errorMessage(error, "筛选启动失败"), "error");
  }
}

async function pollRun(id: string) {
  try {
    const current = await apiRequest<ScreeningRun>(`/api/screening/runs/${encodeURIComponent(id)}`);
    run.value = current;
    if (isTerminalScreeningStatus(current.status)) {
      runBusy.value = false;
      await loadRunZones(id);
      if (current.status === "succeeded") notify("筛选完成", "success");
      else if (current.status === "partial") notify("筛选完成，部分岗位等待核验", "warning");
      else notify("筛选已停止，已完成结果仍被保留", "warning");
      return;
    }
    pollTimer = window.setTimeout(() => void pollRun(id), 1800);
  } catch (error) {
    pollTimer = window.setTimeout(() => void pollRun(id), 4000);
    notify(errorMessage(error, "筛选状态刷新失败，正在重试"), "warning");
  }
}

async function cancelRun() {
  if (!runId.value) return;
  try {
    const cancelled = await apiRequest<ScreeningRun>(`/api/screening/runs/${encodeURIComponent(runId.value)}/cancel`, {
      method: "POST",
      json: {},
    });
    if (pollTimer) window.clearTimeout(pollTimer);
    runBusy.value = false;
    run.value = cancelled;
    await loadRunZones(runId.value);
    notify("筛选已取消，已完成结果已保留", "warning");
  } catch (error) {
    notify(errorMessage(error, "取消筛选失败"), "error");
  }
}

async function resumeRun() {
  if (!runId.value) return;
  runBusy.value = true;
  try {
    const resumed = await apiRequest<ScreeningRun>(`/api/screening/runs/${encodeURIComponent(runId.value)}/resume`, {
      method: "POST",
      json: {},
    });
    run.value = resumed;
    await pollRun(runId.value);
  } catch (error) {
    runBusy.value = false;
    notify(errorMessage(error, "继续筛选失败"), "error");
  }
}

async function loadRunZones(id = runId.value) {
  if (!id) return;
  const profileQuery = props.profileId ? `?profile_id=${encodeURIComponent(props.profileId)}` : "";
  const requests = await Promise.allSettled([
    apiRequest<{ items?: JobItem[] }>(`/api/screening/runs/${encodeURIComponent(id)}/matches${profileQuery}`),
    apiRequest<{ items?: JobItem[] }>(`/api/screening/runs/${encodeURIComponent(id)}/mismatches${profileQuery}`),
    apiRequest<{ items?: JobItem[] }>(`/api/screening/runs/${encodeURIComponent(id)}/pending`),
  ]);
  const keys: Zone[] = ["match", "mismatch", "pending"];
  requests.forEach((request, index) => {
    if (request.status === "fulfilled") zoneJobs.value[keys[index]] = request.value.items || [];
  });
  await loadPersistentZones();
}

async function loadPersistentZones() {
  if (!props.profileId) {
    zoneJobs.value.interested = [];
    zoneJobs.value.trash = [];
    return;
  }
  const profile = encodeURIComponent(props.profileId);
  const [interested, trash] = await Promise.allSettled([
    apiRequest<{ items?: JobItem[] }>(`/api/screening/interested?profile_id=${profile}`),
    apiRequest<{ items?: JobItem[] }>(`/api/screening/trash?profile_id=${profile}`),
  ]);
  if (interested.status === "fulfilled") zoneJobs.value.interested = interested.value.items || [];
  if (trash.status === "fulfilled") zoneJobs.value.trash = trash.value.items || [];
}

function saveRun() {
  if (!runId.value) return;
  localStorage.setItem("boss-screening-run", JSON.stringify({
    run_id: runId.value,
    profile_id: props.profileId,
  }));
}

async function restoreRun() {
  const raw = localStorage.getItem("boss-screening-run");
  if (!raw) return;
  try {
    const saved = JSON.parse(raw) as { run_id?: string; profile_id?: string };
    if (!saved.run_id || saved.profile_id !== props.profileId) return;
    const restored = await apiRequest<ScreeningRun>(`/api/screening/runs/${encodeURIComponent(saved.run_id)}`);
    runId.value = saved.run_id;
    run.value = restored;
    filtersOpen.value = false;
    if (isTerminalScreeningStatus(restored.status)) await loadRunZones(saved.run_id);
    else {
      runBusy.value = true;
      await pollRun(saved.run_id);
    }
  } catch {
    localStorage.removeItem("boss-screening-run");
  }
}

async function ensureProfile(): Promise<string> {
  if (props.profileId) return props.profileId;
  const profile = await apiRequest<CandidateProfile>("/api/profiles", {
    method: "POST",
    json: { name: "筛选工作台", confirmed_fields: currentFilters() },
  });
  emit("profile-created", profile);
  return profile.id;
}

async function markInterest(job: JobItem) {
  const id = jobId(job);
  if (!id || !runId.value || actionBusyIds.value.has(id)) return;
  setActionBusy(id, true);
  try {
    const profileId = await ensureProfile();
    await apiRequest(`/api/screening/jobs/${encodeURIComponent(id)}/interest`, {
      method: "POST",
      json: { profile_id: profileId, run_id: runId.value },
    });
    await loadRunZones();
    notify("已加入感兴趣", "success");
  } catch (error) {
    notify(errorMessage(error, "感兴趣标记失败"), "error");
  } finally {
    setActionBusy(id, false);
  }
}

async function markReject(job: JobItem) {
  const id = jobId(job);
  if (!id || !runId.value || actionBusyIds.value.has(id)) return;
  setActionBusy(id, true);
  try {
    const profileId = await ensureProfile();
    await apiRequest(`/api/screening/jobs/${encodeURIComponent(id)}/reject`, {
      method: "POST",
      json: { profile_id: profileId, run_id: runId.value, origin_zone: activeZone.value },
    });
    await loadRunZones();
    notify("岗位已移入垃圾桶", "info");
  } catch (error) {
    notify(errorMessage(error, "岗位移除失败"), "error");
  } finally {
    setActionBusy(id, false);
  }
}

async function retryPending(job: JobItem) {
  const id = jobId(job);
  if (!id || !runId.value) return;
  setActionBusy(id, true);
  try {
    await apiRequest(`/api/screening/runs/${encodeURIComponent(runId.value)}/pending/${encodeURIComponent(id)}/retry`, {
      method: "POST", json: {},
    });
    await loadRunZones();
  } catch (error) {
    notify(errorMessage(error, "重试失败"), "error");
  } finally {
    setActionBusy(id, false);
  }
}

async function routePending(job: JobItem, target: "match" | "mismatch") {
  const id = jobId(job);
  if (!id || !runId.value) return;
  setActionBusy(id, true);
  try {
    await apiRequest(`/api/screening/runs/${encodeURIComponent(runId.value)}/pending/${encodeURIComponent(id)}/route`, {
      method: "POST", json: { target },
    });
    await loadRunZones();
    activeZone.value = target;
  } catch (error) {
    notify(errorMessage(error, "人工归类失败"), "error");
  } finally {
    setActionBusy(id, false);
  }
}

async function retryAllPending() {
  if (!runId.value) return;
  try {
    await apiRequest(`/api/screening/runs/${encodeURIComponent(runId.value)}/pending/retry-all`, {
      method: "POST", json: {},
    });
    await loadRunZones();
  } catch (error) {
    notify(errorMessage(error, "批量重试失败"), "error");
  }
}

async function restoreTrash(job: JobItem) {
  const id = jobId(job);
  if (!id) return;
  setActionBusy(id, true);
  try {
    const profileId = await ensureProfile();
    const data = await apiRequest<{ restored_to?: Zone; recovery_run_id?: string }>(`/api/screening/trash/${encodeURIComponent(id)}/restore`, {
      method: "POST", json: { profile_id: profileId },
    });
    if (data.recovery_run_id) {
      runId.value = data.recovery_run_id;
      saveRun();
    }
    await loadRunZones();
    activeZone.value = data.restored_to || "match";
    notify("岗位已恢复", "success");
  } catch (error) {
    notify(errorMessage(error, "岗位恢复失败"), "error");
  } finally {
    setActionBusy(id, false);
  }
}

async function previewCleanup() {
  try {
    const data = await apiRequest<{ runs_to_cleanup?: number; pending_at_cleanup?: number }>("/api/screening/cleanup/preview?days=30");
    cleanupMessage.value = `将清理 ${data.runs_to_cleanup || 0} 次临时运行。`
      + (data.pending_at_cleanup ? `其中 ${data.pending_at_cleanup} 条仍待核验。` : "")
      + "长期感兴趣和垃圾桶不会删除。";
    confirmCleanupOpen.value = true;
  } catch (error) {
    notify(errorMessage(error, "清理预览失败"), "error");
  }
}

async function confirmCleanup() {
  cleanupBusy.value = true;
  try {
    await apiRequest("/api/screening/cleanup", { method: "POST", json: { days: 30 } });
    confirmCleanupOpen.value = false;
    notify("临时筛选结果已清理", "success");
  } catch (error) {
    notify(errorMessage(error, "清理失败"), "error");
  } finally {
    cleanupBusy.value = false;
  }
}
</script>

<template>
  <main class="screening-shell" data-testid="screening-view">
    <header class="screening-hero">
      <div>
        <span class="eyebrow">筛选工作台</span>
        <h1>把岗位快速分到该去的位置</h1>
        <p>此模式保留原有硬规则筛选、简历建议、待核验与长期结果区。</p>
      </div>
      <button class="button secondary" type="button" :aria-expanded="filtersOpen" aria-controls="screening-filters" @click="filtersOpen = !filtersOpen">
        <Filter :size="17" aria-hidden="true" />{{ filtersOpen ? "收起筛选" : "修改筛选" }}
      </button>
    </header>

    <section id="screening-filters" v-show="filtersOpen" class="content-card screening-controls">
      <div v-if="aiUnavailable" class="inline-alert" data-tone="warning">
        AI 暂不可用：可以跳过简历并手动填写条件，筛选仍会按现有硬规则执行。
      </div>

      <div class="screening-control-section">
        <div class="section-heading compact-heading">
          <div><span class="card-kicker">可选</span><h2>简历与 AI 建议</h2></div>
        </div>
        <div class="resume-suggest-row">
          <label class="file-input-button button secondary">
            <input type="file" accept=".txt,.pdf,.docx" @change="chooseResume">
            <FileSearch :size="17" aria-hidden="true" />{{ resumeFile?.name || "选择简历" }}
          </label>
          <button class="button secondary" type="button" :disabled="suggestBusy || skipResume" @click="suggestFilters">
            <Sparkles :size="17" aria-hidden="true" />{{ suggestBusy ? "生成中…" : "AI 建议填筛" }}
          </button>
          <label class="compact-check"><input v-model="skipResume" type="checkbox">跳过简历，手动填筛</label>
        </div>
      </div>

      <div class="screening-control-section">
        <div class="section-heading compact-heading"><div><span class="card-kicker">岗位条件</span><h2>留空即不限</h2></div></div>
        <div class="screening-filter-grid">
          <label v-for="key in filterKeys" :key="key" class="field-label">
            <span>{{ filterLabels[key] }}</span>
            <select v-model="filters[key]" :data-testid="`filter-${key}`">
              <option v-for="option in options[key] || [{ label: '不限', value: '' }]" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
        </div>
      </div>

      <div class="screening-control-section execution-row">
        <label class="field-label execution-keyword">
          <span>搜索关键词</span>
          <input v-model="keyword" type="text" data-testid="screening-keyword" placeholder="如：Python 后端">
        </label>
        <label class="field-label compact-number"><span>页数</span><input v-model.number="pages" type="number" min="1" max="10"></label>
        <label class="field-label compact-number"><span>详情数</span><input v-model.number="maxDetails" type="number" min="1"></label>
        <button class="button primary execution-button" type="button" data-testid="start-screening" :disabled="running" @click="startRun">
          <Play :size="17" aria-hidden="true" />{{ running ? "筛选中…" : "执行筛选" }}
        </button>
      </div>
      <div class="filter-summary-line">{{ filterSummary }}</div>
    </section>

    <section v-if="run" class="run-status-strip" aria-live="polite">
      <div>
        <span>状态</span><strong>{{ run.status }}</strong>
      </div>
      <div><span>进度</span><strong>{{ run.processed_count || 0 }} / {{ run.source_count || 0 }}</strong></div>
      <div><span>解析失败</span><strong>{{ run.parse_failure_count || 0 }}</strong></div>
      <div class="run-actions">
        <button v-if="running" class="button danger" type="button" @click="cancelRun"><PauseCircle :size="17" />取消</button>
        <button v-if="canResume" class="button secondary" type="button" @click="resumeRun"><RotateCcw :size="17" />继续处理</button>
      </div>
    </section>

    <section class="screening-results">
      <div class="screening-zone-tabs" role="tablist" aria-label="筛选结果区域">
        <template v-for="(zone, index) in zones" :key="zone.id">
          <span v-if="index === 0" class="zone-group-label">本次运行</span>
          <span v-if="index === 3" class="zone-group-label">长期保存</span>
          <button
            type="button"
            role="tab"
            :data-zone-tab="zone.id"
            :aria-selected="activeZone === zone.id"
            :class="{ active: activeZone === zone.id }"
            @click="activeZone = zone.id"
          >{{ zone.label }}<span>{{ zone.count }}</span></button>
        </template>
      </div>

      <div class="zone-toolbar">
        <span>{{ currentEmptyMessage }}</span>
        <div>
          <button v-if="activeZone === 'pending' && currentJobs.length" class="button secondary" type="button" @click="retryAllPending">批量重试</button>
          <button class="button ghost" type="button" @click="previewCleanup"><Trash2 :size="16" />清理 30 天结果</button>
        </div>
      </div>

      <JobWorkspace :jobs="currentJobs" :empty-message="currentEmptyMessage">
        <template #actions="{ job }">
          <template v-if="activeZone === 'match' || activeZone === 'mismatch'">
            <button class="button primary" type="button" :disabled="actionBusyIds.has(jobId(job))" @click="markInterest(job)">
              <Bookmark :size="17" />感兴趣
            </button>
            <button class="button danger" type="button" :disabled="actionBusyIds.has(jobId(job))" @click="markReject(job)">不感兴趣</button>
          </template>
          <template v-else-if="activeZone === 'pending'">
            <button v-if="job.retryable" class="button secondary" type="button" :disabled="actionBusyIds.has(jobId(job))" @click="retryPending(job)">重试</button>
            <button class="button primary" type="button" :disabled="actionBusyIds.has(jobId(job))" @click="routePending(job, 'match')">人工通过</button>
            <button class="button danger" type="button" :disabled="actionBusyIds.has(jobId(job))" @click="routePending(job, 'mismatch')">人工不通过</button>
          </template>
          <template v-else-if="activeZone === 'trash'">
            <button class="button primary" type="button" :disabled="actionBusyIds.has(jobId(job))" @click="restoreTrash(job)">恢复岗位</button>
          </template>
        </template>
      </JobWorkspace>
    </section>

    <BaseDialog
      id="cleanup-confirm"
      :open="confirmCleanupOpen"
      title="确认清理临时结果"
      :description="cleanupMessage"
      size="sm"
      @close="confirmCleanupOpen = false"
    >
      <p class="confirm-copy">该操作只清理超过 30 天的临时运行，不删除长期感兴趣、已投递或垃圾桶记录。</p>
      <template #footer>
        <button class="button ghost" type="button" @click="confirmCleanupOpen = false">取消</button>
        <button class="button danger" type="button" :disabled="cleanupBusy" @click="confirmCleanup">{{ cleanupBusy ? "清理中…" : "确认清理" }}</button>
      </template>
    </BaseDialog>
  </main>
</template>
