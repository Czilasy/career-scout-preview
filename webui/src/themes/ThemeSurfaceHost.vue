<script setup lang="ts">
import { computed } from "vue";
import { useTheme } from "../composables/useTheme";
import { THEME_REGISTRY } from "./registry";

const { mode } = useTheme();
const surface = computed(() => THEME_REGISTRY.find(theme => theme.id === mode.value)?.surface);
</script>

<template>
  <component :is="surface" v-if="surface" />
</template>
