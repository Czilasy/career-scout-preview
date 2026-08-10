import { nextTick } from "vue";
import { mount } from "@vue/test-utils";
import OneClickScreenDialog from "../OneClickScreenDialog.vue";

const groups = [
  {
    key: "salary",
    label: "薪资范围",
    sentinel: { label: "不限", code: "0" },
    options: [
      ["20-50K", "406"],
      ["50-100K", "807"],
    ] as Array<[string, string]>,
  },
  {
    key: "stage",
    label: "融资阶段",
    sentinel: null,
    options: [["B轮", "804"]] as Array<[string, string]>,
  },
];

function mountOpen(overrides: Record<string, unknown> = {}) {
  return mount(OneClickScreenDialog, {
    props: {
      open: true,
      platform: "boss",
      groups,
      modelValue: { salary: ["406"], stage: [] },
      hasOldResult: false,
      ...overrides,
    },
  });
}

function chip(wrapper: ReturnType<typeof mount>, label: string) {
  const button = wrapper.findAll("button").find((item) => item.text() === label);
  if (!button) throw new Error(`chip ${label} not found`);
  return button;
}

describe("OneClickScreenDialog", () => {
  it("renders defaults, sentinel state and old-result replacement hint", async () => {
    const wrapper = mountOpen({ hasOldResult: true });
    await nextTick();

    expect(wrapper.get('[role="dialog"]').classes()).toContain("dialog-lg");
    expect(wrapper.get('[data-testid="one-click-old-result-hint"]').text())
      .toContain("将开始新一轮，当前结果会被替换");
    expect(chip(wrapper, "20-50K").attributes("aria-pressed")).toBe("true");
    expect(chip(wrapper, "不限").attributes("aria-pressed")).toBe("false");
    expect(chip(wrapper, "B轮").attributes("aria-pressed")).toBe("false");
  });

  it("clears a group through the sentinel chip and emits copied fields on confirm", async () => {
    const wrapper = mountOpen();
    await nextTick();

    await chip(wrapper, "不限").trigger("click");
    await chip(wrapper, "B轮").trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");

    const emitted = wrapper.emitted("confirm");
    expect(emitted).toHaveLength(1);
    expect(emitted![0][0]).toEqual({ salary: [], stage: ["804"] });
  });

  it("keeps multi-select values when confirming and emits close on cancel", async () => {
    const wrapper = mountOpen();
    await nextTick();

    await chip(wrapper, "50-100K").trigger("click");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");

    expect(wrapper.emitted("confirm")![0][0]).toEqual({ salary: ["406", "807"], stage: [] });

    await wrapper.get('[data-testid="one-click-cancel"]').trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("shows an empty-state line when the platform schema has no groups", async () => {
    const wrapper = mount(OneClickScreenDialog, {
      props: {
        open: true,
        platform: "zhilian",
        groups: [],
        modelValue: {},
        hasOldResult: false,
      },
    });
    await nextTick();

    expect(wrapper.text()).toContain("当前平台暂无筛选条件，可直接开始。");
    await wrapper.get('[data-testid="one-click-confirm"]').trigger("click");
    expect(wrapper.emitted("confirm")![0][0]).toEqual({});
  });
});
