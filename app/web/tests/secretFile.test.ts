import { describe, expect, it } from "vitest";
import { MAX_SECRETS, parseSecretFile } from "../src/lib/secretFile";

describe("parseSecretFile", () => {
  it("读取 txt：去 BOM、统一换行、跳过空行并去重", () => {
    const result = parseSecretFile("cards.txt", "\uFEFFA\r\nB\r\n\r\nA\r\nC  \n");

    expect(result).toEqual({ ok: true, text: "A\nB\nC", count: 3 });
  });

  it("txt 保留整行内容，逗号不拆分", () => {
    const result = parseSecretFile("cards.txt", "A,B\nC\n");

    expect(result).toEqual({ ok: true, text: "A,B\nC", count: 2 });
  });

  it("csv 只取第一列并处理引号转义", () => {
    const result = parseSecretFile(
      "cards.csv",
      '卡密,备注\n"ABC,123",备注1\n"QQ""1",备注2\nplain,备注3\n'
    );

    expect(result).toEqual({
      ok: true,
      text: '卡密\nABC,123\nQQ"1\nplain',
      count: 4,
    });
  });

  it("空文件返回错误", () => {
    expect(parseSecretFile("cards.txt", " \n\r\n")).toEqual({
      ok: false,
      error: "文件中没有可导入的卡密",
    });
  });

  it("超过单次上限返回错误", () => {
    const content = Array.from({ length: MAX_SECRETS + 1 }, (_, index) => `S-${index}`).join(
      "\n"
    );

    expect(parseSecretFile("cards.txt", content)).toEqual({
      ok: false,
      error: `文件包含 ${MAX_SECRETS + 1} 条，单次最多 ${MAX_SECRETS} 条`,
    });
  });

  it("内容过大返回错误", () => {
    const content = Array.from({ length: MAX_SECRETS - 1 }, (_, index) =>
      `${index}`.padEnd(60, "x")
    ).join("\n");

    expect(parseSecretFile("cards.txt", content)).toEqual({
      ok: false,
      error: "文件内容过大，单次最多 200,000 字符",
    });
  });
});
