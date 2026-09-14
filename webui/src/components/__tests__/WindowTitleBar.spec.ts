// 036 B084 自绘标题栏组件测试：渲染条件（仅桌面版）、三按钮 js_api 接线、
// 双击最大化、最大化/还原图标切换、特殊主题大类磨砂。
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import WindowTitleBar from "../WindowTitleBar.vue";

type PwApi = NonNullable<Window["pywebview"]>["api"];

function installPywebview(api: Partial<PwApi>, platform = "edgechromium") {
  Object.defineProperty(window, "pywebview", {
    value: { api, platform },
    configurable: true,
  });
}

function clearPywebview() {
  Object.defineProperty(window, "pywebview", {
    value: undefined,
    configurable: true,
  });
}

const mounted: VueWrapper[] = [];

function mountBar(attach = false) {
  const wrapper = mount(WindowTitleBar, attach ? { attachTo: document.body } : {});
  mounted.push(wrapper);
  return wrapper;
}

afterEach(() => {
  for (const wrapper of mounted.splice(0)) wrapper.unmount();
  clearPywebview();
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.body.innerHTML = "";
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-theme-category");
  document.documentElement.removeAttribute("data-cs-region");
});

function mountAttached(api: Partial<PwApi> = {}) {
  installPywebview(api);
  return mountBar(true);
}

function mouseDownOn(target: EventTarget, x: number, y: number, button = 0) {
  const event = new MouseEvent("mousedown", {
    bubbles: true,
    cancelable: true,
    clientX: x,
    clientY: y,
    button,
  });
  target.dispatchEvent(event);
  return event;
}

function mouseMoveOn(target: EventTarget, x: number, y: number) {
  target.dispatchEvent(
    new MouseEvent("mousemove", {
      bubbles: true,
      clientX: x,
      clientY: y,
    }),
  );
}

describe("WindowTitleBar 渲染条件（FR-006）", () => {
  it("仅桌面版（window.pywebview 存在）渲染标题栏", () => {
    installPywebview({});
    const wrapper = mountBar();
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(true);
  });

  it("浏览器模式（无 pywebview）不渲染标题栏", () => {
    clearPywebview();
    const wrapper = mountBar();
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(false);
  });
});

describe("WindowTitleBar 三按钮接线（FR-004）", () => {
  it("显示左侧 Career Scout 文字与三个按钮", () => {
    installPywebview({});
    const wrapper = mountBar();
    expect(wrapper.text()).toContain("Career Scout");
    expect(wrapper.find('[data-testid="titlebar-minimize"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="titlebar-maximize"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="titlebar-close"]').exists()).toBe(true);
  });

  it("点击最小化调用 js_api.window_minimize", async () => {
    const windowMinimize = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_minimize: windowMinimize });
    const wrapper = mountBar();
    await wrapper.get('[data-testid="titlebar-minimize"]').trigger("click");
    expect(windowMinimize).toHaveBeenCalledTimes(1);
  });

  it("点击最大化调用 js_api.window_toggle_maximize", async () => {
    const toggle = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_toggle_maximize: toggle });
    const wrapper = mountBar();
    await wrapper.get('[data-testid="titlebar-maximize"]').trigger("click");
    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it("点击关闭调用 js_api.window_close", async () => {
    const close = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_close: close });
    const wrapper = mountBar();
    await wrapper.get('[data-testid="titlebar-close"]').trigger("click");
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("点击关闭后进入「正在关闭」状态（红底保持 + 禁用重复点击）", async () => {
    const close = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_close: close });
    const wrapper = mountBar();
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
    const wrapper = mountBar();
    await wrapper.get('[data-testid="titlebar-close"]').trigger("click");
    await flushPromises();
    const btn = wrapper.get('[data-testid="titlebar-close"]');
    expect(btn.classes()).not.toContain("closing");
    expect(btn.attributes("disabled")).toBeUndefined();
    expect(btn.attributes("title")).toBe("关闭");
  });

  it("js_api 缺失时按钮点击不抛错（静默）", async () => {
    installPywebview({});
    const wrapper = mountBar();
    await wrapper.get('[data-testid="titlebar-minimize"]').trigger("click");
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(true);
  });
});

describe("WindowTitleBar 拖拽与双击（FR-002/FR-003）", () => {
  it("不再使用 pywebview-drag-region（宿主原生命中不可达，036 v2 D9）", () => {
    installPywebview({});
    const wrapper = mountBar();
    expect(wrapper.get('[data-testid="titlebar-drag-region"]').classes())
      .not.toContain("pywebview-drag-region");
  });

  it("双击标题栏空白区触发 window_toggle_maximize", async () => {
    const toggle = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_toggle_maximize: toggle });
    const wrapper = mountBar();
    await wrapper.get('[data-testid="titlebar-drag-region"]').trigger("dblclick");
    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it("双击按钮不触发最大化（事件不冒泡到标题栏）", async () => {
    const toggle = vi.fn().mockResolvedValue({ ok: true });
    installPywebview({ window_toggle_maximize: toggle });
    const wrapper = mountBar();
    await wrapper.get('[data-testid="titlebar-maximize"]').trigger("dblclick");
    expect(toggle).toHaveBeenCalledTimes(0);
  });
});

describe("WindowTitleBar 窗口交互：区域判定 + 一次调用（036 v2）", () => {
  it("标题带按下调用一次 window_begin_move 并阻止默认行为", () => {
    const beginMove = vi.fn().mockResolvedValue({ ok: true });
    const beginResize = vi.fn().mockResolvedValue({ ok: true });
    const wrapper = mountAttached({
      window_begin_move: beginMove,
      window_begin_resize: beginResize,
    });
    const bar = wrapper.get('[data-testid="titlebar-drag-region"]').element;
    const event = mouseDownOn(bar, 200, 18);
    expect(beginMove).toHaveBeenCalledTimes(1);
    expect(beginMove).toHaveBeenCalledWith(200, 18);
    expect(beginResize).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(true);
  });

  it("四角优先于四边、四边优先于标题带，方向逐项正确", () => {
    const beginResize = vi.fn().mockResolvedValue({ ok: true });
    const beginMove = vi.fn().mockResolvedValue({ ok: true });
    mountAttached({
      window_begin_resize: beginResize,
      window_begin_move: beginMove,
    });
    const w = window.innerWidth;
    const h = window.innerHeight;
    const cases: Array<[number, number, string]> = [
      [2, 2, "TL"],
      [w - 3, 2, "TR"],
      [2, h - 3, "BL"],
      [w - 3, h - 3, "BR"],
      [2, 400, "L"],
      [w - 3, 400, "R"],
      [500, 2, "T"],
      [500, h - 3, "B"],
    ];
    for (const [x, y] of cases) mouseDownOn(document.body, x, y);
    expect(beginResize.mock.calls.map((call) => call.slice(0, 3))).toEqual(
      cases.map(([x, y, direction]) => [direction, x, y]),
    );
    expect(beginMove).not.toHaveBeenCalled();
  });

  it("三个窗口按钮不触发移动/拉伸（按钮区按客户区处理）", () => {
    const beginMove = vi.fn();
    const beginResize = vi.fn();
    const wrapper = mountAttached({
      window_begin_move: beginMove,
      window_begin_resize: beginResize,
    });
    for (const id of ["titlebar-minimize", "titlebar-maximize", "titlebar-close"]) {
      const element = wrapper.get(`[data-testid="${id}"]`).element;
      mouseDownOn(element, window.innerWidth - 30, 18);
    }
    expect(beginMove).not.toHaveBeenCalled();
    expect(beginResize).not.toHaveBeenCalled();
  });

  it("客户区按下不触发任何窗口交互", () => {
    const beginMove = vi.fn();
    const beginResize = vi.fn();
    mountAttached({
      window_begin_move: beginMove,
      window_begin_resize: beginResize,
    });
    mouseDownOn(document.body, 400, 400);
    expect(beginMove).not.toHaveBeenCalled();
    expect(beginResize).not.toHaveBeenCalled();
  });

  it("非左键按下不触发窗口交互", () => {
    const beginMove = vi.fn();
    mountAttached({ window_begin_move: beginMove });
    mouseDownOn(document.body, 400, 18, 2);
    expect(beginMove).not.toHaveBeenCalled();
  });

  it("最大化状态不提供边缘拉伸，但标题带仍可移动", async () => {
    const beginMove = vi.fn().mockResolvedValue({ ok: true });
    const beginResize = vi.fn();
    const wrapper = mountAttached({
      window_is_maximized: vi.fn().mockResolvedValue({ ok: true, maximized: true }),
      window_begin_move: beginMove,
      window_begin_resize: beginResize,
    });
    await flushPromises();
    // 三条边（不在标题带范围内）一律不上报，即最大化态无边缘拉伸
    mouseDownOn(document.body, 2, 400);
    mouseDownOn(document.body, window.innerWidth - 3, 500);
    mouseDownOn(document.body, 500, window.innerHeight - 3);
    expect(beginResize).not.toHaveBeenCalled();
    expect(beginMove).not.toHaveBeenCalled();
    // 标题带可拖动区仍上报移动，作为拖下还原的前置
    const bar = wrapper.get('[data-testid="titlebar-drag-region"]').element;
    mouseDownOn(bar, 2, 2);
    mouseDownOn(bar, 300, 18);
    expect(beginMove).toHaveBeenCalledTimes(2);
    expect(beginResize).not.toHaveBeenCalled();
  });

  it("顶部 36px 里盖着页面内容（非标题带元素）时不移动窗口", () => {
    const beginMove = vi.fn();
    mountAttached({ window_begin_move: beginMove });
    // 模拟对话框遮罩/灵动岛等浮层：按在 body 的顶部区域不该当标题带拖走窗口
    mouseDownOn(document.body, 300, 18);
    mouseDownOn(document.body, window.innerWidth - 40, 18);
    expect(beginMove).not.toHaveBeenCalled();
  });

  it("非 Windows 桌面壳（macOS）不响应窗口交互（FR-019）", () => {
    const beginMove = vi.fn();
    const beginResize = vi.fn();
    installPywebview(
      { window_begin_move: beginMove, window_begin_resize: beginResize },
      "cocoa",
    );
    const wrapper = mountBar(true);
    const bar = wrapper.get('[data-testid="titlebar-drag-region"]').element;
    mouseDownOn(bar, 300, 18);
    mouseDownOn(document.body, 2, 400);
    expect(beginMove).not.toHaveBeenCalled();
    expect(beginResize).not.toHaveBeenCalled();
  });

  it("边缘/角落显示方向一致的指针反馈，离开区域即清除", () => {
    mountAttached({});
    mouseMoveOn(document.body, 3, 400);
    expect(document.documentElement.dataset.csRegion).toBe("L");
    mouseMoveOn(document.body, window.innerWidth - 3, window.innerHeight - 3);
    expect(document.documentElement.dataset.csRegion).toBe("BR");
    mouseMoveOn(document.body, 400, 400);
    expect(document.documentElement.dataset.csRegion).toBeUndefined();
  });

  it("窗口尺寸变化后回查真实最大化态（宿主还原/最大化不落后图标）", async () => {
    vi.useFakeTimers();
    const query = vi.fn().mockResolvedValue({ ok: true, maximized: true });
    mountAttached({ window_is_maximized: query });
    await vi.advanceTimersByTimeAsync(0);
    expect(query).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new Event("resize"));
    await vi.advanceTimersByTimeAsync(300);
    expect(query).toHaveBeenCalledTimes(2);
  });

  it("浏览器模式（无 pywebview）不响应窗口交互", () => {
    clearPywebview();
    const beginMove = vi.fn();
    const wrapper = mountBar(true);
    mouseDownOn(document.body, 2, 2);
    mouseDownOn(document.body, 300, 18);
    expect(beginMove).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(false);
  });
});

describe("WindowTitleBar 最大化/还原图标切换（FR-004）", () => {
  it("初始查询窗口为最大化时显示还原图标（重叠方块）", async () => {
    installPywebview({
      window_is_maximized: vi.fn().mockResolvedValue({ ok: true, maximized: true }),
    });
    const wrapper = mountBar();
    await flushPromises();
    expect(wrapper.find('[data-testid="maximize-icon-restore"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="maximize-icon-square"]').exists()).toBe(false);
  });

  it("初始查询窗口为普通态时显示最大化图标（单方框）", async () => {
    installPywebview({
      window_is_maximized: vi.fn().mockResolvedValue({ ok: true, maximized: false }),
    });
    const wrapper = mountBar();
    await flushPromises();
    expect(wrapper.find('[data-testid="maximize-icon-square"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="maximize-icon-restore"]').exists()).toBe(false);
  });

  it("点击最大化后按返回的真实状态切换为还原图标", async () => {
    installPywebview({
      window_is_maximized: vi.fn().mockResolvedValue({ ok: true, maximized: false }),
      window_toggle_maximize: vi.fn().mockResolvedValue({ ok: true, maximized: true }),
    });
    const wrapper = mountBar();
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
    const wrapper = mountBar();
    expect(wrapper.find('[data-testid="window-titlebar"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="titlebar-close"]').exists()).toBe(true);
  });
});
