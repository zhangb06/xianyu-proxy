import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, setCsrf } from "../src/api";
import LoginPage from "../src/pages/Login";

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

describe("登录 / 首次设置口令", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("首次设置口令提交后写入 csrf 并进入系统", async () => {
    const onSuccess = vi.fn();
    vi.mocked(api.post).mockResolvedValue({ csrf: "csrf-1" });

    render(<LoginPage setupMode onSuccess={onSuccess} />);

    expect(screen.getByText("首次设置管理员口令")).toBeInTheDocument();
    expect(screen.getByText(/请设置至少 8 位的管理口令/)).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText("至少 8 位"), "abcdefgh");
    await userEvent.click(screen.getByRole("button", { name: /设置并进入/ }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/auth/setup", { password: "abcdefgh" })
    );
    expect(setCsrf).toHaveBeenCalledWith("csrf-1");
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
  });

  it("登录模式提交口令并进入系统", async () => {
    const onSuccess = vi.fn();
    vi.mocked(api.post).mockResolvedValue({ csrf: "csrf-2" });

    render(<LoginPage setupMode={false} onSuccess={onSuccess} />);

    expect(screen.getByText("登录闲鱼 Lite")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("至少 8 位"), "abcdefgh");
    await userEvent.click(screen.getByRole("button", { name: /^登\s*录$/ }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/auth/login", { password: "abcdefgh" })
    );
    expect(setCsrf).toHaveBeenCalledWith("csrf-2");
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
  });

  it("口令过短时阻止提交", async () => {
    const onSuccess = vi.fn();

    render(<LoginPage setupMode onSuccess={onSuccess} />);

    await userEvent.type(screen.getByPlaceholderText("至少 8 位"), "short");
    await userEvent.click(screen.getByRole("button", { name: /设置并进入/ }));

    expect(await screen.findByText("口令至少 8 位")).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
