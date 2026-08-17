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
    for (const kind of ["continue"] as const) {
      const wrapper = mount(ScreenRoundActions, {
        props: { action: action(kind), showFinishSave: true },
      });
      const buttons = visibleButtons(wrapper);
      expect(buttons).toHaveLength(2);
      expect(wrapper.get('[data-testid="continue-ai-screen"]').text()).toContain("继续 AI 筛选");
      expect(wrapper.get('[data-testid="finish-save-results"]').text()).toContain("结束并保存结果");
      expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
    }
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
