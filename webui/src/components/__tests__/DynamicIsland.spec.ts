// 037 灵动岛 v2 组件测试（复审 A/B/C 批后修订）：
// - 四态渲染沿用；点击语义分岔（无通知 navigate / 有通知 expand）；
// - 已读语义：展开期间行以未读渲染，收起（backdrop/Escape/collapse/行点击）
//   发 dismiss（App 侧 markAllRead）；
// - WAAPI 弹跳：每次未读增量重播（CSS attribute 递增不会重启动画）；
// - 到达一瞥 peek；焦点移入面板；aria-live 播报。
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import DynamicIsland from "../DynamicIsland.vue";
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

function mountIsland(status: CapsuleStatusPayload | null, notices: IslandNotice[] = [], attach = false) {
  return mount(DynamicIsland, {
    props: { status, notices },
    attachTo: attach ? document.body : undefined,
  });
}

/** jsdom 无 WAAPI：返回结构够 Motion 消费的假 Animation（finished 可等待）。 */
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

describe("DynamicIsland 四态渲染（胶囊本体）", () => {
  it("idle：显示平台名，低调常驻", () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }));
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).toContain("BOSS");
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).not.toContain("匹配");
  });

  it("idle 智联平台显示「智联」", () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "zhilian" }));
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).toContain("智联");
  });

  it("running：显示实时进度数字与呼吸点", () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 128, total: 300 },
    }));
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("抓取 128/300");
    expect(wrapper.get('[data-testid="dynamic-island-running"]').find(".island-live").exists()).toBe(true);
  });

  it("running 筛选态显示筛选进度", () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "screening", done: 45, total: 128 },
    }));
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("筛选 45/128");
  });

  it("running total 未知省略分母（不显示假分母）", () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 12 },
    }));
    const text = wrapper.get('[data-testid="dynamic-island-running"]').text();
    expect(text).toContain("抓取 12");
    expect(text).not.toContain("/");
  });

  it("completed：显示匹配与待确认，待确认>0 标亮", () => {
    const wrapper = mountIsland(makeStatus({
      state: "completed", platform: "boss",
      results: { matched: 5, pending: 2 },
    }));
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("匹配 5");
    expect(el.text()).toContain("待确认 2");
    expect(el.find(".island-pending-dot").exists()).toBe(true);
  });

  it("completed 待确认为 0 时不显示待确认数字", () => {
    const wrapper = mountIsland(makeStatus({
      state: "completed", platform: "boss",
      results: { matched: 5, pending: 0 },
    }));
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("匹配 5");
    expect(el.text()).not.toContain("待确认");
    expect(el.find(".island-pending-dot").exists()).toBe(false);
  });

  it("attention：暂停显示橙色提醒文案", () => {
    const wrapper = mountIsland(makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "paused", message: "任务已暂停，请处理后继续" },
    }));
    const el = wrapper.get('[data-testid="dynamic-island-attention"]');
    expect(el.text()).toContain("任务已暂停");
    expect(el.find(".attention-paused").exists()).toBe(true);
  });

  it("attention：出错显示红色提醒文案", () => {
    const wrapper = mountIsland(makeStatus({
      state: "attention", platform: "boss",
      attention: { kind: "error", message: "任务执行出错" },
    }));
    const el = wrapper.get('[data-testid="dynamic-island-attention"]');
    expect(el.text()).toContain("任务执行出错");
    expect(el.find(".attention-error").exists()).toBe(true);
  });

  it("未筛选轮（phase=scraped）显示「待筛选 N」，与结果页 tab 一致", () => {
    const wrapper = mountIsland(makeStatus(
      { state: "completed", platform: "boss", results: { matched: 90, pending: 0 } },
      { phase: "scraped" },
    ));
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("待筛选 90");
    expect(el.text()).not.toContain("匹配");
  });

  it("已筛选轮（phase=judged）仍显示「匹配 N」", () => {
    const wrapper = mountIsland(makeStatus(
      { state: "completed", platform: "boss", results: { matched: 90, pending: 0 } },
      { phase: "judged" },
    ));
    expect(wrapper.get('[data-testid="dynamic-island-completed"]').text()).toContain("匹配 90");
  });

  it("status 为 null（未上抛）时回退 idle 默认平台", () => {
    const wrapper = mountIsland(null);
    expect(wrapper.find('[data-testid="dynamic-island-idle"]').exists()).toBe(true);
  });

  it("进度数字变化后文本随之更新", async () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 10, total: 100 },
    }));
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("抓取 10/100");
    await wrapper.setProps({
      status: makeStatus({
        state: "running", platform: "boss",
        progress: { phase: "scraping", done: 42, total: 100 },
      }),
    });
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("抓取 42/100");
  });

  it("无未读时 pill 不设 aria-label：动态数字作为可访问名（C2）", () => {
    const wrapper = mountIsland(makeStatus({
      state: "running", platform: "boss",
      progress: { phase: "scraping", done: 12, total: 100 },
    }));
    const pill = wrapper.get('[data-testid="dynamic-island-running"]');
    expect(pill.attributes("aria-label")).toBeUndefined();
    expect(pill.text()).toContain("抓取 12/100");
  });

  it("kaleido 主题冒烟：idle 标签与胶囊正常渲染（C3 覆盖无回归）", async () => {
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

describe("DynamicIsland 通知池联动（037）", () => {
  it("有通知：点击胶囊展开面板 emit expand；面板行以未读态渲染并移焦（B2/C2）", async () => {
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
      // 展开期间未读高亮真实可见（不再被提前 markAllRead 抹掉）。
      expect(row.attributes("data-notice-read")).toBe("false");
      expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(true);
      // 面板 role=dialog + 焦点移入（C2）。
      expect(panel.attributes("role")).toBe("dialog");
      expect(panel.attributes("aria-modal")).toBe("true");
      await nextTick();
      expect(panel.element).toBe(document.activeElement);
    } finally {
      wrapper.unmount();
    }
  });

  it("再点一次胶囊收起面板并 emit dismiss（不重复 expand）", async () => {
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

  it("面板行点击：emit navigate(notice.target) + dismiss 收起", async () => {
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
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
  });

  it("backdrop 点击收起并 emit dismiss（快照=关闭瞬间的通知 id）", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice({ id: "n-a" }), makeNotice({ id: "n-b" })],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    expect(wrapper.find('[data-testid="island-backdrop"]').exists()).toBe(true);
    await wrapper.get('[data-testid="island-backdrop"]').trigger("click");
    expect(wrapper.emitted("dismiss")?.[0]).toEqual([["n-a", "n-b"]]);
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
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

  it("暴露 collapse()：外部（App 开抽屉/profile 切换）可收起并 dismiss", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice()],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    wrapper.vm.collapse();
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("dismiss")).toHaveLength(1);
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
  });

  it("未读红点：数量与 99+ 截断；aria-label 提示未读", () => {
    const many = Array.from({ length: 101 }, (_, i) => makeNotice({ id: `n-${i}` }));
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      many,
    );
    expect(wrapper.get('[data-testid="island-unread"]').text()).toBe("99+");
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').attributes("aria-label")).toContain("未读");

    const wrapper3 = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice(), makeNotice(), makeNotice()],
    );
    expect(wrapper3.get('[data-testid="island-unread"]').text()).toBe("3");
  });

  it("全部已读时胶囊不显示未读红点，aria-label 回退为内部文本（C2）", () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice({ read: true })],
    );
    expect(wrapper.find('[data-testid="island-unread"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').attributes("aria-label")).toBeUndefined();
  });

  it("已读通知全部消费后：点击胶囊回到直达导航（复审三 §13），不再弹面板", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "completed", platform: "boss", results: { matched: 3, pending: 1 } }),
      [makeNotice({ read: true })],
    );
    await wrapper.get('[data-testid="dynamic-island-completed"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["results"]);
    expect(wrapper.emitted("expand")).toBeUndefined();
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
  });

  it("有未读时点击展开面板（即使存在其它已读通知）", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "completed", platform: "boss", results: { matched: 3, pending: 1 } }),
      [makeNotice({ read: true }), makeNotice({ kind: "error", title: "任务出错", read: false })],
    );
    await wrapper.get('[data-testid="dynamic-island-completed"]').trigger("click");
    expect(wrapper.emitted("expand")).toHaveLength(1);
    expect(wrapper.emitted("navigate")).toBeUndefined();
  });
});

describe("DynamicIsland 动画与播报（037 复审 A2/B4）", () => {
  it("通知到达（未读增量）：一瞥摘要出现、aria-live 播报、2.2s 后收回", async () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }), []);
    await wrapper.setProps({ notices: [makeNotice({ title: "任务出错", detail: "网络断连" })] });
    expect(wrapper.get('[data-testid="island-peek"]').text()).toContain("任务出错 · 网络断连");
    expect(wrapper.get('[data-testid="island-sr-live"]').text()).toBe("任务出错：网络断连");
    wrapper.unmount(); // 清 peek timer
  });

  it("面板已展开时新通知到达不弹一瞥（直接看面板）", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice()],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    await wrapper.setProps({ notices: [makeNotice(), makeNotice({ title: "第二条", detail: undefined })] });
    expect(wrapper.find('[data-testid="island-peek"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("未读→未读的同 kind 内容更新也触发一瞥（按新未读 id 而非计数，N3）", async () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }), []);
    await wrapper.setProps({ notices: [makeNotice({ id: "c1", detail: "匹配 5 · 待确认 2" })] });
    expect(wrapper.get('[data-testid="island-peek"]').text()).toContain("匹配 5");
    // 同 kind 替换（未读数 1→1 不变）：内容变了 = 新通知，peek 跟进最新内容。
    await wrapper.setProps({ notices: [makeNotice({ id: "c2", detail: "匹配 6 · 待确认 3" })] });
    expect(wrapper.get('[data-testid="island-peek"]').text()).toContain("匹配 6");
    expect(wrapper.get('[data-testid="island-peek"]').text()).not.toContain("匹配 5");
    wrapper.unmount();
  });

  it("展开面板会清掉 pill 上挂着的一瞥（点开即已看，N3/复审三 §8）", async () => {
    const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }), []);
    await wrapper.setProps({ notices: [makeNotice({ title: "任务出错", detail: "网络断连" })] });
    expect(wrapper.find('[data-testid="island-peek"]').exists()).toBe(true);
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    expect(wrapper.find('[data-testid="island-peek"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("一瞥 2.2s 后自动收回，胶囊恢复常态文案", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }), []);
      await wrapper.setProps({ notices: [makeNotice({ title: "任务出错", detail: "网络断连" })] });
      expect(wrapper.get('[data-testid="island-peek"]').text()).toContain("任务出错");
      vi.advanceTimersByTime(2300);
      await wrapper.vm.$nextTick();
      expect(wrapper.find('[data-testid="island-peek"]').exists()).toBe(false);
      expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).toContain("BOSS");
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("展开面板时写入 --panel-top（B1 窄屏 fixed 定位参数）", async () => {
    const wrapper = mountIsland(
      makeStatus({ state: "idle", platform: "boss" }),
      [makeNotice()],
    );
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    const anchor = wrapper.get(".island-anchor").element as HTMLElement;
    expect(anchor.style.getPropertyValue("--panel-top")).toMatch(/px$/);
    wrapper.unmount();
  });

  it("reduce-motion 下（默认）：弹跳不调用 WAAPI（playPop 短路）", async () => {
    const animateSpy = vi.spyOn(Element.prototype, "animate")
      .mockImplementation(function () {
        return fakeAnimation();
      });
    try {
      const wrapper = mountIsland(makeStatus({ state: "idle", platform: "boss" }), []);
      await wrapper.setProps({ notices: [makeNotice()] });
      expect(animateSpy).not.toHaveBeenCalled();
      wrapper.unmount();
    } finally {
      animateSpy.mockRestore();
    }
  });

  it("动画开启态：每次未读增量重播弹跳（WAAPI 两次调用，CSS 一次性 bug 修复）", async () => {
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
      await wrapper.setProps({ notices: [makeNotice({ id: "n1" }), makeNotice({ id: "n2" })] });
      const pops = calls.filter((c) => c.el === pill);
      expect(pops).toHaveLength(2);
      wrapper.unmount();
    } finally {
      animateSpy.mockRestore();
      (globalThis as any).__setReducedMotionMatchMedia(true);
    }
  });

  it("动画开启态：收起走两阶段退场（先 leaving 再卸载）；reduce 下即时卸载", async () => {
    (globalThis as any).__setReducedMotionMatchMedia(false);
    const animateSpy = vi.spyOn(Element.prototype, "animate")
      .mockImplementation(function () {
        return fakeAnimation();
      });
    try {
      const wrapper = mountIsland(
        makeStatus({ state: "idle", platform: "boss" }),
        [makeNotice()],
      );
      await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
      await wrapper.get('[data-testid="island-backdrop"]').trigger("click");
      // leaving 期间面板仍在（退场动画），220ms 后才卸载。
      expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(true);
      await new Promise((r) => setTimeout(r, 320));
      expect(wrapper.find('[data-testid="island-notice-panel"]').exists()).toBe(false);
      expect(wrapper.emitted("dismiss")).toHaveLength(1);
      wrapper.unmount();
    } finally {
      animateSpy.mockRestore();
      (globalThis as any).__setReducedMotionMatchMedia(true);
    }
  });
});
