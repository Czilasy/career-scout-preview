import { mount } from "@vue/test-utils";
import ExecutionModeSelector from "../ExecutionModeSelector.vue";

describe("ExecutionModeSelector", () => {
  it("renders exactly the three system modes and recent custom", () => {
    const wrapper = mount(ExecutionModeSelector, {
      props: { modelValue: "balanced" },
    });

    const controls = wrapper.findAll('[role="radio"]');
    expect(controls.map((item) => item.text())).toEqual([
      "稳定", "平衡", "极限", "自定义",
    ]);
    expect(wrapper.get('[data-mode="balanced"]').attributes("aria-checked")).toBe("true");
  });

  it("emits only the selected mode and never a pages field", async () => {
    const wrapper = mount(ExecutionModeSelector, {
      props: { modelValue: "custom" },
    });

    await wrapper.get('[data-mode="stable"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([["stable"]]);
    expect(JSON.stringify(wrapper.emitted())).not.toContain("pages");
  });

  it("disables every selection while scope is locked", () => {
    const wrapper = mount(ExecutionModeSelector, {
      props: { modelValue: "stable", disabled: true },
    });

    for (const control of wrapper.findAll("button")) {
      expect(control.attributes()).toHaveProperty("disabled");
    }
  });

  it.each([
    ["stable", "mode-stable"],
    ["balanced", "mode-balanced"],
    ["extreme", "mode-extreme"],
    ["custom", "mode-custom"],
  ] as const)("024 配色：%s 档激活按钮带 %s 类", (mode, expectedClass) => {
    const wrapper = mount(ExecutionModeSelector, {
      props: { modelValue: mode },
    });
    const active = wrapper.get(`[data-mode="${mode}"]`);
    expect(active.classes()).toContain("active");
    expect(active.classes()).toContain(expectedClass);
  });
});
