// ---------------------------------------------------------------------------
// 037 灵动岛通知池：从 App 层 roundStatus 派生四类通知（跑完/出错/暂停）。
//
// 设计要点：
// - 状态跃迁驱动；同 kind 替换（任意时刻同类最多 1 条）。
// - 进入 running/idle 整体清空（新一轮任务启动或重置）。
// - scope="history"（浏览历史轮）只是展示，不派生通知（037 复审 A3）。
// - 初始 prev=null：不产生通知（避免刷新复活已结束任务时弹幽灵通知）。
// - 已读仅是会话级内存标记（与持久化无关）；新一轮启动 / 显式 reset() 清空。
// - 不发请求；不动后端；不修改 036 的派生体（useDiscoveryState.roundStatusPayload）。
// ---------------------------------------------------------------------------
import { computed, ref, watch, type ComputedRef, type Ref } from "vue";
import type { CapsuleStatusPayload, DynamicIslandState } from "./useDiscoveryState";

export type IslandNoticeKind = "completed" | "error" | "paused";

export interface IslandNotice {
  id: string;
  kind: IslandNoticeKind;
  title: string;
  detail?: string;
  target: "task" | "results" | "attention";
  at: number;
  read: boolean;
}

export interface IslandNoticesApi {
  notices: Ref<IslandNotice[]>;
  unreadCount: ComputedRef<number>;
  markAllRead(): void;
  markRead(id: string): void;
  markReadBatch(ids: readonly string[]): void;
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
    // 跃迁到 running / idle：清空（旧通知全部清）。
    if (next.state === "running" || next.state === "idle") {
      prev = next;
      clearAll();
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
        read: false,
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

  function reset(): void {
    prev = null;
    clearAll();
  }

  return { notices, unreadCount, markAllRead, markRead, markReadBatch, reset };
}