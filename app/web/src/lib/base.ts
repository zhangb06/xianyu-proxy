export function joinBase(base: string, path: string): string {
  const prefix = base.replace(/\/+$/, "");
  return `${prefix}${path.startsWith("/") ? path : `/${path}`}`;
}

export const BASE_PATH = import.meta.env.BASE_URL.replace(/\/+$/, "");

export function withBase(path: string): string {
  return joinBase(BASE_PATH, path);
}
