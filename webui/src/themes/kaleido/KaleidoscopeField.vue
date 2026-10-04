<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { mountKaleidoscope } from "./renderKaleidoscope";

const canvas = ref<HTMLCanvasElement | null>(null);
let dispose: (() => void) | undefined;
onMounted(() => {
  if (canvas.value) dispose = mountKaleidoscope(canvas.value);
});
onBeforeUnmount(() => dispose?.());
</script>

<template>
  <div class="kaleido-field" aria-hidden="true" data-testid="theme-surface">
    <canvas ref="canvas"></canvas>
  </div>
</template>
