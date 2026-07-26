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
