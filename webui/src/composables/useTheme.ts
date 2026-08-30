// ===========================================================================
// 主题切换 composable（brand-spec.md 落地）
//
// 两个独立维度：
//   - mode: light | dark  （明暗主题，用户偏好，localStorage 持久化）
//   - platform: boss | zhilian （平台品牌色，跟随当前任务/草稿平台）
//
// 两者正交：boss+暗 / boss+浅 / zhilian+暗 / zhilian+浅 四态自由组合。
// 切换平台时暗色偏好保留；切换暗色时平台不变。
//
// 落地方式：在 <html> 上挂 data-theme 和 data-platform 属性，theme.css 按
// 属性组合切换四套令牌。
// ===========================================================================

import { computed, ref, watch } from "vue";
import type { Platform } from "../types";

export type ThemeMode = "light" | "dark" | "kaleido";

const MODE_STORAGE_KEY = "career-scout-theme-mode";
const THEME_API = "/api/theme";

/** 全局单例：所有 useTheme() 调用共享同一份状态。 */
const mode = ref<ThemeMode>("light");
const platform = ref<Platform>("boss");
let initialized = false;
// 用户手动切换过主题后，启动期的后端回读结果不再覆盖（避免竞态覆盖用户选择）。
let userInteracted = false;

function applyAttributes() {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", mode.value);
  document.documentElement.setAttribute("data-platform", platform.value);
}

/** 从后端读主题偏好（桌面版随机端口导致 localStorage 不可靠，后端为准）。 */
async function loadFromBackend() {
  try {
    const resp = await fetch(THEME_API, { headers: { Accept: "application/json" } });
    if (!resp.ok) return;
    const data = (await resp.json()) as { mode?: string };
      if (!userInteracted && (data.mode === "light" || data.mode === "dark" || data.mode === "kaleido")) {
      mode.value = data.mode;
      applyAttributes();
    }
  } catch {
    /* 后端不可达时保留 localStorage 结果 */
  }
}

/** 写后端主题偏好；失败静默（下次启动以 localStorage 兜底）。 */
function saveToBackend(next: ThemeMode) {
  try {
    void fetch(THEME_API, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: next }),
    });
  } catch {
    /* 静默 */
  }
}

function initFromStorage() {
  if (initialized || typeof window === "undefined") return;
  initialized = true;
  try {
    const saved = window.localStorage.getItem(MODE_STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "kaleido") {
      mode.value = saved;
    } else if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
      // 首次访问跟随系统偏好
      mode.value = "dark";
    }
  } catch {
    /* localStorage 不可用时退化为默认 light */
  }
  applyAttributes();
  // 后端为权威来源：启动时异步回读并覆盖本地值（若用户尚未操作）。
  void loadFromBackend();
}

/** 切换明暗模式（不传参则 toggle 当前值）。 */
export function toggleTheme(next?: ThemeMode): ThemeMode {
  initFromStorage();
  mode.value = next ?? (mode.value === "light" ? "dark" : "light");
  userInteracted = true;
  try {
    window.localStorage.setItem(MODE_STORAGE_KEY, mode.value);
  } catch {
    /* 持久化失败不阻断切换 */
  }
  saveToBackend(mode.value);
  applyAttributes();
  return mode.value;
}

/** 设置当前平台品牌色（不传则保持）。 */
export function setThemePlatform(next: Platform): void {
  initFromStorage();
  platform.value = next;
  applyAttributes();
}

export function useTheme() {
  initFromStorage();

  // 首次调用时确保属性已挂到 <html>
  if (typeof document !== "undefined") {
    const current = document.documentElement.getAttribute("data-theme");
    if (!current) applyAttributes();
  }

  // 同步到 <html>：两 watch 都要写，避免任一变化时属性滞后
  watch(mode, applyAttributes);
  watch(platform, applyAttributes);

  return {
    mode: computed(() => mode.value),
    platform: computed(() => platform.value),
    toggleTheme,
    setThemePlatform,
  };
}
