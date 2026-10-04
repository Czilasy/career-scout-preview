import { afterEach, expect, it, vi } from "vitest";
import { mountKaleidoscope } from "../renderKaleidoscope";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("pauses for visibility and reduced motion, and releases listeners on disposal", () => {
  const context = new Proxy({
    createPattern: () => ({}),
    createRadialGradient: () => ({ addColorStop() {} }),
  }, { get: (target, key) => Reflect.get(target, key) ?? (() => {}) });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  let hidden = false;
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  let mediaChange: (() => void) | undefined;
  const media = {
    matches: false,
    addEventListener: vi.fn((_type, callback) => { mediaChange = callback; }),
    removeEventListener: vi.fn(),
  };
  vi.stubGlobal("matchMedia", () => media);
  const request = vi.fn().mockReturnValue(123);
  const cancel = vi.fn();
  vi.stubGlobal("requestAnimationFrame", request);
  vi.stubGlobal("cancelAnimationFrame", cancel);
  const dispose = mountKaleidoscope(document.createElement("canvas"));
  expect(request).toHaveBeenCalledTimes(1);
  hidden = true;
  document.dispatchEvent(new Event("visibilitychange"));
  expect(cancel).toHaveBeenCalledWith(123);
  hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  expect(request).toHaveBeenCalledTimes(2);
  media.matches = true;
  mediaChange?.();
  expect(cancel).toHaveBeenCalledTimes(2);
  media.matches = false;
  mediaChange?.();
  expect(request).toHaveBeenCalledTimes(3);
  dispose();
  expect(cancel).toHaveBeenCalledTimes(3);
  expect(media.removeEventListener).toHaveBeenCalledWith("change", mediaChange);
  document.dispatchEvent(new Event("visibilitychange"));
  window.dispatchEvent(new Event("resize"));
  expect(request).toHaveBeenCalledTimes(3);
});

it("tolerates an unavailable canvas renderer", () => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  expect(() => mountKaleidoscope(document.createElement("canvas"))()).not.toThrow();
});
