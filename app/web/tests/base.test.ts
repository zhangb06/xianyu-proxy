import { describe, expect, it } from "vitest";

import { joinBase } from "../src/lib/base";

describe("joinBase", () => {
  it("根路径部署保持原有地址", () => {
    expect(joinBase("/", "/api/auth/status")).toBe("/api/auth/status");
  });

  it("二级目录部署为 API 与资源加前缀", () => {
    expect(joinBase("/xy/", "/api/items")).toBe("/xy/api/items");
    expect(joinBase("/xy", "/assets/index.js")).toBe("/xy/assets/index.js");
  });

  it("自动补全缺少的前导斜杠", () => {
    expect(joinBase("/xy/", "accounts")).toBe("/xy/accounts");
  });
});
