// 037/038 灵动岛通知面板测试。
// 037：排序（error→paused→completed）、已读/未读样式钩子、行点击 emit
// row-click、空态文案、role=dialog 无障碍。动画路径已被 setup.ts 的
// reduced-motion 默认短路（:initial=false 直接渲染终态），只断言结构与语义。
// 038：interrupt kind 排序位 2、tone 染色、Bell 图标、completed counts 时
// 四色 chip 渲染（counts 缺省回退 detail 文字）。
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import IslandNoticePanel from "../IslandNoticePanel.vue";
import type { DynamicIslandState } from "../../composables/useDiscoveryState";
import type { IslandNotice } from "../../composables/useIslandNotices";

const capsule: DynamicIslandState = { state: "idle", platform: "boss" };

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

function mountPanel(notices: IslandNotice[]) {
  return mount(IslandNoticePanel, { props: { notices, capsule } });
}

describe("IslandNoticePanel 渲染", () => {
  it("按优先级排序：error 在前，paused 次之，completed 最后", () => {
    const wrapper = mountPanel([
      makeNotice({ kind: "completed", id: "n-done" }),
      makeNotice({ kind: "paused", id: "n-paused", title: "任务已暂停" }),
      makeNotice({ kind: "error", id: "n-error", title: "任务出错" }),
    ]);
    const rows = wrapper.findAll('[data-testid^="island-notice-row-"]');
    expect(rows.map((r) => r.attributes("data-notice-id"))).toEqual(["n-error", "n-paused", "n-done"]);
  });

  it("显示标题与 detail；无 detail 时省略", () => {
    const wrapper = mountPanel([
      makeNotice({ kind: "error", title: "任务出错", detail: "网络断连" }),
      makeNotice({ kind: "completed", title: "本轮任务已完成", detail: undefined }),
    ]);
    const errorRow = wrapper.get('[data-testid="island-notice-row-error"]');
    expect(errorRow.text()).toContain("任务出错");
    expect(errorRow.text()).toContain("网络断连");

    const doneRow = wrapper.get('[data-testid="island-notice-row-completed"]');
    expect(doneRow.find(".notice-detail").exists()).toBe(false);
  });

  it("未读行带 is-unread 钩子与 data-notice-read=false；已读行淡化", () => {
    const wrapper = mountPanel([
      makeNotice({ kind: "error", id: "n-unread" }),
      makeNotice({ kind: "completed", id: "n-read", read: true }),
    ]);
    const unreadRow = wrapper.get('[data-notice-id="n-unread"]');
    expect(unreadRow.classes()).toContain("is-unread");
    expect(unreadRow.attributes("data-notice-read")).toBe("false");

    const readRow = wrapper.get('[data-notice-id="n-read"]');
    expect(readRow.classes()).not.toContain("is-unread");
    expect(readRow.attributes("data-notice-read")).toBe("true");
  });

  it("空列表显示空态文案", () => {
    const wrapper = mountPanel([]);
    expect(wrapper.get('[data-testid="island-notice-panel"]').text()).toContain("暂无需要关注的事项");
  });
});

describe("IslandNoticePanel 无障碍与退场（037 复审 C 批）", () => {
  it("面板根是 role=dialog + aria-modal + tabindex=-1，供父移焦", () => {
    const wrapper = mountPanel([makeNotice()]);
    const panel = wrapper.get('[data-testid="island-notice-panel"]');
    expect(panel.attributes("role")).toBe("dialog");
    expect(panel.attributes("aria-modal")).toBe("true");
    expect(panel.attributes("aria-label")).toBe("灵动岛通知");
    expect(panel.attributes("tabindex")).toBe("-1");
  });

  it("leaving=true 时根挂 is-leaving（退场两阶段由父驱动）", async () => {
    const wrapper = mountPanel([makeNotice()]);
    expect(wrapper.get('[data-testid="island-notice-panel"]').classes()).not.toContain("is-leaving");
    await wrapper.setProps({ leaving: true });
    expect(wrapper.get('[data-testid="island-notice-panel"]').classes()).toContain("is-leaving");
  });

  it("kaleido 主题冒烟：面板与行正常渲染不崩", async () => {
    document.documentElement.setAttribute("data-theme", "kaleido");
    try {
      const wrapper = mountPanel([makeNotice({ kind: "error", title: "任务出错" })]);
      expect(wrapper.get('[data-testid="island-notice-row-error"]').text()).toContain("任务出错");
    } finally {
      document.documentElement.removeAttribute("data-theme");
    }
  });
});

describe("IslandNoticePanel 行点击", () => {
  it("点击行 emit row-click 携带整条通知", async () => {
    const notice = makeNotice({ kind: "error", target: "attention" });
    const wrapper = mountPanel([notice]);
    await wrapper.get('[data-testid="island-notice-row-error"]').trigger("click");
    expect(wrapper.emitted("row-click")?.[0]).toEqual([notice]);
  });

  it("键盘 Enter / Space 也能触发行点击", async () => {
    const notice = makeNotice({ kind: "completed" });
    const wrapper = mountPanel([notice]);
    await wrapper.get('[data-testid="island-notice-row-completed"]').trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("row-click")).toHaveLength(1);
    await wrapper.get('[data-testid="island-notice-row-completed"]').trigger("keydown", { key: " " });
    expect(wrapper.emitted("row-click")).toHaveLength(2);
  });
});

describe("IslandNoticePanel 038 interrupt 行", () => {
  it("interrupt 排在 paused 与 completed 之间（操作告警优先于终态成功）", () => {
    const wrapper = mountPanel([
      makeNotice({ kind: "completed", id: "n-done", title: "本轮任务已完成" }),
      makeNotice({ kind: "interrupt", id: "n-intr", title: "投递提醒", tone: "warning" }),
      makeNotice({ kind: "paused", id: "n-paused", title: "任务已暂停" }),
      makeNotice({ kind: "error", id: "n-error", title: "任务出错" }),
    ]);
    const rows = wrapper.findAll('[data-testid^="island-notice-row-"]');
    expect(rows.map((r) => r.attributes("data-notice-id"))).toEqual([
      "n-error",
      "n-paused",
      "n-intr",
      "n-done",
    ]);
  });

  it("interrupt 行渲染 Bell 图标（数据 testid 钩子 + 图标组件存在）", () => {
    const wrapper = mountPanel([
      makeNotice({ kind: "interrupt", id: "n-intr", title: "投递提醒", detail: "3条逾期", tone: "warning" }),
    ]);
    const row = wrapper.get('[data-testid="island-notice-row-interrupt"]');
    // 图标渲染为 svg（@lucide/vue 输出 <svg>）；非空即说明 iconFor 映射到了 Bell。
    expect(row.find(".notice-icon svg").exists()).toBe(true);
    expect(row.text()).toContain("投递提醒");
    expect(row.text()).toContain("3条逾期");
  });

  it('warning tone 行挂 data-tone="warning" + 行/图标类钩子', () => {
    const wrapper = mountPanel([
      makeNotice({ kind: "interrupt", id: "n-warn", title: "投递提醒", tone: "warning" }),
    ]);
    const row = wrapper.get('[data-testid="island-notice-row-interrupt"]');
    expect(row.attributes("data-tone")).toBe("warning");
    expect(row.classes()).toContain("notice-interrupt");
    const icon = row.get(".notice-icon");
    expect(icon.attributes("data-tone")).toBe("warning");
    expect(icon.classes()).toContain("notice-interrupt");
  });

  it('error tone 行挂 data-tone="error"', () => {
    const wrapper = mountPanel([
      makeNotice({ kind: "interrupt", id: "n-err", title: "导出失败", tone: "error" }),
    ]);
    const row = wrapper.get('[data-testid="island-notice-row-interrupt"]');
    expect(row.attributes("data-tone")).toBe("error");
  });

  it("interrupt 无 tone 时 data-tone 属性省略（Vue 省略 undefined 绑定）", () => {
    const wrapper = mountPanel([
      makeNotice({ kind: "interrupt", id: "n-plain", title: "提示", detail: "无 tone" }),
    ]);
    const row = wrapper.get('[data-testid="island-notice-row-interrupt"]');
    expect(row.attributes("data-tone")).toBeUndefined();
  });

  it("interrupt 行点击仍 emit row-click（沿用 037 行点击直达）", async () => {
    const notice = makeNotice({ kind: "interrupt", id: "n-intr", title: "投递提醒", target: "task", tone: "warning" });
    const wrapper = mountPanel([notice]);
    await wrapper.get('[data-testid="island-notice-row-interrupt"]').trigger("click");
    expect(wrapper.emitted("row-click")?.[0]).toEqual([notice]);
  });

  it("kaleido 主题下 interrupt tone 行不崩（冒烟）", async () => {
    document.documentElement.setAttribute("data-theme", "kaleido");
    try {
      const wrapper = mountPanel([
        makeNotice({ kind: "interrupt", id: "n-warn", title: "投递提醒", tone: "warning" }),
        makeNotice({ kind: "interrupt", id: "n-err", title: "导出失败", tone: "error" }),
      ]);
      expect(wrapper.get('[data-testid="island-notice-row-interrupt"]').text()).toContain("投递提醒");
    } finally {
      document.documentElement.removeAttribute("data-theme");
    }
  });
});

describe("IslandNoticePanel 038 completed 行（两色现实口径）", () => {
  // 复审 P1-2 裁决：counts 四色 chip 渲染已删（capsule 仅上抛 matched+pending，
  // useDiscoveryState 禁改，全链路无生产数据源）；completed 行回退 detail 文字。
  it("completed 行渲染 detail 文字（不渲染 chip 容器）", () => {
    const wrapper = mountPanel([
      makeNotice({
        kind: "completed",
        id: "n-text",
        title: "本轮任务已完成",
        detail: "匹配 5 · 待确认 2",
      }),
    ]);
    expect(wrapper.find('[data-testid="island-notice-chips"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="island-notice-row-completed"]').text()).toContain("匹配 5 · 待确认 2");
  });
});
