// ---------------------------------------------------------------------------
// 037 灵动岛 v3 · 转盘轮播状态机。
//
// 设计要点：
// - mainLaneState 直接读 roundStatus.capsule（computed），永不冻结——
//   打断展示期间 activeLaneIndex 指向 interrupt lane，但 mainLaneState 照常
//   重算，转回 lane 0 时 pill 立即拿到最新数字（硬不变式 FR-011）。
// - 打断队列 FIFO；pushInterrupt 展示位永远切到最新一条（US-3"多条积压只转
//   最新一条"）：旧的非 sticky 打断留在队列由各自 timer 到点沉入 panel——
//   这是"被动响应新打断"的切换，不是"自动轮播"（dismiss 后不补转下一条）。
// - 当前产品来源均不使用 sticky；保留 sticky 分支只作内部防御：若误传，
//   新 sticky 无 timer 也无展示位时直接沉入 panel，永不滞留队列卡死角标。
// - 非 sticky interrupt：定时 duration ms 后自动沉入 notice panel（经
//   onSinkInterrupt 回调），并回 lane 0。
// - sticky interrupt：仅内部防御路径不自动回，等 dismissActive()。
// - reset()：清队列+清 timer+回 lane 0（跨 profile 切换调）。
// - badgeCount = interruptQueue 长度（pill 上未沉入 panel 的打断数）；
//   pill 总未读 = notices.unreadCount + badgeCount（DynamicIsland 组合）。
// - 不发请求；不动后端；不修改 useDiscoveryState。
// ---------------------------------------------------------------------------
import { computed, ref, type ComputedRef, type Ref } from "vue";
import type { CapsuleStatusPayload, DynamicIslandState } from "./useDiscoveryState";
import type { Platform } from "../types";

/** 037 复审：新增 "jd"（JD 详情抓取阶段），与 scraping/screening 并列。 */
export type IslandPhase = "scraping" | "jd" | "screening" | "completed" | "idle" | "attention";

/** pill lane 0（主流程）内容，派生自 roundStatus.capsule。 */
export interface IslandLiveState {
  phase: IslandPhase;
  done?: number;
  total?: number;
  /** completed 态结果计数（capsule 仅携带 matched+pending；4 色完整需后续扩展 capsule）。 */
  counts?: { matched: number; pending: number };
  /** attention 态红光类型（error/paused）。 */
  glow?: "error" | "paused" | "none";
  platform: Platform;
}

/** 打断 lane 内容（投递提醒 / NoticeBar 重要打断；普通反馈由接线层归一）。 */
export interface IslandInterruptContent {
  title: string;
  detail?: string;
  tone: "warning" | "error";
  /** 037：沉入 panel 后行点击直达目标（缺省 "task"；投递提醒给 "reminders"
   *  开提醒抽屉——复审 P2-8：打断行目标按打断类型语义分流，不再一律 task）。 */
  target?: "task" | "results" | "attention" | "reminders";
}

/** carousel 一格：main 渲染 IslandLiveState，interrupt 渲染 IslandInterruptContent。 */
export interface IslandLane {
  id: string;
  type: "main" | "interrupt";
  content: IslandLiveState | IslandInterruptContent;
  duration?: number;
  sticky?: boolean;
}

export interface IslandCarouselApi {
  /** 0=主流程，1+=打断队列位置。computed（不可直接 set）。 */
  activeLaneIndex: ComputedRef<number>;
  lanes: ComputedRef<IslandLane[]>;
  /** pill 上未沉入 panel 的打断数（= interruptQueue 长度）。 */
  badgeCount: ComputedRef<number>;
  /** 主流程 live state（直接读 roundStatus，永不冻结）。 */
  mainLaneState: ComputedRef<IslandLiveState>;
  pushInterrupt(lane: Omit<IslandLane, "id" | "type">): void;
  dismissActive(): void;
  reset(): void;
}

let interruptSeq = 0;

/** 从 capsule 派生 pill 主流程 live state（037 的 runningLabel/completedLabel 的超集）。 */
function deriveLiveState(capsule: DynamicIslandState | null | undefined): IslandLiveState {
  if (!capsule) return { phase: "idle", platform: "boss" };
  switch (capsule.state) {
    case "idle":
      return { phase: "idle", platform: capsule.platform };
    case "running":
      return {
        phase: capsule.progress.phase,
        done: capsule.progress.done,
        total: capsule.progress.total,
        platform: capsule.platform,
      };
    case "completed":
      return {
        phase: "completed",
        counts: { matched: capsule.results.matched, pending: capsule.results.pending },
        platform: capsule.platform,
      };
    case "attention":
      return {
        phase: "attention",
        glow:
          capsule.attention.kind === "error"
            ? "error"
            : capsule.attention.kind === "paused"
              ? "paused"
              : "none",
        platform: capsule.platform,
      };
    default:
      return { phase: "idle", platform: "boss" };
  }
}

export function useIslandCarousel(
  roundStatus: Ref<CapsuleStatusPayload | null>,
  options?: { onSinkInterrupt?: (lane: IslandLane) => void },
): IslandCarouselApi {
  const interruptQueue = ref<IslandLane[]>([]);
  const activeInterruptId = ref<string | null>(null);

  // 硬不变式 FR-011：mainLaneState computed 直接读 roundStatus.capsule——
  // Vue 响应式不停，打断展示期间 done/total 照常推进，转回 lane 0 立即拿到新数字。
  const mainLaneState = computed<IslandLiveState>(() =>
    deriveLiveState(roundStatus.value?.capsule),
  );

  const lanes = computed<IslandLane[]>(() => [
    { id: "main", type: "main", content: mainLaneState.value },
    ...interruptQueue.value,
  ]);

  const activeLaneIndex = computed<number>(() => {
    if (activeInterruptId.value === null) return 0;
    const idx = interruptQueue.value.findIndex((l) => l.id === activeInterruptId.value);
    return idx >= 0 ? idx + 1 : 0;
  });

  const badgeCount = computed(() => interruptQueue.value.length);

  const timers = new Map<string, number>();

  /** 从队列移除该 lane，若是活跃 lane 则回 lane 0，调 onSinkInterrupt 沉入 panel。 */
  function sink(lane: IslandLane): void {
    interruptQueue.value = interruptQueue.value.filter((l) => l.id !== lane.id);
    if (activeInterruptId.value === lane.id) {
      activeInterruptId.value = null;
    }
    options?.onSinkInterrupt?.(lane);
  }

  /**
   * 推入一条打断（US-3：多条积压只转最新一条一次）。
   * - 无打断展示（active=null）：转一次展示这条（FR-004）。
   * - 正在展示非 sticky 打断：展示位切到最新这条；旧的留在队列由各自
   *   timer 到点沉入 panel，不提前退场、不自动轮播（FR-005）。
   * - 正在展示 sticky 打断（等用户手动处理）：新打断不抢位——非 sticky
   *   只入队+timer 到点沉入；新 sticky 无 timer 也无展示机会，直接沉入
   *   panel（未读），永不滞留队列卡死角标（复审 P2-3）。
   * - 非 sticky 在 duration ms（默认 2200）后自动沉入 panel。
   */
  function pushInterrupt(lane: Omit<IslandLane, "id" | "type">): void {
    const id = `interrupt-${++interruptSeq}`;
    const full: IslandLane = { ...lane, id, type: "interrupt" };
    interruptQueue.value = [...interruptQueue.value, full];

    const active = interruptQueue.value.find((l) => l.id === activeInterruptId.value);
    if (active?.sticky) {
      if (full.sticky) {
        sink(full);
        return;
      }
    } else {
      activeInterruptId.value = id;
    }
    if (!full.sticky) {
      armSinkTimer(full);
    }
  }

  /** 非 sticky 打断的自动沉入计时；到点出队 + 沉入 panel（若是活跃 lane 则回 lane 0）。 */
  function armSinkTimer(full: IslandLane): void {
    const duration = full.duration ?? 2200;
    // 用全局 setTimeout（非 window.setTimeout）——composable 是纯状态机，
    // 不应直接依赖 DOM 全局；vitest fake timers 也只替换全局 setTimeout。
    const timer = setTimeout(() => {
      timers.delete(full.id);
      const stillInQueue = interruptQueue.value.find((l) => l.id === full.id);
      if (stillInQueue) sink(stillInQueue);
    }, duration) as unknown as number;
    timers.set(full.id, timer);
  }

  /** sticky interrupt 手动 dismiss → 沉入 panel + 回 lane 0。 */
  function dismissActive(): void {
    if (activeInterruptId.value === null) return;
    const active = interruptQueue.value.find((l) => l.id === activeInterruptId.value);
    if (active) {
      const timer = timers.get(active.id);
      if (timer !== undefined) {
        clearTimeout(timer);
        timers.delete(active.id);
      }
      sink(active);
    } else {
      activeInterruptId.value = null;
    }
  }

  /** 跨 profile 切换：清队列、清 timer、回 lane 0。 */
  function reset(): void {
    for (const timer of timers.values()) {
      clearTimeout(timer);
    }
    timers.clear();
    interruptQueue.value = [];
    activeInterruptId.value = null;
  }

  return { activeLaneIndex, lanes, badgeCount, mainLaneState, pushInterrupt, dismissActive, reset };
}
