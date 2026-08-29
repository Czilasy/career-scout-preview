<script setup lang="ts">
import { computed, ref } from "vue";
import { Check, CircleAlert, LoaderCircle } from "@lucide/vue";
import { browserRegistryApi, errorMessage } from "../api";
import type {
  BrowserRegistryEntryState,
  BrowserSelection,
  BrowserSelectionSave,
} from "../api";

// 内嵌在「浏览器与账号」弹窗里的抓取浏览器悬浮选择器（由父级定位为 popover）。
// 清单/当前选择/生效路径由父级加载传入。
// 点击语义：点选项只移动选中高亮（草稿），由「使用所选浏览器」按钮执行切换；
// 切换的登录代价以按钮旁提示文案说明，不弹模态确认。
const props = defineProps<{
  entries: BrowserRegistryEntryState[];
  selection: BrowserSelection;
  effectivePath: string | null;
  /** 任务运行中锁定：与账号操作同一把锁，暂停时仍可切换。 */
  locked?: boolean;
}>();

const emit = defineEmits<{
  changed: [payload: { selection: BrowserSelection; effectivePath: string | null }];
}>();

// 草稿选中：null 表示没有待应用的改动
const draft = ref<BrowserSelectionSave | null>(null);

// 手动指定路径分支：路径属于草稿态，校验通过后直接保存
const manualMode = ref(false);
const manualPath = ref("");
const validating = ref(false);
const validationText = ref("");
const validationOk = ref(false);
const saving = ref(false);
const noticeText = ref("");
const noticeTone = ref<"success" | "error">("success");

// 零安装指引（029 FR-010）：清单内一个都没探测到且未选手动时提示
const noneInstalled = computed(() =>
  props.entries.length > 0 && props.entries.every((entry) => !entry.installed)
);

function saveOf(selection: BrowserSelection): BrowserSelectionSave {
  if (selection.mode === "registry") return { mode: "registry", key: selection.key || "" };
  if (selection.mode === "manual") return { mode: "manual", path: selection.manual_path || "" };
  return { mode: "auto" };
}

function samePayload(a: BrowserSelectionSave, b: BrowserSelectionSave): boolean {
  if (a.mode !== b.mode) return false;
  if (a.mode === "registry") return a.key === (b as { key: string }).key;
  if (a.mode === "manual") return a.path === (b as { path: string }).path;
  return true;
}

// 高亮对象：有草稿显示草稿，否则显示当前已保存选择
const marked = computed(() => draft.value ?? saveOf(props.selection));

const autoSelected = computed(() => !manualMode.value && marked.value.mode === "auto");

function registrySelected(key: string): boolean {
  return !manualMode.value && marked.value.mode === "registry" && marked.value.key === key;
}

const manualSelected = computed(() => manualMode.value || marked.value.mode === "manual");

function pick(candidate: BrowserSelectionSave) {
  if (props.locked || saving.value || validating.value) return;
  manualMode.value = false;
  draft.value = samePayload(candidate, saveOf(props.selection)) ? null : candidate;
}

function selectAuto() {
  pick({ mode: "auto" });
}

function selectRegistry(key: string, installed: boolean) {
  if (!installed) return; // 未安装置灰，不可选
  pick({ mode: "registry", key });
}

function selectManual() {
  if (props.locked || saving.value || validating.value) return;
  draft.value = null;
  manualMode.value = true;
  // 已是手动模式时带入当前路径，便于改路径而非从零填
  manualPath.value = props.selection.manual_path || "";
  validationText.value = "";
  validationOk.value = false;
}

async function applyDraft() {
  const payload = draft.value;
  if (!payload || saving.value) return;
  saving.value = true;
  noticeText.value = "";
  try {
    const data = await browserRegistryApi.save(payload);
    draft.value = null;
    manualMode.value = false;
    emit("changed", { selection: data.selection, effectivePath: data.effective_path });
  } catch (error) {
    noticeText.value = errorMessage(error, "保存失败");
    noticeTone.value = "error";
  } finally {
    saving.value = false;
  }
}

async function validateAndUseManualPath() {
  if (!manualPath.value.trim() || validating.value || saving.value) return;
  validating.value = true;
  validationText.value = "";
  let ok = false;
  try {
    const result = await browserRegistryApi.validatePath(manualPath.value.trim());
    ok = Boolean(result.ok);
    validationOk.value = ok;
    validationText.value = result.ok
      ? `校验通过：${result.version}`
      : (result as { message: string }).message;
  } catch (error) {
    validationOk.value = false;
    validationText.value = errorMessage(error, "路径校验失败");
  } finally {
    validating.value = false;
  }
  if (!ok) return;
  saving.value = true;
  try {
    const data = await browserRegistryApi.save({ mode: "manual", path: manualPath.value.trim() });
    manualMode.value = false;
    emit("changed", { selection: data.selection, effectivePath: data.effective_path });
  } catch (error) {
    noticeText.value = errorMessage(error, "保存失败");
    noticeTone.value = "error";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="kernel-picker" data-testid="browser-kernel-picker">
    <button
      type="button"
      :class="autoSelected ? 'kernel-option selected' : 'kernel-option'"
      data-testid="browser-option-auto"
      :disabled="locked || saving"
      @click="selectAuto"
    >
      <span class="kernel-option-name">自动探测</span>
      <Check v-if="autoSelected" class="kernel-check" :size="15" />
    </button>
    <button
      v-for="entry in entries"
      :key="entry.key"
      type="button"
      :class="registrySelected(entry.key) ? 'kernel-option selected' : 'kernel-option'"
      :disabled="locked || saving || !entry.installed"
      :title="entry.installed ? (entry.path || entry.name) : '未安装'"
      :data-testid="`browser-option-${entry.key}`"
      @click="selectRegistry(entry.key, entry.installed)"
    >
      <span class="kernel-option-name">{{ entry.name }}</span>
      <Check v-if="registrySelected(entry.key)" class="kernel-check" :size="15" />
    </button>
    <p
      v-if="noneInstalled && !manualMode"
      class="kernel-zero-hint"
      data-testid="browser-zero-hint"
    >
      未探测到已安装的浏览器，请在下方手动指定路径。
    </p>
    <button
      type="button"
      :class="manualSelected ? 'kernel-option selected' : 'kernel-option'"
      data-testid="browser-option-manual"
      :disabled="locked || saving"
      @click="selectManual"
    >
      <span class="kernel-option-name">手动指定路径</span>
      <Check v-if="manualSelected" class="kernel-check" :size="15" />
    </button>

    <div v-if="manualMode" class="kernel-manual">
      <input
        v-model="manualPath"
        type="text"
        class="kernel-manual-input"
        placeholder="浏览器可执行文件完整路径"
        data-testid="browser-manual-input"
        @input="validationText = ''"
      />
      <button
        type="button"
        class="kernel-validate"
        data-testid="browser-validate-button"
        :disabled="validating || saving || !manualPath.trim()"
        @click="validateAndUseManualPath"
      >
        <LoaderCircle v-if="validating" class="spin" :size="14" />
        {{ validating ? "校验中…" : "校验并使用" }}
      </button>
      <p
        v-if="validationText"
        :class="validationOk ? 'kernel-validation ok' : 'kernel-validation bad'"
        data-testid="browser-validation-message"
      >
        {{ validationText }}
      </p>
    </div>

    <p v-if="effectivePath" class="kernel-effective" data-testid="browser-effective-path">
      当前生效：{{ effectivePath }}
    </p>

    <div v-if="draft" class="kernel-apply">
      <p class="kernel-apply-warn">
        切换后所有账号需在各平台重新登录一次；原浏览器的登录数据保留，切回时无需重新登录。
      </p>
      <button
        type="button"
        class="button primary kernel-apply-button"
        data-testid="browser-apply-button"
        :disabled="saving"
        @click="applyDraft"
      >
        <LoaderCircle v-if="saving" class="spin" :size="15" />
        {{ saving ? "切换中…" : "使用所选浏览器" }}
      </button>
    </div>

    <p
      v-if="noticeText"
      :class="noticeTone === 'success' ? 'kernel-notice ok' : 'kernel-notice bad'"
      data-testid="browser-save-notice"
    >
      <CircleAlert v-if="noticeTone === 'error'" :size="15" />
      {{ noticeText }}
    </p>
  </div>
</template>

<style scoped>
.kernel-picker {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kernel-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  min-height: 32px;
  padding: 5px 9px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  text-align: left;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.kernel-option:hover:not(:disabled) {
  background: var(--brand-wash);
}

.kernel-option.selected {
  border-color: var(--brand-edge);
  background: var(--brand-wash);
  color: var(--brand-strong);
  font-weight: 600;
}

.kernel-option:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.kernel-option-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kernel-zero-hint {
  margin: 0;
  font-size: 12px;
  color: var(--unsure-deep);
}

.kernel-check {
  flex: 0 0 auto;
}

.kernel-manual {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px dashed var(--hair);
  border-radius: 7px;
}

.kernel-manual-input {
  width: 100%;
  padding: 7px 9px;
  border: 1px solid var(--hair);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
}

.kernel-validate {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border: 1px solid var(--hair);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.kernel-validation {
  margin: 0;
  font-size: 12px;
}

.kernel-validation.ok {
  color: var(--match-deep);
}

.kernel-validation.bad {
  color: var(--reject-deep);
}

.kernel-effective {
  margin: 0;
  padding: 0 2px;
  font-size: 11px;
  color: var(--muted);
  word-break: break-all;
}

.kernel-apply {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 2px;
  padding-top: 8px;
  border-top: 1px solid var(--hair);
}

.kernel-apply-warn {
  margin: 0;
  font-size: 12px;
  color: var(--unsure-deep);
}

.kernel-apply-button {
  align-self: flex-end;
}

.kernel-notice {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.kernel-notice.ok {
  color: var(--match-deep);
}

.kernel-notice.bad {
  color: var(--reject-deep);
}

.spin {
  animation: kernel-spin 1s linear infinite;
}

@keyframes kernel-spin {
  to { transform: rotate(360deg); }
}
</style>
