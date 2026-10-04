// 主题注册口聚焦测试：可用主题登记、值校验。
import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { THEME_REGISTRY, isThemeId } from "../registry";

describe("theme registry", () => {
  it("registers base modes and the approved kaleidoscope in picker order", () => {
    expect(THEME_REGISTRY.map((t) => t.id)).toEqual(["light", "dark", "kaleido"]);
    expect(THEME_REGISTRY.map((t) => t.label)).toEqual(["亮", "暗", "万花筒"]);
  });

  it("validates theme ids", () => {
    expect(isThemeId("kaleido")).toBe(true);
    expect(isThemeId("light")).toBe(true);
    expect(isThemeId("dark")).toBe(true);
    expect(isThemeId("neon")).toBe(false);
    expect(isThemeId(undefined)).toBe(false);
  });
});

describe("theme module boundaries", () => {
  it("keeps independent theme selectors and module imports inside themes", () => {
    const sourceRoot = resolve(process.cwd(), "src");
    const violations: string[] = [];
    const walk = (directory: string) => {
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        if (["themes", "__tests__"].includes(entry.name)) continue;
        const file = resolve(directory, entry.name);
        if (entry.isDirectory()) {
          walk(file);
        } else if (/\.(vue|ts|css)$/.test(entry.name)) {
          const source = readFileSync(file, "utf8");
          const selectors = [...source.matchAll(/\[data-theme=["']([^"']+)["']\]/g)];
          if (selectors.some((match) => !["light", "dark"].includes(match[1]!))
            || /from\s+["'][^"']*themes\/[^/"']+\//.test(source)) {
            violations.push(file.slice(sourceRoot.length + 1));
          }
        }
      }
    };
    walk(sourceRoot);
    expect(violations).toEqual([]);
  });
});
