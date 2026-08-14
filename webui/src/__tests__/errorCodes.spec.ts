import { describe, expect, it } from "vitest";
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
    ]) {
      expect(codeSet.has(code)).toBe(true);
    }
  });

  it("exposes the mirror type for API payloads", () => {
    const code: ErrorCode = "internal_error";
    expect(code).toBe("internal_error");
  });
});
