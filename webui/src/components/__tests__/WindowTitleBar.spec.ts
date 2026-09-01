// 036 B084 自绘标题栏组件测试：渲染条件（仅桌面版）、三按钮 js_api 接线、
// 双击最大化、主题类名随主题变化。
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WindowTitleBar from "../WindowTitleBar.vue";

type PwApi = NonNullable<Window["pywebview"]>["api"];

function installPywebview(api: Partial<PwApi>) {
  Object.defineProperty(window, "pywebview", {
    value: { api },
    configurable: true,
  });
}

function clearPywebview() {
  Object.defineProperty(window, "pywebview", {
    value: undefined,
    configurable: true,
  });
}

afterEach(() => {
  clearPywebview();
  vi.restoreAllMocks();
  document.documentElement.removeAttribute("data-theme");
});

describe("WindowTitleBar 渲染条件（FR-006）", () => {
  it("仅桌面版（window.pywebview 存在）渲染标题栏", () => {
    installPywebview({});
    const wrapper = mount(WindowTitleBar);
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(true);
  });

  it("浏览器模式（无 pywebview）不渲染标题栏", () => {
    clearPywebview();
    const wrapper = mount(WindowTitleBar);
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(false);
  });
});

describe("WindowTitleBar 三按钮接线（FR-004）", () => {
  it("显示左侧 Career Scout 文字与三个按钮", () => {
    installPywebview({});
    const wrapper = mount(WindowTitleBar);
    expect(wrapper.text()).toContain("Career Scout");
    expect(wrapper.find('[data-testid="titlebar-minimize"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="titlebar-maximize"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="titlebar-close"]').exists()).toBe(true);
  });

  it("点击最小化调用 js_api.window_minimize", async () => {
    const windowMinimize = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_minimize: windowMinimize });
    const wrapper = mount(WindowTitleBar);
    await wrapper.get('[data-testid="titlebar-minimize"]').trigger("click");
    expect(windowMinimize).toHaveBeenCalledTimes(1);
  });

  it("点击最大化调用 js_api.window_toggle_maximize", async () => {
    const toggle = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_toggle_maximize: toggle });
    const wrapper = mount(WindowTitleBar);
    await wrapper.get('[data-testid="titlebar-maximize"]').trigger("click");
    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it("点击关闭调用 js_api.window_close", async () => {
    const close = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_close: close });
    const wrapper = mount(WindowTitleBar);
    await wrapper.get('[data-testid="titlebar-close"]').trigger("click");
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("js_api 缺失时按钮点击不抛错（静默）", async () => {
    installPywebview({});
    const wrapper = mount(WindowTitleBar);
    await wrapper.get('[data-testid="titlebar-minimize"]').trigger("click");
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(true);
  });
});

describe("WindowTitleBar 拖拽与双击（FR-002/FR-003）", () => {
  it("拖拽区带 pywebview-drag-region 类（easy_drag 机制）", () => {
    installPywebview({});
    const wrapper = mount(WindowTitleBar);
    expect(wrapper.get('[data-testid="titlebar-drag-region"]').classes())
      .toContain("pywebview-drag-region");
  });

  it("双击标题栏空白区触发 window_toggle_maximize", async () => {
    const toggle = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_toggle_maximize: toggle });
    const wrapper = mount(WindowTitleBar);
    await wrapper.get('[data-testid="titlebar-drag-region"]').trigger("dblclick");
    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it("双击按钮不触发最大化（事件不冒泡到标题栏）", async () => {
    const toggle = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_toggle_maximize: toggle });
    const wrapper = mount(WindowTitleBar);
    await wrapper.get('[data-testid="titlebar-maximize"]').trigger("dblclick");
    expect(toggle).toHaveBeenCalledTimes(0);
  });
});
