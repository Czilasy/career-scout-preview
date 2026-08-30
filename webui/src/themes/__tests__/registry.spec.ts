// 主题注册口聚焦测试：三态登记、值校验。
import { describe, expect, it } from "vitest";

import { THEME_REGISTRY, isThemeId } from "../registry";

describe("theme registry", () => {
  it("registers light, dark and kaleido in picker order", () => {
    expect(THEME_REGISTRY.map((t) => t.id)).toEqual(["light", "dark", "kaleido"]);
  });

  it("labels kaleido as the easter egg entry", () => {
    const kaleido = THEME_REGISTRY.find((t) => t.id === "kaleido");
    expect(kaleido?.label).toBe("万花筒");
  });

  it("validates theme ids", () => {
    expect(isThemeId("kaleido")).toBe(true);
    expect(isThemeId("light")).toBe(true);
    expect(isThemeId("neon")).toBe(false);
    expect(isThemeId(undefined)).toBe(false);
  });
});
