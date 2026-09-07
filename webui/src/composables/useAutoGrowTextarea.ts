import { nextTick, onBeforeUnmount, onMounted, watch, type Ref } from "vue";

const DEFAULT_MIN_HEIGHT = 96;

export function useAutoGrowTextarea(
  input: Ref<HTMLTextAreaElement | null>,
  value: Ref<string>,
  minHeight = DEFAULT_MIN_HEIGHT,
) {
  function resize() {
    const element = input.value;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.max(element.scrollHeight, minHeight)}px`;
  }

  function scheduleResize() {
    void nextTick(resize);
  }

  function handleWindowResize() {
    scheduleResize();
  }

  onMounted(() => {
    window.addEventListener("resize", handleWindowResize);
    scheduleResize();
  });

  onBeforeUnmount(() => {
    window.removeEventListener("resize", handleWindowResize);
  });

  watch(value, scheduleResize);

  return { scheduleResize };
}
