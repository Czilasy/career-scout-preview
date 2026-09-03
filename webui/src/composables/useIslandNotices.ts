// ---------------------------------------------------------------------------
// 038 灵动岛 v3 通知池：037 骨架 + 三项增强。
//
// 038 变更：
// - running 态不再 clearAll（037 L93 主动丢弃 running 数据 → 038 改为保留，
//   running 期间的 live state 由 useIslandCarousel 直接读 roundStatus 派生，
//   useIslandNotices 只负责终态历史 + interrupt 沉入）；idle 仍 clearAll。
// - IslandNoticeKind 增加 "interrupt"（carousel 转完一条打断后沉入此处）。
// - 暴露 sinkInterrupt(notice)：供 useIslandCarousel 的 onSinkInterrupt 回调。
//   沉入走 append（打断有唯一 id，逐条事件流）——不走终态通知的
//   "同 kind 只保留最新一条" upsert（复审 P1-1：多条打断连沉时
//   upsert 会互相吞掉，panel 只剩 1 条，US-3"panel 有 3 条未读"落空）。
// - completed 终态通知 read:true（复审 P2-1 裁决，FR-008 vs FR-013）：
//   "完成"信号已由 pill completed live state（彩色芯片）实时展示，
//   panel 的 completed 行只是历史记录、不算未读——完成后点 pill 不会
//   被"自己刚发的未读通知"拦住，直达结果页（等价被删 toast 的一键直达）。
//   error/paused/interrupt 保持未读（用户没看过的告警仍要角标提示）。
//
// 037 不变：
// - 终态事件（completed/error/paused 跃迁）仍 upsert 进 notices（panel 历史）。
// - 同 kind 替换（仅终态通知）；scope="history" 不派生；初始 prev=null
//   不弹幽灵；已读会话级。
// ---------------------------------------------------------------------------
import { computed, ref, watch, type ComputedRef, type Ref } from "vue";
import type { CapsuleStatusPayload, DynamicIslandState } from "./useDiscoveryState";

export type IslandNoticeKind = "completed" | "error" | "paused" | "interrupt";

export interface IslandNotice {
  id: string;
  kind: IslandNoticeKind;
  title: string;
  detail?: string;
  /** 038：interrupt 行 tone 染色（warning 琥珀 / error 红）；终态 kind 不填。
   *  sinkInterrupt 把 IslandInterruptContent.tone 透传过来，面板按此渲染行边框/背景。 */
  tone?: "warning" | "error";
  /** 038：打断行点击直达目标（"reminders"=提醒抽屉 / "task"=任务页）；
   *  终态通知沿用 037 的 task/results/attention。 */
  target: IslandNoticeTarget;
  at: number;
  read: boolean;
}

/** 通知行 navigate 目标：三个胶囊导航目标 + "reminders"（App 层拦截开提醒抽屉，
 *  requestCapsuleNavigation 不认识它——useDiscoveryState 禁改，分流在 App 做）。 */
export type IslandNoticeTarget = "task" | "results" | "attention" | "reminders";

export interface IslandNoticesApi {
  notices: Ref<IslandNotice[]>;
  unreadCount: ComputedRef<number>;
  markAllRead(): void;
  markRead(id: string): void;
  markReadBatch(ids: readonly string[]): void;
  /** 038：打断沉入——carousel 转完一条后把该打断加入 notices（未读，进 panel）。 */
  sinkInterrupt(notice: Omit<IslandNotice, "at" | "read">): void;
  reset(): void;
}

/** 胶囊 attention 有 error/paused/pending 三种；只有前两种进通知池（pending 仅胶囊显示）。 */
const ATTENTION_KIND: Partial<Record<"error" | "paused" | "pending", { kind: IslandNoticeKind; title: string }>> = {
  error: { kind: "error", title: "任务出错" },
  paused: { kind: "paused", title: "任务已暂停" },
};

/** 跑完通知的 detail：与结果页同源（scraped→待筛选 N；judged→匹配 M · 待确认 P）。 */
function completedDetail(state: Extract<DynamicIslandState, { state: "completed" }>, phase: CapsuleStatusPayload["phase"] | undefined): { detail: string } {
  if (phase === "scraped") return { detail: `待筛选 ${state.results.matched}` };
  const { matched, pending } = state.results;
  return { detail: pending > 0 ? `匹配 ${matched} · 待确认 ${pending}` : `匹配 ${matched}` };
}

function makeId(kind: IslandNoticeKind, seq: number): string {
  return `${kind}-${seq}`;
}

export function createIslandNotices(
  roundStatus: Ref<CapsuleStatusPayload | null>,
): IslandNoticesApi {
  const notices = ref<IslandNotice[]>([]);
  const unreadCount = computed(() => notices.value.filter((n) => !n.read).length);
  let seq = 0;
  let prev: DynamicIslandState | null = null;

  function upsert(next: IslandNotice): void {
    const list = notices.value.slice();
    const idx = list.findIndex((n) => n.kind === next.kind);
    if (idx >= 0) {
      const cur = list[idx];
      // 内容没变：忽略（防 SSE 重连等重复事件把已读通知复活成未读）。
      if (cur.title === next.title && cur.detail === next.detail) return;
      // 内容更新：视为新通知（未读），同 kind 只保留最新一条。
      list[idx] = next;
    } else {
      list.push(next);
    }
    notices.value = list;
  }

  function clearAll(): void {
    if (notices.value.length === 0) return;
    notices.value = [];
  }

  function processTransition(next: DynamicIslandState, phase: CapsuleStatusPayload["phase"] | undefined, scope: CapsuleStatusPayload["scope"] | undefined): void {
    // scope="history"（浏览/切换历史轮）：只是展示，不是完成事件。
    // 必须在 prev==null 分支之前返回且不推进 prev——否则首帧停在历史轮会把
    // prev 污染成 history-completed，回到 live completed（无 running 过渡）时
    // 会误发"本轮任务已完成"幽灵通知（复审二 N1）。
    // 已知边界（复审三 N4）：浏览历史期间 live 任务恰好跑完会被此分支"遮蔽"，
    // 通知顺延到"回到最新"触发 live completed 时补发；若用户在顺延补发前立刻
    // 开新一轮（running 清池）会错过该通知——属延迟+竞态，暂不处理（低频）。
    if (scope === "history") {
      return;
    }
    // 038：running 态不再 clearAll（live state 由 useIslandCarousel 直接读
    // roundStatus 派生，useIslandNotices 只管终态历史 + interrupt 沉入）。
    // idle 态仍清空（无主流程，旧轮终态通知归零）。
    if (next.state === "idle") {
      prev = next;
      clearAll();
      return;
    }
    if (next.state === "running") {
      prev = next;
      // 不 clearAll：running 期间终态通知保留在 panel 历史（用户可能还没看）。
      return;
    }
    // 跃迁到 attention / completed：从初始观察（prev==null）跳过。
    if (prev == null) {
      prev = next;
      return;
    }
    if (next.state === "completed") {
      const { detail } = completedDetail(next, phase);
      upsert({
        id: makeId("completed", ++seq),
        kind: "completed",
        title: "本轮任务已完成",
        detail,
        target: "results",
        at: Date.now(),
        // read:true（P2-1 裁决）：完成信号已由 pill completed live state 展示，
        // panel 行只是历史；不产生未读，完成后点 pill 直达结果页（FR-008）。
        read: true,
      });
    } else if (next.state === "attention") {
      const meta = ATTENTION_KIND[next.attention.kind];
      if (meta) {
        upsert({
          id: makeId(meta.kind, ++seq),
          kind: meta.kind,
          title: meta.title,
          detail: next.attention.message,
          target: "attention",
          at: Date.now(),
          read: false,
        });
      }
    }
    prev = next;
  }

  // 注意观察 capsule（嵌套字段），而不是整个 roundStatus（避免外层无关变化触发）。
  // flush:sync —— 跃迁驱动模型必须逐个状态处理：同一 tick 内 running→completed→idle
  // 若被 pre 批处理合并，会漏掉 completed 通知（且派生只动自身 ref，无重入风险）。
  watch(
    () => roundStatus.value?.capsule ?? null,
    (capsule) => {
      if (!capsule) {
        prev = null;
        clearAll();
        return;
      }
      processTransition(capsule, roundStatus.value?.phase, roundStatus.value?.scope);
    },
    { immediate: true, flush: "sync" },
  );

  function markRead(id: string): void {
    markReadBatch([id]);
  }

  /** 只把给定 id 集合标已读（复审二 N2：dismiss 携带关闭瞬间快照，
   *  关闭窗口期新到达的通知保持未读，不被误吞）。 */
  function markReadBatch(ids: readonly string[]): void {
    if (ids.length === 0) return;
    const wanted = new Set(ids);
    let changed = false;
    const next = notices.value.map((n) => {
      if (!wanted.has(n.id) || n.read) return n;
      changed = true;
      return { ...n, read: true };
    });
    if (changed) notices.value = next;
  }

  function markAllRead(): void {
    markReadBatch(notices.value.map((n) => n.id));
  }

  /** 038：打断沉入——carousel 转完一条后调此方法，把打断加入 panel 未读。
   *  append 而非 upsert：打断是逐条事件流（id 唯一，interrupt-N 递增），
   *  不适用终态通知"同 kind 只保留最新一条"的去重语义（复审 P1-1：
   *  多条打断连沉时 upsert 会互相吞掉，panel 只剩 1 条）。 */
  function sinkInterrupt(notice: Omit<IslandNotice, "at" | "read">): void {
    notices.value = [...notices.value, { ...notice, at: Date.now(), read: false }];
  }

  function reset(): void {
    prev = null;
    clearAll();
  }

  return { notices, unreadCount, markAllRead, markRead, markReadBatch, sinkInterrupt, reset };
}