// ===========================================================================
// 主题模块注册口
//
// 主题容器：每个主题一个自包含子目录（样式/组件/动效），在此登记后经
// <html data-theme> 属性生效。亮/暗共用 base/theme.css 令牌。
// 具体主题的样式、组件、动效、资源与预览必须留在自己的子目录。
//
// 引用方向：App.vue → registry → 具体主题模块；registry 不反向依赖视图。
// ===========================================================================

import "./base/theme.css";
import "./picker.css";
import "./kaleido/theme.css";
import { markRaw, type Component } from "vue";
import KaleidoscopeField from "./kaleido/KaleidoscopeField.vue";

export type ThemeId = "light" | "dark" | "kaleido";

/** 主题大类：base = 明/暗基座；special = 特殊主题大类。
 *  特殊主题大类的统一样式（如窗口控制条半透明毛玻璃磨砂，
 *  spec 036 A9）挂在大类上，新增特殊主题登记时标为 special 即自动继承。 */
export type ThemeCategory = "base" | "special";

export const THEME_CATEGORIES: Record<ThemeId, ThemeCategory> = {
  light: "base",
  dark: "base",
  kaleido: "special",
};

export interface ThemeRegistration {
  id: ThemeId;
  label: string;
  description: string;
  surface?: Component;
}

/** 长按弹层的选项顺序即此数组的顺序。 */
export const THEME_REGISTRY: ThemeRegistration[] = [
  { id: "light", label: "亮", description: "白日工作台" },
  { id: "dark", label: "暗", description: "夜间工作台" },
  { id: "kaleido", label: "万花筒", description: "彩色碎片 · 镜像重组", surface: markRaw(KaleidoscopeField) },
];

export function isThemeId(value: unknown): value is ThemeId {
  return THEME_REGISTRY.some((theme) => theme.id === value);
}
