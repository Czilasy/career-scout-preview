import { createApp } from "vue";
import App from "./App.vue";
import "./styles.css";
import "./styles/theme.css";

// 全局 tooltip：.tip[data-tip] 的提示以 fixed 浮层挂在 body 上，
// 不再受滚动容器 overflow 裁剪（高级设置面板内提示曾被遮挡）。
function initTooltips() {
  let box: HTMLElement | null = null;
  let arrow: HTMLElement | null = null;

  const ensureBox = (): HTMLElement => {
    if (!box) {
      box = document.createElement("div");
      box.className = "js-tooltip";
      box.setAttribute("role", "tooltip");
      box.hidden = true;
      arrow = document.createElement("span");
      arrow.className = "js-tooltip-arrow";
      box.appendChild(arrow);
      document.body.appendChild(box);
    }
    return box;
  };

  const hide = () => {
    if (box) box.hidden = true;
  };

  const place = (target: HTMLElement) => {
    const text = target.getAttribute("data-tip") || "";
    if (!text) return;
    const el = ensureBox();
    el.textContent = text;
    el.appendChild(arrow as HTMLElement);
    el.hidden = false;
    const r = target.getBoundingClientRect();
    const bw = el.offsetWidth;
    const bh = el.offsetHeight;
    const gap = 8;
    let left = r.left + r.width / 2 - bw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - bw - 8));
    let top = r.top - bh - gap;
    let below = false;
    if (top < 8) {
      top = r.bottom + gap;
      below = true;
    }
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    el.dataset.below = below ? "1" : "0";
    const arrowLeft = Math.max(10, Math.min(r.left + r.width / 2 - left - 4, bw - 18));
    (arrow as HTMLElement).style.left = `${arrowLeft}px`;
  };

  const findTip = (node: EventTarget | null): HTMLElement | null =>
    node instanceof Element ? (node.closest(".tip[data-tip]") as HTMLElement | null) : null;

  document.addEventListener("mouseover", (e) => {
    const tip = findTip(e.target);
    if (tip) place(tip);
  });
  document.addEventListener("mouseout", (e) => {
    const tip = findTip(e.target);
    if (tip && !(e.relatedTarget instanceof Node && tip.contains(e.relatedTarget))) hide();
  });
  document.addEventListener("focusin", (e) => {
    const tip = findTip(e.target);
    if (tip) place(tip);
  });
  document.addEventListener("focusout", () => hide());
  // 任何容器滚动时收起，避免浮层与锚点错位
  document.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);

  // 让 ? 图标可键盘聚焦，动态渲染的节点由观察器补 tabindex
  const markFocusable = (root: ParentNode) => {
    root.querySelectorAll<HTMLElement>(".tip[data-tip]:not([tabindex])").forEach((el) => {
      el.tabIndex = 0;
    });
  };
  markFocusable(document);
  new MutationObserver(() => markFocusable(document)).observe(document.body, {
    childList: true,
    subtree: true,
  });
}

createApp(App).mount("#app");
initTooltips();
