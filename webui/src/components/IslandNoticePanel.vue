<script setup lang="ts">
// ---------------------------------------------------------------------------
// 037 灵动岛通知面板：弹出在胶囊下方（绝对定位），按 kind 排序（error→paused
// →completed），未读左侧高亮，已读淡化。点击行直达目标（父处理导航）。
//
// 动画：Motion 驱动 scale + opacity 入场；退场由父两阶段驱动（leaving prop
// 切换 :animate 目标，淡出后父再卸载，避免 AnimatePresence 在 jsdom 卡 DOM）。
// 无障碍：role=dialog/tabindex 经 attrs 透传到渲染元素，父展开后移焦。
// ---------------------------------------------------------------------------
import { computed } from "vue";
import { Motion, useReducedMotion } from "motion-v";
import { AlertTriangle, CheckCircle2, Pause } from "@lucide/vue";
import type { DynamicIslandState } from "../composables/useDiscoveryState";
import type { IslandNotice } from "../composables/useIslandNotices";

const props = defineProps<{
  notices: IslandNotice[];
  capsule: DynamicIslandState;
  leaving?: boolean;
}>();

const emit = defineEmits<{
  "row-click": [notice: IslandNotice];
}>();

const KIND_ORDER: Record<string, number> = { error: 0, paused: 1, completed: 2 };

const reduced = useReducedMotion();
const animOn = computed(() => !reduced.value);
const spring = computed(() =>
  reduced.value ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 30 },
);

const orderedNotices = computed(() =>
  props.notices.slice().sort((a, b) => {
    const oa = KIND_ORDER[a.kind] ?? 99;
    const ob = KIND_ORDER[b.kind] ?? 99;
    return oa - ob;
  }),
);

function iconFor(kind: IslandNotice["kind"]) {
  if (kind === "error") return AlertTriangle;
  if (kind === "paused") return Pause;
  return CheckCircle2;
}

function onRowClick(notice: IslandNotice) {
  emit("row-click", notice);
}
</script>

<template>
  <Motion
    as="div"
    class="island-panel"
    :class="{ 'is-leaving': leaving }"
    data-testid="island-notice-panel"
    role="dialog"
    aria-modal="true"
    aria-label="灵动岛通知"
    tabindex="-1"
    :initial="animOn ? { opacity: 0, scale: 0.85, x: '-50%', y: 6 } : false"
    :animate="leaving ? { opacity: 0, scale: 0.96, x: '-50%', y: 6 } : { opacity: 1, scale: 1, x: '-50%', y: 0 }"
    :transition="spring"
  >
    <Motion
      as="button"
      type="button"
      class="notice-row"
      :class="[`notice-${notice.kind}`, { 'is-unread': !notice.read }]"
      :data-testid="`island-notice-row-${notice.kind}`"
      :data-notice-id="notice.id"
      :data-notice-read="notice.read ? 'true' : 'false'"
      :initial="animOn ? { opacity: 0, y: 4 } : false"
      :animate="{ opacity: notice.read ? 0.65 : 1, y: 0 }"
      :transition="spring"
      v-for="notice in orderedNotices"
      :key="notice.id"
      :while-hover="animOn ? { scale: 1.01 } : undefined"
      :while-press="animOn ? { scale: 0.99 } : undefined"
      @click="onRowClick(notice)"
      @keydown.enter.prevent="onRowClick(notice)"
      @keydown.space.prevent="onRowClick(notice)"
    >
      <span class="notice-icon" :class="`notice-${notice.kind}`" aria-hidden="true">
        <component :is="iconFor(notice.kind)" :size="14" />
      </span>
      <span class="notice-body">
        <span class="notice-title">{{ notice.title }}</span>
        <span v-if="notice.detail" class="notice-detail">{{ notice.detail }}</span>
      </span>
      <span class="notice-go" aria-hidden="true">›</span>
    </Motion>
    <p v-if="!orderedNotices.length" class="island-empty">暂无需要关注的事项</p>
  </Motion>
</template>

<style scoped>
.island-panel {
  pointer-events: auto;
  position: absolute;
  top: calc(100% + 10px);
  left: 50%;
  z-index: 75;
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: min(380px, calc(100vw - 32px));
  max-height: min(420px, calc(100vh - 120px));
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--hair);
  border-radius: 13px;
  background: var(--panel);
  box-shadow: var(--shadow);
  transform-origin: center top;
  outline: none;
}

.notice-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--hair-2);
  border-radius: 10px;
  background: var(--panel-2, var(--panel));
  color: var(--ink-1);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s ease, background-color 0.18s ease;
  transform-origin: center center;
  will-change: transform;
}
.notice-row:hover {
  border-color: var(--brand);
  background: var(--brand-wash);
}
.notice-row:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.notice-row.is-unread {
  border-color: var(--brand);
  background: var(--brand-wash);
}

.notice-icon {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  color: #fff;
}
.notice-icon.notice-error {
  background: var(--reject, #a85751);
}
.notice-icon.notice-paused {
  background: #e5a13a;
}
.notice-icon.notice-completed {
  background: var(--match, #12905f);
}

.notice-body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.notice-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-1);
  overflow-wrap: anywhere;
}
.notice-detail {
  font-size: 12px;
  color: var(--text-soft, #9fb0c3);
  overflow-wrap: anywhere;
}

.notice-go {
  flex: 0 0 auto;
  color: var(--text-soft, #9fb0c3);
  font-size: 18px;
  line-height: 1;
  margin-left: 4px;
}

.island-empty {
  margin: 0;
  padding: 16px 8px;
  text-align: center;
  color: var(--text-soft, #9fb0c3);
  font-size: 13px;
}

:global([data-theme="kaleido"]) .island-panel {
  background: rgba(20, 18, 30, 0.72);
  border-color: rgba(255, 255, 255, 0.22);
  backdrop-filter: blur(12px);
}
:global([data-theme="kaleido"]) .notice-row {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.18);
}
:global([data-theme="kaleido"]) .notice-title {
  color: rgba(255, 255, 255, 0.92);
}
:global([data-theme="kaleido"]) .notice-detail {
  color: rgba(255, 255, 255, 0.65);
}
:global([data-theme="kaleido"]) .notice-row:hover,
:global([data-theme="kaleido"]) .notice-row.is-unread {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.35);
}

@media (max-width: 760px) {
  /* B1：锚点（胶囊）在窄屏 flex-wrap 顶栏里偏左，absolute 锚定必然溢出视口；
     改 fixed 按视口居中，top 由父组件实测胶囊底缘经 --panel-top 传入。 */
  .island-panel {
    position: fixed;
    top: var(--panel-top, 76px);
    left: 50%;
    width: calc(100vw - 32px);
  }
}
</style>