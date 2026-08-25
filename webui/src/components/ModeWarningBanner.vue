<script setup lang="ts">
defineProps<{
  extremeWarning: boolean;
  largeTaskWarning: boolean;
}>();
</script>

<template>
  <div
    v-if="extremeWarning || largeTaskWarning"
    class="mode-warning-banner"
    data-testid="mode-warning-banner"
    role="status"
  >
    <p v-if="extremeWarning" data-testid="extreme-warning">极高概率限流封号</p>
    <p v-if="largeTaskWarning" data-testid="large-task-warning">任务规模过大可能封号</p>
  </div>
</template>

<style scoped>
.mode-warning-banner {
  display: grid;
  gap: 2px;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, #eab308 55%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, #eab308 16%, transparent);
  color: #b45309;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.5;
}

.mode-warning-banner p {
  margin: 0;
}
</style>
