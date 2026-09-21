<script setup lang="ts">
// Spec 044 B100 US2：第一页的配置包小入口与选择框。
//
// 组件只负责展示与事件：打开时由页面请求列表（emit open），点击某套配置后
// 由页面负责完整校验与回填（emit select）。这里不请求接口、不切页、不按
// 平台过滤，也不在任何情况下自动创建配置包。
import { ref } from "vue";
import { Bookmark, Pencil, Trash2, X } from "@lucide/vue";
import type { SearchPackageSummary } from "../types";

defineProps<{
  packages: SearchPackageSummary[];
  listBusy: boolean;
  listError: string;
  manageBusy: boolean;
  currentPackageId: string | null;
}>();

const emit = defineEmits<{
  open: [];
  select: [id: string];
  retry: [];
  rename: [id: string, name: string];
  remove: [id: string];
}>();

const dialogOpen = ref(false);
const editingId = ref<string | null>(null);
const renameDraft = ref("");
const pendingDeleteId = ref<string | null>(null);

function resetRowState() {
  editingId.value = null;
  renameDraft.value = "";
  pendingDeleteId.value = null;
}

function openDialog() {
  resetRowState();
  dialogOpen.value = true;
  emit("open");
}

function closeDialog() {
  resetRowState();
  dialogOpen.value = false;
}

function choose(packageId: string) {
  // 先收起选择框：选择成功会进入第二页，失败也会在灵动岛上给出红点提示。
  resetRowState();
  dialogOpen.value = false;
  emit("select", packageId);
}

function startRename(item: SearchPackageSummary) {
  pendingDeleteId.value = null;
  editingId.value = item.id;
  renameDraft.value = item.name;
}

function cancelRename() {
  editingId.value = null;
  renameDraft.value = "";
}

function confirmRename(packageId: string) {
  const name = renameDraft.value.trim();
  if (!name) return; // 空名称不发请求：改名必须留下可读名称。
  cancelRename();
  emit("rename", packageId, name);
}

function askDelete(packageId: string) {
  editingId.value = null;
  pendingDeleteId.value = packageId;
}

function cancelDelete() {
  pendingDeleteId.value = null;
}

function confirmDelete(packageId: string) {
  pendingDeleteId.value = null;
  emit("remove", packageId);
}
</script>

<template>
  <button
    class="button ghost small saved-package-entry"
    type="button"
    data-testid="saved-package-entry"
    @click="openDialog"
  >
    <Bookmark :size="15" aria-hidden="true" />
    使用已有配置
  </button>

  <Teleport to="body">
    <div
      v-if="dialogOpen"
      class="saved-package-backdrop"
      data-testid="saved-package-dialog"
      @click.self="closeDialog"
    >
      <section class="saved-package-panel" role="dialog" aria-modal="true" aria-label="常用搜索配置">
      <header class="saved-package-panel-head">
        <h3>常用搜索配置</h3>
        <button
          class="icon-button"
          type="button"
          data-testid="saved-package-close"
          aria-label="关闭常用配置"
          @click="closeDialog"
        >
          <X :size="16" aria-hidden="true" />
        </button>
      </header>
      <p class="saved-package-panel-hint">
        选一套配置直接进入第二页，不重新分析简历，也不会自动开始搜索。
      </p>

      <p v-if="listBusy" class="saved-package-state" data-testid="package-list-loading">
        正在加载常用配置…
      </p>
      <div v-else-if="listError" class="saved-package-state is-error" data-testid="package-list-error">
        <span>{{ listError }}</span>
        <button class="button secondary small" type="button" data-testid="package-list-retry" @click="emit('retry')">
          重试
        </button>
      </div>
      <p v-else-if="!packages.length" class="saved-package-state" data-testid="package-list-empty">
        还没有保存过常用配置。在第二页点“保存为常用配置”即可建立第一套。
      </p>
      <ul v-else class="saved-package-list">
        <li v-for="item in packages" :key="item.id" class="saved-package-row">
          <template v-if="editingId === item.id">
            <input
              v-model="renameDraft"
              class="saved-package-rename-input"
              data-testid="package-rename-input"
              type="text"
              maxlength="80"
              @keydown.enter.prevent="confirmRename(item.id)"
            >
            <button
              class="button primary small"
              type="button"
              data-testid="package-rename-confirm"
              :disabled="manageBusy"
              @click="confirmRename(item.id)"
            >确定</button>
            <button
              class="button ghost small"
              type="button"
              data-testid="package-rename-cancel"
              :disabled="manageBusy"
              @click="cancelRename"
            >取消</button>
          </template>
          <template v-else-if="pendingDeleteId === item.id">
            <span class="saved-package-confirm-text">删除「{{ item.name }}」？</span>
            <button
              class="button primary small"
              type="button"
              data-testid="package-delete-confirm"
              :disabled="manageBusy"
              @click="confirmDelete(item.id)"
            >删除</button>
            <button
              class="button ghost small"
              type="button"
              data-testid="package-delete-cancel"
              :disabled="manageBusy"
              @click="cancelDelete"
            >取消</button>
          </template>
          <template v-else>
            <button
              class="saved-package-item"
              type="button"
              data-testid="package-item"
              @click="choose(item.id)"
            >
              <span class="saved-package-item-name">{{ item.name }}</span>
              <small v-if="item.id === currentPackageId" class="saved-package-item-current">当前</small>
            </button>
            <button
              class="icon-button"
              type="button"
              data-testid="package-rename"
              aria-label="重命名"
              :disabled="manageBusy"
              @click="startRename(item)"
            >
              <Pencil :size="15" aria-hidden="true" />
            </button>
            <button
              class="icon-button"
              type="button"
              data-testid="package-delete"
              aria-label="删除"
              :disabled="manageBusy"
              @click="askDelete(item.id)"
            >
              <Trash2 :size="15" aria-hidden="true" />
            </button>
          </template>
        </li>
      </ul>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.saved-package-backdrop {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgb(0 0 0 / 24%);
}
.saved-package-entry {
  min-height: 36px;
  padding: 0 12px;
  white-space: nowrap;
}
.saved-package-panel {
  width: min(560px, 100%);
  max-height: min(70vh, 560px);
  overflow: auto;
  padding: 18px 20px 20px;
  border-radius: 14px;
  border: 1px solid var(--hair);
  background: var(--panel);
  box-shadow: var(--shadow);
}
.saved-package-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.saved-package-panel-head h3 {
  margin: 0;
  font-size: 15px;
}
.saved-package-panel-hint {
  margin: 8px 0 14px;
  font-size: 12px;
  color: var(--text-muted, #64748b);
}
.saved-package-state {
  margin: 12px 0;
  font-size: 13px;
  color: var(--text-muted, #64748b);
}
.saved-package-state.is-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #b91c1c;
}
.saved-package-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.saved-package-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.saved-package-item {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 13px;
  border-radius: 10px;
  border: 1px solid var(--line, rgba(148, 163, 184, 0.35));
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: 13px;
}
.saved-package-item:hover {
  border-color: var(--brand, #0f766e);
}
.saved-package-item-current {
  color: var(--brand, #0f766e);
}
.saved-package-rename-input {
  flex: 1;
  min-width: 0;
  padding: 9px 11px;
  border-radius: 10px;
  border: 1px solid var(--line, rgba(148, 163, 184, 0.35));
  background: transparent;
  font-size: 13px;
}
.saved-package-confirm-text {
  flex: 1;
  font-size: 13px;
  color: #b91c1c;
}
</style>
