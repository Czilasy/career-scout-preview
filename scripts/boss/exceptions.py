# -*- coding: utf-8 -*-

"""异常族（限流/详情/风险控制/CDP/登录）（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

from scripts.boss.constants import MSG_USER_CANCELLED_SCRAPE
import sys as _sys
def _facade():
    return _sys.modules.get("scripts.boss_cdp_raw")

# ============================================================
# 运行级请求计数器辅助（B053）
# ============================================================
class RequestLimitExceededError(RuntimeError):
    """单次抓取运行命中 API 请求上限（B053）。"""


class DetailExtractionError(ValueError):
    """The rendered page does not contain a usable job description."""


class DetailLoginRequiredError(DetailExtractionError):
    """The detail page is truncated because the BOSS session is not logged in."""


class DetailVerificationRequiredError(DetailExtractionError):
    """The detail page shows a captcha/slider verification instead of JD content."""


class DetailRateLimitedError(DetailExtractionError):
    """The detail page shows an account/IP rate-limit message instead of JD."""


class RiskControlError(RuntimeError):
    """抓取中途命中风控/验证码/无法确认状态，立即停止（不静默跳过、不伪装完成）。

    携带诊断信息，供终端醒目报错：第几页挂的、为什么、已抓多少条存哪了、
    从哪页续抓。``code`` 为注册表错误码（016-error-module-rework）：
    实锤类（验证码/限流/登录失效）与 source_status_unclear（无法确认），
    入口统一以结构化失败行输出给 webui 分类。
    """

    def __init__(self, reason, *, page=None, scraped_count=0, output_path="",
                 resume_page=None, code=""):
        self.reason = reason
        self.page = page
        self.scraped_count = scraped_count
        self.output_path = output_path
        self.resume_page = resume_page
        self.code = str(code or "")
        super().__init__(reason)


class CDPUnavailableError(RuntimeError):
    """连不上调试浏览器（Chrome 没开 / 端口不通 / 端口被占用）。"""


class SearchCancelled(RuntimeError):
    """用户通过 cancel_event 取消抓取，已写产物保留。

    programmatic 入口专用（CLI 路径无取消语义）；调用方（TaskRunner /
    WorkbenchRunner）将其映射为「用户取消」中断，不落失败码。
    """

    def __init__(self, message=MSG_USER_CANCELLED_SCRAPE):
        super().__init__(message)


class LoginRequiredError(RuntimeError):
    """未检测到 BOSS 登录态（programmatic 等价 CLI exit 1）。

    main() 在 _facade().check_login_state 返回 False 时 sys.exit(1)；programmatic
    入口改为抛出本异常，调用方映射为登录失效失败码。
    """


class ResultFileWriteError(RuntimeError):
    """结果文件落盘失败（os.replace 重试耗尽仍被占用/锁定）。

    与登录失效严格区分：CLI 顶层捕获后映射为独立退出码 + 结构化失败行
    ``source_result_write_failed``，禁止回退为退出码 1 的通用兜底文案。
    """
