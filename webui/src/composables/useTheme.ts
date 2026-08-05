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

export type ThemeMode = "light" | "dark";

const MODE_STORAGE_KEY = "career-scout-theme-mode";

/** 全局单例：所有 useTheme() 调用共享同一份状态。 */
const mode = ref<ThemeMode>("light");
const platform = ref<Platform>("boss");
let initialized = false;

function applyAttributes() {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", mode.value);
  document.documentElement.setAttribute("data-platform", platform.value);
}

function initFromStorage() {
  if (initialized || typeof window === "undefined") return;
  initialized = true;
  try {
    const saved = window.localStorage.getItem(MODE_STORAGE_KEY);
    if (saved === "light" || saved === "dark") {
      mode.value = saved;
    } else if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
      // 首次访问跟随系统偏好
      mode.value = "dark";
    }
  } catch {
    /* localStorage 不可用时退化为默认 light */
  }
  applyAttributes();
}

/** 切换明暗模式（不传参则 toggle 当前值）。 */
export function toggleTheme(next?: ThemeMode): ThemeMode {
  initFromStorage();
  mode.value = next ?? (mode.value === "light" ? "dark" : "light");
  try {
    window.localStorage.setItem(MODE_STORAGE_KEY, mode.value);
  } catch {
    /* 持久化失败不阻断切换 */
  }
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
