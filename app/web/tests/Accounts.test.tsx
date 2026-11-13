import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import AccountsPage from "../src/pages/Accounts";

vi.mock("../src/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const accounts = [
  {
    id: 1,
    xianyu_id: "10001",
    remark: "主号",
    enabled: true,
    status: "online",
    device_id: "",
    proxy_url: "",
    verification_mode: "manual",
    last_error: "",
  },
  {
    id: 2,
    xianyu_id: "10002",
    remark: "",
    enabled: false,
    status: "verification_required",
    device_id: "",
    proxy_url: "http://127.0.0.1:7890",
    verification_mode: "auto_fallback",
    last_error: "需要验证",
  },
];

describe("账号管理", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.get).mockResolvedValue(accounts);
    vi.mocked(api.post).mockResolvedValue({});
    vi.mocked(api.patch).mockResolvedValue({});
    vi.mocked(api.put).mockResolvedValue({});
    vi.mocked(api.delete).mockResolvedValue({ ok: true });
  });

  it("展示账号状态、验证模式与错误信息", async () => {
    render(<AccountsPage />);

    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/accounts"));
    expect(await screen.findByText("主号")).toBeInTheDocument();
    expect(screen.getByText("10001")).toBeInTheDocument();
    expect(screen.getByText("在线")).toBeInTheDocument();
    expect(screen.getByText("需验证")).toBeInTheDocument();
    expect(screen.getByText("人工")).toBeInTheDocument();
    expect(screen.getByText("自动兜底")).toBeInTheDocument();
    expect(screen.getByText("http://127.0.0.1:7890")).toBeInTheDocument();
    expect(screen.getByText("需要验证")).toBeInTheDocument();
  });

  it("停用账号调用 disable 接口并重新加载", async () => {
    render(<AccountsPage />);
    await screen.findByText("主号");

    await userEvent.click(screen.getAllByRole("button", { name: /^停\s*用$/ })[0]);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/accounts/1/disable")
    );
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });

  it("编辑账号保存备注、代理、验证模式与自动化开关", async () => {
    vi.mocked(api.get).mockImplementation((path: string) =>
      path.endsWith("/ops")
        ? Promise.resolve({ account_id: 1, auto_polish: true, flower_enabled: false })
        : Promise.resolve(accounts)
    );
    render(<AccountsPage />);
    await screen.findByText("主号");

    await userEvent.click(screen.getAllByRole("button", { name: /^编\s*辑$/ })[0]);
    expect(await screen.findByText("编辑账号 10001")).toBeInTheDocument();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/accounts/1/ops"));

    const autoSwitch = screen
      .getByText("每天自动擦亮在售商品")
      .closest(".ant-form-item")
      ?.querySelector('[role="switch"]') as HTMLElement;
    expect(autoSwitch).toHaveAttribute("aria-checked", "true");

    const remark = screen.getByDisplayValue("主号");
    await userEvent.clear(remark);
    await userEvent.type(remark, "新备注");

    const flowerSwitch = screen
      .getByText("发货后自动请求小红花")
      .closest(".ant-form-item")
      ?.querySelector('[role="switch"]') as HTMLElement;
    expect(flowerSwitch).toHaveAttribute("aria-checked", "false");
    await userEvent.click(flowerSwitch);

    await userEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.patch).mock.calls[0][0]).toBe("/api/accounts/1");
    expect(vi.mocked(api.patch).mock.calls[0][1]).toMatchObject({
      remark: "新备注",
      proxy_url: "",
      verification_mode: "manual",
    });
    expect(vi.mocked(api.patch).mock.calls[0][1]).not.toHaveProperty("auto_polish");
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith("/api/accounts/1/ops", {
        auto_polish: true,
        flower_enabled: true,
      })
    );
  });

  it("删除账号需要二次确认", async () => {
    render(<AccountsPage />);
    await screen.findByText("主号");

    await userEvent.click(screen.getAllByRole("button", { name: /^删\s*除$/ })[0]);
    expect(await screen.findByText("删除账号及其凭据、会话与消息记录？")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith("/api/accounts/1")
    );
  });

  it("扫码添加弹窗获取并展示二维码", async () => {
    vi.mocked(api.post).mockResolvedValue({
      session_id: "s1",
      qr: "data:image/png;base64,AAA",
      state: "waiting",
    });

    render(<AccountsPage />);
    await screen.findByText("主号");

    await userEvent.click(screen.getByRole("button", { name: /扫码添加/ }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/accounts/qr", {}));
    expect(await screen.findByAltText("登录二维码")).toHaveAttribute(
      "src",
      "data:image/png;base64,AAA"
    );
    expect(screen.getByText("请使用闲鱼 App 扫码")).toBeInTheDocument();
    expect(screen.getByText("扫码添加账号")).toBeInTheDocument();
  });

  it("账号行重新扫码绑定该账号并提示扫码对象", async () => {
    vi.mocked(api.post).mockResolvedValue({
      session_id: "s2",
      qr: "data:image/png;base64,BBB",
      state: "waiting",
    });

    render(<AccountsPage />);
    await screen.findByText("主号");

    await userEvent.click(screen.getAllByRole("button", { name: /重新扫码/ })[0]);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/accounts/qr", { account_id: 1 })
    );
    expect(await screen.findByText("重新扫码登录 10001")).toBeInTheDocument();
    expect(
      screen.getByText("请用闲鱼号 10001 扫码；扫其他账号会被拒绝，不会新增账号或覆盖登录。")
    ).toBeInTheDocument();
  });

  it("重新扫码扫到其他账号时提示用同一账号，且不新增账号", async () => {
    vi.mocked(api.post).mockResolvedValue({
      session_id: "s3",
      qr: "data:image/png;base64,CCC",
      state: "waiting",
    });
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/api/accounts") {
        return Promise.resolve(accounts);
      }
      if (path === "/api/accounts/qr/s3") {
        return Promise.resolve({
          state: "error",
          message:
            "扫码的是闲鱼号 20002，不是要重新登录的 10001；请用同一账号扫码，本次未新增或修改账号",
        });
      }
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });

    render(<AccountsPage />);
    await screen.findByText("主号");

    await userEvent.click(screen.getAllByRole("button", { name: /重新扫码/ })[0]);

    expect(
      await screen.findByText(/请用同一账号扫码/, undefined, { timeout: 4000 })
    ).toBeInTheDocument();
    expect(screen.getByText("登录未完成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新获取二维码/ })).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith("/api/accounts/qr", { account_id: 1 });
  });

  it("编辑账号可读取闲鱼昵称并填入备注", async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path.endsWith("/ops")) {
        return Promise.resolve({ account_id: 1, auto_polish: false, flower_enabled: false });
      }
      return Promise.resolve(accounts);
    });
    vi.mocked(api.post).mockResolvedValue({ nickname: "18号当铺" });

    render(<AccountsPage />);
    await screen.findByText("主号");

    await userEvent.click(screen.getAllByRole("button", { name: /^编\s*辑$/ })[0]);
    await screen.findByText("编辑账号 10001");
    const remark = screen.getByDisplayValue("主号");

    await userEvent.click(screen.getByRole("button", { name: /读取闲鱼昵称/ }));

    await waitFor(() => expect(remark).toHaveValue("18号当铺"));
    expect(api.post).toHaveBeenCalledWith("/api/accounts/1/nickname");
  });

  it("窄屏使用卡片列表并直接显示账号操作", async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/api/accounts") {
        return Promise.resolve(accounts);
      }
      if (path.endsWith("/ops")) {
        return Promise.resolve({ account_id: 1, auto_polish: false, flower_enabled: false });
      }
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });

    window.innerWidth = 375;
    try {
      render(<AccountsPage />);

      await screen.findByText("主号");
      expect(screen.getAllByRole("button", { name: /重新扫码/ }).length).toBe(2);

      await userEvent.click(screen.getAllByRole("button", { name: /^编\s*辑$/ })[0]);
      expect(await screen.findByText("编辑账号 10001")).toBeInTheDocument();
    } finally {
      window.innerWidth = 1024;
    }
  });
});
