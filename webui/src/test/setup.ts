import { expectedBackendBuildHash, setBuildIdentity } from "../api";

setBuildIdentity(expectedBackendBuildHash);

// 026 B078：已结束事实持久化在 localStorage（进 04 页置位），
// 所有测试用例间统一隔离，防止跨用例污染 Discovery 恢复判定。
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

const values = new Map<string, string>();
const storage: Storage = {
  get length() { return values.size; },
  clear() { values.clear(); },
  getItem(key) { return values.get(key) ?? null; },
  key(index) { return [...values.keys()][index] ?? null; },
  removeItem(key) { values.delete(key); },
  setItem(key, value) { values.set(String(key), String(value)); },
};

Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true });
Object.defineProperty(window, "localStorage", { value: storage, configurable: true });

// jsdom 不实现 IntersectionObserver；提供可手动触发的测试替身。
// 各组件 mount 时创建的实例会被推入全局数组，测试可取出最后一个手动 trigger。
class MockIntersectionObserver {
  callback: IntersectionObserverCallback;
  elements: Element[] = [];
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    mockIntersectionObservers.push(this);
  }
  observe(el: Element) { this.elements.push(el); }
  unobserve(el: Element) { this.elements = this.elements.filter((e) => e !== el); }
  disconnect() { this.elements = []; }
  trigger(isIntersecting: boolean) {
    this.callback(
      this.elements.map((target) => ({ isIntersecting, target }) as IntersectionObserverEntry),
      this as unknown as IntersectionObserver,
    );
  }
}

const mockIntersectionObservers: MockIntersectionObserver[] = [];
(globalThis as unknown as { __mockIntersectionObservers: MockIntersectionObserver[] })
  .__mockIntersectionObservers = mockIntersectionObservers;
(globalThis as any).IntersectionObserver = MockIntersectionObserver;

// jsdom 不实现 matchMedia；提供可配置替身（默认宽屏 matches=false）。
// 测试可通过 __setNarrowMatchMedia(true/false) 切换，并触发 change 事件。
type MatchMediaListener = (e: { matches: boolean }) => void;
let narrowMedia = false;
const mediaListeners = new Set<MatchMediaListener>();

(globalThis as any).matchMedia = (query: string) => ({
  get matches() { return narrowMedia; },
  media: query,
  addEventListener: (_type: string, cb: MatchMediaListener) => mediaListeners.add(cb),
  removeEventListener: (_type: string, cb: MatchMediaListener) => mediaListeners.delete(cb),
  addListener: (cb: MatchMediaListener) => mediaListeners.add(cb),
  removeListener: (cb: MatchMediaListener) => mediaListeners.delete(cb),
  onchange: null,
  dispatchEvent: () => true,
});

(globalThis as any).__setNarrowMatchMedia = (narrow: boolean) => {
  narrowMedia = narrow;
  for (const cb of [...mediaListeners]) cb({ matches: narrow });
};
