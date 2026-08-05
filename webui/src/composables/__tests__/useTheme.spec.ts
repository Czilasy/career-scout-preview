import { beforeEach, describe, expect, it, vi } from "vitest";

// useTheme 用模块级单例保存 mode/platform + initialized 标志。
// 每个测试通过 vi.resetModules() + 动态 import 重新加载模块，
// 确保 initialized 重置为 false，模拟"首次访问"。

async function loadModule() {
  vi.resetModules();
  return await import("../useTheme");
}

function resetRootAttributes() {
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-platform");
}

describe("useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
    resetRootAttributes();
  });

  it("applies light mode as default when no saved preference", async () => {
    const { useTheme } = await loadModule();
    const { mode } = useTheme();
    expect(mode.value).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("toggles mode between light and dark and persists to localStorage", async () => {
    const { useTheme, toggleTheme } = await loadModule();
    const { mode } = useTheme();
    expect(mode.value).toBe("light");

    const next = toggleTheme();
    expect(next).toBe("dark");
    expect(mode.value).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem("career-scout-theme-mode")).toBe("dark");

    const back = toggleTheme();
    expect(back).toBe("light");
    expect(localStorage.getItem("career-scout-theme-mode")).toBe("light");
  });

  it("accepts an explicit mode argument without toggling", async () => {
    const { toggleTheme } = await loadModule();
    toggleTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    toggleTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("restores saved mode from localStorage on first access", async () => {
    localStorage.setItem("career-scout-theme-mode", "dark");
    const { useTheme } = await loadModule();
    const { mode } = useTheme();
    expect(mode.value).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("sets platform attribute on <html> without affecting mode", async () => {
    const { useTheme, setThemePlatform } = await loadModule();
    const { mode, platform } = useTheme();
    expect(platform.value).toBe("boss");

    setThemePlatform("zhilian");
    expect(platform.value).toBe("zhilian");
    expect(document.documentElement.getAttribute("data-platform")).toBe("zhilian");
    expect(mode.value).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("keeps mode and platform orthogonal: switching platform preserves dark mode", async () => {
    const { toggleTheme, setThemePlatform } = await loadModule();
    toggleTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    setThemePlatform("zhilian");
    expect(document.documentElement.getAttribute("data-platform")).toBe("zhilian");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("initializes attributes on <html> when useTheme is first called", async () => {
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
    expect(document.documentElement.getAttribute("data-platform")).toBeNull();

    const { useTheme } = await loadModule();
    useTheme();

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(document.documentElement.getAttribute("data-platform")).toBe("boss");
  });

  it("does not persist platform preference (only mode is persisted)", async () => {
    const { setThemePlatform } = await loadModule();
    setThemePlatform("zhilian");
    expect(localStorage.getItem("career-scout-theme-mode")).toBeNull();
  });

  it("falls back to light when localStorage has an invalid value", async () => {
    localStorage.setItem("career-scout-theme-mode", "neon");
    const { useTheme } = await loadModule();
    const { mode } = useTheme();
    expect(mode.value).toBe("light");
  });
});
