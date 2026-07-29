import { mount } from "@vue/test-utils";
import ExecutionModeSelector from "../ExecutionModeSelector.vue";

describe("ExecutionModeSelector", () => {
  it("renders exactly the three system modes and recent custom", () => {
    const wrapper = mount(ExecutionModeSelector, {
      props: { modelValue: "balanced", taskSize: "medium" },
    });

    const controls = wrapper.findAll('[role="radio"]');
    expect(controls.map((item) => item.text())).toEqual([
      "稳定", "平衡", "极限", "自定义",
    ]);
    expect(wrapper.get('[data-mode="balanced"]').attributes("aria-checked")).toBe("true");
    expect(wrapper.get('[data-testid="mode-task-size"]').text()).toContain("中任务");
  });

  it("emits only the selected mode and never a pages field", async () => {
    const wrapper = mount(ExecutionModeSelector, {
      props: { modelValue: "custom", taskSize: "small" },
    });

    await wrapper.get('[data-mode="stable"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([["stable"]]);
    expect(JSON.stringify(wrapper.emitted())).not.toContain("pages");
  });

  it("disables every selection while scope is locked", () => {
    const wrapper = mount(ExecutionModeSelector, {
      props: { modelValue: "stable", taskSize: "large", disabled: true },
    });

    for (const control of wrapper.findAll("button")) {
      expect(control.attributes()).toHaveProperty("disabled");
    }
  });
});
