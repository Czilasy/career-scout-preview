import { mount } from "@vue/test-utils";
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
