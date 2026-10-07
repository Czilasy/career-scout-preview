import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import path from "node:path";
import ScreenRoundActions from "../ScreenRoundActions.vue";
import type { ScreenPrimaryAction } from "../../screenFlow";

function action(kind: ScreenPrimaryAction["kind"]): ScreenPrimaryAction {
  switch (kind) {
    case "pause": return { kind, label: "暂停筛选" };
    case "continue": return { kind, label: "继续 AI 筛选" };
    case "start": return { kind, label: "开始 AI 筛选" };
    case "recrawl": return { kind, label: "全部重抓" };
    case "pause-recrawl": return { kind, label: "暂停重抓" };
    case "continue-recrawl": return { kind, label: "继续重抓" };
    case "retry-track": return { kind, label: "重试" };
    default: return { kind };
  }
}

const ACTION_IDS = [
  "pause-ai-screen",
  "continue-ai-screen",
  "start-ai-screen",
  "finish-save-results",
  "view-screen-results",
  "pause-recrawl",
  "continue-recrawl",
];

function visibleButtons(wrapper: ReturnType<typeof mount>): string[] {
  return ACTION_IDS
    .filter((id) => wrapper.find(`[data-testid="${id}"]`).exists())
    .map((id) => wrapper.get(`[data-testid="${id}"]`).text());
}

describe("ScreenRoundActions 按钮矩阵", () => {
  it("运行中：暂停筛选 + 结束并保存结果，最多 2 个动作按钮", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("pause"), showFinishSave: true },
    });
    const buttons = visibleButtons(wrapper);
    expect(buttons).toHaveLength(2);
    expect(buttons.join("|")).toContain("暂停筛选");
    expect(buttons.join("|")).toContain("结束并保存结果");
    expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
  });

  it("暂停/失败：继续 AI 筛选 + 结束并保存结果，无查看结果", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("continue"), showFinishSave: true },
    });
    const buttons = visibleButtons(wrapper);
    expect(buttons).toHaveLength(2);
    expect(wrapper.get('[data-testid="continue-ai-screen"]').text()).toContain("继续 AI 筛选");
    expect(wrapper.get('[data-testid="finish-save-results"]').text()).toContain("结束并保存结果");
    expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
  });

  it("结束保存/关闭态：0 个动作按钮", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: { kind: "none" }, showFinishSave: false },
    });
    expect(wrapper.findAll("button")).toHaveLength(0);
    expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
  });

  it("任意给定组合的可见动作按钮不超过 2 个", () => {
    const cases: Array<{ action: ScreenPrimaryAction; showFinishSave?: boolean }> = [
      { action: action("pause"), showFinishSave: true },
      { action: action("continue"), showFinishSave: true },
      { action: action("start") },
      { action: { kind: "none" } },
      { action: action("pause-recrawl"), showFinishSave: true },
      { action: action("continue-recrawl"), showFinishSave: true },
    ];
    for (const props of cases) {
      const wrapper = mount(ScreenRoundActions, { props });
      expect(visibleButtons(wrapper).length).toBeLessThanOrEqual(2);
    }
  });

  it("renders only the primary AI action while running without finish save", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("pause") },
    });
    expect(wrapper.get('[data-testid="pause-ai-screen"]').text()).toContain("暂停筛选");
    expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(false);
  });

  it("shows spinner and save label on finish while finish is busy", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("continue"),
        busy: true,
        busyAction: "finish",
        showFinishSave: true,
      },
    });
    const finish = wrapper.get('[data-testid="finish-save-results"]');
    expect(finish.attributes("disabled")).toBeDefined();
    expect(finish.text()).toContain("正在保存…");
    expect(finish.find(".spin").exists()).toBe(true);
    expect(wrapper.get('[data-testid="continue-ai-screen"]').text()).not.toContain("正在继续…");
  });

  it.each([
    { label: "主动作", busy: true, busyAction: "pause" },
    { label: "结束保存", finishBusy: true },
    { label: "终止", cancelBusy: true },
  ])("$label进行时禁用全部共享动作", ({ busy, busyAction, finishBusy, cancelBusy }) => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("pause"),
        busy,
        busyAction,
        finishBusy,
        cancelBusy,
        showFinishSave: true,
        showCancel: true,
      },
    });

    expect(wrapper.findAll("button")).toHaveLength(3);
    expect(wrapper.findAll("button").every((button) => button.attributes("disabled") !== undefined)).toBe(true);
  });

  it("shows busy label and disables the primary button during pause", async () => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("pause"),
        busy: true,
        busyLabel: "正在暂停…",
      },
    });
    const button = wrapper.get('[data-testid="pause-ai-screen"]');
    expect(button.attributes("disabled")).toBeDefined();
    expect(button.text()).toContain("正在暂停…");
  });

  it("renders recrawl running controls with finish save", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("pause-recrawl"),
        showFinishSave: true,
      },
    });
    expect(wrapper.get('[data-testid="pause-recrawl"]').text()).toContain("暂停重抓");
    expect(wrapper.get('[data-testid="finish-save-results"]').text()).toContain("结束并保存结果");
    expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
  });

  it.each([
    { label: "screen pause", kind: "pause" as const, ids: ["pause-ai-screen", "finish-save-results"] },
    { label: "screen continue", kind: "continue" as const, ids: ["continue-ai-screen", "finish-save-results"] },
    { label: "recrawl pause", kind: "pause-recrawl" as const, ids: ["finish-save-results", "pause-recrawl"] },
    { label: "recrawl continue", kind: "continue-recrawl" as const, ids: ["finish-save-results", "continue-recrawl"] },
  ])("maps the shared $label action to its buttons", ({ kind, ids }) => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action(kind), showFinishSave: true },
    });
    expect(
      ACTION_IDS.filter((id) => wrapper.find(`[data-testid="${id}"]`).exists()),
    ).toEqual(ids);
    wrapper.unmount();
  });

  it("emits the matching action event on click", async () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("continue") },
    });
    await wrapper.get('[data-testid="continue-ai-screen"]').trigger("click");
    expect(wrapper.emitted("continue")).toHaveLength(1);
  });

  it("047 C2: failed 轨道渲染独立重试按钮，点击发 retry，不发 finish/cancel", async () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("retry-track"), showFinishSave: false, showCancel: false },
    });
    const retry = wrapper.get('[data-testid="retry-flow-track"]');
    expect(retry.text()).toContain("重试");
    expect(retry.attributes("disabled")).toBeUndefined();
    await retry.trigger("click");
    expect(wrapper.emitted("retry-track")).toHaveLength(1);
    expect(wrapper.emitted("finish-save")).toBeUndefined();
    expect(wrapper.emitted("cancel")).toBeUndefined();
    expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="cancel-scrape"]').exists()).toBe(false);
  });

  it("047 C2: failed 轨统一用 retry-track kind，落 retry-flow-track 测试 id", async () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: { kind: "retry-track", label: "重试" } as const, showFinishSave: false, showCancel: false },
    });
    const retry = wrapper.get('[data-testid="retry-flow-track"]');
    await retry.trigger("click");
    expect(wrapper.emitted("retry-track")).toHaveLength(1);
  });

  it("uses the start-ai-screen test id for fresh rounds", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("start") },
    });
    expect(wrapper.get('[data-testid="start-ai-screen"]').text()).toContain("开始 AI 筛选");
  });
});

// ---------------------------------------------------------------------------
// 046 D-03 / FR-015：「结束并保存结果」与「放弃本轮」是用户在这条线上唯一的收口出路。
// 现场只读（disabled 说的是这一片现场不可再操作，例如本轮已锁定、状态读不到）没有任何
// 条文授权把这两条一起锁掉：锁掉之后用户只能刷新页面。这里把边界钉回主操作一条。
// 负向断言（收尾仍可点）与正向断言（真的在飞时照旧锁住）配对，防止拿忙态锁当借口删掉。
// ---------------------------------------------------------------------------
describe("ScreenRoundActions 现场只读时的收尾出口", () => {
  function readOnlyRound() {
    return mount(ScreenRoundActions, {
      props: {
        action: action("pause"),
        disabled: true,
        showFinishSave: true,
        showCancel: true,
        cancelLabel: "放弃本轮",
      },
    });
  }

  it("主操作被禁用且没有任何操作在飞时，结束并保存与放弃本轮仍可点", async () => {
    const wrapper = readOnlyRound();

    expect(wrapper.get('[data-testid="pause-ai-screen"]').attributes("disabled")).toBeDefined();
    const finish = wrapper.get('[data-testid="finish-save-results"]');
    const cancel = wrapper.get('[data-testid="cancel-scrape"]');
    expect(finish.attributes("disabled")).toBeUndefined();
    expect(cancel.attributes("disabled")).toBeUndefined();

    await finish.trigger("click");
    await cancel.trigger("click");
    expect(wrapper.emitted("finish-save")).toHaveLength(1);
    expect(wrapper.emitted("cancel")).toHaveLength(1);
    wrapper.unmount();
  });

  it.each([
    { label: "主动作在飞", props: { busy: true, busyAction: "pause" } },
    { label: "结束保存在飞", props: { finishBusy: true } },
    { label: "终止在飞", props: { cancelBusy: true } },
  ])("$label时收尾两条按忙态锁住", ({ props }) => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("pause"),
        showFinishSave: true,
        showCancel: true,
        ...props,
      },
    });
    expect(wrapper.get('[data-testid="finish-save-results"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="cancel-scrape"]').attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });
});

// ---------------------------------------------------------------------------
// 047 US4/FR-006 / C5：共享操作区的局部紧凑密度与清楚层级。
// 只钉结构与局部样式作用域：主操作保持 primary；「结束并保存结果」「终止」降为
// 次级形态但保留危险色语义。像素与最终视觉由 T051 真实渲染核对，这里不写像素镜像。
// ---------------------------------------------------------------------------
describe("ScreenRoundActions 047 紧凑层级", () => {
  it("主操作保持 primary，保存与终止是次级形态且仍带危险色", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("pause"),
        showFinishSave: true,
        showCancel: true,
        cancelLabel: "终止本轨",
      },
    });

    const primary = wrapper.get('[data-testid="pause-ai-screen"]');
    expect(primary.classes()).toContain("primary");
    expect(primary.classes()).not.toContain("secondary");

    const finish = wrapper.get('[data-testid="finish-save-results"]');
    expect(finish.classes()).toContain("danger");
    expect(finish.classes()).toContain("secondary");

    const cancel = wrapper.get('[data-testid="cancel-scrape"]');
    expect(cancel.classes()).toContain("danger");
    expect(cancel.classes()).toContain("secondary");
    expect(cancel.text()).toContain("终止本轨");
    wrapper.unmount();
  });

  it("重试轨道按钮与主操作同档，不再套危险色（失败恢复不是破坏动作）", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("retry-track"), showFinishSave: false, showCancel: false },
    });
    const retry = wrapper.get('[data-testid="retry-flow-track"]');
    expect(retry.classes()).toContain("primary");
    expect(retry.classes()).not.toContain("danger");
    wrapper.unmount();
  });

  it("紧凑密度只落在共享操作区内部，且忙态只换文案不增删按钮", () => {
    const css = readFileSync(path.join(__dirname, "../ScreenRoundActions.vue"), "utf8");
    const scoped = css.match(/<style scoped>[\s\S]*?<\/style>/)?.[0] || "";
    expect(scoped).toContain(".screen-round-actions .button");
    expect(scoped).toMatch(/min-height:\s*32px/);
    expect(scoped).not.toMatch(/^button\b/m);
    expect(scoped).not.toMatch(/\.button\s*\{[^}]*min-height:\s*44px/s);

    const running = mount(ScreenRoundActions, {
      props: { action: action("pause"), showFinishSave: true, showCancel: true },
    });
    const before = running.findAll("button").length;
    const busy = mount(ScreenRoundActions, {
      props: {
        action: action("pause"),
        showFinishSave: true,
        showCancel: true,
        busy: true,
        busyAction: "pause",
        busyLabel: "正在暂停…",
      },
    });
    expect(busy.findAll("button").length).toBe(before);
    expect(busy.get('[data-testid="pause-ai-screen"]').text()).toContain("正在暂停…");
    const clickable = busy.findAll("button").filter((b) => b.attributes("disabled") === undefined);
    expect(clickable).toHaveLength(0);
    running.unmount();
    busy.unmount();
  });
});

describe("ScreenRoundActions 047 US4 02/03 局部排列", () => {
  it("命令条/筛选卡动作区的紧凑覆盖只落在 02/03 局部选择器内", () => {
    const css = readFileSync(path.join(__dirname, "../../styles.css"), "utf8");
    const cta = css.match(/\.one-click-cta \{[^}]*\}/s)?.[0] || "";
    expect(cta).toMatch(/min-height:\s*44px/);
    expect(cta).not.toMatch(/min-height:\s*50px/);

    const local = css.match(/\.one-click-secondary-actions \.button,[\s\S]*?\}/)?.[0] || "";
    expect(local).toMatch(/min-height:\s*32px/);
    // 全局 button 基础档没有被这轮改动覆盖。
    const base = css.match(/button\.button \{[^}]*\}/s)?.[0] || "";
    expect(base).toMatch(/min-height:\s*44px/);
    // 没有把紧凑规则写成裸 .button 选择器（那会波及全站）。
    expect(local.startsWith(".one-click-secondary-actions")).toBe(true);
  });
});
