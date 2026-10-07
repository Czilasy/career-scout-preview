"""047 结构前置：Flow 共享错误与安全文案（自 flow_service 搬运）。

恢复/操作域从这里引用，避免反向 import FlowService；
原 flow_service 保持兼容 re-export，异常对象身份不变。
"""

from __future__ import annotations

from webui.store_flow import FlowConflictError


FLOW_ERROR_MESSAGES = {
    "track_submit_failed": "平台任务提交失败",
    "scrape_failed": "平台抓取失败",
    "flow_ai_start_failed": "AI 筛选启动失败",
    "ai_unavailable": "AI 筛选暂不可用",
    "submit_failed": "后台任务提交失败",
    "internal_error": "流程执行失败",
    "account_pool_empty": "平台账号池为空，任务未能启动",
    "browser_busy": "平台浏览器资源正忙，任务已暂停",
    "source_cdp_unavailable": "平台登录空间暂不可用，任务已暂停",
    "source_login_required": "平台登录已失效，请重新登录后重试",
    "scope_validation_failed": "搜索范围参数无效",
    "config_resolution_failed": "执行配置无效",
    "location_validation_failed": "搜索地点参数无效",
    "platform_disabled": "该平台当前不可用",
    "platform_validation_failed": "平台参数无效",
    "location_catalog_unavailable": "地点目录暂时不可用，任务已暂停",
    "scope_preview_required": "搜索范围已失效，请重新校验",
    "scope_platform_mismatch": "搜索范围与平台不一致",
    "scope_request_mismatch": "搜索参数与已确认范围不一致",
    "preflight_resume_unavailable": "任务提交条件尚未满足，请稍后重试",
    "whitebox_incomplete": "AI 筛选证据不足，岗位暂未标记为已筛选",
    "flow_result_incomplete": "AI 筛选未完成，岗位暂未标记为已筛选",
    "flow_result_empty": "AI 筛选未生成可展示结果",
    "flow_result_save_failed": "筛选结果保存失败，请重试",
}

def public_flow_message(error_code: str, _detail: object = "") -> str:
    """Map internal exceptions to a stable, credential-safe public message."""
    return FLOW_ERROR_MESSAGES.get(str(error_code or ""), "流程执行失败")


class PlatformUnavailableError(ValueError):
    """A requested platform cannot accept a new task."""

    def __init__(self, platform: str):
        self.platform = str(platform)
        super().__init__(f"platform unavailable: {self.platform}")


class FlowResumeError(FlowConflictError):
    """A preflight-paused Track remains retryable but cannot resume now."""

    def __init__(self, error_code: str, message: str | None = None):
        self.error_code = str(error_code or "preflight_resume_unavailable")
        self.retryable = True
        self.message = message or public_flow_message(self.error_code)
        super().__init__(self.message)
