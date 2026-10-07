<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { Check, LoaderCircle, ScrollText, Trash2, X } from "@lucide/vue";
import { formatHistoryTime, type FlowHistoryItem, type FlowHistoryTrack, type HistoryRoundItem } from "../composables/resultHistory";
import type { HistoryRoundDetail } from "../composables/resultHistory";
import { historyStatusLabel, platformLabel } from "../discovery";

const props = defineProps<{
  open: boolean;
  items: HistoryRoundItem[];
  loading: boolean;
  error: string;
  deleting: boolean;
  deleteTarget: HistoryRoundItem | null;
  detail?: HistoryRoundDetail | null;
  flowItems?: FlowHistoryItem[];
}>();

const emit = defineEmits<{
  close: [];
  "open-round": [runId: string];
  "confirm-delete": [item: HistoryRoundItem];
  "cancel-delete": [];
  "delete-round": [item: HistoryRoundItem];
  "view-log": [item: HistoryRoundItem];
}>();

const closeEl = ref<HTMLButtonElement | null>(null);
const panelEl = ref<HTMLElement | null>(null);
let previousFocus: HTMLElement | null = null;
const headerBottom = ref<string>();
let headerObserver: ResizeObserver | null = null;

function measureHeader() {
  const header = document.querySelector<HTMLElement>(".app-header");
  const bottom = header?.getBoundingClientRect().bottom || 0;
  headerBottom.value = bottom > 0 ? `${bottom + 8}px` : undefined;
}

function releaseHeaderObserver() {
  headerObserver?.disconnect();
  headerObserver = null;
  window.removeEventListener("resize", measureHeader);
}

function observeHeader() {
  releaseHeaderObserver();
  measureHeader();
  const header = document.querySelector<HTMLElement>(".app-header");
  if (header && typeof ResizeObserver !== "undefined") {
    headerObserver = new ResizeObserver(measureHeader);
    headerObserver.observe(header);
  }
  window.addEventListener("resize", measureHeader);
}

const activePlatform = ref<"aggregate" | "boss" | "zhilian">("aggregate");
// Flow 卡片视图只承载有 durable flow 身份的真实流程；后端会把每一条旧结果轮
// 合成为 legacy Flow（flowItems 因此恒非空），legacy 轮必须继续走平铺轮次列表，
// 否则 043 的「删除轮次」「查看运行日志」与计数明细会在界面上整块消失。
const realFlowItems = computed(() => (props.flowItems || []).filter((flow) => flow.legacy !== true));
const aggregateFlowItems = computed(() => realFlowItems.value.filter((flow) => flow.selection === "all"));
const singlePlatformFlowItems = computed(() => realFlowItems.value.filter((flow) => flow.selection !== "all"));
const flowCardItems = computed(() => activePlatform.value === "aggregate"
  ? aggregateFlowItems.value
  : singlePlatformFlowItems.value.filter((flow) => flow.selection === activePlatform.value));
// 单平台流程复用紧凑轮次外观；组合流程的各平台结果可在对应平台页独立查看。
const flowOwnedRunIds = computed(() => new Set(
  singlePlatformFlowItems.value
    .flatMap((flow) => (flow.tracks || []).map((track) => flowTrackRunId(track)))
    .filter(Boolean),
));
const roundItems = computed(() => (props.items || []).filter((item) => !flowOwnedRunIds.value.has(item.run_id)));
const bossItems = computed(() => roundItems.value.filter((item) => item.platform === "boss"));
const zhilianItems = computed(() => roundItems.value.filter((item) => item.platform === "zhilian"));
const platformCounts = computed(() => ({
  aggregate: aggregateFlowItems.value.length,
  boss: bossItems.value.length + singlePlatformFlowItems.value.filter((flow) => flow.selection === "boss").length,
  zhilian: zhilianItems.value.length + singlePlatformFlowItems.value.filter((flow) => flow.selection === "zhilian").length,
}));
const roundItemsById = computed(() => new Map(props.items.map((item) => [item.run_id, item])));

function flowTrackHistoryItem(track: FlowHistoryTrack): HistoryRoundItem | undefined {
  return roundItemsById.value.get(flowTrackRunId(track));
}

function flowTrackRunId(track: FlowHistoryTrack): string {
  // Only a persisted result snapshot can answer the history-detail request.
  // Task IDs are useful for status/log presentation, but opening them as
  // round detail would fabricate a result for failed or partial tracks.
  return String(track.result_run_id || "");
}

function flowTrackDeleteId(track: FlowHistoryTrack): string {
  return flowTrackRunId(track) || String(track.id || "");
}

function historyDeleteBlocked(status: unknown): boolean {
  return ["queued", "running", "paused"].includes(String(status || ""));
}

/**
 * 047 US6：服务端权威删除资格经响应原样带到这一层。两个历史形状都带
 * 索引签名/未知厂商字段，按字段读取，不要求后端或共享类型先行升级；
 * 字段缺席（旧响应）才回退既有的「非活动即允许」显示，真正是否删除
 * 始终由 DELETE 重判。
 */
function authoritativeBool(source: unknown, field: string): boolean | undefined {
  const value = (source as Record<string, unknown> | null | undefined)?.[field];
  return typeof value === "boolean" ? value : undefined;
}

function authoritativeText(source: unknown, field: string): string {
  const value = (source as Record<string, unknown> | null | undefined)?.[field];
  return typeof value === "string" ? value : "";
}

function flowTrackDeleteAllowed(track: FlowHistoryTrack): boolean {
  const authoritative = authoritativeBool(track, "can_delete");
  if (authoritative !== undefined) return authoritative;
  return !historyDeleteBlocked(track.status);
}

function flowTrackDeleteTitle(track: FlowHistoryTrack): string {
  if (flowTrackDeleteAllowed(track)) return "删除该轮次";
  return authoritativeText(track, "delete_block_reason") || "请先结束或取消流程，再删除历史轮次";
}

function roundDeleteAllowed(item: HistoryRoundItem): boolean {
  const authoritative = authoritativeBool(item, "can_delete");
  if (authoritative !== undefined) return authoritative;
  return !historyDeleteBlocked(item.status);
}

function roundDeleteTitle(item: HistoryRoundItem): string {
  if (roundDeleteAllowed(item)) return "删除该轮次";
  return authoritativeText(item, "delete_block_reason") || "请先结束或取消流程，再删除历史轮次";
}

function flowTrackJobCount(track: FlowHistoryTrack): number {
  const jobs = Array.isArray(track.jobs) ? track.jobs.length : 0;
  const dropped = Array.isArray(track.dropped) ? track.dropped.length : 0;
  return jobs + dropped;
}

const NO_RESULT_LABEL = "无结果";
const UNSCREENED_LABEL = "已抓取，未筛选";
const UNKNOWN_STATUS_LABEL = "状态未知";
// 只有这三种结论在声称「这一轮有结果」，结果轮被删后必须降级。
const RESULT_BEARING_LABELS = new Set(["完成", "部分结果"]);
// 树干 historyStatusLabel 之外的流程态：抽屉用到的状态在这里补齐中文，
// 缺项一律给中性可读文案，绝不把后端枚举原样吐给用户。
const FLOW_EXTRA_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  paused: "已暂停",
  interrupted: "已中断",
  failed: "失败",
  stopped: "已停止",
  cancelled: "已停止",
  empty: NO_RESULT_LABEL,
  unknown: UNKNOWN_STATUS_LABEL,
};

function flowStatusLabel(status: unknown): string {
  const key = String(status || "").trim().toLowerCase();
  // 完成 / 部分结果 / 已抓取，未筛选三种结论口径仍由树干那一份决定。
  const trunkLabel = historyStatusLabel(key, 0);
  if (trunkLabel) return trunkLabel;
  return FLOW_EXTRA_STATUS_LABELS[key] || UNKNOWN_STATUS_LABEL;
}

// 结果轮被删（flow_tracks.result_run_id 被外键清空）之后不能再谎称「完成」：
// 岗位也没了就是无结果，只剩抓取台账就如实说已抓取、未筛选。
function flowResultBearingStatus(status: unknown, hasResultRound: boolean, jobCount: number): string {
  const label = flowStatusLabel(status);
  if (!RESULT_BEARING_LABELS.has(label) || hasResultRound) return label;
  return jobCount ? UNSCREENED_LABEL : NO_RESULT_LABEL;
}

// Flow 内层平台块与平铺轮次行共用同一套删除/日志事件：载荷仍是这一轮的
// 轮次身份（run id + 抓取任务 id），不新增第二套历史动作入口。
function flowRoundItem(track: FlowHistoryTrack): HistoryRoundItem {
  const historyItem = flowTrackHistoryItem(track);
  if (historyItem) return historyItem;
  const jobs = Array.isArray(track.jobs) ? track.jobs.length : 0;
  const dropped = Array.isArray(track.dropped) ? track.dropped.length : 0;
  return {
    run_id: flowTrackDeleteId(track),
    platform: track.platform,
    status: String(track.status || ""),
    scrape_task_id: String(track.scrape_run_id || ""),
    created_at: "",
    started_at: null,
    finished_at: null,
    total_scraped: jobs + dropped,
    total_kept: jobs,
    total_matched: 0,
    mismatch_count: 0,
    total_dropped: dropped,
    pending_count: 0,
    keyword_summary: "",
    profile_summary_preview: "",
    archived_at: null,
    is_latest: false,
  };
}

function flowTrackStatus(track: FlowHistoryTrack): string {
  return flowResultBearingStatus(track.status, Boolean(flowTrackRunId(track)), flowTrackJobCount(track));
}

// 卡片外层状态看的是「这个流程还有没有结果」：任一轨道还有结果轮就按流程态
// 说话，全被删光就如实降级，不再另立第二套状态口径。
function flowCardStatus(flow: FlowHistoryItem): string {
  const tracks = Array.isArray(flow.tracks) ? flow.tracks : [];
  return flowResultBearingStatus(
    flow.status,
    tracks.some((track) => Boolean(flowTrackRunId(track))),
    tracks.reduce((count, track) => count + flowTrackJobCount(track), 0),
  );
}

const focusableSelector = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
    return;
  }
  if (event.key !== "Tab" || !panelEl.value) return;
  const candidates = Array.from(panelEl.value.querySelectorAll<HTMLElement>(focusableSelector));
  if (!candidates.length) {
    event.preventDefault();
    panelEl.value.focus();
    return;
  }
  const first = candidates[0];
  const last = candidates[candidates.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

watch(() => props.open, (open) => {
  if (open) {
    previousFocus = document.activeElement as HTMLElement | null;
    nextTick(() => {
      if (!props.open) return;
      observeHeader();
      closeEl.value?.focus();
    });
  } else {
    releaseHeaderObserver();
    previousFocus?.focus();
  }
}, { immediate: true });

onBeforeUnmount(() => {
  releaseHeaderObserver();
  if (props.open) previousFocus?.focus();
});

const countParts = (item: HistoryRoundItem) => [
  { label: "匹配", value: item.total_matched, tone: "match" },
  { label: "不匹配", value: item.mismatch_count, tone: "mismatch" },
  { label: "待确认", value: item.pending_count, tone: "unsure" },
  { label: "剔除", value: item.total_dropped, tone: "reject" },
] as const;

// 整行可开（点击已存在），键盘必须等价可达：Tab 落到行、Enter/Space 打开该轮。
// 行内有日志/删除两个按钮，所以不能换成 button 套 button；改为只认行本身的
// 按键（event.target 必须是行自己），行内按钮的 Enter/Space 仍归它们自己。
function onRoundRowKeydown(event: KeyboardEvent, item: HistoryRoundItem): void {
  if (event.target !== event.currentTarget) return;
  if (event.key !== "Enter" && event.key !== " ") return;
  if (props.deleteTarget?.run_id === item.run_id) return;
  event.preventDefault();
  emit("open-round", item.run_id);
}
</script>

<template>
  <Transition name="drawer">
    <div
      v-if="open"
      class="history-drawer-backdrop"
      data-testid="history-drawer"
      @mousedown.self="emit('close')"
    >
      <aside
        ref="panelEl"
        class="history-drawer"
        :style="{ '--history-header-bottom': headerBottom }"
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-drawer-title"
        tabindex="-1"
        @keydown="handleKeydown"
      >
        <header class="history-drawer-header">
          <div class="history-drawer-heading">
            <h2 id="history-drawer-title" tabindex="-1">历史轮次</h2>
            <p class="history-drawer-total">共 {{ platformCounts[activePlatform] }} {{ activePlatform === 'aggregate' ? '个流程' : '轮' }}</p>
          </div>
          <button
            ref="closeEl"
            class="icon-button"
            type="button"
            aria-label="关闭历史轮次抽屉"
            data-testid="history-close"
            @click="emit('close')"
          >
            <X :size="20" aria-hidden="true" />
          </button>
        </header>

        <div class="history-drawer-navigation">
          <div class="history-platform-tabs" role="tablist" aria-label="按流程与平台查看历史轮次">
            <button
              v-for="platform in (['aggregate', 'boss', 'zhilian'] as const)"
              :key="platform"
              type="button"
              role="tab"
              :aria-selected="activePlatform === platform"
              :class="['history-platform-tab', { active: activePlatform === platform }]"
              :data-testid="`history-platform-tab-${platform}`"
              @click="activePlatform = platform"
            >
              <span>{{ platform === 'aggregate' ? '聚合' : platformLabel(platform) }}</span>
              <span class="history-platform-count">{{ platformCounts[platform] }}</span>
            </button>
          </div>
        </div>

        <div class="history-drawer-body">
          <div v-if="loading" class="history-drawer-state" data-testid="history-loading">正在加载历史轮次…</div>
          <div v-else-if="error" class="history-drawer-state history-drawer-error" role="alert" data-testid="history-error">
            <p>{{ error }}</p>
            <button class="button secondary" type="button" @click="emit('close')">关闭</button>
          </div>
          <div v-else-if="!platformCounts[activePlatform]" class="history-drawer-state" data-testid="history-empty">
            {{ activePlatform === 'aggregate' ? '暂无聚合流程' : `暂无${platformLabel(activePlatform)}历史轮次` }}
          </div>
          <template v-else>
            <h3 v-if="activePlatform !== 'aggregate'" class="history-platform-title">{{ platformLabel(activePlatform) }}</h3>
            <template v-if="flowCardItems.length">
              <article
                v-for="flow in flowCardItems"
                :key="flow.flow_id"
                :class="{ 'history-flow-card': activePlatform === 'aggregate' }"
                :data-testid="activePlatform === 'aggregate' ? 'history-flow-card' : undefined"
                :data-flow-id="flow.flow_id"
              >
                <header v-if="activePlatform === 'aggregate'" class="history-flow-head">
                  <strong>流程 · {{ flow.selection === 'all' ? '全部' : platformLabel(flow.selection) }}</strong>
                  <span class="history-flow-time">{{ formatHistoryTime(flow.updated_at || flow.created_at) || "时间未知" }}</span>
                  <span class="history-round-status">{{ flowCardStatus(flow) }}</span>
                </header>
                <div
                  v-for="track in flow.tracks"
                  :key="`${flow.flow_id}-${track.platform}`"
                  :class="{ 'history-flow-track': activePlatform === 'aggregate', 'history-single-round': activePlatform !== 'aggregate' }"
                  data-testid="history-flow-track"
                  :data-platform="track.platform"
                >
                  <Transition name="delete-confirm">
                    <div
                      v-if="deleteTarget && deleteTarget.run_id === flowTrackDeleteId(track)"
                      class="history-delete-confirm"
                      data-testid="history-delete-confirm"
                      @click.stop
                    >
                      <span class="history-delete-title">确认删除</span>
                      <span class="history-delete-actions">
                        <button
                          class="icon-button history-delete-action history-delete-yes"
                          type="button"
                          :disabled="deleting"
                          aria-label="确认删除该轮次"
                          data-testid="history-delete-confirm-yes"
                          @click.stop="emit('delete-round', flowRoundItem(track))"
                        >
                          <LoaderCircle v-if="deleting" class="spin" :size="18" aria-hidden="true" />
                          <Check v-else :size="18" aria-hidden="true" />
                        </button>
                        <button
                          class="icon-button history-delete-action history-delete-no"
                          type="button"
                          aria-label="取消删除"
                          data-testid="history-delete-confirm-no"
                          @click.stop="emit('cancel-delete')"
                        >
                          <X :size="18" aria-hidden="true" />
                        </button>
                      </span>
                    </div>
                    <div
                      v-else
                      :class="['history-round-row', { 'history-flow-track-line': activePlatform === 'aggregate', 'history-flow-track-line--static': !flowTrackRunId(track) }]"
                      :data-testid="activePlatform !== 'aggregate' ? 'history-round-row' : undefined"
                      :data-run-id="flowTrackRunId(track) || undefined"
                      :role="flowTrackRunId(track) ? 'button' : undefined"
                      :tabindex="flowTrackRunId(track) ? 0 : undefined"
                      @click="flowTrackRunId(track) && emit('open-round', flowTrackRunId(track))"
                      @keydown="flowTrackRunId(track) && onRoundRowKeydown($event, flowRoundItem(track))"
                    >
                      <span v-if="activePlatform === 'aggregate'" class="history-round-head history-flow-platform">{{ platformLabel(track.platform) }}</span>
                      <span v-else class="history-round-head">
                        <span class="history-round-time">{{ formatHistoryTime(flowTrackHistoryItem(track)?.finished_at || flowTrackHistoryItem(track)?.created_at || flow.updated_at || flow.created_at) || "时间未知" }}</span>
                        <span v-if="flowTrackHistoryItem(track)?.is_latest" class="history-latest-badge" data-testid="history-latest-badge">最新</span>
                      </span>
                      <span class="history-round-status" data-testid="history-flow-track-status">{{ flowTrackStatus(track) }}</span>
                      <span class="history-round-total" data-testid="history-round-total">
                        共 {{ flowTrackHistoryItem(track)?.total_scraped ?? flowTrackJobCount(track) }} 个岗位
                      </span>
                      <span v-if="flowTrackHistoryItem(track)" class="history-round-meta" data-testid="history-round-meta">
                        <span v-for="part in countParts(flowRoundItem(track))" :key="part.label" class="history-metric" :data-tone="part.tone">
                          <span class="history-metric-dot" aria-hidden="true"></span>
                          <span>{{ part.label }} {{ part.value }}</span>
                        </span>
                      </span>
                      <span v-if="flowTrackHistoryItem(track)?.keyword_summary" class="history-round-keyword">
                        {{ flowTrackHistoryItem(track)?.keyword_summary }}
                      </span>
                      <span v-if="track.message" class="history-flow-track-message">{{ track.message }}</span>
                      <!-- 详情认结果轮，日志认抓取任务，删除认持久轮次或轨道身份。 -->
                      <span
                        v-if="track.scrape_run_id || flowTrackDeleteId(track)"
                        :class="['history-row-actions', { 'history-flow-track-actions': activePlatform === 'aggregate' }]"
                        @click.stop
                      >
                        <button
                          v-if="track.scrape_run_id"
                          class="icon-button history-log"
                          type="button"
                          :aria-label="`查看 ${platformLabel(track.platform)} 该轮运行日志`"
                          data-testid="history-log-trigger"
                          @click="emit('view-log', flowRoundItem(track))"
                        >
                          <ScrollText :size="16" aria-hidden="true" />
                        </button>
                        <button
                          v-if="flowTrackDeleteId(track)"
                          class="icon-button history-delete"
                          type="button"
                          :disabled="deleting || !flowTrackDeleteAllowed(track)"
                          :title="flowTrackDeleteTitle(track)"
                          :aria-label="`删除 ${platformLabel(track.platform)} 该轮次`"
                          data-testid="history-delete-trigger"
                          @click="emit('confirm-delete', flowRoundItem(track))"
                        >
                          <Trash2 :size="16" aria-hidden="true" />
                        </button>
                      </span>
                    </div>
                  </Transition>
                </div>
              </article>
            </template>

            <template v-if="activePlatform !== 'aggregate' && roundItems.length">
              <section
                v-for="platform in (['boss', 'zhilian'] as const)"
                :key="platform"
                v-show="activePlatform === platform"
                class="history-platform-group"
                :data-platform="platform"
                :aria-hidden="activePlatform !== platform"
              >
                <div
                  v-for="item in platform === 'boss' ? bossItems : zhilianItems"
                  :key="item.run_id"
                  :class="['history-round-row', { 'history-round-row--confirming': deleteTarget?.run_id === item.run_id }]"
                  data-testid="history-round-row"
                  :data-run-id="item.run_id"
                  role="button"
                  tabindex="0"
                  @click="emit('open-round', item.run_id)"
                  @keydown="onRoundRowKeydown($event, item)"
                >
                  <span class="history-round-head">
                    <span class="history-round-time">{{ formatHistoryTime(item.finished_at || item.created_at) || "时间未知" }}</span>
                    <span v-if="item.is_latest" class="history-latest-badge" data-testid="history-latest-badge">最新</span>
                  </span>
                  <span class="history-round-status" :data-status="item.status">
                    {{ historyStatusLabel(item.status, item.total_kept) }}
                  </span>
                  <span class="history-round-total" data-testid="history-round-total">
                    共 {{ item.total_scraped }} 个岗位
                  </span>
                  <span class="history-round-meta" data-testid="history-round-meta">
                    <template v-for="part in countParts(item)" :key="part.label">
                      <span class="history-metric" :data-tone="part.tone">
                        <span class="history-metric-dot" aria-hidden="true"></span>
                        <span>{{ part.label }} {{ part.value }}</span>
                      </span>
                    </template>
                  </span>
                  <span class="history-round-keyword">{{ item.keyword_summary || "未记录关键词" }}</span>
                  <Transition name="delete-confirm">
                  <span
                    v-if="deleteTarget?.run_id === item.run_id"
                    class="history-delete-confirm"
                    data-testid="history-delete-confirm"
                    @click.stop
                  >
                    <span class="history-delete-title">确认删除</span>
                    <span class="history-delete-actions">
                      <button
                        class="icon-button history-delete-action history-delete-yes"
                        type="button"
                        :disabled="deleting"
                        aria-label="确认删除该轮次"
                        data-testid="history-delete-confirm-yes"
                        @click.stop="emit('delete-round', item)"
                      >
                        <LoaderCircle v-if="deleting" class="spin" :size="18" aria-hidden="true" />
                        <Check v-else :size="18" aria-hidden="true" />
                      </button>
                      <button
                        class="icon-button history-delete-action history-delete-no"
                        type="button"
                        aria-label="取消删除"
                        data-testid="history-delete-confirm-no"
                        @click.stop="emit('cancel-delete')"
                      >
                        <X :size="18" aria-hidden="true" />
                      </button>
                    </span>
                  </span>
                  <span
                    v-else
                    class="history-row-actions"
                    @click.stop
                  >
                    <button
                      v-if="item.scrape_task_id"
                      class="icon-button history-log"
                      type="button"
                      :aria-label="`查看 ${formatHistoryTime(item.finished_at || item.created_at) || '该轮次'} 运行日志`"
                      data-testid="history-log-trigger"
                      @click="emit('view-log', item)"
                    >
                      <ScrollText :size="16" aria-hidden="true" />
                    </button>
                    <button
                      class="icon-button history-delete"
                      type="button"
                      :disabled="deleting || !roundDeleteAllowed(item)"
                      :title="roundDeleteTitle(item)"
                      :aria-label="`删除 ${formatHistoryTime(item.finished_at || item.created_at) || '该轮次'}`"
                      data-testid="history-delete-trigger"
                      @click="emit('confirm-delete', item)"
                    >
                      <Trash2 :size="16" aria-hidden="true" />
                    </button>
                  </span>
                  </Transition>
                </div>
                <p v-if="!platformCounts[platform]" class="history-platform-empty">
                  暂无{{ platformLabel(platform) }}历史轮次
                </p>
              </section>
            </template>
          </template>
        </div>
      </aside>
    </div>
  </Transition>
</template>

<style scoped>
.history-drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: transparent;
}

.history-drawer {
  position: fixed;
  top: max(calc(80px + var(--titlebar-offset)), var(--history-header-bottom, 0px));
  right: 16px;
  bottom: 16px;
  z-index: 61;
  display: flex;
  flex-direction: column;
  width: min(380px, calc(100vw - 32px));
  overflow-x: hidden;
  border: 1px solid var(--hair);
  border-radius: 13px;
  background: var(--panel);
  box-shadow: var(--shadow);
}

.history-drawer-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 16px 18px 12px;
  border-bottom: 1px solid var(--hair-2);
}

.history-drawer-heading h2 {
  margin: 0;
  font-size: 1.05rem;
}

.history-drawer-total {
  margin: 2px 0 0;
  color: var(--text-soft);
  font-size: 0.85rem;
}


.history-drawer-body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 8px 16px 16px;
  overflow-y: auto;
  overflow-x: hidden;
}

.history-drawer-navigation {
  flex: 0 0 auto;
  padding: 12px 16px 0;
}

.history-drawer-state {
  padding: 32px 8px;
  color: var(--text-soft);
  text-align: center;
}

.history-drawer-error {
  color: var(--danger);
}

.history-platform-tabs {
  display: flex;
  align-items: center;
  align-self: flex-start;
  gap: 2px;
  margin: 0;
  padding: 2px;
  border: 1px solid var(--hair);
  border-radius: 8px;
  background: var(--panel-2);
}

.history-platform-tab {
  appearance: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  min-width: 0;
  gap: 6px;
  min-height: 34px;
  padding: 4px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ink-3);
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.history-platform-tab.active {
  background: var(--brand-wash);
  color: var(--brand-ink);
}

.history-platform-tab:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}

.history-platform-count {
  display: inline-grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--hair);
  color: var(--ink-2);
  font-size: 0.72rem;
  font-weight: 700;
}

.history-platform-tab.active .history-platform-count {
  background: var(--brand);
  color: var(--panel);
}

.history-platform-group {
  margin: 0;
}

.history-platform-title {
  margin: 0 0 6px;
  padding: 0 4px;
  color: var(--text-soft);
  font-size: 0.82rem;
  letter-spacing: 0;
}

.history-platform-empty {
  margin: 8px 4px 0;
  padding: 18px 8px;
  border: 1px dashed var(--hair);
  border-radius: 8px;
  color: var(--text-soft);
  font-size: 0.85rem;
  text-align: center;
}

.history-flow-card {
  display: grid;
  gap: 0;
  margin: 0 0 10px;
  padding: 10px 12px 0;
  border: 1px solid var(--hair);
  border-radius: 8px;
  background: var(--panel);
}

.history-flow-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px 8px;
  padding-bottom: 10px;
  color: var(--text-soft);
  font-size: 0.84rem;
}

.history-flow-time {
  grid-column: 1;
}

.history-flow-head > .history-round-status {
  grid-column: 2;
  grid-row: 1 / span 2;
  color: var(--ink-1);
}

.history-flow-track {
  position: relative;
  min-width: 0;
  min-height: 92px;
  border-top: 1px solid var(--hair);
}

.history-flow-platform {
  color: var(--text-soft);
  font-size: 0.84rem;
  font-weight: 600;
}

/* 紧凑流程行保留独立定位，删除确认只覆盖当前轮次。 */
.history-single-round {
  position: relative;
  min-width: 0;
  min-height: 92px;
  margin-bottom: 8px;
}

.history-single-round > :deep(.history-round-row) {
  margin-bottom: 0;
}

.history-flow-track-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.history-flow-track-message {
  color: var(--text-soft);
  font-size: 0.78rem;
  line-height: 1.5;
}

.history-round-row {
  position: relative;
  display: grid;
  gap: 4px;
  width: 100%;
  min-height: 92px;
  margin: 0 0 8px;
  padding: 10px 42px 10px 12px;
  border: 1px solid var(--hair);
  border-radius: 8px;
  background: var(--panel);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.history-round-row:hover {
  border-color: var(--brand-edge);
}

.history-round-row.history-flow-track-line {
  margin: 0;
  padding: 10px 80px 12px 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.history-round-row.history-flow-track-line--static {
  cursor: default;
}

/* 行本身可用键盘打开，焦点落点必须看得见（与平台页签同一档焦点环）。 */
.history-round-row:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}

.history-round-row--confirming {
  cursor: default;
}

.history-round-head {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
}

.history-round-time {
  color: var(--text-soft);
  font-size: 0.84rem;
}

.history-latest-badge {
  padding: 1px 8px;
  border: 1px solid var(--brand-edge);
  border-radius: 999px;
  color: var(--brand);
  font-size: 0.75rem;
}

.history-round-status {
  font-weight: 600;
}

.history-round-total {
  color: var(--text-soft);
  font-size: 0.75rem;
  font-weight: 400;
}

.history-round-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px 8px;
  color: var(--text-soft);
  font-size: 0.85rem;
  font-weight: 500;
}

.history-metric {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.history-metric-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.history-metric[data-tone="match"] {
  color: var(--match-deep);
}

.history-metric[data-tone="mismatch"] {
  color: var(--unsure-deep);
}

.history-metric[data-tone="unsure"] {
  color: var(--unsure-deep);
}

.history-metric[data-tone="reject"] {
  color: var(--reject-deep);
}

.history-round-keyword {
  color: var(--text-soft);
  font-size: 0.85rem;
}

.history-row-actions {
  position: absolute;
  top: 10px;
  right: 8px;
}

.history-delete {
  min-width: 34px;
  min-height: 34px;
}

.history-delete-confirm {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--panel) 58%, transparent);
  -webkit-backdrop-filter: blur(7px) saturate(1.2);
  backdrop-filter: blur(7px) saturate(1.2);
}

.history-delete-title {
  color: var(--ink-1);
  font-size: 0.9rem;
  font-weight: 600;
}

.history-delete-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.history-delete-action {
  width: auto;
  height: auto;
  min-width: 40px;
  min-height: 40px;
  padding: 6px;
  border: 0;
  border-radius: 8px;
  color: var(--ink-3);
  background: transparent;
}

.history-delete-action:hover:not(:disabled) {
  color: var(--ink-1);
  background: transparent;
}

.history-delete-action:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--brand) 28%, transparent);
  outline-offset: 1px;
}

.history-delete-action:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.history-delete-yes {
  color: var(--reject-deep);
  background: transparent;
}

.history-delete-yes:hover:not(:disabled) {
  color: var(--reject);
  background: transparent;
}

.history-delete-no:hover:not(:disabled) {
  color: var(--ink-1);
  background: transparent;
}

.delete-confirm-enter-active,
.delete-confirm-leave-active {
  transition: opacity 0.2s ease;
}

.delete-confirm-enter-active {
  transition:
    opacity 0.22s ease,
    transform 0.22s ease,
    -webkit-backdrop-filter 0.28s ease,
    backdrop-filter 0.28s ease;
}

.delete-confirm-enter-from,
.delete-confirm-leave-to {
  opacity: 0;
  -webkit-backdrop-filter: blur(0) saturate(1);
  backdrop-filter: blur(0) saturate(1);
}

.delete-confirm-enter-from {
  transform: translateY(4px);
}

.delete-confirm-leave-active {
  transition:
    opacity 0.16s ease,
    -webkit-backdrop-filter 0.18s ease,
    backdrop-filter 0.18s ease;
}

.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.2s ease;
}

.drawer-enter-active .history-drawer,
.drawer-leave-active .history-drawer {
  transition: transform 0.2s ease;
}

.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}

.drawer-enter-from .history-drawer,
.drawer-leave-to .history-drawer {
  transform: translateX(28px);
}

.drawer-leave-active {
  pointer-events: none;
}

@media (max-width: 720px) {
  .history-drawer {
    top: max(calc(72px + var(--titlebar-offset)), var(--history-header-bottom, 0px));
    right: 16px;
    bottom: 8px;
    width: calc(100vw - 32px);
  }
}
</style>
