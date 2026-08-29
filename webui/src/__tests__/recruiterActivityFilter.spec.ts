import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import OneClickScreenDialog from "../components/OneClickScreenDialog.vue";
import { singleSelectNextValue } from "../discovery";

// 028 第 7 类「招聘者上次活跃」：单选交互（点新值替换、点已选值取消）。

describe("singleSelectNextValue", () => {
  it("selects a new code by replacing the old one", () => {
    expect(singleSelectNextValue(false, ["week"], "month")).toEqual(["month"]);
  });

  it("clears when clicking the selected code", () => {
    expect(singleSelectNextValue(false, ["week"], "week")).toEqual([]);
  });

  it("keeps selection replaceable across thresholds", () => {
    expect(singleSelectNextValue(false, ["half_year"], "quarter")).toEqual([
      "quarter",
    ]);
  });

  it("returns null for multi-select fields so callers keep add/remove logic", () => {
    expect(singleSelectNextValue(true, ["a"], "b")).toBeNull();
    expect(singleSelectNextValue(undefined, ["a"], "b")).toBeNull();
  });
});

describe("OneClickScreenDialog single-select group", () => {
  const groups = [
    {
      key: "recruiter_activity",
      label: "招聘者上次活跃",
      multiple: false,
      sentinel: null,
      options: [
        ["近一周", "week"],
        ["近一个月", "month"],
      ] as Array<[string, string]>,
    },
    {
      key: "salary",
      label: "薪资范围",
      sentinel: null,
      options: [
        ["10-20K", "405"],
        ["20-50K", "406"],
      ] as Array<[string, string]>,
    },
  ];

  function mountDialog() {
    return mount(OneClickScreenDialog, {
      props: {
        open: true,
        platform: "boss",
        groups,
        modelValue: {},
        hasOldResult: false,
      },
      attachTo: document.body,
    });
  }

  it("replaces the single-select threshold when picking another one", async () => {
    const wrapper = mountDialog();
    const chips = wrapper.findAll("button.choice-chip");
    const week = chips[0];
    const month = chips[1];

    await week.trigger("click");
    expect(week.classes()).toContain("selected");

    await month.trigger("click");
    expect(month.classes()).toContain("selected");
    expect(week.classes()).not.toContain("selected");
    wrapper.unmount();
  });

  it("clears the single-select threshold when clicking it again", async () => {
    const wrapper = mountDialog();
    const week = wrapper.findAll("button.choice-chip")[0];

    await week.trigger("click");
    await week.trigger("click");
    expect(week.classes()).not.toContain("selected");
    wrapper.unmount();
  });

  it("keeps multi-select behaviour for the six legacy fields", async () => {
    const wrapper = mountDialog();
    const chips = wrapper.findAll("button.choice-chip");
    const salary10 = chips[2];
    const salary20 = chips[3];

    await salary10.trigger("click");
    await salary20.trigger("click");
    expect(salary10.classes()).toContain("selected");
    expect(salary20.classes()).toContain("selected");
    wrapper.unmount();
  });
});
