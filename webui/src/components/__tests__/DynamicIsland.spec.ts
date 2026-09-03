// 038 灵动岛 v3 组件测试：
// - 四态渲染沿用（标签 038 更新：正在抓取/AI精筛）+ completed 彩色芯片；
// - carousel 转一次（pushInterrupt → activeLaneIndex 变化）；
// - 红光层（attention 时 data-glow 出现，离开移除）；
// - 038 组件级：宽度 spring data-pill-width / 数字弹动 playPop / labelStack 旧值淡出 / FR-011 主流程数字推进 / P2-1 completed 直达；
// - 037 保留：点击语义/dismiss 快照/焦点圈闭/aria-live 播报。
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";
import DynamicIsland from "../DynamicIsland.vue";
import { useIslandCarousel, type IslandCarouselApi } from "../../composables/useIslandCarousel";
import type { CapsuleStatusPayload, DynamicIslandState } from "../../composables/useDiscoveryState";
import type { IslandNotice } from "../../composables/useIslandNotices";

function makeStatus(
  capsule: DynamicIslandState,
  overrides: Partial<CapsuleStatusPayload> = {},
): CapsuleStatusPayload {
  return {
    platform: capsule.platform,
    phase: "judged",
    judged: 0,
    scope: capsule.platform,
    capsule,
    ...overrides,
  };
}

function makeNotice(overrides: Partial<IslandNotice> = {}): IslandNotice {
  return {
    id: `notice-${Math.random().toString(36).slice(2, 8)}`,
    kind: "completed",
    title: "本轮任务已完成",
    detail: "匹配 5 · 待确认 2",
    target: "results",
    at: Date.now(),
    read: false,
    ...overrides,
  };
}

function mountIsland(
  status: CapsuleStatusPayload | null,
  notices: IslandNotice[] = [],
  attach = false,
) {
  const statusRef = ref(status);
  const carousel = useIslandCarousel(statusRef);
  const wrapper = mount(DynamicIsland, {
    props: { status, notices, carousel },
    attachTo: attach ? document.body : undefined,
  }) as unknown as ReturnType<typeof mount> & {
    statusRef: typeof statusRef;
    carousel: IslandCarouselApi;
  };
  // 暴露 statusRef（模拟 App 的 roundStatus ref，便于测试驱动状态推进）
  // 与 carousel（直接调 pushInterrupt/dismissActive/reset）。
  wrapper.statusRef = statusRef;
  wrapper.carousel = carousel;
  return wrapper;
}

/** Update both the status prop AND the carousel's internal ref (same as App.vue's roundStatus) */
async function updateStatus(wrapper: ReturnType<typeof mountIsland>, status: CapsuleStatusPayload | null) {
  (wrapper as any).statusRef.value = status;
  await wrapper.setProps({ status });
}

function fakeAnimation(): Animation {
  return {
    cancel() {},
    finish() {},
    pause() {},
    play() {},
    reverse() {},
    addEventListener() {},
    removeEventListener() {},
    finished: Promise.resolve(),
    ready: Promise.resolve(),
    currentTime: 0,
    playState: "finished",
  } as unknown as Animation;
}

function backdropEl(): HTMLElement {
  const el = document.querySelector<HTMLElement>('[data-testid="island-backdrop"]');
  if (!el) throw new Error("island-backdrop 未挂载");
  return el;
}
async function clickBackdrop() {
  backdropEl().dispatchEvent(new MouseEvent("click", { bubbles: true }));
  await nextTick();
}

describe("DynamicIsland 四态渲染（胶囊本体）", () => {
  it("idle：显示平台名", () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).toContain("BOSS");
  });

  it("idle 智联平台显示「智联」", () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "zhilian" }));
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).toContain("智联");
  });

  it("running：显示「正在抓取 N/M」+ live dot", () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 128, total: 300 },
    }));
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("正在抓取 128/300");
    expect(wrapper.find(".island-live.phase-scraping").exists()).toBe(true);
  });

  it("running 筛选态显示「AI精筛 N/M」", () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "screening", done: 45, total: 128 },
    }));
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("AI精筛 45/128");
    expect(wrapper.find(".island-live.phase-screening").exists()).toBe(true);
  });

  it("running total 未知省略分母", () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 12 },
    }));
    const text = wrapper.get('[data-testid="dynamic-island-running"]').text();
    expect(text).toContain("正在抓取 12");
    expect(text).not.toContain("/");
  });

  it("completed：显示彩色芯片（匹配绿 + 待确认琥珀）", () => {
    const wrapper = mountIsland(makeStatus({
      state: "completed", platform: "boss",
      results: { matched: 5, pending: 2 },
    }));
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("匹配 5");
    expect(el.text()).toContain("待确认 2");
    expect(el.find(".island-chip.c-green").exists()).toBe(true);
    expect(el.find(".island-chip.c-amber").exists()).toBe(true);
    expect(el.find(".island-pending-dot").exists()).toBe(true);
  });

  it("completed 待确认为 0 时不显示待确认芯片", () => {
    const wrapper = mountIsland(makeStatus({
      state: "completed", platform: "boss",
      results: { matched: 5, pending: 0 },
    }));
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("匹配 5");
    expect(el.text()).not.toContain("待确认");
    expect(el.find(".island-pending-dot").exists()).toBe(false);
    expect(el.find(".island-chip.c-amber").exists()).toBe(false);
  });

  it("attention：暂停显示文案 + 红光层", () => {
    const wrapper = mountIsland(makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "paused", message: "任务已暂停" },
    }));
    const el = wrapper.get('[data-testid="dynamic-island-attention"]');
    expect(el.text()).toContain("任务已暂停");
    expect(el.find(".island-glow[data-glow='paused']").exists()).toBe(true);
    expect(el.attributes("data-glow")).toBe("paused");
  });

  it("attention：出错显示红色红光层", () => {
    const wrapper = mountIsland(makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "任务执行出错" },
    }));
    const el = wrapper.get('[data-testid="dynamic-island-attention"]');
    expect(el.text()).toContain("任务执行出错");
    expect(el.find(".island-glow[data-glow='error']").exists()).toBe(true);
  });

  it("非 attention 态无红光层", () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
    expect(wrapper.find(".island-glow").exists()).toBe(false);
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').attributes("data-glow")).toBeUndefined();
  });

  it("未筛选轮（phase=scraped）显示「待筛选 N」", () => {
    const wrapper = mountIsland(makeStatus(
      { state: "completed", platform: "boss", results: { matched: 90, pending: 0 } },
      { phase: "scraped" },
    ));
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("待筛选 90");
    expect(el.text()).not.toContain("匹配");
  });

  it("status 为 null 时回退 idle", () => {
    const wrapper = mountIsland(null);
    expect(wrapper.find('[data-testid="dynamic-island-idle"]').exists()).toBe(true);
  });

  it("进度数字变化后文本随之更新", async () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 10, total: 100 },
    }));
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("正在抓取 10/100");
    await updateStatus(wrapper, makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 42, total: 100 },
    }));
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("正在抓取 42/100");
  });

  it("无未读时 pill 不设 aria-label", () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 12, total: 100 },
    }));
    const pill = wrapper.get('[data-testid="dynamic-island-running"]');
    expect(pill.attributes("aria-label")).toBeUndefined();
    expect(pill.text()).toContain("正在抓取 12/100");
  });

  it("kaleido 主题冒烟：idle 正常渲染", async () => {
    document.documentElement.setAttribute("data-theme", "kaleido");
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
      expect(wrapper.get('[data-testid="island-idle"]').text()).toContain("BOSS");
      wrapper.unmount();
    } finally {
      document.documentElement.removeAttribute("data-theme");
    }
  });
});

describe("DynamicIsland 点击派发（无通知 → 直达导航）", () => {
  it("idle 点击回主页", async () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["home"]);
  });

  it("running 点击回任务", async () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 1, total: 10 },
    }));
    await wrapper.get('[data-testid="dynamic-island-running"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["task"]);
  });

  it("completed 点击去结果页", async () => {
    const wrapper = mountIsland(makeStatus({
      state: "completed", platform: "boss",
      results: { matched: 3, pending: 1 },
    }));
    await wrapper.get('[data-testid="dynamic-island-completed"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["results"]);
  });

  it("attention 点击去处理现场", async () => {
    const wrapper = mountIsland(makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "paused", message: "任务已暂停" },
    }));
    await wrapper.get('[data-testid="dynamic-island-attention"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["attention"]);
  });
});

describe("DynamicIsland 通知池联动（037 保留）", () => {
  it("有通知：点击胶囊展开面板 emit expand", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } }),
      [makeNotice({ kind: "error", title: "任务出错", detail: "网络断连", target: "attention" })],
      true,
    );
    try {
      expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
      await wrapper.get('[data-testid="dynamic-island-completed"]').trigger("click");
      expect(wrapper.emitted("expand")).toHaveLength(1);
      const panel = wrapper.find('[data-testid="island-notice-panel"]');
      expect(panel.exists()).toBe(true);
      const row = wrapper.get('[data-testid="island-notice-row-error"]');
      expect(row.text()).toContain("网络断连");
      expect(row.attributes("data-notice-read")).toBe("false");
      expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(true);
      expect(panel.attributes("role")).toBe("dialog");
      expect(panel.attributes("aria-modal")).toBe("true");
      await nextTick();
      expect(panel.element).toBe(document.activeElement);
    } finally {
      wrapper.unmount();
    }
  });

  it("再点一次胶囊收起面板并 emit dismiss", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice()],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    expect(wrapper.emitted("expand")).toHaveLength(1);
    expect(wrapper.emitted("dismiss")).toHaveLength(1);
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
  });

  it("面板行点击：emit navigate + dismiss", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [
        makeNotice({ kind: "completed", target: "results" }),
        makeNotice({ kind: "error", title: "任务出错", target: "attention" }),
      ],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    await wrapper.get('[data-testid="island-notice-row-error"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["attention"]);
    expect(wrapper.emitted("dismiss")).toHaveLength(1);
  });

  it("backdrop 点击收起并 emit dismiss", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice({ id: "n-a" }), makeNotice({ id: "n-b" })],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    expect(backdropEl()).toBeTruthy();
    await clickBackdrop();
    expect(wrapper.emitted("dismiss")?.[0]).toEqual([["n-a", "n-b"]]);
  });

  it("Escape 收起面板并 emit dismiss，焦点归还胶囊", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice()],
      true,
    );
    try {
      await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      await wrapper.vm.$nextTick();
      expect(wrapper.emitted("dismiss")).toHaveLength(1);
      expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
      expect(wrapper.get('[data-testid="dynamic-island-idle"]').element).toBe(document.activeElement);
    } finally {
      wrapper.unmount();
    }
  });

  it("暴露 collapse()：外部可收起并 dismiss", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice()],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    (wrapper.vm as unknown as { collapse: () => void }).collapse();
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("dismiss")).toHaveLength(1);
  });

  it("未读红点：数量与 99+ 截断", () => {
    const many = Array.from({ length: 101 }, (_, i) => makeNotice({ id: `n-${i}` }));
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      many,
    );
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("99+");
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').attributes("aria-label")).toContain("未读");
  });

  it("全部已读时胶囊不显示未读红点", () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice({ read: true })],
    );
    expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').attributes("aria-label")).toBeUndefined();
  });

  it("已读通知全部消费后：点击胶囊回到直达导航", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "completed", platform: "boss", results: { matched: 3, pending: 1 } }),
      [makeNotice({ read: true })],
    );
    await wrapper.get('[data-testid="dynamic-island-completed"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["results"]);
    expect(wrapper.emitted("expand")).toBeUndefined();
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
  });
});

describe("DynamicIsland 038 carousel + 红光 + 芯片", () => {
  it("reduce-motion 下（默认）：弹跳不调用 WAAPI", async () => {
    const animateSpy = vi.spyOn(Element.prototype, "animate")
      .mockImplementation(function () { return fakeAnimation(); });
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }), []);
      await wrapper.setProps({ notices: [makeNotice()] });
      expect(animateSpy).not.toHaveBeenCalled();
      wrapper.unmount();
    } finally {
      animateSpy.mockRestore();
    }
  });

  it("动画开启态：通知到达重播弹跳", async () => {
    (globalThis as any).__setReducedMotionMatchMedia(false);
    const calls: Array<{ el: Element }> = [];
    const animateSpy = vi.spyOn(Element.prototype, "animate")
      .mockImplementation(function (this: Element) {
        calls.push({ el: this });
        return { cancel() {} } as Animation;
      });
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }), []);
      const pill = wrapper.get('[data-testid="dynamic-island-idle"]').element;
      await wrapper.setProps({ notices: [makeNotice({ id: "n1" })] });
      const pops = calls.filter((c) => c.el === pill);
      expect(pops.length).toBeGreaterThanOrEqual(1);
      wrapper.unmount();
    } finally {
      animateSpy.mockRestore();
      (globalThis as any).__setReducedMotionMatchMedia(true);
    }
  });

  it("展开面板写入 --panel-top（窄屏参数）", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice()],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    const anchor = wrapper.get(".island-anchor").element as HTMLElement;
    expect(anchor.style.getPropertyValue("--panel-top")).toMatch(/px$/);
    wrapper.unmount();
  });

  it("动画开启态：收起走两阶段退场；reduce 下即时卸载", async () => {
    (globalThis as any).__setReducedMotionMatchMedia(false);
    const animateSpy = vi.spyOn(Element.prototype, "animate")
      .mockImplementation(function () { return fakeAnimation(); });
    vi.useFakeTimers();
    try {
      const wrapper = mountIsland(
        makeStatus({ state: "idle", platform: "boss" }),
        [makeNotice()],
      );
      await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
      await clickBackdrop();
      expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(true);
      await vi.advanceTimersByTimeAsync(230);
      await wrapper.vm.$nextTick();
      expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
      expect(wrapper.emitted("dismiss")).toHaveLength(1);
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
      animateSpy.mockRestore();
      (globalThis as any).__setReducedMotionMatchMedia(true);
    }
  });

  it("038 completed 芯片：匹配绿 + 待确认琥珀（kaleido 适配）", async () => {
    document.documentElement.setAttribute("data-theme", "kaleido");
    try {
      const wrapper = mountIsland(makeStatus({
        state: "completed", platform: "boss",
        results: { matched: 7, pending: 3 },
      }));
      const el = wrapper.get('[data-testid="dynamic-island-completed"]');
      expect(el.find(".island-chip.c-green").exists()).toBe(true);
      expect(el.find(".island-chip.c-amber").exists()).toBe(true);
      wrapper.unmount();
    } finally {
      document.documentElement.removeAttribute("data-theme");
    }
  });

  it("038 红光层：attention 时出现，离开时移除", async () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 5, total: 10 },
    }));
    expect(wrapper.find(".island-glow").exists()).toBe(false);

    await updateStatus(wrapper, makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "出错" },
    }));
    expect(wrapper.find(".island-glow[data-glow='error']").exists()).toBe(true);

    await updateStatus(wrapper, makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 6, total: 10 },
    }));
    expect(wrapper.find(".island-glow").exists()).toBe(false);
    wrapper.unmount();
  });
});

describe("DynamicIsland 038 组件级 carousel + 宽度 spring + 数字弹动", () => {
  it("pushInterrupt → interrupt lane 渲染（title/detail/data-tone）+ activeLaneIndex 切换", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
      await nextTick();
      expect(wrapper.carousel.activeLaneIndex.value).toBe(0);
      expect(wrapper.find(".island-lane-interrupt").exists()).toBe(false);

      wrapper.carousel.pushInterrupt({
        content: {
          title: "投递提醒",
          detail: "新提醒 3 条",
          tone: "warning",
          target: "reminders",
        },
        duration: 100000,
      });
      await nextTick();

      expect(wrapper.carousel.activeLaneIndex.value).toBe(1);
      const lane = wrapper.find(".island-lane-interrupt");
      expect(lane.exists()).toBe(true);
      expect(lane.attributes("data-tone")).toBe("warning");
      expect(lane.text()).toContain("投递提醒");
      expect(lane.text()).toContain("新提醒 3 条");
      wrapper.carousel.reset();
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("FR-011 硬不变式：打断展示期间主流程数字继续推进，转回主流程显示新值", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = mountIsland(makeStatus({
        state: "running", platform: "boss",
        progress: { phase: "scraping", done: 10, total: 100 },
      }));
      await nextTick();

      // 打断切到 lane 1，但 mainLaneState 仍读 roundStatus，永不冻结
      wrapper.carousel.pushInterrupt({
        content: { title: "投递提醒", tone: "warning", target: "reminders" },
        duration: 100,
      });
      await nextTick();
      expect(wrapper.carousel.activeLaneIndex.value).toBe(1);

      // 打断展示期间主流程数字推进到 20
      await updateStatus(wrapper, makeStatus({
        state: "running", platform: "boss",
        progress: { phase: "scraping", done: 20, total: 100 },
      }));
      await nextTick();
      // 仍在 interrupt lane，但 mainLaneState 已是新值
      expect(wrapper.carousel.mainLaneState.value.done).toBe(20);

      // timer 到点 → interrupt 沉入 → 回 lane 0 → 显示新值
      await vi.advanceTimersByTimeAsync(100);
      await nextTick();
      expect(wrapper.carousel.activeLaneIndex.value).toBe(0);
      expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("正在抓取 20/100");
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("badgeCount 增长触发 playPop（reduced=false）", async () => {
    (globalThis as any).__setReducedMotionMatchMedia(false);
    const calls: Array<{ el: Element }> = [];
    const animateSpy = vi.spyOn(Element.prototype, "animate")
      .mockImplementation(function (this: Element) {
        calls.push({ el: this });
        return fakeAnimation();
      });
    vi.useFakeTimers();
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
      const pill = wrapper.get('[data-testid="dynamic-island-idle"]').element;
      await nextTick();

      // 第一次 push 被 badgeBoot 消费（建立基线）
      wrapper.carousel.pushInterrupt({
        content: { title: "A", tone: "warning", target: "reminders" },
        duration: 100000,
      });
      await nextTick();
      calls.length = 0;

      // 第二次 push → badgeCount 1→2 → playPop
      wrapper.carousel.pushInterrupt({
        content: { title: "B", tone: "warning", target: "reminders" },
        duration: 100000,
      });
      await nextTick();

      const pops = calls.filter((c) => c.el === pill);
      expect(pops.length).toBeGreaterThanOrEqual(1);
      wrapper.carousel.reset();
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
      animateSpy.mockRestore();
      (globalThis as any).__setReducedMotionMatchMedia(true);
    }
  });

  it("SC-001：数字变化触发 playPop（reduced=false）", async () => {
    (globalThis as any).__setReducedMotionMatchMedia(false);
    const calls: Array<{ el: Element }> = [];
    const animateSpy = vi.spyOn(Element.prototype, "animate")
      .mockImplementation(function (this: Element) {
        calls.push({ el: this });
        return fakeAnimation();
      });
    try {
      const wrapper = mountIsland(makeStatus({
        state: "running", platform: "boss",
        progress: { phase: "scraping", done: 10, total: 100 },
      }));
      const pill = wrapper.get('[data-testid="dynamic-island-running"]').element;
      await nextTick();
      calls.length = 0;

      // done 10 → 20 触发 liveDone watch → playPop
      await updateStatus(wrapper, makeStatus({
        state: "running", platform: "boss",
        progress: { phase: "scraping", done: 20, total: 100 },
      }));
      await nextTick();

      const pops = calls.filter((c) => c.el === pill);
      expect(pops.length).toBeGreaterThanOrEqual(1);
      wrapper.unmount();
    } finally {
      animateSpy.mockRestore();
      (globalThis as any).__setReducedMotionMatchMedia(true);
    }
  });

  it("SC-001：labelStack 旧值上滑淡出，400ms 后出栈", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = mountIsland(makeStatus({
        state: "running", platform: "boss",
        progress: { phase: "scraping", done: 10, total: 100 },
      }));
      await nextTick();

      await updateStatus(wrapper, makeStatus({
        state: "running", platform: "boss",
        progress: { phase: "scraping", done: 20, total: 100 },
      }));
      await nextTick();

      const out = wrapper.findAll(".island-value.is-value-out");
      expect(out.length).toBeGreaterThan(0);
      expect(out[0].text()).toContain("正在抓取 10/100");

      // 新值正常显示
      expect(wrapper.get('[data-testid="island-running-value"]').text()).toContain("正在抓取 20/100");

      // 400ms 后旧值出栈
      await vi.advanceTimersByTimeAsync(400);
      await nextTick();
      expect(wrapper.findAll(".island-value.is-value-out").length).toBe(0);
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  // 038 复审：JD 抓取（phase=jd）必须有专属文案与圆点色，旧版与 AI 精筛
  // 共用 "AI精筛" 文案（用户实测：抓 JD 时显示成 AI 精筛）。
  it("038 复审：JD 抓取阶段显示「抓取 JD」文案 + phase-jd 圆点", async () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "jd", done: 7, total: 20 },
    }));
    await nextTick();
    expect(wrapper.get('[data-testid="island-running-value"]').text()).toContain("抓取 JD 7/20");
    expect(wrapper.get(".island-live").classes()).toContain("phase-jd");
    wrapper.unmount();
  });

  it("FR-007 宽度 spring：data-pill-width 随角标出现重算（增宽）", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
      const pill = wrapper.get('[data-testid="dynamic-island-idle"]');
      await nextTick();
      // onMounted remeasureWidth 已跑：pillWidth = max(0+32, 56) = 56
      const initial = pill.attributes("data-pill-width");
      expect(initial).toBeTruthy();
      expect(Number(initial)).toBeGreaterThanOrEqual(56);

      // pushInterrupt → badgeCount 1 → unread 1 → remeasureWidth 增 BADGE_W=30
      wrapper.carousel.pushInterrupt({
        content: { title: "投递提醒", tone: "warning", target: "reminders" },
        duration: 100000,
      });
      // watch(contentKey, unread, flush:post) → await nextTick → remeasureWidth → DOM
      await nextTick();
      await nextTick();
      await nextTick();

      const after = pill.attributes("data-pill-width");
      expect(after).toBeTruthy();
      expect(Number(after)).toBeGreaterThan(Number(initial));
      wrapper.carousel.reset();
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("P2-1 回归：completed + read:true 通知，点 pill 直达 results（不展开 panel）", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "completed", platform: "boss", results: { matched: 5, pending: 2 } }),
      [makeNotice({ kind: "completed", read: true, target: "results" })],
    );
    await wrapper.get('[data-testid="dynamic-island-completed"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["results"]);
    expect(wrapper.emitted("expand")).toBeUndefined();
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("reduce-motion 下 carousel 状态机仍工作（push → sink → 回 lane 0）", async () => {
    // setup.ts 默认 reduced=true，无需手动设置
    vi.useFakeTimers();
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
      await nextTick();
      expect(wrapper.carousel.activeLaneIndex.value).toBe(0);

      wrapper.carousel.pushInterrupt({
        content: { title: "投递提醒", tone: "warning", target: "reminders" },
        duration: 50,
      });
      await nextTick();
      expect(wrapper.carousel.activeLaneIndex.value).toBe(1);
      expect(wrapper.find(".island-lane-interrupt").exists()).toBe(true);

      // timer 到点 → sink → 回 lane 0
      await vi.advanceTimersByTimeAsync(50);
      await nextTick();
      expect(wrapper.carousel.activeLaneIndex.value).toBe(0);
      expect(wrapper.find(".island-lane-interrupt").exists()).toBe(false);
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});
