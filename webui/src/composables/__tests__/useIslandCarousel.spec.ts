// 038 useIslandCarousel 测试：转盘轮播状态机
// - mainLaneState 实时跟随 roundStatus（不冻结，硬不变式 FR-011）
// - pushInterrupt 转 activeLaneIndex 0→1，duration 后回 0 + sink
// - 多条积压：只转最新一条（展示位切到最新 push 的，US-3 复审 P2-2 修正）
// - sticky interrupt 不自动回，dismissActive 才回
// - 展示位被 sticky 占据时：非 sticky 不抢位只入队；新 sticky 直接沉入（P2-3）
// - 打断期间 mainLane done 照常推进
// - reset 清队列回 lane 0
import { describe, expect, it, vi } from "vitest";
import { ref, nextTick } from "vue";
import { useIslandCarousel, type IslandLane } from "../useIslandCarousel";
import type { CapsuleStatusPayload } from "../useDiscoveryState";

function makeStatus(capsule: CapsuleStatusPayload["capsule"], overrides: Partial<CapsuleStatusPayload> = {}): CapsuleStatusPayload {
  return {
    platform: capsule.platform,
    phase: "judged",
    judged: 0,
    scope: capsule.platform,
    capsule,
    ...overrides,
  };
}

function interruptLane(overrides: Partial<Omit<IslandLane, "id" | "type">> = {}) {
  return {
    content: { title: "投递提醒", detail: "3条逾期", tone: "warning" as const },
    duration: 2200,
    ...overrides,
  };
}

describe("useIslandCarousel — mainLaneState 实时跟随（不冻结）", () => {
  it("running done 5→6 时 mainLane 数字变 6（硬不变式 FR-011）", async () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 5, total: 30 } }),
    );
    const { mainLaneState } = useIslandCarousel(status);
    expect(mainLaneState.value.done).toBe(5);
    expect(mainLaneState.value.phase).toBe("scraping");

    status.value = makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 6, total: 30 } });
    await nextTick();
    expect(mainLaneState.value.done).toBe(6);
  });

  it("idle 态派生 phase=idle", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "idle", platform: "boss" }),
    );
    const { mainLaneState } = useIslandCarousel(status);
    expect(mainLaneState.value.phase).toBe("idle");
    expect(mainLaneState.value.platform).toBe("boss");
  });

  it("completed 态派生 counts（matched+pending）", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } }),
    );
    const { mainLaneState } = useIslandCarousel(status);
    expect(mainLaneState.value.phase).toBe("completed");
    expect(mainLaneState.value.counts).toEqual({ matched: 5, pending: 2 });
  });

  it("attention error 态派生 glow=error", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "attention", platform: "boss", attention: { kind: "error", message: "出错" } }),
    );
    const { mainLaneState } = useIslandCarousel(status);
    expect(mainLaneState.value.phase).toBe("attention");
    expect(mainLaneState.value.glow).toBe("error");
  });

  it("attention paused 态派生 glow=paused", () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "attention", platform: "boss", attention: { kind: "paused", message: "已暂停" } }),
    );
    const { mainLaneState } = useIslandCarousel(status);
    expect(mainLaneState.value.glow).toBe("paused");
  });

  it("status 为 null 时回退 idle", () => {
    const status = ref<CapsuleStatusPayload | null>(null);
    const { mainLaneState } = useIslandCarousel(status);
    expect(mainLaneState.value.phase).toBe("idle");
  });

  it("scraping → screening phase 切换", async () => {
    const status = ref<CapsuleStatusPayload | null>(
      makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 10, total: 30 } }),
    );
    const { mainLaneState } = useIslandCarousel(status);
    expect(mainLaneState.value.phase).toBe("scraping");
    status.value = makeStatus({ state: "running", platform: "boss", progress: { phase: "screening", done: 3, total: 30 } });
    await nextTick();
    expect(mainLaneState.value.phase).toBe("screening");
  });
});

describe("useIslandCarousel — pushInterrupt 转一次", () => {
  it("在主流程时 push：activeLaneIndex 0→1，duration 后回 0 + sink", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 5, total: 30 } }),
      );
      const sunk: IslandLane[] = [];
      const { activeLaneIndex, badgeCount, pushInterrupt } = useIslandCarousel(status, {
        onSinkInterrupt: (lane) => { sunk.push(lane); },
      });
      expect(activeLaneIndex.value).toBe(0);
      expect(badgeCount.value).toBe(0);

      pushInterrupt(interruptLane({ duration: 2200 }));
      await nextTick();
      expect(activeLaneIndex.value).toBe(1);
      expect(badgeCount.value).toBe(1);

      vi.advanceTimersByTime(2300);
      await nextTick();
      expect(activeLaneIndex.value).toBe(0);
      expect(badgeCount.value).toBe(0);
      expect(sunk).toHaveLength(1);
      expect(sunk[0].content).toEqual({ title: "投递提醒", detail: "3条逾期", tone: "warning" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("多条积压：连推 3 条只转最新一条（展示位切到最后 push 的，不自动轮播余数）", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 12, total: 30 } }),
      );
      const sunk: IslandLane[] = [];
      const { activeLaneIndex, badgeCount, pushInterrupt, lanes } = useIslandCarousel(status, {
        onSinkInterrupt: (lane) => { sunk.push(lane); },
      });

      // A 用 2200ms；B/C 用 5000ms（错开，验证"只转最新"而非只转 A）
      pushInterrupt(interruptLane({ duration: 2200, content: { title: "A", tone: "warning" } }));
      await nextTick();
      expect(activeLaneIndex.value).toBe(1); // A 到达：转一次展示 A

      pushInterrupt(interruptLane({ duration: 5000, content: { title: "B", tone: "warning" } }));
      await nextTick();
      expect(activeLaneIndex.value).toBe(2); // B 到达：展示位切到最新（B）

      pushInterrupt(interruptLane({ duration: 5000, content: { title: "C", tone: "warning" } }));
      await nextTick();
      expect(activeLaneIndex.value).toBe(3); // C 到达：展示位切到最新（C）
      const activeLane = lanes.value[activeLaneIndex.value];
      expect((activeLane.content as { title: string }).title).toBe("C");

      expect(badgeCount.value).toBe(3); // 3 条都在队列

      // A 的 duration 到 → A 沉入（C 仍展示，不自动轮播、不回 lane 0）
      vi.advanceTimersByTime(2300);
      await nextTick();
      expect(activeLaneIndex.value).toBe(2); // C 移前（A 出队后 index-1），仍展示最新
      expect(sunk).toHaveLength(1);
      expect(sunk[0].content).toEqual({ title: "A", tone: "warning" });
      // B 和 C 的 timer 还在跑，快进
      vi.advanceTimersByTime(5000);
      await nextTick();
      expect(sunk).toHaveLength(3);
      expect(activeLaneIndex.value).toBe(0); // 全部沉入后回主流程
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("useIslandCarousel — sticky interrupt", () => {
  it("sticky interrupt 不自动回，dismissActive 才回", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "idle", platform: "boss" }),
      );
      const sunk: IslandLane[] = [];
      const { activeLaneIndex, pushInterrupt, dismissActive } = useIslandCarousel(status, {
        onSinkInterrupt: (lane) => { sunk.push(lane); },
      });

      pushInterrupt(interruptLane({ sticky: true, content: { title: "严重错误", tone: "error" } }));
      await nextTick();
      expect(activeLaneIndex.value).toBe(1);

      vi.advanceTimersByTime(5000);
      await nextTick();
      expect(activeLaneIndex.value).toBe(1); // sticky 不自动回

      dismissActive();
      await nextTick();
      expect(activeLaneIndex.value).toBe(0);
      expect(sunk).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("sticky 展示中 push 非 sticky：不抢位，只入队 + timer 到点沉入", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "idle", platform: "boss" }),
      );
      const sunk: IslandLane[] = [];
      const { activeLaneIndex, badgeCount, pushInterrupt, dismissActive, lanes } = useIslandCarousel(status, {
        onSinkInterrupt: (lane) => { sunk.push(lane); },
      });

      pushInterrupt(interruptLane({ sticky: true, content: { title: "严重错误", tone: "error" } }));
      await nextTick();
      const stickyLaneId = lanes.value[activeLaneIndex.value]?.id;

      pushInterrupt(interruptLane({ duration: 2200, content: { title: "普通提醒", tone: "warning" } }));
      await nextTick();
      // 不抢 sticky 展示位；仍展示 sticky
      expect(lanes.value[activeLaneIndex.value]?.id).toBe(stickyLaneId);
      expect(badgeCount.value).toBe(2);

      vi.advanceTimersByTime(2300);
      await nextTick();
      // 非 sticky 到点沉入，sticky 仍展示
      expect(sunk).toHaveLength(1);
      expect((sunk[0].content as { title: string }).title).toBe("普通提醒");
      expect(lanes.value[activeLaneIndex.value]?.id).toBe(stickyLaneId);

      dismissActive();
      await nextTick();
      expect(sunk).toHaveLength(2);
      expect(activeLaneIndex.value).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("sticky 展示中再 push sticky：新 sticky 直接沉入 panel，永不滞留队列（P2-3）", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "idle", platform: "boss" }),
      );
      const sunk: IslandLane[] = [];
      const { activeLaneIndex, badgeCount, pushInterrupt } = useIslandCarousel(status, {
        onSinkInterrupt: (lane) => { sunk.push(lane); },
      });

      pushInterrupt(interruptLane({ sticky: true, content: { title: "严重错误", tone: "error" } }));
      await nextTick();

      pushInterrupt(interruptLane({ sticky: true, content: { title: "另一个严重错误", tone: "error" } }));
      await nextTick();
      // 新 sticky 无 timer 也无展示位（sticky 不抢 sticky）——直接沉入 panel
      expect(sunk).toHaveLength(1);
      expect((sunk[0].content as { title: string }).title).toBe("另一个严重错误");
      expect(badgeCount.value).toBe(1); // 队列里只有正在展示的第一条
      expect(activeLaneIndex.value).toBe(1);

      vi.advanceTimersByTime(60000);
      await nextTick();
      expect(sunk).toHaveLength(1); // 无残留 timer 回调
      expect(badgeCount.value).toBe(1); // 无卡死：角标不虚增不滞留
    } finally {
      vi.useRealTimers();
    }
  });

  it("非 sticky 展示中 push sticky：sticky 抢占展示位（需用户处理的优先）", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "idle", platform: "boss" }),
      );
      const sunk: IslandLane[] = [];
      const { activeLaneIndex, pushInterrupt, dismissActive, lanes } = useIslandCarousel(status, {
        onSinkInterrupt: (lane) => { sunk.push(lane); },
      });

      pushInterrupt(interruptLane({ duration: 5000, content: { title: "普通", tone: "warning" } }));
      await nextTick();
      expect((lanes.value[activeLaneIndex.value].content as { title: string }).title).toBe("普通");

      pushInterrupt(interruptLane({ sticky: true, content: { title: "严重", tone: "error" } }));
      await nextTick();
      // sticky 抢占展示位（普通条继续靠自己的 timer 沉入）
      expect((lanes.value[activeLaneIndex.value].content as { title: string }).title).toBe("严重");

      dismissActive();
      await nextTick();
      expect(activeLaneIndex.value).toBe(0); // 不自动轮播普通条
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("useIslandCarousel — 硬不变式（打断期间数据不冻结）", () => {
  it("打断展示期间 mainLane done 照常推进", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 12, total: 30 } }),
      );
      const { mainLaneState, activeLaneIndex, pushInterrupt } = useIslandCarousel(status);

      pushInterrupt(interruptLane({ duration: 2200 }));
      await nextTick();
      expect(activeLaneIndex.value).toBe(1); // 展示打断

      // 打断期间 mainLane 数字推进（roundStatus 照常更新）
      status.value = makeStatus({ state: "running", platform: "boss", progress: { phase: "scraping", done: 13, total: 30 } });
      await nextTick();
      expect(mainLaneState.value.done).toBe(13); // 不冻结！

      vi.advanceTimersByTime(2300);
      await nextTick();
      expect(activeLaneIndex.value).toBe(0); // 回主流程
      // 转回后立即看到新数字
      expect(mainLaneState.value.done).toBe(13);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("useIslandCarousel — reset", () => {
  it("reset 清队列回 lane 0", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "idle", platform: "boss" }),
      );
      const { activeLaneIndex, badgeCount, pushInterrupt, reset } = useIslandCarousel(status);

      pushInterrupt(interruptLane({ sticky: true }));
      // 第二条 sticky 展示位被占 → 直接沉入（P2-3），队列只剩 1 条。
      pushInterrupt(interruptLane({ sticky: true }));
      await nextTick();
      expect(activeLaneIndex.value).toBe(1);
      expect(badgeCount.value).toBe(1);

      reset();
      await nextTick();
      expect(activeLaneIndex.value).toBe(0);
      expect(badgeCount.value).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reset 后 timers 清除（不残留回调）", async () => {
    vi.useFakeTimers();
    try {
      const status = ref<CapsuleStatusPayload | null>(
        makeStatus({ state: "idle", platform: "boss" }),
      );
      const sunk: IslandLane[] = [];
      const { pushInterrupt, reset } = useIslandCarousel(status, {
        onSinkInterrupt: (lane) => { sunk.push(lane); },
      });

      pushInterrupt(interruptLane({ duration: 2200 }));
      reset();
      await nextTick();
      vi.advanceTimersByTime(5000);
      await nextTick();
      expect(sunk).toHaveLength(0); // reset 后 timer 不再 fire
    } finally {
      vi.useRealTimers();
    }
  });
});
