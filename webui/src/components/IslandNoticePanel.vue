<script setup lang="ts">
// ---------------------------------------------------------------------------
// 037 灵动岛通知面板：旧版骨架 + interrupt 行 + 当前两色口径。
//
// 037 变更：
// - KIND_ORDER 增 "interrupt"（位 2，paused 与 completed 之间）——打断类（投递
//   提醒/NoticeBar warning/error）属操作告警，比终态成功事件更需先看到。
// - iconFor：interrupt → Bell（@lucide/vue）。
// - row 挂 data-tone（warning/error）；icon + 行边框/底色按 tone 染色
//   （warning 琥珀 / error 红）。
// - 打断行点击直达 notice.target（interrupt 带 "reminders" 时开提醒抽屉，
//   App 层分流；终态通知沿用 task/results/attention）。
//   复审 P1-2 裁决：删除 counts 四色 chip 渲染——capsule 仅上抛 matched+pending
//   两元（useDiscoveryState 禁改），全链路无生产数据源，属死代码；待后续
//   capsule 扩展批次再随 spec 一起加回。
//
// 037 不变：
// - 排序（KIND_ORDER）/未读高亮（is-unread）/已读淡化（opacity 0.65）/行点击
//   emit row-click 直达目标/role=dialog 焦点管理。
// - 动画：Motion 驱动 scale+opacity 入场；退场由父两阶段驱动（leaving prop 切
//   :animate 目标，淡出后父再卸载，避免 AnimatePresence 在 jsdom 卡 DOM）。
// - 无障碍：role=dialog/tabindex 经 attrs 透传到渲染元素，父展开后移焦。
// ---------------------------------------------------------------------------
import { computed } from "vue";
import { Motion, useReducedMotion } from "motion-v";
import { AlertTriangle, Bell, CheckCircle2, Pause } from "@lucide/vue";
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

// 037：interrupt 在 paused 与 completed 之间——打断类（投递提醒/NoticeBar
// warning/error）属操作告警，比终态成功事件更需用户先看到，故排在 completed 前。
const KIND_ORDER: Record<string, number> = { error: 0, paused: 1, interrupt: 2, completed: 3 };

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
  if (kind === "interrupt") return Bell;
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
      :data-tone="notice.tone"
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
      <span class="notice-icon" :class="`notice-${notice.kind}`" :data-tone="notice.tone" aria-hidden="true">
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
/* 037：interrupt 图标按 tone 染色（warning 琥珀 / error 红）。无 tone 时退灰。 */
.notice-icon.notice-interrupt {
  background: #6b7280;
}
.notice-icon.notice-interrupt[data-tone="warning"] {
  background: #e5a13a;
}
.notice-icon.notice-interrupt[data-tone="error"] {
  background: var(--reject, #a85751);
}

/* 037：interrupt 行边框/底色按 tone 染色——比终态行更显眼，比 error 略柔。 */
.notice-row.notice-interrupt[data-tone="warning"] {
  border-color: #e5a13a;
  background: rgba(229, 161, 58, 0.10);
}
.notice-row.notice-interrupt[data-tone="warning"].is-unread {
  border-color: #e5a13a;
  background: rgba(229, 161, 58, 0.18);
}
.notice-row.notice-interrupt[data-tone="error"] {
  border-color: var(--reject, #a85751);
  background: rgba(168, 87, 81, 0.10);
}
.notice-row.notice-interrupt[data-tone="error"].is-unread {
  border-color: var(--reject, #a85751);
  background: rgba(168, 87, 81, 0.18);
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
/* 037 复审：interrupt 行 title 按 tone 染色（与 pill 内打断 lane 一致）——
   warning 琥珀 / error 红，醒目不撞 panel 背景。 */
.notice-row.notice-interrupt[data-tone="warning"] .notice-title { color: #e5a13a; }
.notice-row.notice-interrupt[data-tone="error"] .notice-title { color: #e5484d; }

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
  backdrop-filter: blur(6px);
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
/* 037：kaleido 下 interrupt tone 行在半透明毛玻璃上保持可见。 */
:global([data-theme="kaleido"]) .notice-row.notice-interrupt[data-tone="warning"] {
  background: rgba(229, 161, 58, 0.18);
  border-color: rgba(229, 161, 58, 0.45);
}
:global([data-theme="kaleido"]) .notice-row.notice-interrupt[data-tone="warning"].is-unread {
  background: rgba(229, 161, 58, 0.28);
  border-color: rgba(229, 161, 58, 0.60);
}
:global([data-theme="kaleido"]) .notice-row.notice-interrupt[data-tone="error"] {
  background: rgba(168, 87, 81, 0.18);
  border-color: rgba(168, 87, 81, 0.45);
}
:global([data-theme="kaleido"]) .notice-row.notice-interrupt[data-tone="error"].is-unread {
  background: rgba(168, 87, 81, 0.28);
  border-color: rgba(168, 87, 81, 0.60);
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
