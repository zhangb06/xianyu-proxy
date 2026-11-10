export const MAX_SECRETS = 5000;
export const MAX_TEXT_LENGTH = 200_000;

export type SecretFileResult =
  | { ok: true; text: string; count: number }
  | { ok: false; error: string };

function firstCsvField(line: string): string {
  if (!line.startsWith('"')) {
    const comma = line.indexOf(",");
    return (comma === -1 ? line : line.slice(0, comma)).trim();
  }
  let value = "";
  let index = 1;
  while (index < line.length) {
    const char = line[index];
    if (char === '"') {
      if (line[index + 1] === '"') {
        value += '"';
        index += 2;
        continue;
      }
      break;
    }
    value += char;
    index += 1;
  }
  return value.trim();
}

export function parseSecretFile(name: string, raw: string): SecretFileResult {
  const isCsv = /\.csv$/i.test(name);
  const content = raw.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
  const lines = content
    .split("\n")
    .map((line) => (isCsv ? firstCsvField(line) : line.trim()))
    .filter((line) => line.length > 0);

  if (lines.length === 0) {
    return { ok: false, error: "文件中没有可导入的卡密" };
  }

  const unique = [...new Set(lines)];
  if (unique.length > MAX_SECRETS) {
    return { ok: false, error: `文件包含 ${unique.length} 条，单次最多 ${MAX_SECRETS} 条` };
  }

  const text = unique.join("\n");
  if (text.length > MAX_TEXT_LENGTH) {
    return { ok: false, error: "文件内容过大，单次最多 200,000 字符" };
  }

  return { ok: true, text, count: unique.length };
}
