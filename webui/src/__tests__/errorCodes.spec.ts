import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { ERROR_CODES, ERROR_MESSAGES, type ErrorCode } from "../errorCodes";

describe("errorCodes mirror (B043)", () => {
  it("keeps codes unique and messages complete", () => {
    expect(new Set(ERROR_CODES).size).toBe(ERROR_CODES.length);
    for (const code of ERROR_CODES) {
      expect(ERROR_MESSAGES[code]).toBeTruthy();
    }
    expect(Object.keys(ERROR_MESSAGES).length).toBe(ERROR_CODES.length);
  });

  it("covers stable platform and source codes", () => {
    const codeSet = new Set<string>(ERROR_CODES);
    for (const code of [
      "platform_validation_failed",
      "platform_url_mismatch",
      "job_identity_conflict",
      "source_login_required",
      "source_blocked",
      "source_invalid_output",
      "source_request_limit_exceeded",
    ]) {
      expect(codeSet.has(code)).toBe(true);
    }
  });

  it("exposes the mirror type for API payloads", () => {
    const code: ErrorCode = "internal_error";
    expect(code).toBe("internal_error");
  });
});

describe("ApiError 消息链（020 US2：机器码查中文映射表）", () => {
  it("payload 仅含映射表内机器码时显示中文文案", () => {
    const error = new ApiError(409, { error_code: "job_offline" });
    expect(error.message).toBe("岗位已下架");
  });

  it("user_message / message / error_reason 仍优先于映射表", () => {
    expect(new ApiError(400, { error_code: "job_offline", user_message: "自定义提示" }).message)
      .toBe("自定义提示");
    expect(new ApiError(400, { error_code: "job_offline", message: "消息字段" }).message)
      .toBe("消息字段");
    expect(new ApiError(400, { error_code: "job_offline", error_reason: "原因字段" }).message)
      .toBe("原因字段");
  });

  it("映射表没有的码沿既有链直出原始值", () => {
    expect(new ApiError(400, { error_code: "block_not_resolved" }).message)
      .toBe("block_not_resolved");
    expect(new ApiError(400, { error: "raw_error_text" }).message).toBe("raw_error_text");
    expect(new ApiError(500, {}).message).toBe("请求失败（500）");
  });
});
