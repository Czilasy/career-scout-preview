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

// 037 D 批：jsdom 不实现 Web Animations API；提供记录用 no-op 桩。
// 灵动岛弹跳走 Element.animate（CSS attribute 递增不会重启动画），
// 动画开启态测试用 vi.spyOn(Element.prototype, "animate") 计数调用。
if (typeof (Element.prototype as unknown as { animate?: unknown }).animate !== "function") {
  (Element.prototype as unknown as { animate: unknown }).animate = function () {
    return { cancel() {} };
  };
}

// jsdom 不实现 matchMedia；提供可配置替身（默认宽屏 matches=false）。
// 测试可通过 __setNarrowMatchMedia(true/false) 切换，并触发 change 事件。
//
// 037：按 query 分流（island 动画要靠 prefers-reduced-motion）。
// 默认行为：(prefers-reduced-motion: reduce) 返回 true（动画静态），
//          (max-width: ...) 返回 false（宽屏），其它返回 false。
type MatchMediaListener = (e: { matches: boolean }) => void;
let narrowMedia = false;
let reducedMedia = true; // 测试默认减少动态：保证元素直接渲染最终态。
const mediaListeners = new Map<string, Set<MatchMediaListener>>();

function listenersFor(query: string): Set<MatchMediaListener> {
  let set = mediaListeners.get(query);
  if (!set) {
    set = new Set();
    mediaListeners.set(query, set);
  }
  return set;
}

function matchesFor(query: string): boolean {
  const q = query.toLowerCase();
  if (q.includes("prefers-reduced-motion")) return reducedMedia;
  if (q.startsWith("(max-width")) return narrowMedia;
  return false;
}

(globalThis as any).matchMedia = (query: string) => {
  let current = matchesFor(query);
  return {
    get matches() { return current; },
    media: query,
    onchange: null,
    addEventListener: (_type: string, cb: MatchMediaListener) => listenersFor(query).add(cb),
    removeEventListener: (_type: string, cb: MatchMediaListener) => listenersFor(query).delete(cb),
    addListener: (cb: MatchMediaListener) => listenersFor(query).add(cb),
    removeListener: (cb: MatchMediaListener) => listenersFor(query).delete(cb),
    dispatchEvent: () => true,
    // 给 useReducedMotion 用的 ref 实例触发器：
    _setMatches(m: boolean) { current = m; },
  };
};

(globalThis as any).__setNarrowMatchMedia = (narrow: boolean) => {
  narrowMedia = narrow;
  for (const cb of listenersFor("(max-width: 760px)")) cb({ matches: narrow });
};
(globalThis as any).__setReducedMotionMatchMedia = (reduced: boolean) => {
  reducedMedia = reduced;
  for (const cb of listenersFor("(prefers-reduced-motion: reduce)")) cb({ matches: reduced });
};
