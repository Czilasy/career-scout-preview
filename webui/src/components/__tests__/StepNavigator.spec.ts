import { mount } from "@vue/test-utils";
import StepNavigator from "../StepNavigator.vue";

const steps = [
  { id: "upload", label: "上传简历" },
  { id: "search", label: "广泛抓取" },
  { id: "screen", label: "AI 筛选" },
  { id: "results", label: "查看结果" },
];

describe("StepNavigator", () => {
  it("blocks future steps until their prerequisites are complete", async () => {
    const wrapper = mount(StepNavigator, {
      props: { steps, activeStep: "upload", enabledSteps: ["upload"] },
    });

    const buttons = wrapper.findAll("button");
    expect(buttons).toHaveLength(4);
    expect(buttons[0].attributes("aria-current")).toBe("step");
    expect(buttons[1].attributes("disabled")).toBeDefined();

    await buttons[1].trigger("click");
    expect(wrapper.emitted("select")).toBeUndefined();

    await wrapper.setProps({ enabledSteps: ["upload", "search"] });
    await wrapper.findAll("button")[1].trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["search"]);
  });
});
