import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import App from "../src/App";

vi.mock("../src/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  setCsrf: vi.fn(),
}));

const overview = {
  accounts: { online: 1, total: 1, items: [{ id: 1, remark: "主号", xianyu_id: "10001", status: "online" }] },
  today: { inbound: 0, outbound: 0 },
  issues: [],
};

function authStatus(setup: boolean, authenticated: boolean) {
  return { password_set: !setup, authenticated, csrf: authenticated ? "csrf-1" : "" };
}

describe("应用入口与鉴权", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.pushState({}, "", "/");
  });

  it("未设置口令时进入首次设置页", async () => {
    vi.mocked(api.get).mockResolvedValue(authStatus(true, false));

    render(<App />);

    expect(await screen.findByText("首次设置管理员口令")).toBeInTheDocument();
  });

  it("未登录时进入登录页", async () => {
    vi.mocked(api.get).mockResolvedValue(authStatus(false, false));

    render(<App />);

    expect(await screen.findByText("登录闲鱼 Lite")).toBeInTheDocument();
  });

  it("已登录时展示主导航与总览", async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/api/auth/status") {
        return Promise.resolve(authStatus(false, true));
      }
      if (path === "/api/overview") {
        return Promise.resolve(overview);
      }
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });

    render(<App />);

    expect(await screen.findByText("闲鱼 Lite", { selector: "div" })).toBeInTheDocument();
    expect(screen.getByText("总览")).toBeInTheDocument();
    expect(screen.getByText("账号")).toBeInTheDocument();
    expect(screen.getByText("商品")).toBeInTheDocument();
    expect(screen.getByText("规则")).toBeInTheDocument();
    expect(screen.getByText("会话")).toBeInTheDocument();
    expect(screen.getByText("自动发货")).toBeInTheDocument();
    expect(await screen.findByText("在线账号")).toBeInTheDocument();
  });

  it("退出登录后回到登录页", async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/api/auth/status") {
        return Promise.resolve(authStatus(false, true));
      }
      if (path === "/api/overview") {
        return Promise.resolve(overview);
      }
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });
    vi.mocked(api.post).mockResolvedValue({ ok: true });

    render(<App />);
    await screen.findByText("在线账号");

    await userEvent.click(screen.getByRole("button", { name: /退出登录/ }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/auth/logout"));
    expect(await screen.findByText("登录闲鱼 Lite")).toBeInTheDocument();
  });

  it("窄屏使用抽屉导航", async () => {
    window.innerWidth = 375;
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/api/auth/status") {
        return Promise.resolve(authStatus(false, true));
      }
      if (path === "/api/overview") {
        return Promise.resolve(overview);
      }
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });

    try {
      render(<App />);
      expect(await screen.findByText("在线账号")).toBeInTheDocument();

      await userEvent.click(screen.getByRole("button", { name: "菜单" }));

      expect(await screen.findByText("自动发货")).toBeInTheDocument();
    } finally {
      window.innerWidth = 1024;
    }
  });
});
