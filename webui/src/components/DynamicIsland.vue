<script setup lang="ts">
// ---------------------------------------------------------------------------
// 037 顶栏灵动岛 v2：常驻活组件 + motion-v 驱动。
//
// 数据：消费 App 上抛的 CapsuleStatusPayload（036 链路）与 useIslandNotices
// 产生的通知池；不做数据抓取（036 FR-017 沿用）。
//
// 动画策略（037 复审后修订）：
// - 入场用 Motion 组件 + :initial/:animate（弹簧 spring）；
// - 通知到达弹跳改 Web Animations API（CSS attribute 递增不会重启动画，
//   0→1 后 1→2、2→3 全部静默——复审 P1）；reduce-motion 下跳过；
// - 新通知到达时 pill 短暂展示最新未读摘要（"一瞥"，2.2s 收回）；
// - idle 常驻呼吸（scale 1↔1.006，4s）兑现 spec US-1 的生命感；
// - 退场：手写两阶段（leaving 220ms 淡出再卸载），reduce-motion 下即时卸载，
//   兼顾 jsdom 测试确定性（不动 AnimatePresence）；
// - 系统"减少动态"时 useReducedMotion() 短路所有弹簧为 {duration:0}。
//
// 点击行为：
// - 有未读 → 展开面板（emit expand；已读在收起 dismiss 时统一标记，
//   dismiss 携带关闭瞬间的 id 快照——展开期间行以未读渲染，复审二 B2/N2）；
// - 全部已读/无通知 → 按 live capsule 状态 emit navigate（复审三 §13：
//   已读后回到直达语义，不永远弹历史列表）。
//
// 无障碍（复审 C2/§7）：
// - pill 无未读时不设 aria-label，让运行进度/结果数字作为可访问名；
// - 通知到达经 visually-hidden aria-live 播报；
// - 面板 role=dialog，展开时移焦入面板、收起时焦点归还 pill，Tab 轻量圈闭。
//
// 暴露：collapse() via defineExpose（App 打开抽屉/profile 切换时调用）。
// ---------------------------------------------------------------------------
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Motion, useReducedMotion } from "motion-v";
import type { CapsuleStatusPayload, DynamicIslandState } from "../composables/useDiscoveryState";
import type { IslandNotice } from "../composables/useIslandNotices";
import IslandNoticePanel from "./IslandNoticePanel.vue";

export type CapsuleTarget = "home" | "task" | "results" | "attention";

const props = defineProps<{
  status: CapsuleStatusPayload | null;
  notices: IslandNotice[];
}>();

const emit = defineEmits<{
  navigate: [target: CapsuleTarget];
  expand: [];
  // dismiss 携带关闭瞬间的通知 id 快照：App 只把快照内标记已读，
  // 关闭窗口期（leaving 220ms）新到达的通知保持未读，不被误吞（复审二 N2）。
  dismiss: [ids: string[]];
}>();

const open = ref(false);
const leaving = ref(false);
const peekText = ref("");
const liveText = ref("");

const pillRef = ref<HTMLElement | null>(null);
const anchorEl = ref<HTMLElement | null>(null);

let peekTimer: number | undefined;
let leaveTimer: number | undefined;
let popAnim: Animation | null = null;

const capsule = computed<DynamicIslandState>(
  () => props.status?.capsule ?? { state: "idle", platform: "boss" },
);

const platformLabel = computed(() =>
  capsule.value.platform === "zhilian" ? "智联" : "BOSS",
);

const reduced = useReducedMotion();
const animOn = computed(() => !reduced.value);
const spring = computed(() =>
  reduced.value ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 30 },
);
const hoverSpring = computed(() =>
  reduced.value ? { duration: 0 } : { type: "spring", stiffness: 460, damping: 18 },
);

const runningLabel = computed(() => {
  if (capsule.value.state !== "running") return "";
  const { progress } = capsule.value;
  const prefix = progress.phase === "scraping" ? "抓取" : "筛选";
  return progress.total === undefined
    ? `${prefix} ${progress.done}`
    : `${prefix} ${progress.done}/${progress.total}`;
});

const completedLabel = computed(() => {
  if (capsule.value.state !== "completed") return "";
  const { results } = capsule.value;
  if (props.status?.phase === "scraped") return `待筛选 ${results.matched}`;
  return results.pending > 0
    ? `匹配 ${results.matched} · 待确认 ${results.pending}`
    : `匹配 ${results.matched}`;
});

const stateTarget = computed<CapsuleTarget>(() => {
  switch (capsule.value.state) {
    case "running": return "task";
    case "completed": return "results";
    case "attention": return "attention";
    default: return "home";
  }
});

const unread = computed(() => props.notices.filter((n) => !n.read).length);

// ---- 通知到达反馈（复审二 N3）：按"新出现的未读 id"触发，而非未读计数 ----
// upsert 把"同 kind 内容更新"定义为新通知（未读数不变），只看计数会漏掉
// 未读→未读的更新（无 pop/peek/播报）。用 seenUnread 集合求差集识别新增。
let seenUnread = new Set<string>();
let boot = true;

function playPop() {
  const el = pillRef.value;
  if (!el || !animOn.value) return;
  popAnim?.cancel();
  popAnim = el.animate(
    [
      { transform: "scale(1)" },
      { transform: "scale(1.12)", offset: 0.35 },
      { transform: "scale(0.97)", offset: 0.65 },
      { transform: "scale(1)" },
    ],
    { duration: 380, easing: "cubic-bezier(0.34, 1.56, 0.64, 1)" },
  );
}

function showPeek(notice: IslandNotice) {
  peekText.value = notice.detail ? `${notice.title} · ${notice.detail}` : notice.title;
  if (peekTimer !== undefined) window.clearTimeout(peekTimer);
  peekTimer = window.setTimeout(() => {
    peekText.value = "";
    peekTimer = undefined;
  }, 2200);
}

function announce(notice: IslandNotice) {
  liveText.value = notice.detail ? `${notice.title}：${notice.detail}` : notice.title;
}

function clearPeek() {
  if (peekTimer !== undefined) {
    window.clearTimeout(peekTimer);
    peekTimer = undefined;
  }
  peekText.value = "";
}

watch(
  () => props.notices,
  (list) => {
    const current = new Set(list.filter((n) => !n.read).map((n) => n.id));
    // 首次观察只建基线（mount 已有未读 = 用户尚未有机会看到，不补反馈）。
    if (boot) {
      seenUnread = current;
      boot = false;
      return;
    }
    const fresh = [...current].filter((id) => !seenUnread.has(id));
    seenUnread = current;
    if (fresh.length === 0) return;
    const newest = list
      .filter((n) => fresh.includes(n.id))
      .sort((a, b) => b.at - a.at)[0];
    if (!newest) return;
    announce(newest);
    // 面板已展开或正在退场时直接看面板，不叠加弹跳/一瞥（红点照常出现）。
    if (open.value || leaving.value) return;
    playPop();
    showPeek(newest);
  },
  { immediate: true },
);

// ---- 开合与焦点管理 ----
let closingIds: string[] = [];

function openPanel() {
  if (open.value) return;
  leaving.value = false;
  open.value = true;
  emit("expand");
  // 点开即"已看"：清掉 pill 上还挂着的一瞥文案（复审三 §8）。
  clearPeek();
  // B1：窄屏面板改 fixed 视口居中，top 取胶囊实测底缘（锚点偏左时会溢出）。
  const rect = pillRef.value?.getBoundingClientRect();
  if (anchorEl.value) {
    const top = rect ? Math.min(rect.bottom + 8, window.innerHeight - 48) : 76;
    anchorEl.value.style.setProperty("--panel-top", `${Math.max(top, 8)}px`);
  }
  // 焦点移入面板（C2）：面板自身 role=dialog/tabindex=-1，首 Tab 从面板行开始。
  void nextTick(() => {
    anchorEl.value?.querySelector<HTMLElement>('[data-testid="island-notice-panel"]')?.focus({ preventScroll: true });
  });
}

function finishClose() {
  if (leaveTimer !== undefined) {
    window.clearTimeout(leaveTimer);
    leaveTimer = undefined;
  }
  leaving.value = false;
  if (!open.value) return;
  open.value = false;
  // 快照在 requestClose 时已定格；只有快照内通知被标已读。
  if (closingIds.length > 0) {
    emit("dismiss", closingIds);
    closingIds = [];
  }
  // 焦点归还仅当焦点仍在岛内（面板/行/胶囊）时执行（复审三 N1）：
  // App 主动收岛去开抽屉/菜单时，焦点已在别处，不能 220ms 后抢回。
  if (anchorEl.value?.contains(document.activeElement)) {
    pillRef.value?.focus({ preventScroll: true });
  }
}

function requestClose() {
  if (!open.value || leaving.value) return;
  closingIds = props.notices.map((n) => n.id); // 定格关闭瞬间的通知集合（N2）
  if (animOn.value) {
    leaving.value = true;
    // 两阶段退场：spring 收敛 ~200ms，150ms 时只差最后透明度，留 70ms 余量
    //（复审三 §10：220ms 内视觉基本跑完，reduce 态即时卸载）。
    leaveTimer = window.setTimeout(() => finishClose(), 220);
  } else {
    finishClose();
  }
}

function onPillClick() {
  // 有未读 → 面板（查看/直达）；全部已读 → 通知池已消费，回到直达语义
  //（复审三 §13：iOS 岛轻点 = 打开对应内容，不永远弹历史列表）。
  if (unread.value > 0) {
    if (open.value) {
      requestClose();
    } else {
      openPanel();
    }
    return;
  }
  emit("navigate", stateTarget.value);
}

function onRowClick(notice: IslandNotice) {
  emit("navigate", notice.target);
  requestClose();
}

function onBackdrop() {
  requestClose();
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && open.value) {
    event.preventDefault();
    requestClose();
    return;
  }
  // 轻量焦点圈闭（复审三 §7）：shift+Tab 从胶囊回末行、Tab 在末行回胶囊，
  // 让 aria-modal=true 名副其实——键盘与鼠标同处面板封锁级。
  if (event.key === "Tab" && open.value && !leaving.value) {
    const anchor = anchorEl.value;
    if (!anchor) return;
    const focusables = Array.from(
      anchor.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex]:not([tabindex="-1"])'),
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === pillRef.value)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }
}

function collapse() {
  requestClose();
}

defineExpose({ collapse });

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
  if (peekTimer !== undefined) window.clearTimeout(peekTimer);
  if (leaveTimer !== undefined) window.clearTimeout(leaveTimer);
  popAnim?.cancel();
});
</script>

<template>
  <div ref="anchorEl" class="island-anchor">
    <button
      ref="pillRef"
      type="button"
      class="island-pill"
      :class="[`is-${capsule.state}`, { 'has-unread': unread > 0, 'is-open': open }]"
      :data-testid="`dynamic-island-${capsule.state}`"
      :aria-expanded="open"
      :aria-label="unread > 0 ? `灵动岛，${unread} 条未读提醒` : undefined"
      @click="onPillClick"
    >
      <Motion
        as="span"
        class="island-pill-inner"
        :while-hover="animOn ? { scale: 1.03 } : undefined"
        :while-press="animOn ? { scale: 0.96 } : undefined"
        :transition="hoverSpring"
      >
        <!-- 通知到达一瞥：短暂展示最新未读摘要（B4），否则渲染胶囊态 -->
        <template v-if="peekText">
          <Motion
            :key="`peek-${peekText}`"
            :initial="animOn ? { y: 6, opacity: 0 } : false"
            :animate="{ y: 0, opacity: 1 }"
            :transition="spring"
            as="span"
            class="island-peek"
            data-testid="island-peek"
          >{{ peekText }}</Motion>
        </template>

        <!-- idle：低调常驻 + 4s 呼吸 -->
        <template v-else-if="capsule.state === 'idle'">
          <Motion
            :key="`idle-${capsule.platform}`"
            :initial="animOn ? { y: 6, opacity: 0, scale: 0.92 } : false"
            :animate="{ y: 0, opacity: 1, scale: 1 }"
            :transition="spring"
            as="span"
            class="island-idle-label"
            data-testid="island-idle"
          >{{ platformLabel }}</Motion>
        </template>

        <!-- running：实时进度 + 呼吸点 -->
        <template v-else-if="capsule.state === 'running'">
          <span class="island-live" aria-hidden="true"></span>
          <Motion
            :key="runningLabel"
            :initial="animOn ? { y: 8, opacity: 0 } : false"
            :animate="{ y: 0, opacity: 1 }"
            :transition="spring"
            as="span"
            class="island-value"
            data-testid="island-running-value"
          >{{ runningLabel }}</Motion>
        </template>

        <!-- completed：结果数字；待确认 >0 标亮 -->
        <template v-else-if="capsule.state === 'completed'">
          <Motion
            :key="completedLabel"
            :initial="animOn ? { y: 8, opacity: 0, scale: 0.9 } : false"
            :animate="{ y: 0, opacity: 1, scale: 1 }"
            :transition="spring"
            as="span"
            class="island-value"
            data-testid="island-completed-value"
          >{{ completedLabel }}</Motion>
          <span
            v-if="capsule.results.pending > 0"
            class="island-pending-dot"
            aria-hidden="true"
          ></span>
        </template>

        <!-- attention：提醒色 + 文案 -->
        <template v-else>
          <Motion
            :key="`att-${capsule.attention.kind}-${capsule.attention.message}`"
            :initial="animOn ? { y: 6, opacity: 0, scale: 0.92 } : false"
            :animate="{ y: 0, opacity: 1, scale: 1 }"
            :transition="spring"
            as="span"
            class="island-attention-row"
          >
            <span
              class="island-attention-mark"
              :class="`attention-${capsule.attention.kind}`"
              aria-hidden="true"
            ></span>
            <span class="island-value">{{ capsule.attention.message }}</span>
          </Motion>
        </template>

        <!-- 未读红点 -->
        <span
          v-if="unread > 0"
          class="island-unread"
          data-testid="island-unread"
          aria-hidden="true"
        >{{ unread > 99 ? "99+" : unread }}</span>
      </Motion>
    </button>

    <!-- 读屏播报（视觉隐藏，C2）：通知到达 announce() 更新 -->
    <span
      class="island-sr-live"
      role="status"
      aria-live="polite"
      data-testid="island-sr-live"
    >{{ liveText }}</span>

    <Motion
      v-if="open"
      as="div"
      class="island-backdrop"
      :class="{ 'is-leaving': leaving }"
      :initial="animOn ? { opacity: 0 } : false"
      :animate="{ opacity: 1 }"
      :transition="{ duration: animOn ? 0.18 : 0 }"
      data-testid="island-backdrop"
      @click="onBackdrop"
    />
    <IslandNoticePanel
      v-if="open"
      :notices="notices"
      :capsule="capsule"
      :leaving="leaving"
      @row-click="onRowClick"
    />
  </div>
</template>

<style scoped>
.island-anchor {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-self: center;
}

.island-pill {
  position: relative;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  height: 34px;
  padding: 0 16px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2);
  background: var(--panel);
  border: 1px solid var(--hair);
  border-radius: 999px;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease,
    color 0.18s ease,
    box-shadow 0.18s ease;
}
/* idle 常驻呼吸（US-1）：几乎不可察但打破静止；transform 由 CSS 动画独占，
 * 与 pill-inner 的 Motion 缩放（子元素）互不冲突，WAAPI 弹跳期间优先。 */
.island-pill.is-idle {
  animation: island-idle-breathe 4s ease-in-out infinite;
}
.island-pill:hover {
  border-color: var(--brand);
  color: var(--brand-ink);
  background: var(--brand-wash);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
}
.island-pill:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.island-pill.is-open {
  /* 复审三 §5/§8：开态 pill 提到 backdrop(65) 之上——hover 高亮复活、
     点胶囊原位可 toggle 收起（否则点击永远落在 backdrop 上）。 */
  z-index: 68;
  border-color: var(--brand);
  background: var(--brand-wash);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.1);
}
.island-pill.has-unread {
  border-color: var(--brand);
}

.island-pill-inner {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  transform-origin: center center;
  will-change: transform;
}

.island-value {
  font-family: var(--font-display);
  font-weight: 700;
  color: var(--ink-1);
  display: inline-block;
}

/* 一瞥摘要：到达瞬间 pill 内短暂展示最新通知 */
.island-peek {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  color: var(--brand-ink);
  display: inline-block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 230px;
}

.island-idle-label {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--brand-ink);
  background: var(--brand-wash);
  padding: 2px 8px;
  border-radius: 5px;
  display: inline-block;
}

.island-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--match);
  box-shadow: 0 0 0 3px var(--match-wash);
  animation: island-breathe 1.8s ease-in-out infinite;
}

.island-pending-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e5a13a;
  animation: island-breathe 1.6s ease-in-out infinite;
}

.island-attention-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.island-attention-mark {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 auto;
}
.island-attention-mark.attention-paused {
  background: #e5a13a;
  box-shadow: 0 0 0 3px rgba(229, 161, 58, 0.25);
}
.island-attention-mark.attention-error {
  background: #e5484d;
  box-shadow: 0 0 0 3px rgba(229, 72, 77, 0.25);
}
.island-attention-mark.attention-pending {
  background: #e5a13a;
  box-shadow: 0 0 0 3px rgba(229, 161, 58, 0.25);
}

.island-unread {
  font-family: var(--font-display);
  font-size: 10px;
  font-weight: 700;
  color: #fff;
  background: #e5484d;
  border-radius: 999px;
  padding: 1px 6px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-left: 2px;
  animation: island-breathe 1.8s ease-in-out infinite;
}

.island-backdrop {
  position: fixed;
  inset: 0;
  z-index: 65;
  background: transparent;
}
/* 复审三 N2：退场阶段 backdrop 只需遮罩语义、不再拦点击——220ms 窗口内
 * App 已可能打开抽屉/菜单（z 低于 backdrop），放行指针避免点击黑洞。 */
.island-backdrop.is-leaving {
  pointer-events: none;
}

/* 读屏播报区：视觉隐藏但保留给 AT */
.island-sr-live {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}

@keyframes island-breathe {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.6; transform: scale(1.18); }
}
@keyframes island-idle-breathe {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.006); }
}

:global([data-theme="kaleido"]) .island-pill {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.22);
  color: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(6px);
}
:global([data-theme="kaleido"]) .island-pill:hover,
:global([data-theme="kaleido"]) .island-pill.is-open {
  background: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.35);
  color: #fff;
}
:global([data-theme="kaleido"]) .island-value {
  color: #fff;
}
/* C3：kaleido 下 idle 平台标签也走白玻璃，避免暗色 wash 叠出脏灰绿 */
:global([data-theme="kaleido"]) .island-idle-label {
  color: rgba(255, 255, 255, 0.92);
  background: rgba(255, 255, 255, 0.16);
}
:global([data-theme="kaleido"]) .island-peek {
  color: #fff;
}

@media (prefers-reduced-motion: reduce) {
  .island-live,
  .island-pending-dot,
  .island-unread,
  .island-pill.is-idle {
    animation: none;
  }
}
</style>
