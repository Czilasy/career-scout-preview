import { mount } from "@vue/test-utils";
import AppSettingsMenu from "../AppSettingsMenu.vue";

describe("AppSettingsMenu", () => {
  it("renders all menu entries and keeps the data-testid contract", () => {
    const wrapper = mount(AppSettingsMenu, {
      props: { open: true, hasUpdate: true, updateVersion: "2.6.0" },
    });
    expect(wrapper.findAll('[role="menuitem"]')).toHaveLength(6);
    expect(wrapper.get('[data-testid="ai-settings-trigger"]').text()).toContain("AI 设置");
    expect(wrapper.get('[data-testid="browser-accounts-trigger"]').text()).toContain("浏览器与账号");
    expect(wrapper.get('[data-testid="env-check-trigger"]').text()).toContain("环境检查");
    expect(wrapper.get('[data-testid="logs-trigger"]').text()).toContain("日志");
    expect(wrapper.get('[data-testid="manual-update-check"]').text()).toContain("检查更新");
    expect(wrapper.get('[data-testid="manual-update-check"]').text()).toContain("v2.6.0");
    expect(wrapper.get('[data-testid="github-link"]').text()).toContain("GitHub 仓库");
  });

  it("emits menu item actions", async () => {
    const wrapper = mount(AppSettingsMenu, {
      props: { open: true, hasUpdate: false, updateVersion: "" },
    });
    await wrapper.get('[data-testid="ai-settings-trigger"]').trigger("click");
    await wrapper.get('[data-testid="browser-accounts-trigger"]').trigger("click");
    await wrapper.get('[data-testid="env-check-trigger"]').trigger("click");
    await wrapper.get('[data-testid="manual-update-check"]').trigger("click");
    await wrapper.get('[data-testid="github-link"]').trigger("click");
    expect(wrapper.emitted("open-ai-settings")).toHaveLength(1);
    expect(wrapper.emitted("open-browser-accounts")).toHaveLength(1);
    expect(wrapper.emitted("open-env-check")).toHaveLength(1);
    expect(wrapper.emitted("manual-update-check")).toHaveLength(1);
    expect(wrapper.emitted("open-github")).toHaveLength(1);
  });

  it("closes on Escape", async () => {
    const wrapper = mount(AppSettingsMenu, {
      props: { open: true, hasUpdate: false, updateVersion: "" },
    });
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("close")).toHaveLength(1);
  });
});
