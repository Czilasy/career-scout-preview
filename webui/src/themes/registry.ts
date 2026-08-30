// ===========================================================================
// 主题模块注册口（032 万花筒彩蛋主题）
//
// 主题容器：每个主题一个自包含子目录（样式/组件/动效），在此登记后经
// <html data-theme> 属性生效。亮/暗为主题基座（styles/theme.css 令牌），
// 万花筒为第一个彩蛋主题模块（themes/kaleido/，样式与光场组件全在该目录）。
//
// 引用方向：App.vue → registry → 具体主题模块；registry 不反向依赖视图。
// ===========================================================================

import "./kaleido/kaleido.css";

export type ThemeId = "light" | "dark" | "kaleido";

export interface ThemeRegistration {
  id: ThemeId;
  label: string;
  description: string;
}

/** 长按弹层的选项顺序即此数组的顺序。 */
export const THEME_REGISTRY: ThemeRegistration[] = [
  { id: "light", label: "亮", description: "白日工作台" },
  { id: "dark", label: "暗", description: "夜间工作台" },
  { id: "kaleido", label: "万花筒", description: "彩蛋 · 暗膛里发光" },
];

export function isThemeId(value: unknown): value is ThemeId {
  return value === "light" || value === "dark" || value === "kaleido";
}
