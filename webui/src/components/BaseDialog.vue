<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { X } from "@lucide/vue";

const props = defineProps<{
  open: boolean;
  title: string;
  description?: string;
  size?: "sm" | "md" | "lg";
}>();

const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement | null>(null);
let previousFocus: HTMLElement | null = null;

const focusableSelector = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function close() {
  emit("close");
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return;
  }
  if (event.key !== "Tab" || !panel.value) return;
  const items = Array.from(panel.value.querySelectorAll<HTMLElement>(focusableSelector));
  if (!items.length) {
    event.preventDefault();
    panel.value.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

watch(() => props.open, async (open) => {
  if (open) {
    previousFocus = document.activeElement as HTMLElement | null;
    document.body.classList.add("dialog-open");
    await nextTick();
    const first = panel.value?.querySelector<HTMLElement>(focusableSelector);
    (first || panel.value)?.focus();
  } else {
    document.body.classList.remove("dialog-open");
    previousFocus?.focus();
  }
});

onBeforeUnmount(() => document.body.classList.remove("dialog-open"));
</script>

<template>
  <div v-if="open" class="dialog-backdrop" @mousedown.self="close">
    <section
      ref="panel"
      class="dialog-panel"
      :class="`dialog-${size || 'md'}`"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="`${$attrs.id || 'dialog'}-title`"
      :aria-describedby="description ? `${$attrs.id || 'dialog'}-description` : undefined"
      tabindex="-1"
      @keydown="handleKeydown"
    >
      <header class="dialog-header">
        <div>
          <h2 :id="`${$attrs.id || 'dialog'}-title`">{{ title }}</h2>
          <p v-if="description" :id="`${$attrs.id || 'dialog'}-description`">{{ description }}</p>
        </div>
        <button class="icon-button" type="button" :aria-label="`关闭${title}`" @click="close">
          <X :size="20" />
        </button>
      </header>
      <div class="dialog-body">
        <slot />
      </div>
      <footer v-if="$slots.footer" class="dialog-footer">
        <slot name="footer" />
      </footer>
    </section>
  </div>
</template>
