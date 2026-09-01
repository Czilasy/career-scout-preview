<script setup lang="ts">
// ---------------------------------------------------------------------------
// 036 B088 顶栏胶囊灵动岛：常驻活组件，四态（idle/running/completed/attention）
// 按 spec FR-013 优先级取一件，点击直达目标页。
//
// 数据：只消费 App 上抛的 CapsuleStatusPayload（经 round-status 链路），
// 不做任何数据抓取（spec FR-017）。
// 动画：数字跳动/呼吸点/待确认标亮；系统「减少动态」时退化为静态（FR-018）。
// 主题：明暗用主题令牌；特殊主题（kaleido）半透明保证可见（FR-020）。
// ---------------------------------------------------------------------------
import { computed } from "vue";
import type { CapsuleStatusPayload, DynamicIslandState } from "../composables/useDiscoveryState";

export type CapsuleTarget = "home" | "task" | "results" | "attention";

const props = defineProps<{
  status: CapsuleStatusPayload | null;
}>();

const emit = defineEmits<{
  navigate: [target: CapsuleTarget];
}>();

const capsule = computed<DynamicIslandState>(
  () => props.status?.capsule ?? { state: "idle", platform: "boss" },
);

const platformLabel = computed(() =>
  capsule.value.platform === "zhilian" ? "智联" : "BOSS");

const target = computed<CapsuleTarget>(() => {
  switch (capsule.value.state) {
    case "running": return "task";
    case "completed": return "results";
    case "attention": return "attention";
    default: return "home";
  }
});

/** 运行中文案：抓取 128/300 或 筛选 45/128；total 未知省略分母（B088）。 */
const runningLabel = computed(() => {
  if (capsule.value.state !== "running") return "";
  const { progress } = capsule.value;
  const prefix = progress.phase === "scraping" ? "抓取" : "筛选";
  return progress.total === undefined
    ? `${prefix} ${progress.done}`
    : `${prefix} ${progress.done}/${progress.total}`;
});

/** 跑完文案：匹配 N · 待确认 M；待确认 0 时只显示匹配数（B088）。
 *  未筛选轮（payload.phase="scraped"）结果页展示为「待筛选 N」，胶囊文案
 *  与结果页保持一致（SC-009 / B038：未筛选轮不显示"已判定/匹配"）。 */
const completedLabel = computed(() => {
  if (capsule.value.state !== "completed") return "";
  const { results } = capsule.value;
  if (props.status?.phase === "scraped") return `待筛选 ${results.matched}`;
  return results.pending > 0
    ? `匹配 ${results.matched} · 待确认 ${results.pending}`
    : `匹配 ${results.matched}`;
});

function onClick() {
  emit("navigate", target.value);
}
</script>

<template>
  <button
    class="dynamic-island"
    :class="`island-${capsule.state}`"
    :data-testid="`dynamic-island-${capsule.state}`"
    type="button"
    @click="onClick"
  >
    <!-- idle：低调常驻，显示平台名 -->
    <template v-if="capsule.state === 'idle'">
      <span class="island-idle-label">{{ platformLabel }}</span>
    </template>

    <!-- running：实时进度数字 + 呼吸点 -->
    <template v-else-if="capsule.state === 'running'">
      <span class="island-live" aria-hidden="true"></span>
      <span class="island-value" :key="runningLabel">{{ runningLabel }}</span>
    </template>

    <!-- completed：结果数字；待确认>0 标亮 -->
    <template v-else-if="capsule.state === 'completed'">
      <span class="island-value" :key="completedLabel">{{ completedLabel }}</span>
      <span
        v-if="capsule.results.pending > 0"
        class="island-pending-dot"
        aria-hidden="true"
      ></span>
    </template>

    <!-- attention：提醒条（暂停橙 / 出错红） -->
    <template v-else>
      <span
        class="island-attention-mark"
        :class="`attention-${capsule.attention.kind}`"
        aria-hidden="true"
      ></span>
      <span class="island-value">{{ capsule.attention.message }}</span>
    </template>
  </button>
</template>

<style scoped>
.dynamic-island {
  justify-self: center;
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
  transition: background-color .18s ease, border-color .18s ease, color .18s ease;
}

.dynamic-island:hover {
  border-color: var(--brand);
  color: var(--brand-ink);
  background: var(--brand-wash);
}

/* 数字变化：整体平滑过渡（prefers-reduced-motion 时退化为瞬时） */
.island-value {
  font-family: var(--font-display);
  font-weight: 700;
  color: var(--ink-1);
  transition: opacity .18s ease, transform .18s ease;
}

/* idle：平台名低调显示 */
.island-idle-label {
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .04em;
  color: var(--brand-ink);
  background: var(--brand-wash);
  padding: 2px 8px;
  border-radius: 5px;
}

/* running：呼吸点 */
.island-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--match);
  box-shadow: 0 0 0 3px var(--match-wash);
  animation: island-breathe 1.8s ease-in-out infinite;
}

@keyframes island-breathe {
  0%, 100% { opacity: 1; }
  50% { opacity: .35; }
}

/* completed：待确认标亮小圆点 */
.island-pending-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--attention, #e5a13a);
  animation: island-breathe 1.6s ease-in-out infinite;
}

/* attention：状态色标（暂停橙 / 出错红 / 待处理琥珀） */
.island-attention-mark {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 auto;
}
.island-attention-mark.attention-paused {
  background: #e5a13a;
  box-shadow: 0 0 0 3px rgba(229, 161, 58, .25);
}
.island-attention-mark.attention-error {
  background: #e5484d;
  box-shadow: 0 0 0 3px rgba(229, 72, 77, .25);
}
.island-attention-mark.attention-pending {
  background: #e5a13a;
  box-shadow: 0 0 0 3px rgba(229, 161, 58, .25);
}

/* 特殊主题（kaleido）：半透明保证可见 */
:global([data-theme="kaleido"]) .dynamic-island {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.22);
  color: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(6px);
}
:global([data-theme="kaleido"]) .dynamic-island:hover {
  background: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.35);
  color: #fff;
}
:global([data-theme="kaleido"]) .island-value {
  color: #fff;
}

/* 系统「减少动态」：动画退化为静态（呼吸点静止） */
@media (prefers-reduced-motion: reduce) {
  .island-live,
  .island-pending-dot {
    animation: none;
  }
  .island-value {
    transition: none;
  }
}
</style>
