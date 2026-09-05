<script setup lang="ts">
// ---------------------------------------------------------------------------
// 037 灵动岛 v3：live 仪表盘 + 转盘轮播 + 两色芯片 + 红光 live state。
//
// 037 核心变更（在旧版骨架上叠加，不回退既有行为）：
// - pill 内部改为 vertical spring carousel（lane 0=主流程 live state pinned，
//   lane 1+=打断队列 FIFO）；translateY 用 motion-v spring 有 overshoot 才"弹"。
// - running 态显示 live 进度（正在抓取/AI精筛 N/M），数字跳动有弹性：
//   done 变化时旧值上滑淡出（labelStack 栈）+ 新值下滑淡入 + playPop 弹动。
// - completed 态显示彩色芯片（匹配绿 / 待确认琥珀），phase=scraped 仍显示"待筛选 N"。
// - attention 态叠加红光层（data-glow="error"|"paused"），任务恢复自动褪去。
// - pill 宽度 spring 弹性伸缩（FR-007"弹弹的"）：测量展示位 lane 的自然宽，
//   Motion 以 spring 过渡到目标宽（含角标占位）；上限 100vw-32px（窄屏边角）。
// - 打断到达：carousel 垂直转一次展示打断、~2.2s 后转回主流程，打断沉入 panel。
// - 037 的 peek 一瞥机制移除（live state 已是实时仪表盘，无需临时文本）；
//   playPop + aria-live announce 保留（终态通知 + 打断都触发）。
// - 037 的 panel/dismiss snapshot/互斥/tab trap/Teleport backdrop/navigate/aria 全保留。
// - navigate 目标含 "reminders"（打断行点击开提醒抽屉，App 层分流——
//   requestCapsuleNavigation 不认识它，useDiscoveryState 禁改）。
// ---------------------------------------------------------------------------
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Motion, useReducedMotion } from "motion-v";
import type { CapsuleStatusPayload, DynamicIslandState } from "../composables/useDiscoveryState";
import type { IslandNotice } from "../composables/useIslandNotices";
import type {
  IslandCarouselApi,
  IslandInterruptContent,
  IslandLiveState,
} from "../composables/useIslandCarousel";
import type { IntegrityConclusion } from "../types";
import { useIslandValueTransition } from "../composables/useIslandValueTransition";
import IslandNoticePanel from "./IslandNoticePanel.vue";

export type CapsuleTarget = "home" | "task" | "results" | "attention" | "reminders";

const props = defineProps<{
  status: CapsuleStatusPayload | null;
  notices: IslandNotice[];
  carousel: IslandCarouselApi;
}>();

const emit = defineEmits<{
  navigate: [target: CapsuleTarget];
  expand: [];
  dismiss: [ids: string[]];
}>();

const LANE_HEIGHT = 34;

const open = ref(false);
const leaving = ref(false);
const liveText = ref("");

const pillRef = ref<HTMLElement | null>(null);
const anchorEl = ref<HTMLElement | null>(null);
const trackEl = ref<HTMLElement | null>(null);

let leaveTimer: number | undefined;
let popAnim: Animation | null = null;

const capsule = computed<DynamicIslandState>(
  () => props.status?.capsule ?? { state: "idle", platform: "boss" },
);
const integrityConclusion = computed<IntegrityConclusion | "">(
  () => props.status?.integrity?.conclusion || "",
);
const integrityLabel = computed(() => {
  if (integrityConclusion.value === "partial") return "部分完成";
  if (integrityConclusion.value === "unverifiable") return "无法确认";
  if (integrityConclusion.value === "failed") return "执行失败";
  if (integrityConclusion.value === "interrupted") return "任务已中断";
  if (integrityConclusion.value === "empty") return "没有找到岗位";
  return "";
});

const mainLaneState = props.carousel.mainLaneState;
const activeLaneIndex = props.carousel.activeLaneIndex;
const lanes = props.carousel.lanes;
const badgeCount = props.carousel.badgeCount;

const interruptLanes = computed(() => lanes.value.filter((l) => l.type === "interrupt"));

const livePhase = computed(() => mainLaneState.value.phase);
const liveDone = computed(() => mainLaneState.value.done);
const liveTotal = computed(() => mainLaneState.value.total);
const liveCounts = computed(() => mainLaneState.value.counts);
const glow = computed(() => mainLaneState.value.glow ?? "none");

const platformLabel = computed(() =>
  capsule.value.platform === "zhilian" ? "智联" : "BOSS",
);

const isScrapedPhase = computed(() => props.status?.phase === "scraped");

const runningLabel = computed(() => {
  // 037 复审：三种阶段文案——列表抓取 / JD 抓取 / AI 精筛（旧版只有前两种，
  // 抓 JD 阶段被显示成"AI精筛"，与真·AI 精筛撞车）。
  if (!["scraping", "jd", "screening"].includes(livePhase.value)) return "";
  const prefix =
    livePhase.value === "scraping" ? "正在抓取"
      : livePhase.value === "jd" ? "抓取 JD"
        : "AI精筛";
  const done = liveDone.value ?? 0;
  return liveTotal.value === undefined
    ? `${prefix} ${done}`
    : `${prefix} ${done}/${liveTotal.value}`;
});

const completedSummary = computed(() => {
  if (livePhase.value !== "completed") return "";
  if (integrityLabel.value && integrityConclusion.value !== "succeeded") {
    return integrityLabel.value;
  }
  if (isScrapedPhase.value) return `待筛选 ${liveCounts.value?.matched ?? 0}`;
  const matched = liveCounts.value?.matched ?? 0;
  const pending = liveCounts.value?.pending ?? 0;
  return pending > 0 ? `匹配 ${matched} · 待确认 ${pending}` : `匹配 ${matched}`;
});

const attentionMessage = computed(() =>
  capsule.value.state === "attention" ? capsule.value.attention.message : "",
);

const stateTarget = computed<CapsuleTarget>(() => {
  switch (capsule.value.state) {
    case "running": return "task";
    case "completed": return "results";
    case "attention": return "attention";
    default: return "home";
  }
});

// 037：总未读 = panel 未读（终态通知 + 已沉入的打断） + carousel 队列（未沉入的打断）
const unread = computed(() =>
  props.notices.filter((n) => !n.read).length + badgeCount.value,
);

const reduced = useReducedMotion();
const animOn = computed(() => !reduced.value);
const carouselSpring = computed(() =>
  reduced.value ? { duration: 0 } : { type: "spring" as const, stiffness: 300, damping: 26 },
);
const hoverSpring = computed(() =>
  reduced.value ? { duration: 0 } : { type: "spring" as const, stiffness: 460, damping: 18 },
);
const valueSpring = computed(() =>
  // 037 复审：stiffness 380 → 520（收敛更快）——数字跳动动画窗口越长，
  // 文字停留在亚像素位移+半透明状态越久越"糊"（用户反馈）。
  reduced.value ? { duration: 0 } : { type: "spring" as const, stiffness: 520, damping: 32 },
);

const runningValueTransition = useIslandValueTransition(runningLabel, {
  enabled: () => animOn.value,
});
const completedValueTransition = useIslandValueTransition(completedSummary, {
  enabled: () => animOn.value,
});
const runningExitValues = runningValueTransition.exiting;
const completedExitValues = completedValueTransition.exiting;

// ---- 037 FR-007：pill 宽度 spring 弹性伸缩（"弹弹的"） ----
// 测量展示位 lane 的自然宽（inline-flex nowrap，优先保留 CSS 计算出的亚像素宽度），
// Motion 以 spring 过渡到目标宽 = lane 宽 + 左右 padding + 左右边框 + 角标占位。
// 上限 100vw - 32px（窄屏边角）；reduce-motion 瞬时（widthSpring duration 0）。
const PILL_PAD_X = 36; // 左右 padding 18*2（与 .island-pill padding 同口径）
const PILL_BORDER_W = 2; // 左右边框 1*2（.island-pill 使用 border-box）
const ISLAND_EFFECT_BLEED = 4; // 圆点 3px 光晕 + 1px 抗锯齿余量
const BADGE_W = 30; // 未读角标占位（含 margin）
/* 037 复审：56 → 60——短文案（如 idle 的"BOSS"）时 pill 不过窄，
   内容左右留出可见的呼吸空间，不再"截断一丢丢"。
   约束：必须小于 PILL_PAD_X + BADGE_W（36+30=66）。否则 jsdom 下
   （无布局、offsetWidth 恒为 0）目标宽全部被最小值夹住，"角标出现后
   增宽"的 FR-007 断言失去差值——真实浏览器不受影响，但不掩盖测试语义。 */
const PILL_MIN_W = 60;

const pillWidth = ref<number | null>(null);
const widthSpring = computed(() =>
  reduced.value ? { duration: 0 } : { type: "spring" as const, stiffness: 380, damping: 26 },
);
const widthAnimate = computed(() =>
  pillWidth.value !== null ? { width: pillWidth.value } : undefined,
);

function remeasureWidth() {
  const track = trackEl.value;
  if (!track) return;
  // track 的 children 顺序 = [main lane, ...interrupt lanes]，与 activeLaneIndex 对齐。
  const el = track.children[activeLaneIndex.value] as HTMLElement | undefined;
  if (!el) return;
  const extra = unread.value > 0 ? BADGE_W : 0;
  const cssWidth = Number.parseFloat(window.getComputedStyle(el).width);
  const naturalWidth = Number.isFinite(cssWidth) && cssWidth > 0 ? cssWidth : el.offsetWidth;
  const target =
    naturalWidth + ISLAND_EFFECT_BLEED * 2 + PILL_PAD_X + PILL_BORDER_W + extra;
  pillWidth.value = Math.min(Math.max(target, PILL_MIN_W), window.innerWidth - 32);
}

// 内容变化触发重测：主流程文案/芯片计数/打断内容/角标出现。
const contentKey = computed(() => {
  const lane = lanes.value[activeLaneIndex.value];
  if (lane && lane.type === "interrupt") {
    const c = lane.content as IslandInterruptContent;
    return `int:${c.title}:${c.detail ?? ""}`;
  }
  switch (livePhase.value) {
    // 037 复审：加上 "jd"——漏掉它会落进 default（idle 口径），JD 抓取阶段
    // 文案变化触发不了宽度重测，pill 不跟着撑宽，文字被裁。
    case "scraping":
    case "jd":
    case "screening":
      return `run:${runningLabel.value}`;
    case "completed":
      return `done:${isScrapedPhase.value ? "s" : "j"}:${liveCounts.value?.matched ?? 0}:${liveCounts.value?.pending ?? 0}`;
    case "attention":
      return `att:${attentionMessage.value}`;
    default:
      return `idle:${capsule.value.platform}`;
  }
});

watch([contentKey, unread], async () => {
  await nextTick();
  remeasureWidth();
}, { flush: "post" });

onMounted(() => {
  remeasureWidth();
  window.addEventListener("resize", remeasureWidth);
  // 首帧可能仍使用回退字体；字体就绪后正文宽度会变化，必须重新测量，
  // 否则短文案也可能因 pill 的 overflow:hidden 被右侧裁掉。
  const fontsReady = document.fonts?.ready;
  if (fontsReady) void fontsReady.then(() => remeasureWidth());
});

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

function announce(title: string, detail?: string) {
  liveText.value = detail ? `${title}：${detail}` : title;
}

// ---- 037 终态通知反馈：watch notices 新未读 → playPop + announce ----
let seenUnread = new Set<string>();
let boot = true;

watch(
  () => props.notices,
  (list) => {
    const current = new Set(list.filter((n) => !n.read).map((n) => n.id));
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
    announce(newest.title, newest.detail);
    if (!open.value && !leaving.value) playPop();
  },
  { immediate: true },
);

// ---- 037 打断反馈：watch badgeCount 增长 → playPop + announce ----
let badgeBoot = true;
let seenBadge = 0;

watch(badgeCount, (count) => {
  if (badgeBoot) {
    seenBadge = count;
    badgeBoot = false;
    return;
  }
  if (count <= seenBadge) {
    seenBadge = count;
    return;
  }
  seenBadge = count;
  if (open.value || leaving.value) return;
  const active = lanes.value[activeLaneIndex.value];
  if (active && active.type === "interrupt") {
    const content = active.content as IslandInterruptContent;
    announce(content.title, content.detail);
    playPop();
  }
});

watch(liveDone, (_next, prev) => {
  if (prev === undefined || prev === _next) return;
  // 数字推进时"啵"一下（reduce-motion 由 playPop 内部 animOn 守卫短路）。
  if (!open.value && !leaving.value) playPop();
});

// ---- 开合与焦点管理（沿用 037） ----
let closingIds: string[] = [];

function openPanel() {
  if (open.value) return;
  leaving.value = false;
  open.value = true;
  emit("expand");
  const rect = pillRef.value?.getBoundingClientRect();
  if (anchorEl.value) {
    const top = rect ? Math.min(rect.bottom + 8, window.innerHeight - 48) : 76;
    anchorEl.value.style.setProperty("--panel-top", `${Math.max(top, 8)}px`);
  }
  void nextTick(() => {
    const panel = anchorEl.value?.querySelector<HTMLElement>('[data-testid="island-notice-panel"]');
    const first = panel?.querySelector<HTMLElement>('button:not(:disabled), [tabindex]:not([tabindex="-1"])');
    (first ?? panel)?.focus({ preventScroll: true });
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
  if (closingIds.length > 0) {
    emit("dismiss", closingIds);
    closingIds = [];
  }
  if (anchorEl.value?.contains(document.activeElement)) {
    pillRef.value?.focus({ preventScroll: true });
  }
}

function requestClose() {
  if (!open.value || leaving.value) return;
  closingIds = props.notices.map((n) => n.id);
  if (animOn.value) {
    leaving.value = true;
    leaveTimer = window.setTimeout(() => finishClose(), 220);
  } else {
    finishClose();
  }
}

function onPillClick() {
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
  if (event.key === "Tab" && open.value && !leaving.value) {
    const anchor = anchorEl.value;
    if (!anchor) return;
    const panel = anchor.querySelector<HTMLElement>('[data-testid="island-notice-panel"]');
    if (!panel) return;
    const focusables = Array.from(
      panel.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex]:not([tabindex="-1"])'),
    );
    if (focusables.length === 0) {
      event.preventDefault();
      panel.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (!panel.contains(active)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && (active === first || active === panel)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || active === panel)) {
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
  window.removeEventListener("resize", remeasureWidth);
  if (leaveTimer !== undefined) window.clearTimeout(leaveTimer);
  popAnim?.cancel();
});
// Motion 组件实例 → 真实 DOM（$el）；普通元素直接用。pill/track 的 ref 均经此适配。
function setPillRef(el: unknown): void {
  const dom = (el as { $el?: unknown } | null)?.$el ?? el;
  pillRef.value = dom instanceof HTMLElement ? dom : null;
}
function setTrackRef(el: unknown): void {
  const dom = (el as { $el?: unknown } | null)?.$el ?? el;
  trackEl.value = dom instanceof HTMLElement ? dom : null;
}

</script>

<template>
  <div ref="anchorEl" class="island-anchor">
    <Motion
      :ref="setPillRef"
      as="button"
      type="button"
      class="island-pill"
      :class="[`is-${capsule.state}`, { 'has-unread': unread > 0, 'is-open': open, 'has-glow': glow !== 'none' }]"
      :data-testid="`dynamic-island-${capsule.state}`"
      :data-pill-width="pillWidth ?? null"
      :data-integrity="integrityConclusion || undefined"
      :data-glow="glow !== 'none' ? glow : undefined"
      :aria-expanded="open"
      :aria-label="unread > 0 ? `灵动岛，${unread} 条未读提醒` : undefined"
      :animate="widthAnimate"
      :transition="widthSpring"
      :while-hover="animOn ? { scale: 1.03 } : undefined"
      :while-press="animOn ? { scale: 0.96 } : undefined"
      @click="onPillClick"
    >
      <!-- 037 红光层（attention live state，subtle glow） -->
      <span
        v-if="glow !== 'none'"
        class="island-glow"
        :data-glow="glow"
        aria-hidden="true"
      ></span>

      <!-- 037 转盘轮播：viewport（clip 窗口）与 track（被 translate 的轨道）分离。
           037 复审根因修复：旧版把 overflow:hidden 直接放在被 translate 的 track 上，
           CSS overflow clip 区随 transform 一起移动，translateY 只把整块内容推出
           pill（被外层裁掉），永远只显示 lane0——打断 lane 从不进入可视区（用户实测
           打断弹开但内容空白）。现固定 viewport 高 34 + 单独的垂直 clip 不移动，
           内层 track 以 translateY=-activeLaneIndex*LANE_HEIGHT 精确轮播。 -->
      <span class="island-content-frame">
        <span class="island-carousel-viewport">
          <Motion
            :ref="setTrackRef"
            as="span"
            class="island-carousel-track"
            :animate="{ y: -activeLaneIndex * LANE_HEIGHT }"
            :transition="carouselSpring"
          >
        <!-- Lane 0: 主流程（live state，pinned） -->
        <span class="island-lane island-lane-main">
          <!-- idle：平台名 + 呼吸 -->
          <Motion
            v-if="livePhase === 'idle'"
            :key="`idle-${capsule.platform}`"
             :initial="animOn ? { y: 6, opacity: 0, scaleY: 0.92 } : false"
             :animate="{ y: 0, opacity: 1, scaleY: 1 }"
            :transition="valueSpring"
            as="span"
            class="island-idle-label"
            data-testid="island-idle"
          >{{ platformLabel }}</Motion>

          <!-- running：正在抓取 / 抓取 JD / AI精筛 + live dot；旧值上滑淡出 -->
          <template v-else-if="['scraping', 'jd', 'screening'].includes(livePhase)">
            <span class="island-live" :class="`phase-${livePhase}`" aria-hidden="true"></span>
            <span
              v-for="old in runningExitValues"
              :key="`out-${old}`"
              class="island-value is-value-out"
              data-testid="island-running-value-out"
              aria-hidden="true"
            >{{ old }}</span>
            <Motion
              :key="runningLabel"
               :initial="animOn ? { y: 5, opacity: 0, scaleY: 1.08 } : false"
               :animate="{ y: 0, opacity: 1, scaleY: 1 }"
              :transition="valueSpring"
              as="span"
              class="island-value"
              data-testid="island-running-value"
            >{{ runningLabel }}</Motion>
          </template>

          <!-- completed：彩色芯片（匹配绿 / 待确认琥珀）；scraped 显示"待筛选 N" -->
          <template v-else-if="livePhase === 'completed'">
            <span
              v-for="old in completedExitValues"
              :key="`completed-out-${old}`"
              class="island-value is-value-out"
              data-testid="island-completed-value-out"
              aria-hidden="true"
            >{{ old }}</span>
            <Motion
              :key="completedSummary"
               :initial="animOn ? { y: 5, opacity: 0, scaleY: 1.08 } : false"
               :animate="{ y: 0, opacity: 1, scaleY: 1 }"
              :transition="valueSpring"
              as="span"
              class="island-completed-content"
            >
              <span v-if="isScrapedPhase" class="island-value" data-testid="island-completed-value">{{ `待筛选 ${liveCounts?.matched ?? 0}` }}</span>
              <span v-else class="island-chips">
                <span
                  class="island-chip"
                  :class="integrityConclusion && integrityConclusion !== 'succeeded' ? 'c-amber' : 'c-green'"
                  data-testid="island-completed-value"
                >{{ completedSummary || `匹配 ${liveCounts?.matched ?? 0}` }}</span>
                <span
                  v-if="!integrityConclusion && (liveCounts?.pending ?? 0) > 0"
                  class="island-chip c-amber island-pending-dot"
                  data-testid="island-pending-chip"
                >待确认 {{ liveCounts?.pending }}</span>
              </span>
            </Motion>
          </template>

          <!-- attention：提醒色 + 文案 -->
          <template v-else-if="livePhase === 'attention'">
            <Motion
              :key="`att-${glow}-${attentionMessage}`"
               :initial="animOn ? { y: 6, opacity: 0, scaleY: 0.92 } : false"
               :animate="{ y: 0, opacity: 1, scaleY: 1 }"
              :transition="valueSpring"
              as="span"
              class="island-attention-row"
            >
              <span class="island-attention-mark" :class="`attention-${glow}`" aria-hidden="true"></span>
              <span class="island-value">{{ attentionMessage }}</span>
            </Motion>
          </template>
        </span>

        <!-- Lane 1+: 打断队列 -->
        <span
          v-for="lane in interruptLanes"
          :key="lane.id"
          class="island-lane island-lane-interrupt"
          :data-tone="(lane.content as IslandInterruptContent).tone"
        >
          <span class="island-interrupt-mark" :class="`tone-${(lane.content as IslandInterruptContent).tone}`" aria-hidden="true"></span>
          <span class="island-interrupt-title">{{ (lane.content as IslandInterruptContent).title }}</span>
          <span
            v-if="(lane.content as IslandInterruptContent).detail"
            class="island-interrupt-detail"
          >{{ (lane.content as IslandInterruptContent).detail }}</span>
        </span>
          </Motion>
        </span>

        <!-- 未读角标（037 badge + 037 carousel queue） -->
        <span
          v-if="unread > 0"
          class="island-unread"
          data-testid="island-unread"
          aria-hidden="true"
        >{{ unread > 99 ? "99+" : unread }}</span>
      </span>
    </Motion>

    <!-- 读屏播报（视觉隐藏）：通知/打断到达 announce() 更新 -->
    <span
      class="island-sr-live"
      role="status"
      aria-live="polite"
      data-testid="island-sr-live"
    >{{ liveText }}</span>

    <Teleport to="body">
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
    </Teleport>
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
  gap: 0;
  height: 34px;
  /* 037 复审：0 16 → 0 18——用户反馈内容"左右截断一丢丢"（贴边框）。
     注意 PILL_PAD_X 必须与此处左右 padding 之和同口径（18*2=36），
     否则 remeasureWidth 测出的目标宽会偏窄、内容仍显贴边。 */
  padding: 0 18px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2);
  background: var(--panel);
  border: 1px solid var(--hair);
  border-radius: 999px;
  white-space: nowrap;
  cursor: pointer;
  overflow: hidden;
  /* 037 窄屏边角：pill 宽度上限不超屏宽 -32px（宽度 spring 由 Motion 驱动，
     目标宽 JS 侧同口径 clamp，此处兜底防溢出视口）。 */
  max-width: calc(100vw - 32px);
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease,
    color 0.18s ease,
    box-shadow 0.18s ease;
}
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
  z-index: 68;
  border-color: var(--brand);
  background: var(--brand-wash);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.1);
}
.island-pill.has-unread {
  border-color: var(--brand);
}

/* 037 红光层：attention 时 pill 背景隐隐闪光泛红 */
.island-glow {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  pointer-events: none;
  z-index: 0;
  animation: island-glow 2.4s ease-in-out infinite;
}
.island-glow[data-glow="error"] {
  background: radial-gradient(ellipse at center, rgba(229, 72, 77, 0.18), transparent 70%);
  box-shadow: inset 0 0 12px rgba(229, 72, 77, 0.15);
}
.island-glow[data-glow="paused"] {
  background: radial-gradient(ellipse at center, rgba(229, 161, 58, 0.16), transparent 70%);
  box-shadow: inset 0 0 12px rgba(229, 161, 58, 0.12);
}
@keyframes island-glow {
  0%, 100% { opacity: 0.6; }
  50%      { opacity: 1; }
}

/* 037 裁边修复：内容边界集中到一个安全框，给圆点光晕预留水平余量。 */
.island-content-frame {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  flex: 0 0 auto;
  padding-inline: 4px;
}

/* 037 转盘轮播：viewport 与 track 分离。
   037 复审根因修复：viewport 固定单 lane 视口高 + 独立 clip 窗口
   不随 transform 移动；内层 track 是纯 flex column 轨道（高=内容自然堆叠），
   以 translateY=-activeLaneIndex*LANE_HEIGHT 轮播，打断 lane 才能进入视口。
   旧版把 height:34 + overflow:hidden 直接放在被 translate 的 track 上：CSS
   overflow clip 区随 transform 同步移动，translateY 只是让整块内容从 pill 顶部
   滑出被外层裁掉，永远只显示 lane0——打断 lane 从不进入可视区（用户实测：
   打断弹开但内容空白、只留红角标）。单 lane（idle）时 track 高=viewport 高，
   viewport align-items:center 垂直居中不偏上。 */
.island-carousel-viewport {
  display: inline-flex;
  /* track 顶部对齐 viewport：多 lane 时 track 高=34*N，居中会让 lane0 被裁；
     flex-start 保证 track 的 y=0（lane0 顶）对准 clip 窗口顶，translateY
     精确轮播。单 lane 时 track 高=viewport 高，无空隙不偏上。 */
  align-items: flex-start;
  height: 34px;
  /* 只裁上下轮播，水平向外放出 4px，避免圆点 box-shadow 被切半。 */
  overflow: visible;
  clip-path: inset(0 -4px);
}

.island-carousel-track {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0;
  flex: none;
  transform-origin: center center;
  /* 037 复审：去掉 will-change: transform——它把 track 常驻提升为合成层，
     文字走纹理渲染配合 spring 的亚像素位移（实测 -20.36/-34.13）会明显发虚
     （用户反馈"灵动岛里的字是真的糊"）。小元素无需常驻提示。 */
}

.island-lane {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  height: 34px;
  white-space: nowrap;
  flex-shrink: 0;
}

.island-value {
  font-family: var(--font-display);
  font-weight: 700;
  color: var(--ink-1);
  display: inline-block;
}

/* 037 SC-001 数字跳动：旧值上滑淡出（新值由 :key 重建的 Motion 下滑淡入）。
   037 复审：0.32s → 0.18s、位移 -10px → -6px——抓取中数字频繁刷新，动画
   窗口越长文字停留在亚像素位移+半透明状态越久，看上去"糊"（用户反馈）。
   缩短并减幅后清晰，跳动感保留。 */
.island-value.is-value-out {
  position: absolute;
  left: 50%;
  transform: translate(-50%, 0);
  pointer-events: none;
  animation: island-value-out 0.18s ease forwards;
}
@keyframes island-value-out {
  from { opacity: 0.9; transform: translate(-50%, 0); }
  to   { opacity: 0;   transform: translate(-50%, -6px); }
}

.island-idle-label {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  /* 037 复审：color 从 var(--brand-ink) 改 var(--ink-1)——dark boss 下
     --brand-ink=#005e53 深青色与深色 pill 背景撞色看不见（用户反馈）。
     --ink-1 在 dark/light 都与 pill --panel 对比够。 */
  color: var(--ink-1);
  /* 037 复审：去掉 idle label 自己的 wash 底色块（background/padding/
     border-radius）——用户反馈"不要带方块"：pill 本身就是圆角胶囊框，
     内部再嵌一个青色小药丸显得零碎；改为纯文字，视觉更干净。 */
  display: inline-block;
}

.island-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
  /* 037 复审：圆点只做不透明度呼吸，不再 scale（island-breathe 的
     scale 1→1.18 让用户觉得"弹弹嫩嫩"太跳）；未读角标保留原动画。 */
  animation: island-live-breathe 1.8s ease-in-out infinite;
}
.island-live.phase-scraping {
  background: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
}
/* 037 复审：JD 抓取阶段专用色（青色，介于抓取蓝与精筛紫之间，三阶段可辨）。 */
.island-live.phase-jd {
  background: #00d4bc;
  box-shadow: 0 0 0 3px rgba(0, 212, 188, 0.22);
}
.island-live.phase-screening {
  background: #8b5cf6;
  box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.2);
}

/* 037 completed 彩色芯片 */
.island-chips {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.island-chip {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  line-height: 1.4;
}
.island-chip.c-green {
  color: #12905f;
  background: rgba(18, 144, 95, 0.12);
}
.island-chip.c-amber {
  color: #b45309;
  background: rgba(229, 161, 58, 0.15);
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
.island-attention-mark.attention-none {
  background: var(--text-muted, #888);
}

/* 037 打断 lane */
.island-interrupt-mark {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
}
.island-interrupt-mark.tone-warning {
  background: #e5a13a;
  box-shadow: 0 0 0 3px rgba(229, 161, 58, 0.2);
}
.island-interrupt-mark.tone-error {
  background: #e5484d;
  box-shadow: 0 0 0 3px rgba(229, 72, 77, 0.2);
}
.island-interrupt-title {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-1);
}
/* 037 复审：打断 title 按 tone 染色——warning 琥珀 / error 红，比 --ink-1
   更醒目且不与 pill 背景撞色（用户反馈打断弹开后字看不见，--ink-1 在某些
   theme 下对比不足）。pill 内打断 lane 直接用 tone 色，沉入 panel 的
   notice-title 同步染色（见 IslandNoticePanel）。 */
.island-lane-interrupt[data-tone="warning"] .island-interrupt-title { color: #e5a13a; }
.island-lane-interrupt[data-tone="error"] .island-interrupt-title { color: #e5484d; }
.island-interrupt-detail {
  font-size: 11px;
  color: var(--text-soft, #9fb0c3);
}
.island-lane-interrupt[data-tone="warning"] .island-interrupt-detail { color: #e5a13a; opacity: 0.85; }
.island-lane-interrupt[data-tone="error"] .island-interrupt-detail { color: #e5484d; opacity: 0.85; }

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
  z-index: 60;
  background: transparent;
}
.island-backdrop.is-leaving {
  pointer-events: none;
}

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
/* 037 复审：idle 呼吸不再用 transform scale（scale 1→1.006 无限循环，让 pill
   内容一直处于非整数倍缩放 → 文字渲染发虚，实测 computed transform 恒为
   matrix(1.00224,...)）。改为只呼吸边框光晕：不动 transform，文字保持清晰，
   呼吸感仍保留（用户反馈"灵动岛里的字是真的糊"）。 */
@keyframes island-idle-breathe {
  0%, 100% { box-shadow: 0 0 0 0 rgba(0, 212, 188, 0); }
  50%      { box-shadow: 0 0 10px 0 rgba(0, 212, 188, 0.3); }
}
/* 037 复审：运行指示点只呼吸不透明度、不缩放（原 island-breathe 的
   scale 1→1.18 即用户说的"弹弹嫩嫩"）；未读角标仍用 island-breathe。 */
@keyframes island-live-breathe {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.45; }
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
:global([data-theme="kaleido"]) .island-idle-label {
  /* 037 复审：与主主题一致去掉 wash 底色块（"不要带方块"），只保留字色。 */
  color: rgba(255, 255, 255, 0.92);
}
:global([data-theme="kaleido"]) .island-chip.c-green {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.15);
}
:global([data-theme="kaleido"]) .island-chip.c-amber {
  color: #fbbf24;
  background: rgba(251, 191, 36, 0.15);
}
:global([data-theme="kaleido"]) .island-interrupt-title {
  color: #fff;
}
:global([data-theme="kaleido"]) .island-interrupt-detail {
  color: rgba(255, 255, 255, 0.65);
}

@media (prefers-reduced-motion: reduce) {
  .island-live,
  .island-unread,
  .island-pill.is-idle,
  .island-glow,
  .island-value.is-value-out {
    animation: none;
  }
}
</style>
