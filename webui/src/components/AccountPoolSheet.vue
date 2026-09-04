<script setup lang="ts">
import { Check, ExternalLink, LoaderCircle, Trash2, X } from "@lucide/vue";
import type { BrowserAccount, Platform } from "../types";

interface PlatformBadge {
  text: string;
  tone: string;
}

const props = defineProps<{
  accounts: BrowserAccount[];
  busy: boolean;
  activeAccount: string;
  poolBusy: boolean;
  busyAccount: string;
  rateLimitClearAccount: string;
  platformLabels: Record<Platform, string>;
  displayName: (account: BrowserAccount) => string;
  platformsOf: (account: BrowserAccount) => Platform[];
  platformBadge: (account: BrowserAccount, platform: Platform) => PlatformBadge;
  canOpenPlatform: (accountId: string, platform: Platform) => boolean;
  canManage: (accountId: string) => boolean;
}>();

const emit = defineEmits<{
  "toggle-pool": [account: BrowserAccount];
  "update-quota": [account: BrowserAccount, field: "r1_quota" | "r2_quota", value: number];
  "open-platform": [account: BrowserAccount, platform: Platform];
  activate: [accountId: string];
  remove: [accountId: string];
  "clear-rate-limited": [account: BrowserAccount];
}>();
</script>

<template>
  <div class="account-sheet" data-testid="account-sheet-header">
    <div class="account-sheet-header account-sheet-sticky-header">
      <strong class="account-sheet-title">账号池</strong>
      <div class="account-sheet-columns" aria-hidden="true">
        <span data-testid="account-sheet-column-account">账号</span>
        <span class="account-sheet-column-pool" data-testid="account-sheet-column-pool">轮询与配额</span>
        <span data-testid="account-sheet-column-platform">平台</span>
        <span data-testid="account-sheet-column-actions">操作</span>
      </div>
    </div>
    <article
      v-for="account in props.accounts"
      :key="account.id"
      class="browser-account-card account-sheet-row"
      :data-active="account.id === props.activeAccount || undefined"
      :data-rate-limited="account.rate_limited ? 'true' : undefined"
    >
      <div class="account-sheet-identity">
        <div class="account-sheet-name">
          <strong :class="{ 'rate-limited-name': account.rate_limited }">{{ props.displayName(account) }}</strong>
          <span v-if="account.rate_limited" class="account-sheet-badge rate-limited" :data-testid="`rate-limited-${account.id}`">
            限流
            <button
              type="button"
              class="rate-limited-clear rate-limited-clear-compact rate-limited-clear-always-visible"
              :data-testid="`clear-rate-limited-${account.id}`"
              aria-label="清除限流标记"
              title="清除限流标记"
              :disabled="props.rateLimitClearAccount === account.id"
              @click="emit('clear-rate-limited', account)"
            >
              <LoaderCircle v-if="props.rateLimitClearAccount === account.id" class="spin" :size="12" aria-hidden="true" />
              <X v-else :size="13" aria-hidden="true" />
            </button>
          </span>
        </div>
      </div>

      <label class="pool-toggle">
        <input
          type="checkbox"
          :checked="account.pool?.selected ?? true"
          :disabled="props.poolBusy"
          :data-testid="`pool-selected-${account.id}`"
          @change="emit('toggle-pool', account)"
        >
        <span>参与轮询</span>
      </label>

      <div class="account-sheet-quotas" :data-testid="`pool-config-${account.id}`">
        <label class="pool-quota account-sheet-quota-row">
          <span>R1</span>
          <input
            type="number"
            min="1"
            max="50"
            :value="account.pool?.r1_quota ?? 25"
            placeholder="1-50"
            class="account-sheet-quota-input account-sheet-quota-input-fill"
            :disabled="props.poolBusy"
            :data-testid="`pool-r1-quota-${account.id}`"
            @change="emit('update-quota', account, 'r1_quota', Number(($event.target as HTMLInputElement).value))"
          >
        </label>
        <label class="pool-quota account-sheet-quota-row">
          <span>R2</span>
          <input
            type="number"
            min="1"
            max="300"
            :value="account.pool?.r2_quota ?? 150"
            placeholder="1-300"
            class="account-sheet-quota-input account-sheet-quota-input-fill"
            :disabled="props.poolBusy"
            :data-testid="`pool-r2-quota-${account.id}`"
            @change="emit('update-quota', account, 'r2_quota', Number(($event.target as HTMLInputElement).value))"
          >
        </label>
      </div>

      <ul class="account-sheet-platforms" :data-testid="`account-platforms-${account.id}`">
        <li v-for="platform in props.platformsOf(account)" :key="platform" :data-platform="platform">
          <span class="account-sheet-platform-label">{{ props.platformLabels[platform] }}</span>
          <span
            class="account-sheet-state"
            :data-tone="props.platformBadge(account, platform).tone"
            :data-testid="`account-state-${account.id}-${platform}`"
          >{{ props.platformBadge(account, platform).text }}</span>
          <button
            type="button"
            class="account-sheet-open"
            :data-testid="`open-${platform}-${account.id}`"
            :aria-label="`打开${props.platformLabels[platform]}浏览器`"
            :title="`打开${props.platformLabels[platform]}浏览器`"
            :disabled="!props.canOpenPlatform(account.id, platform)"
            @click="emit('open-platform', account, platform)"
          >
            <LoaderCircle v-if="props.busyAccount === account.id" class="spin" :size="13" aria-hidden="true" />
            <ExternalLink v-else :size="13" aria-hidden="true" />
          </button>
        </li>
      </ul>

      <div class="account-sheet-actions">
        <button
          v-if="account.id !== props.activeAccount"
          type="button"
          class="icon-button activate-toggle"
          :data-testid="`activate-${account.id}`"
          aria-label="设为当前账号"
          title="设为当前账号"
          :disabled="!props.canManage(account.id)"
          @click="emit('activate', account.id)"
        >
          <LoaderCircle v-if="props.busyAccount === account.id" class="spin" :size="17" aria-hidden="true" />
          <Check v-else :size="17" aria-hidden="true" />
        </button>
        <button
          v-if="!account.builtin"
          type="button"
          class="icon-button danger-icon"
          :data-testid="`delete-${account.id}`"
          aria-label="删除账号"
          title="删除账号"
          :disabled="!props.canManage(account.id)"
          @click="emit('remove', account.id)"
        ><Trash2 :size="17" aria-hidden="true" /></button>
      </div>
    </article>
    <p v-if="!props.accounts.length && !props.busy" class="account-sheet-empty">暂无账号，先添加一个账号。</p>
  </div>
</template>

<style scoped>
.account-sheet{border:1px solid var(--hair);border-radius:10px;overflow:auto;max-height:min(420px,52vh);margin-bottom:18px}.account-sheet-header{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:34px;padding:0 11px;border-bottom:1px solid var(--hair);background:var(--panel-2);color:var(--muted);font-size:11px;letter-spacing:.03em}.account-sheet-header strong{color:var(--ink-1);font-size:13px;letter-spacing:0}.account-sheet-row{display:grid;grid-template-columns:minmax(120px,1.15fr) 68px 116px minmax(118px,1fr) auto;gap:8px;align-items:center;padding:10px 11px;border-top:1px solid var(--hair);background:var(--panel)}.account-sheet-row:first-of-type{border-top:0}.account-sheet-row[data-active="true"]{border-color:var(--brand-edge);background:var(--brand-wash)}.account-sheet-row[data-rate-limited="true"]{border-color:var(--danger-edge,#f0a0a0);background:var(--danger-wash,rgba(220,60,60,.06))}.account-sheet-identity{display:flex;align-items:center;gap:6px;min-width:0}.account-sheet-icon{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:8px;color:var(--brand-strong);background:var(--brand-wash)}.account-sheet-name{display:flex;align-items:center;gap:5px;min-width:0}.account-sheet-name>strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.account-sheet-badge{padding:2px 7px;border:1px solid var(--brand-edge);border-radius:999px;color:var(--brand-ink);background:var(--brand-wash);font-size:11px;font-weight:700;white-space:nowrap}.account-sheet-badge.muted{color:var(--ink-3);border-color:var(--hair);background:var(--hair-2)}.rate-limited-name{color:#c01818}.account-sheet-badge.rate-limited{display:inline-flex;align-items:center;position:relative;padding-right:21px;color:#c01818;border-color:#e04040;background:rgba(220,60,60,.1)}.rate-limited-clear{position:absolute;right:2px;top:1px;display:inline-flex;align-items:center;justify-content:center;width:17px;height:17px;padding:0;border:0;border-radius:4px;background:transparent;color:currentColor;opacity:0;cursor:pointer;transition:opacity 140ms ease,background 140ms ease}.rate-limited:hover .rate-limited-clear,.rate-limited:focus-within .rate-limited-clear{opacity:1}.rate-limited-clear:hover:not(:disabled),.rate-limited-clear:focus-visible{background:rgba(192,24,24,.14);outline:1px solid currentColor}.rate-limited-clear:disabled{cursor:wait}.pool-toggle{display:inline-flex;align-items:center;gap:5px;color:var(--ink-1);font-size:12px;cursor:pointer}.pool-toggle input{margin:0}.account-sheet-quotas{display:grid;grid-template-columns:1fr 1fr;gap:5px}.pool-quota{display:flex;align-items:center;gap:4px;color:var(--muted);font-size:12px}.pool-quota input{width:45px;padding:3px 5px;border:1px solid var(--hair);border-radius:5px;background:var(--panel-2);color:var(--ink-1);font:inherit;font-size:12px}.pool-quota input:disabled{opacity:.6;cursor:not-allowed}.account-sheet-platforms{display:grid;gap:3px;margin:0;padding:0;list-style:none}.account-sheet-platforms li{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:5px;color:var(--muted);font-size:11px;line-height:1.4}.account-sheet-platform-label{font-weight:600;color:var(--ink-1)}.account-sheet-state{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:1px 5px;border-radius:999px;font-size:10px;font-weight:700}.account-sheet-state[data-tone="ok"]{color:var(--match-deep);background:var(--match-wash)}.account-sheet-state[data-tone="warn"],.account-sheet-state[data-tone="refresh"]{color:var(--unsure-deep);background:var(--unsure-wash)}.account-sheet-state[data-tone="restricted"]{color:var(--reject-deep);background:var(--reject-wash)}.account-sheet-state[data-tone="empty"]{color:var(--ink-3);background:var(--hair-2)}.account-sheet-open{display:inline-flex;align-items:center;gap:3px;min-height:22px;padding:2px 5px;border:1px solid var(--brand-edge);border-radius:5px;color:var(--brand-strong);background:var(--panel);font:inherit;font-size:11px;font-weight:600;cursor:pointer}.account-sheet-open:hover:not(:disabled){color:var(--brand-ink);background:var(--brand-wash)}.account-sheet-open:disabled{opacity:.55;cursor:not-allowed}.account-sheet-actions{display:flex;align-items:center;gap:4px}.activate-toggle{color:var(--match-deep)}.danger-icon{color:var(--reject-deep)}.account-sheet-empty{margin:0;padding:24px 0;color:var(--muted);text-align:center}@media (max-width:640px){.account-sheet-row{grid-template-columns:minmax(120px,1fr) auto;align-items:start}.pool-toggle,.account-sheet-quotas,.account-sheet-platforms{grid-column:1 / -1}.account-sheet-actions{grid-column:2;grid-row:1}.account-sheet-platforms{grid-template-columns:1fr 1fr}.account-sheet-header span{display:none}}
</style>

<style scoped>
.account-sheet {
  --account-sheet-columns: minmax(130px, 1.15fr) 82px 130px minmax(200px, 1.35fr) 44px;
}
.account-sheet-header {
  position: sticky;
  top: 0;
  z-index: 2;
  display: block;
  min-height: 0;
  padding: 0;
  background: var(--panel-2);
}
.account-sheet-sticky-header {
  background: var(--panel);
}
.account-sheet-title {
  display: block;
  min-height: 34px;
  padding: 9px 11px 6px;
  border-bottom: 1px solid var(--hair);
  color: var(--ink-1);
  font-size: 13px;
  letter-spacing: 0;
}
.account-sheet-columns,
.account-sheet-row {
  display: grid;
  grid-template-columns: var(--account-sheet-columns);
  gap: 8px;
}
.account-sheet-columns {
  align-items: center;
  min-height: 30px;
  padding: 0 11px;
  border-bottom: 1px solid var(--hair);
  font-size: 11px;
  font-weight: 700;
}
.account-sheet-column-pool {
  grid-column: 2 / span 2;
}
.account-sheet-row {
  grid-template-columns: var(--account-sheet-columns);
}
.account-sheet-quotas {
  grid-template-columns: 1fr;
  gap: 3px;
}
.account-sheet-quota-row {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 5px;
  align-items: center;
}
.account-sheet-quota-row input {
  width: 54px;
  min-width: 54px;
  height: 28px;
  min-height: 0;
  box-sizing: border-box;
}
.account-sheet-quota-row input.account-sheet-quota-input-fill {
  width: 100%;
  min-width: 0;
}
.account-sheet-open {
  width: 24px;
  min-width: 24px;
  height: 22px;
  justify-content: center;
  padding: 2px;
}
.rate-limited-clear-compact {
  min-height: 0;
}
.rate-limited-clear-always-visible {
  opacity: 1;
}
@media (max-width: 640px) {
  .account-sheet-columns {
    grid-template-columns: minmax(120px, 1fr) auto;
  }
  .account-sheet-columns span:nth-child(2),
  .account-sheet-columns span:nth-child(3) {
    display: none;
  }
  .account-sheet-columns span:first-child {
    grid-column: 1;
  }
  .account-sheet-columns span:last-child {
    grid-column: 2;
  }
}
</style>
