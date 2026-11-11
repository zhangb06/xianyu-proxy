const KEEP_ACCOUNT_KEY = "xy.account_id";

export function storedAccountId(): number | null {
  try {
    const raw = window.localStorage.getItem(KEEP_ACCOUNT_KEY);
    const id = raw ? Number(raw) : NaN;
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

export function rememberAccountId(id: number) {
  try {
    window.localStorage.setItem(KEEP_ACCOUNT_KEY, String(id));
  } catch {
    // 隐私模式等场景忽略即可，不影响本次使用
  }
}

export function pickAccountId(rows: any[], prev: number | null): number | null {
  if (prev !== null && rows.some((row) => row.id === prev)) {
    return prev;
  }
  const saved = storedAccountId();
  if (saved !== null && rows.some((row) => row.id === saved)) {
    return saved;
  }
  return rows[0]?.id ?? null;
}

export function accountLabel(account: { remark?: string; xianyu_id: string }) {
  return account.remark ? `${account.remark}（${account.xianyu_id}）` : `闲鱼号 ${account.xianyu_id}`;
}