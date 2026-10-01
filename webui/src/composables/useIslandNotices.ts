// ---------------------------------------------------------------------------
// 037 灵动岛 v3 通知池：旧版通知骨架 + 三项增强。
//
// 037 变更：
// - running 态不再 clearAll（旧版曾主动丢弃 running 数据 → 当前版改为保留，
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
// 既有行为：
// - 终态事件（completed/error/paused 跃迁）仍 upsert 进 notices（panel 历史）。
// - 同 kind 替换（仅终态通知）；scope="history" 不派生；初始 prev=null
//   不弹幽灵；已读会话级。
//
// 046 第五轮：paused 一族的行标题跟着事实走（见 attentionRow）——可恢复的暂停说
// 「任务已暂停」，被服务重启打断的说「任务已中断」，与顶栏胶囊、同一屏阶段卡同一说法；
// 性质由生产端随胶囊传下来，本池不再按 kind 猜第二次。id / 去重 / 落点 / 未读 / 轮播语义不变。
//
// 046 D-06：每一行都登记"它是哪一轮说的话"（轮次身份取现场存档既有的轮次令牌，
// 本池不自己数轮次）。开新一轮换发新身份时，只撤销上一轮的中断/attention 告警族行
// ——running 分支不清池（037：用户可能还没看），没有轮次归属就任何路径都撤不掉旧轮告警。
// 撤销按归属逐行做，不整池清空：本轮刚到达的「结果已加入」等通知必须留下（US3 只提示一次），
// 旧轮的完成历史行也按既有语义留在面板里。
// ---------------------------------------------------------------------------
import { computed, ref, watch, type ComputedRef, type Ref } from "vue";
import { pausedFamilyNoticeTitle, type CapsuleStatusPayload, type DynamicIslandState } from "./useDiscoveryState";

export type IslandNoticeKind = "completed" | "error" | "paused" | "interrupt" | "notice";

/** 随轮次结束而失效的告警族：中断（沉入的旧轮恢复提示）与 attention 派生行
 *  （error / paused）。completed / notice 是历史与本轮新到达的事实，不在此列。 */
const ROUND_SCOPED_KINDS: readonly IslandNoticeKind[] = ["interrupt", "paused", "error"];

export interface IslandNotice {
  id: string;
  kind: IslandNoticeKind;
  title: string;
  detail?: string;
  /** 037：interrupt 行 tone 染色（warning 琥珀 / error 红）；终态 kind 不填。
   *  sinkInterrupt 把 IslandInterruptContent.tone 透传过来，面板按此渲染行边框/背景。 */
  tone?: "warning" | "error";
  /** 037：打断行点击直达目标（"reminders"=提醒抽屉 / "task"=任务页）；
   *  终态通知沿用既有的 task/results/attention。 */
  target: IslandNoticeTarget;
  at: number;
  read: boolean;
  /** 046 D-06：这行通知到达时所属的轮次身份（现场存档的轮次令牌；空串＝当时无轮次身份）。 */
  round?: string;
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
  /** 037：打断沉入——carousel 转完一条后把该打断加入 notices（未读，进 panel）。 */
  sinkInterrupt(notice: Omit<IslandNotice, "at" | "read">): void;
  /** 043：未收尾流程一次性提醒——显式推入一行未读通知（去重由调用方负责）。 */
  pushNotice(notice: Omit<IslandNotice, "at" | "read" | "kind">): void;
  reset(): void;
}

type AttentionContent = Extract<DynamicIslandState, { state: "attention" }>["attention"];

/** 胶囊 attention 有 error/paused/pending 三种；只有前两种进通知池（pending 仅胶囊显示）。
 *  error 的行标题固定；paused 一族的两种性质（用户可恢复的暂停 / 服务重启打断）共用同一条
 *  kind 通道，行标题必须跟着事实走——写死成「任务已暂停」会和同一行的详情、同一屏的阶段卡
 *  与顶栏胶囊互相打脸。性质由判定现场随胶囊传下来（attention.pausedFact），这里复用顶栏那
 *  一份口径（pausedFamilyNoticeTitle），不判第二次状态、也不再写一套中文。 */
function attentionRow(attention: AttentionContent): { kind: IslandNoticeKind; title: string } | null {
  if (attention.kind === "error") return { kind: "error", title: "任务出错" };
  if (attention.kind !== "paused") return null;
  // 生产端没带性质（历史数据与手写桩）时按可恢复暂停说，保持既有行为。
  return { kind: "paused", title: pausedFamilyNoticeTitle(attention.pausedFact ?? "paused") };
}

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
  roundId?: Ref<string>,
): IslandNoticesApi {
  const notices = ref<IslandNotice[]>([]);
  const unreadCount = computed(() => notices.value.filter((n) => !n.read).length);
  let seq = 0;
  let prev: DynamicIslandState | null = null;

  /** 当前轮次身份：没有接线（或未换发身份）时是空串——空串的行不参与撤销。 */
  function currentRound(): string {
    return String(roundId?.value ?? "");
  }

  /** 046 D-06：撤销上一轮留下的告警族行（中断 / attention 派生行）。
   *  只按轮次归属逐行撤，绝不整池清空：本轮新到达的通知、以及旧轮的完成
   *  历史行都必须按既有语义留在面板里。 */
  function revokePreviousRounds(): void {
    const round = currentRound();
    if (!round) return;
    const kept = notices.value.filter(
      (n) => !ROUND_SCOPED_KINDS.includes(n.kind) || n.round === round || !n.round,
    );
    if (kept.length !== notices.value.length) notices.value = kept;
  }

  function upsert(next: IslandNotice): void {
    revokePreviousRounds();
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
    // 037：running 态不再 clearAll（live state 由 useIslandCarousel 直接读
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
        round: currentRound(),
        // read:true（P2-1 裁决）：完成信号已由 pill completed live state 展示，
        // panel 行只是历史；不产生未读，完成后点 pill 直达结果页（FR-008）。
        read: true,
      });
    } else if (next.state === "attention") {
      const row = attentionRow(next.attention);
      if (row) {
        upsert({
          id: makeId(row.kind, ++seq),
          kind: row.kind,
          title: row.title,
          detail: next.attention.message,
          target: "attention",
          at: Date.now(),
          round: currentRound(),
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

  // 046 D-06：轮次身份换发（开新一轮）即撤销上一轮留下的告警族行。
  // flush:sync——必须在下一次状态跃迁进池前完成，否则同一 tick 里旧轮告警
  // 会被读面板的用户看成"本轮还在喊"。
  if (roundId) {
    watch(roundId, () => {
      revokePreviousRounds();
    }, { flush: "sync" });
  }

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

  /** 037：打断沉入——carousel 转完一条后调此方法，把打断加入 panel 未读。
   *  append 而非 upsert：打断是逐条事件流（id 唯一，interrupt-N 递增），
   *  不适用终态通知"同 kind 只保留最新一条"的去重语义（复审 P1-1：
   *  多条打断连沉时 upsert 会互相吞掉，panel 只剩 1 条）。 */
  function sinkInterrupt(notice: Omit<IslandNotice, "at" | "read">): void {
    revokePreviousRounds();
    notices.value = [
      ...notices.value,
      { ...notice, at: Date.now(), read: false, round: currentRound() },
    ];
  }

  /** 043：显式推入一条"一次性提醒"（append；kind 固定 "notice"，未读）。 */
  function pushNotice(notice: Omit<IslandNotice, "at" | "read" | "kind">): void {
    revokePreviousRounds();
    notices.value = [
      ...notices.value,
      { ...notice, kind: "notice", at: Date.now(), read: false, round: currentRound() },
    ];
  }

  function reset(): void {
    prev = null;
    clearAll();
    // profile 切换由 App 调用 reset；先清掉旧胶囊源状态，避免新 profile
    // 的 roundStatus 到达前继续展示旧 profile 的运行进度/结果/错误。
    if (roundStatus.value !== null) roundStatus.value = null;
  }

  return { notices, unreadCount, markAllRead, markRead, markReadBatch, sinkInterrupt, pushNotice, reset };
}
