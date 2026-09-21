// Spec 044 B100 US1：第二页保存动作组件的交互测试。
// 组件只发事件，不直接调接口——"事件有没有按用户动作发出来"是本文件的断言对象。
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SavedSearchPackageSaveActions from "../SavedSearchPackageSaveActions.vue";

function mountActions(props: Partial<{
  defaultName: string;
  currentId: string | null;
  currentName: string;
  busy: boolean;
}> = {}) {
  return mount(SavedSearchPackageSaveActions, {
    props: {
      defaultName: "产品经理 · 上海",
      currentId: null,
      currentName: "",
      busy: false,
      ...props,
    },
    global: { stubs: { Teleport: true } },
  });
}

describe("SavedSearchPackageSaveActions", () => {
  it("首次保存：点击后出现可编辑的默认名称", async () => {
    const wrapper = mountActions();
    await wrapper.get('[data-testid="package-save"]').trigger("click");

    const input = wrapper.get('[data-testid="package-name-input"]');
    expect((input.element as HTMLInputElement).value).toBe("产品经理 · 上海");
  });

  it("确认命名后发出保存事件，名称取用户编辑结果", async () => {
    const wrapper = mountActions();
    await wrapper.get('[data-testid="package-save"]').trigger("click");
    await wrapper.get('[data-testid="package-name-input"]').setValue("  我的配置  ");
    await wrapper.get('[data-testid="package-confirm-save"]').trigger("click");

    expect(wrapper.emitted("save")?.[0]).toEqual(["我的配置"]);
    expect(wrapper.find('[data-testid="package-name-input"]').exists()).toBe(false);
  });

  it("取消命名不发任何事件，也不留下命名面板", async () => {
    const wrapper = mountActions();
    await wrapper.get('[data-testid="package-save"]').trigger("click");
    await wrapper.get('[data-testid="package-cancel-save"]').trigger("click");

    expect(wrapper.emitted("save")).toBeUndefined();
    expect(wrapper.emitted("save-as")).toBeUndefined();
    expect(wrapper.find('[data-testid="package-name-input"]').exists()).toBe(false);
  });

  it("无论是否从已有配置进入，保存都重新打开命名面板并创建新配置", async () => {
    const wrapper = mountActions({ currentId: "pkg-1", currentName: "旧配置" });

    await wrapper.get('[data-testid="package-save"]').trigger("click");
    await wrapper.get('[data-testid="package-name-input"]').setValue("修改后的新配置");
    await wrapper.get('[data-testid="package-confirm-save"]').trigger("click");

    await wrapper.get('[data-testid="package-save"]').trigger("click");

    await wrapper.get('[data-testid="package-name-input"]').setValue("副本配置");
    await wrapper.get('[data-testid="package-confirm-save"]').trigger("click");

    expect(wrapper.emitted("save")).toEqual([["修改后的新配置"], ["副本配置"]]);
  });

  it("只展示保存为常用配置按钮，不展示更新、另存为或当前配置名称", () => {
    const wrapper = mountActions({ currentId: "pkg-1", currentName: "旧配置" });
    expect(wrapper.get('[data-testid="package-save"]').text()).toContain("保存为常用配置");
    expect(wrapper.find('[data-testid="package-save-as"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="package-current-name"]').exists()).toBe(false);
  });

  it("忙碌时保存按钮禁用", () => {
    const wrapper = mountActions({ busy: true });
    expect(
      (wrapper.get('[data-testid="package-save"]').element as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
