
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
  document.documentElement.removeAttribute("data-theme-category");
}

describe("useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
    resetRootAttributes();
    // 主题后端持久化依赖 fetch：测试环境统一 stub 为失败，避免真实网络调用。
    global.fetch = vi.fn(async () => ({ ok: false }) as Response);
  });

  it("applies light mode as default when no saved preference", async () => {
    const { useTheme } = await loadModule();
    const { mode } = useTheme();
    expect(mode.value).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(document.documentElement.getAttribute("data-theme-category")).toBe("base");
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

  it("applies backend mode on first load when present", async () => {
    global.fetch = vi.fn(
      async () => ({ ok: true, json: async () => ({ mode: "dark" }) }) as Response,
    );
    const { useTheme } = await loadModule();
    const { mode } = useTheme();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mode.value).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("does not let backend load overwrite user interaction", async () => {
    let resolveFetch: ((value: Response) => void) | undefined;
    global.fetch = vi.fn(
      () => new Promise<Response>((resolve) => { resolveFetch = resolve; }),
    );
    const { useTheme, toggleTheme } = await loadModule();
    const { mode } = useTheme();
    toggleTheme("dark");
    // 后端回读迟到且携带旧值：不得覆盖用户刚做的选择
    resolveFetch?.({ ok: true, json: async () => ({ mode: "light" }) } as Response);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mode.value).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("persists toggle to backend endpoint", async () => {
    const { useTheme, toggleTheme } = await loadModule();
    useTheme();
    toggleTheme("dark");
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    const putCall = fetchMock.mock.calls.find(([url, init]) =>
      url === "/api/theme" && (init as RequestInit)?.method === "PUT");
    expect(putCall).toBeTruthy();
    const body = JSON.parse((putCall![1] as RequestInit).body as string);
    expect(body.mode).toBe("dark");
  });

  it("accepts kaleido as an explicit easter egg mode and persists it", async () => {
    const { toggleTheme } = await loadModule();
    const next = toggleTheme("kaleido");
    expect(next).toBe("kaleido");
    expect(document.documentElement.getAttribute("data-theme")).toBe("kaleido");
    // 特殊主题大类标记：万花筒归 special（spec 036 A9 大类统一样式的依据）
    expect(document.documentElement.getAttribute("data-theme-category")).toBe("special");
    expect(localStorage.getItem("career-scout-theme-mode")).toBe("kaleido");
  });

  it("restores saved kaleido mode from localStorage on first access", async () => {
    localStorage.setItem("career-scout-theme-mode", "kaleido");
    const { useTheme } = await loadModule();
    const { mode } = useTheme();
    expect(mode.value).toBe("kaleido");
    expect(document.documentElement.getAttribute("data-theme")).toBe("kaleido");
  });

  it("lets backend restore kaleido mode before user interaction", async () => {
    global.fetch = vi.fn(
      async () =>
        ({ ok: true, json: async () => ({ mode: "kaleido" }) }) as Response,
    );
    const { useTheme } = await loadModule();
    const { mode } = useTheme();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mode.value).toBe("kaleido");
    expect(document.documentElement.getAttribute("data-theme")).toBe("kaleido");
  });

});
