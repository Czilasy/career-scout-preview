// 036 B084 自绘标题栏组件测试：渲染条件（仅桌面版）、三按钮 js_api 接线、
// 双击最大化、最大化/还原图标切换、特殊主题大类磨砂。
import { flushPromises, mount } from "@vue/test-utils";
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
  document.documentElement.removeAttribute("data-theme-category");
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

  it("点击关闭后进入「正在关闭」状态（红底保持 + 禁用重复点击）", async () => {
    const close = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_close: close });
    const wrapper = mount(WindowTitleBar);
    await wrapper.get('[data-testid="titlebar-close"]').trigger("click");
    await flushPromises();
    const btn = wrapper.get('[data-testid="titlebar-close"]');
    expect(btn.classes()).toContain("closing");
    expect(btn.attributes("disabled")).toBeDefined();
    expect(btn.attributes("aria-busy")).toBeDefined();
    expect(btn.attributes("title")).toBe("正在关闭…");
    expect(close).toHaveBeenCalledTimes(1);
    // 重复点击不再触发（按钮已禁用 + 函数守卫）
    await wrapper.get('[data-testid="titlebar-close"]').trigger("click");
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("桌面壳返回关闭失败（ok:false）时按钮复位", async () => {
    const close = vi.fn().mockResolvedValue({ ok: false });
    installPywebview({ window_close: close });
    const wrapper = mount(WindowTitleBar);
    await wrapper.get('[data-testid="titlebar-close"]').trigger("click");
    await flushPromises();
    const btn = wrapper.get('[data-testid="titlebar-close"]');
    expect(btn.classes()).not.toContain("closing");
    expect(btn.attributes("disabled")).toBeUndefined();
    expect(btn.attributes("title")).toBe("关闭");
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

describe("WindowTitleBar 最大化/还原图标切换（FR-004）", () => {
  it("初始查询窗口为最大化时显示还原图标（重叠方块）", async () => {
    installPywebview({
      window_is_maximized: vi.fn().mockResolvedValue({ ok: true, maximized: true }),
    });
    const wrapper = mount(WindowTitleBar);
    await flushPromises();
    expect(wrapper.find('[data-testid="maximize-icon-restore"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="maximize-icon-square"]').exists()).toBe(false);
  });

  it("初始查询窗口为普通态时显示最大化图标（单方框）", async () => {
    installPywebview({
      window_is_maximized: vi.fn().mockResolvedValue({ ok: true, maximized: false }),
    });
    const wrapper = mount(WindowTitleBar);
    await flushPromises();
    expect(wrapper.find('[data-testid="maximize-icon-square"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="maximize-icon-restore"]').exists()).toBe(false);
  });

  it("点击最大化后按返回的真实状态切换为还原图标", async () => {
    installPywebview({
      window_is_maximized: vi.fn().mockResolvedValue({ ok: true, maximized: false }),
      window_toggle_maximize: vi.fn().mockResolvedValue({ ok: true, maximized: true }),
    });
    const wrapper = mount(WindowTitleBar);
    await flushPromises();
    expect(wrapper.find('[data-testid="maximize-icon-square"]').exists()).toBe(true);
    await wrapper.get('[data-testid="titlebar-maximize"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="maximize-icon-restore"]').exists()).toBe(true);
  });
});

describe("WindowTitleBar 特殊主题大类磨砂（FR-008/FR-009）", () => {
  it("data-theme-category=special 时组件正常渲染（不绑定具体主题 id）", () => {
    installPywebview({});
    document.documentElement.setAttribute("data-theme-category", "special");
    const wrapper = mount(WindowTitleBar);
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="titlebar-close"]').exists()).toBe(true);
  });
});
