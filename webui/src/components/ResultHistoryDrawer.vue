<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { Trash2, X } from "@lucide/vue";
import { formatHistoryTime, type HistoryRoundItem } from "../composables/resultHistory";
import { historyStatusLabel } from "../discovery";

const props = defineProps<{
  open: boolean;
  items: HistoryRoundItem[];
  loading: boolean;
  error: string;
  deleting: boolean;
  deleteTarget: HistoryRoundItem | null;
}>();

const emit = defineEmits<{
  close: [];
  "open-round": [runId: string];
  "confirm-delete": [item: HistoryRoundItem];
  "cancel-delete": [];
  "delete-round": [item: HistoryRoundItem];
}>();

const closeEl = ref<HTMLButtonElement | null>(null);
const panelEl = ref<HTMLElement | null>(null);
let previousFocus: HTMLElement | null = null;

const activePlatform = ref<"boss" | "zhilian">("boss");
const bossItems = computed(() => props.items.filter((item) => item.platform === "boss"));
const zhilianItems = computed(() => props.items.filter((item) => item.platform === "zhilian"));
const platformCounts = computed(() => ({
  boss: bossItems.value.length,
  zhilian: zhilianItems.value.length,
}));

function platformLabel(platform: "boss" | "zhilian"): string {
  return platform === "boss" ? "BOSS" : "智联";
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
    nextTick(() => closeEl.value?.focus());
  } else {
    previousFocus?.focus();
  }
}, { immediate: true });

watch(() => props.items, () => {
  const hasBoss = bossItems.value.length > 0;
  const hasZhilian = zhilianItems.value.length > 0;
  const currentHasItems = activePlatform.value === "boss" ? hasBoss : hasZhilian;
  if (!currentHasItems && (hasBoss || hasZhilian)) {
    activePlatform.value = hasBoss ? "boss" : "zhilian";
  }
}, { immediate: true });

onBeforeUnmount(() => {
  if (props.open) previousFocus?.focus();
});

const countParts = (item: HistoryRoundItem) => [
  { label: "匹配", value: item.total_matched, tone: "match" },
  { label: "待确认", value: item.pending_count, tone: "unsure" },
  { label: "剔除", value: item.total_dropped, tone: "reject" },
] as const;
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
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-drawer-title"
        tabindex="-1"
        @keydown="handleKeydown"
      >
        <header class="history-drawer-header">
          <div class="history-drawer-heading">
            <h2 id="history-drawer-title" tabindex="-1">历史轮次</h2>
            <p v-if="items.length" class="history-drawer-total">共 {{ items.length }} 轮</p>
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

        <div class="history-drawer-body">
          <div v-if="loading" class="history-drawer-state" data-testid="history-loading">正在加载历史轮次…</div>
          <div v-else-if="error" class="history-drawer-state history-drawer-error" role="alert" data-testid="history-error">
            <p>{{ error }}</p>
            <button class="button secondary" type="button" @click="emit('close')">关闭</button>
          </div>
          <div v-else-if="!items.length" class="history-drawer-state" data-testid="history-empty">
            暂无历史轮次
          </div>
          <template v-else>
            <div
              class="history-platform-tabs"
              role="tablist"
              aria-label="按平台查看历史轮次"
            >
              <button
                v-for="platform in (['boss', 'zhilian'] as const)"
                :key="platform"
                type="button"
                role="tab"
                :aria-selected="activePlatform === platform"
                :class="['history-platform-tab', { active: activePlatform === platform }]"
                :data-testid="`history-platform-tab-${platform}`"
                @click="activePlatform = platform"
              >
                <span>{{ platformLabel(platform) }}</span>
                <span class="history-platform-count">{{ platformCounts[platform] }}</span>
              </button>
            </div>

            <section
              v-for="platform in (['boss', 'zhilian'] as const)"
              :key="platform"
              v-show="activePlatform === platform"
              class="history-platform-group"
              :data-platform="platform"
              :aria-hidden="activePlatform !== platform"
            >
              <h3 class="history-platform-title">{{ platformLabel(platform) }}</h3>
              <div
                v-for="item in platform === 'boss' ? bossItems : zhilianItems"
                :key="item.run_id"
                class="history-round-row"
                data-testid="history-round-row"
                :data-run-id="item.run_id"
                @click="emit('open-round', item.run_id)"
              >
                <span class="history-round-head">
                  <span class="history-round-time">{{ formatHistoryTime(item.created_at) || "时间未知" }}</span>
                  <span v-if="item.is_latest" class="history-latest-badge" data-testid="history-latest-badge">最新</span>
                </span>
                <span class="history-round-status" :data-status="item.status">
                  {{ historyStatusLabel(item.status, item.total_kept) }}
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
                <span
                  v-if="deleteTarget?.run_id === item.run_id"
                  class="history-delete-confirm"
                  data-testid="history-delete-confirm"
                  @click.stop
                >
                  <span>删除后保留任务日志，确定删除？</span>
                  <span class="history-delete-actions">
                    <button
                      class="button danger small"
                      type="button"
                      :disabled="deleting"
                      data-testid="history-delete-confirm-yes"
                      @click.stop="emit('delete-round', item)"
                    >删除</button>
                    <button
                      class="button secondary small"
                      type="button"
                      data-testid="history-delete-confirm-no"
                      @click.stop="emit('cancel-delete')"
                    >取消</button>
                  </span>
                </span>
                <span
                  v-else
                  class="history-row-actions"
                  @click.stop
                >
                  <button
                    class="icon-button history-delete"
                    type="button"
                    :aria-label="`删除 ${formatHistoryTime(item.created_at) || '该轮次'}`"
                    data-testid="history-delete-trigger"
                    @click="emit('confirm-delete', item)"
                  >
                    <Trash2 :size="16" aria-hidden="true" />
                  </button>
                </span>
              </div>
              <p v-if="!platformCounts[platform]" class="history-platform-empty">
                暂无{{ platformLabel(platform) }}历史轮次
              </p>
            </section>
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
  top: 80px;
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

.history-drawer-state {
  padding: 32px 8px;
  color: var(--text-soft);
  text-align: center;
}

.history-drawer-error {
  color: var(--danger);
}

.history-platform-tabs {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  gap: 2px;
  margin: 0 0 10px;
  padding: 2px;
  border: 1px solid var(--hair);
  border-radius: 8px;
  background: var(--panel-2);
}

.history-platform-tab {
  appearance: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 4px 14px;
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
  overflow-wrap: anywhere;
}

.history-round-row:hover {
  border-color: var(--brand-edge);
}

.history-round-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding: 8px;
  border: 1px solid var(--reject-edge);
  border-radius: 8px;
  background: var(--reject-wash);
  font-size: 0.85rem;
}

.history-delete-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
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
    top: 72px;
    right: 16px;
    bottom: 8px;
    width: calc(100vw - 32px);
  }
}
</style>
