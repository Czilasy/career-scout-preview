// ===========================================================================
// 万花筒交互动效（032）：转筒（点击空白）、瞳孔追踪与苏醒、logo 逃生舱、
// 首启轻转场。reduced-motion 声明下全部短路（CSS 层另有静态兜底）。
// 监听均挂 document，组件卸载时整体拆除；不改任何业务逻辑。
// ===========================================================================

/** 视为"交互元素"的点击目标：不触发转筒。 */
const INTERACTIVE_SELECTOR =
  "button, a, input, textarea, select, label, summary, [role='option'], [role='button']";

const ENTRY_FLAG = "kaleido-entry-played";
const CORE_X_RATIO = 0.5; // 光核在视口中的位置（viewBox 720/1440）
const CORE_Y_RATIO = 285 / 900;
const AWAKE_RADIUS = 210;
const PUPIL_RANGE = 7;
const CALM_MS = 3000;
const ENTER_MS = 1100;

export function useKaleidoMotion(
  fieldEl: HTMLElement | null,
  eyeEl: SVGGElement | null,
): (() => void) | undefined {
  if (!fieldEl || !eyeEl) return undefined;
  if (typeof document === "undefined") return undefined;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    return undefined;
  }

  const root = document.documentElement;
  let spin = 0;
  let tx = 0;
  let ty = 0;
  let calmTimer: number | null = null;
  let enterTimer: number | null = null;
  let entering = false;

  // ---- 首启轻转场：暗幕一落一起 + 光轮快放一整圈（会话内只播一次） ----
  let played = false;
  try {
    played = window.sessionStorage.getItem(ENTRY_FLAG) === "1";
  } catch {
    played = false;
  }
  if (!played) {
    entering = true;
    fieldEl.classList.add("kaleido-enter");
    enterTimer = window.setTimeout(() => {
      fieldEl.classList.remove("kaleido-enter");
      entering = false;
      try {
        window.sessionStorage.setItem(ENTRY_FLAG, "1");
      } catch {
        /* 会话标记失败不影响功能 */
      }
    }, ENTER_MS);
  }

  // ---- 点击：logo=逃生舱；空白=转筒；交互元素=不扰 ----
  const onClick = (event: MouseEvent) => {
    const target = event.target as HTMLElement | null;
    if (!target) return;
    if (target.closest(".brand")) {
      // 逃生舱："扶我一下"——全场静止 3 秒
      root.classList.add("kaleido-calm");
      eyeEl.style.transform = "translate(0, 0)";
      if (calmTimer !== null) window.clearTimeout(calmTimer);
      calmTimer = window.setTimeout(() => {
        root.classList.remove("kaleido-calm");
        calmTimer = null;
      }, CALM_MS);
      return;
    }
    if (entering) return;
    if (target.closest(INTERACTIVE_SELECTOR)) return;
    if (String(window.getSelection?.())) return;
    spin += 360; // 转筒：光场整转一圈，弹性过冲由 CSS transition 承担
    fieldEl.style.setProperty("--k-spin", `${spin}deg`);
  };

  // ---- 瞳孔：迟滞追随光标；靠近光核 210px 内苏醒 ----
  const onPointerMove = (event: PointerEvent | MouseEvent) => {
    const cx = window.innerWidth * CORE_X_RATIO;
    const cy = window.innerHeight * CORE_Y_RATIO;
    const dx0 = event.clientX - cx;
    const dy0 = event.clientY - cy;
    const nx = Math.max(-1, Math.min(1, dx0 / window.innerWidth));
    const ny = Math.max(-1, Math.min(1, dy0 / window.innerHeight));
    tx = nx * 2 * PUPIL_RANGE;
    ty = ny * 2 * PUPIL_RANGE;
    const awake = Math.hypot(dx0, dy0) < AWAKE_RADIUS;
    fieldEl.classList.toggle("k-eye-awake", awake);
  };

  const applyTimer = window.setInterval(() => {
    if (root.classList.contains("kaleido-calm")) return;
    eyeEl.style.transform = `translate(${tx.toFixed(1)}px, ${ty.toFixed(1)}px)`;
  }, 120);

  document.addEventListener("click", onClick);
  document.addEventListener("pointermove", onPointerMove);

  return () => {
    document.removeEventListener("click", onClick);
    document.removeEventListener("pointermove", onPointerMove);
    window.clearInterval(applyTimer);
    if (calmTimer !== null) window.clearTimeout(calmTimer);
    if (enterTimer !== null) window.clearTimeout(enterTimer);
    root.classList.remove("kaleido-calm");
    fieldEl.classList.remove("k-eye-awake", "kaleido-enter");
    fieldEl.style.removeProperty("--k-spin");
    eyeEl.style.removeProperty("transform");
  };
}
