// ---------------------------------------------------------------------------
// 037 提醒按钮回退：角标单源 = 服务端 /api/job-reminders/count 的 total。
// 抽取自 App.vue（036 通用化块作废后），保留 seq 守卫与 99+ 截断。
//
// 不动后端合同；不传 platform 过滤；不在前端伪造任何数字。
// ---------------------------------------------------------------------------
import { computed, ref, watch, type Ref } from "vue";
import { getJobReminderCount } from "../jobFeedback";

export interface ReminderBadgeApi {
  reminderTotal: Ref<number>;
  badgeText: Ref<string>;
  ariaLabel: Ref<string>;
  refreshReminderCount(): Promise<void>;
}

export function useReminderBadge(currentProfileId: Ref<string | null | undefined>): ReminderBadgeApi {
  const reminderTotal = ref(0);
  /** seq 守卫：profile 切换后旧 profile 的 count 响应不得覆盖新 profile。 */
  let reminderCountSeq = 0;

  async function refreshReminderCount(): Promise<void> {
    const profileId = currentProfileId.value;
    if (!profileId) {
      reminderTotal.value = 0;
      return;
    }
    const seq = ++reminderCountSeq;
    try {
      const data = await getJobReminderCount(profileId);
      if (seq !== reminderCountSeq || profileId !== currentProfileId.value) return;
      reminderTotal.value = data.total;
    } catch {
      // 失败保留上次已知数量；下一次变更再刷新（FR-019）。
    }
  }

  watch(currentProfileId, (profileId, oldProfileId) => {
    if (profileId === oldProfileId) return;
    reminderTotal.value = 0;
    reminderCountSeq += 1;
    if (profileId) void refreshReminderCount();
  });

  const badgeText = computed(() =>
    reminderTotal.value >= 100 ? "99+" : String(reminderTotal.value),
  );

  const ariaLabel = computed(() => {
    if (reminderTotal.value === 0) return "查看提醒";
    return `查看投递提醒，共 ${reminderTotal.value} 个逾期岗位`;
  });

  return { reminderTotal, badgeText, ariaLabel, refreshReminderCount };
}