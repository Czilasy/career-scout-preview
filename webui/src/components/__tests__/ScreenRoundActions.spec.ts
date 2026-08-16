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

describe("ScreenRoundActions", () => {
  it("renders only the primary AI action while running", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("pause") },
    });
    expect(wrapper.get('[data-testid="pause-ai-screen"]').text()).toContain("暂停筛选");
    expect(wrapper.find('[data-testid="view-screen-results"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="finish-save-results"]').exists()).toBe(false);
  });

  it("renders continue plus view results after pause", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("continue"),
        showViewResults: true,
      },
    });
    expect(wrapper.get('[data-testid="continue-ai-screen"]').text()).toContain("继续 AI 筛选");
    expect(wrapper.get('[data-testid="view-screen-results"]').text()).toContain("查看结果");
  });

  it("emits view-results on click", async () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("continue"), showViewResults: true },
    });
    await wrapper.get('[data-testid="view-screen-results"]').trigger("click");
    expect(wrapper.emitted("view-results")).toHaveLength(1);
  });

  it("emits view-results after switching from running props", async () => {
    const wrapper = mount(ScreenRoundActions, {
      props: { action: action("pause"), busy: true },
    });
    await wrapper.setProps({ action: action("continue"), busy: false, showViewResults: true });
    await wrapper.get('[data-testid="view-screen-results"]').trigger("click");
    expect(wrapper.emitted("view-results")).toHaveLength(1);
  });

  it("renders continue plus finish save after failure", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("continue"),
        showFinishSave: true,
      },
    });
    expect(wrapper.find('[data-testid="continue-ai-screen"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="finish-save-results"]').text()).toContain("结束并保存结果");
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
  });

  it("renders recrawl paused controls with view results", () => {
    const wrapper = mount(ScreenRoundActions, {
      props: {
        action: action("continue-recrawl"),
        showViewResults: true,
      },
    });
    expect(wrapper.get('[data-testid="continue-recrawl"]').text()).toContain("继续重抓");
    expect(wrapper.get('[data-testid="view-screen-results"]').text()).toContain("查看结果");
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
