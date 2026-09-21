// Spec 044 B100 US2/US3：第一页配置包入口与选择框的交互测试。
// 组件不直接请求接口：本文件断言"用户动作 → 事件"是否正确，
// 以及空态/加载态/失败重试是否都有明确出口。
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SavedSearchPackagePicker from "../SavedSearchPackagePicker.vue";
import type { SearchPackageSummary } from "../../types";

function makePackage(id: string, name: string): SearchPackageSummary {
  return {
    id,
    name,
    createdAt: "2026-09-21T10:00:00+08:00",
    updatedAt: "2026-09-21T10:00:00+08:00",
  };
}

function mountPicker(props: Partial<{
  packages: SearchPackageSummary[];
  listBusy: boolean;
  listError: string;
  manageBusy: boolean;
  currentPackageId: string | null;
}> = {}) {
  return mount(SavedSearchPackagePicker, {
    props: {
      packages: [],
      listBusy: false,
      listError: "",
      manageBusy: false,
      currentPackageId: null,
      ...props,
    },
    global: { stubs: { Teleport: true } },
    attachTo: document.body,
  });
}

async function openDialog(wrapper: ReturnType<typeof mountPicker>) {
  await wrapper.get('[data-testid="saved-package-entry"]').trigger("click");
}

describe("SavedSearchPackagePicker 第一页入口（US2）", () => {
  it("入口文案为使用已有配置", () => {
    const wrapper = mountPicker();

    expect(wrapper.get('[data-testid="saved-package-entry"]').text().trim()).toBe("使用已有配置");
  });

  it("显示小入口按钮，点击后请求列表并打开选择框", async () => {
    const wrapper = mountPicker();
    expect(wrapper.find('[data-testid="saved-package-dialog"]').exists()).toBe(false);

    await openDialog(wrapper);

    expect(wrapper.emitted("open")).toHaveLength(1);
    expect(wrapper.find('[data-testid="saved-package-dialog"]').exists()).toBe(true);
  });

  it("展示全部已保存配置包", async () => {
    const wrapper = mountPicker({
      packages: [makePackage("p1", "产品经理 · 上海"), makePackage("p2", "运营 · 北京")],
    });
    await openDialog(wrapper);

    const items = wrapper.findAll('[data-testid="package-item"]');
    expect(items).toHaveLength(2);
    expect(items[0].text()).toContain("产品经理 · 上海");
    expect(items[1].text()).toContain("运营 · 北京");
  });

  it("没有配置包时显示空状态，不诱导自动创建", async () => {
    const wrapper = mountPicker();
    await openDialog(wrapper);

    expect(wrapper.get('[data-testid="package-list-empty"]').text()).toContain("还没有");
    expect(wrapper.find('[data-testid="package-item"]').exists()).toBe(false);
  });

  it("加载中显示加载态", async () => {
    const wrapper = mountPicker({ listBusy: true });
    await openDialog(wrapper);

    expect(wrapper.find('[data-testid="package-list-loading"]').exists()).toBe(true);
  });

  it("列表加载失败保留可重试状态", async () => {
    const wrapper = mountPicker({ listError: "常用配置加载失败，请重试" });
    await openDialog(wrapper);

    expect(wrapper.get('[data-testid="package-list-error"]').text()).toContain("加载失败");
    await wrapper.get('[data-testid="package-list-retry"]').trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
  });

  it("点击某个配置包发出选择事件并关闭选择框", async () => {
    const wrapper = mountPicker({ packages: [makePackage("p1", "产品经理 · 上海")] });
    await openDialog(wrapper);
    await wrapper.get('[data-testid="package-item"]').trigger("click");

    expect(wrapper.emitted("select")?.[0]).toEqual(["p1"]);
    expect(wrapper.find('[data-testid="saved-package-dialog"]').exists()).toBe(false);
  });
});

describe("SavedSearchPackagePicker 管理配置包（US3）", () => {
  it("重命名：输入框预填原名称，确认后发出改名事件", async () => {
    const wrapper = mountPicker({ packages: [makePackage("p1", "产品经理 · 上海")] });
    await openDialog(wrapper);
    await wrapper.get('[data-testid="package-rename"]').trigger("click");

    const input = wrapper.get('[data-testid="package-rename-input"]');
    expect((input.element as HTMLInputElement).value).toBe("产品经理 · 上海");
    await input.setValue("  产品经理（新）  ");
    await wrapper.get('[data-testid="package-rename-confirm"]').trigger("click");

    expect(wrapper.emitted("rename")?.[0]).toEqual(["p1", "产品经理（新）"]);
    expect(wrapper.find('[data-testid="package-rename-input"]').exists()).toBe(false);
  });

  it("重命名取消或名称为空时不发事件", async () => {
    const wrapper = mountPicker({ packages: [makePackage("p1", "产品经理 · 上海")] });
    await openDialog(wrapper);
    await wrapper.get('[data-testid="package-rename"]').trigger("click");
    await wrapper.get('[data-testid="package-rename-cancel"]').trigger("click");
    expect(wrapper.emitted("rename")).toBeUndefined();

    await wrapper.get('[data-testid="package-rename"]').trigger("click");
    await wrapper.get('[data-testid="package-rename-input"]').setValue("   ");
    await wrapper.get('[data-testid="package-rename-confirm"]').trigger("click");
    expect(wrapper.emitted("rename")).toBeUndefined();
  });

  it("删除必须先二次确认；取消不发删除", async () => {
    const wrapper = mountPicker({ packages: [makePackage("p1", "产品经理 · 上海")] });
    await openDialog(wrapper);
    await wrapper.get('[data-testid="package-delete"]').trigger("click");

    expect(wrapper.find('[data-testid="package-delete-confirm"]').exists()).toBe(true);
    expect(wrapper.emitted("remove")).toBeUndefined();
    await wrapper.get('[data-testid="package-delete-cancel"]').trigger("click");

    expect(wrapper.emitted("remove")).toBeUndefined();
    expect(wrapper.find('[data-testid="package-delete-confirm"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-testid="package-item"]')).toHaveLength(1);
  });

  it("确认删除后发出删除事件，仅目标项从列表移除", async () => {
    const wrapper = mountPicker({
      packages: [makePackage("p1", "第一套"), makePackage("p2", "第二套")],
    });
    await openDialog(wrapper);
    await wrapper.findAll('[data-testid="package-delete"]')[0].trigger("click");
    await wrapper.get('[data-testid="package-delete-confirm"]').trigger("click");

    expect(wrapper.emitted("remove")?.[0]).toEqual(["p1"]);
    await wrapper.setProps({ packages: [makePackage("p2", "第二套")] });
    const items = wrapper.findAll('[data-testid="package-item"]');
    expect(items).toHaveLength(1);
    expect(items[0].text()).toContain("第二套");
  });

  it("管理动作忙碌时重命名与删除按钮禁用", async () => {
    const wrapper = mountPicker({
      packages: [makePackage("p1", "第一套")],
      manageBusy: true,
    });
    await openDialog(wrapper);

    expect(
      (wrapper.get('[data-testid="package-rename"]').element as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (wrapper.get('[data-testid="package-delete"]').element as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
