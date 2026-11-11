import { beforeEach, describe, expect, it } from "vitest";
import {
  accountLabel,
  pickAccountId,
  rememberAccountId,
  storedAccountId,
} from "../src/lib/account";

const rows = [
  { id: 1, xianyu_id: "10001", remark: "主号" },
  { id: 2, xianyu_id: "10002", remark: "" },
];

describe("账号选择辅助", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("有备注显示备注和闲鱼号，无备注显示闲鱼号", () => {
    expect(accountLabel(rows[0])).toBe("主号（10001）");
    expect(accountLabel(rows[1])).toBe("闲鱼号 10002");
  });

  it("记住上次选择的账号并在下次优先使用", () => {
    expect(storedAccountId()).toBeNull();
    rememberAccountId(2);
    expect(storedAccountId()).toBe(2);
    expect(pickAccountId(rows, null)).toBe(2);
  });

  it("账号已不存在或未选择时回退到第一个", () => {
    rememberAccountId(9);
    expect(pickAccountId(rows, null)).toBe(1);
    expect(pickAccountId(rows, 2)).toBe(2);
    expect(pickAccountId([], null)).toBeNull();
  });
});