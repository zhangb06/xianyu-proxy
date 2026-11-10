import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import RulesPage from "../src/pages/Rules";

vi.mock("../src/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const aiConfig = {
  enabled: true,
  base_url: "https://api.deepseek.com",
  model: "deepseek-chat",
  persona: "二手小店",
  product_info: "",
  listing_price_cents: 9900,
  price_floor_cents: 9000,
  max_history: 10,
  timeout_seconds: 20,
  has_api_key: true,
};

const bundle = {
  rules: [{ id: 5, keyword: "在吗", reply: "在的", priority: 10, enabled: true }],
  config: { default_reply: "您好", debounce_seconds: 30, reply_expiry_seconds: 600 },
  blocks: [{ id: 7, kind: "user", value: "buyer1" }],
};

function mockGet() {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === "/api/accounts") {
      return Promise.resolve([{ id: 1, remark: "主号", xianyu_id: "10001" }]);
    }
    if (path === "/api/accounts/1/ai") {
      return Promise.resolve(aiConfig);
    }
    if (path === "/api/accounts/1/rules") {
      return Promise.resolve(bundle);
    }
    if (path === "/api/accounts/1/items/1096") {
      return Promise.resolve({
        item_id: "1096",
        title: "电子书",
        description: "拍下自动发货",
        price_cents: 990,
        status: "在售",
      });
    }
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
}

describe("自动回复规则", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet();
    vi.mocked(api.put).mockImplementation((path: string, body: any) =>
      Promise.resolve(path.endsWith("/ai") ? { ...aiConfig, ...body } : body)
    );
    vi.mocked(api.post).mockImplementation((path: string, body: any) => {
      if (path === "/api/accounts/1/ai/test") {
        return Promise.resolve({ ok: true, text: "可以便宜 1 元", error: "", latency_ms: 12 });
      }
      return Promise.resolve({ id: 9, ...(body as object) });
    });
    vi.mocked(api.patch).mockResolvedValue({});
    vi.mocked(api.delete).mockResolvedValue({ ok: true });
  });

  it("加载 AI 配置并提示缺少商品补充信息", async () => {
    render(<RulesPage />);

    expect(await screen.findByDisplayValue("deepseek-chat")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("99")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("90")).toBeInTheDocument();
    expect(screen.getByText("API Key（已配置，留空则不修改）")).toBeInTheDocument();
    expect(screen.getByText("配置不完整，AI 回复会受限")).toBeInTheDocument();
    expect(screen.getByText("商品详情（自动读取，AI 回答依据）")).toBeInTheDocument();
  });

  it("保存 AI 配置时把元换算为分", async () => {
    render(<RulesPage />);
    await screen.findByDisplayValue("deepseek-chat");

    const listing = screen.getByLabelText(/标价/);
    await userEvent.clear(listing);
    await userEvent.type(listing, "120");
    await userEvent.click(screen.getByRole("button", { name: /^保\s*存$/ }));

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    const [path, payload] = vi.mocked(api.put).mock.calls[0];
    expect(path).toBe("/api/accounts/1/ai");
    expect(payload).toMatchObject({
      enabled: true,
      base_url: "https://api.deepseek.com",
      model: "deepseek-chat",
      listing_price_cents: 12000,
      price_floor_cents: 9000,
      max_history: 10,
      timeout_seconds: 20,
    });
    expect((payload as any).api_key).toBeUndefined();
  });

  it("AI 连通性测试展示回复", async () => {
    render(<RulesPage />);
    await screen.findByDisplayValue("deepseek-chat");

    await userEvent.click(screen.getByRole("button", { name: "发送测试" }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/accounts/1/ai/test", {
        text: "这个能便宜点吗？最低多少钱？",
      })
    );
    expect(await screen.findByText("可以便宜 1 元")).toBeInTheDocument();
  });

  it("读取商品详情作为 AI 回答依据", async () => {
    render(<RulesPage />);
    await screen.findByDisplayValue("deepseek-chat");

    await userEvent.type(
      screen.getByPlaceholderText("商品 ID，例如 1096185526203"),
      "1096"
    );
    await userEvent.click(screen.getByRole("button", { name: /^读\s*取$/ }));

    expect(await screen.findByText("电子书（标价 9.90 元，在售）")).toBeInTheDocument();
    expect(screen.getByText("拍下自动发货")).toBeInTheDocument();
  });

  it("新增关键词规则并提交默认优先级", async () => {
    render(<RulesPage />);
    await screen.findByDisplayValue("deepseek-chat");
    await userEvent.click(screen.getByRole("tab", { name: "关键词规则" }));
    expect(await screen.findByText("在吗")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /新增规则/ }));
    await userEvent.type(screen.getByLabelText("关键词（消息包含即命中）"), "发货");
    await userEvent.type(screen.getByLabelText("回复内容"), "马上下单");
    await userEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe("/api/accounts/1/rules");
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({
      keyword: "发货",
      reply: "马上下单",
      priority: 100,
    });
  });

  it("规则启用开关调用 PATCH", async () => {
    render(<RulesPage />);
    await screen.findByDisplayValue("deepseek-chat");
    await userEvent.click(screen.getByRole("tab", { name: "关键词规则" }));
    await screen.findByText("在吗");

    await userEvent.click(screen.getByRole("switch"));

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith("/api/rules/5", { enabled: false })
    );
  });

  it("保存默认回复配置", async () => {
    render(<RulesPage />);
    await screen.findByDisplayValue("deepseek-chat");
    await userEvent.click(screen.getByRole("tab", { name: "默认回复" }));

    const textarea = screen.getByLabelText("默认回复（无关键词命中时发送）");
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "稍等");
    await userEvent.click(screen.getByRole("button", { name: /^保\s*存$/ }));

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.put).mock.calls[0][0]).toBe("/api/accounts/1/reply-config");
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({
      default_reply: "稍等",
      debounce_seconds: 30,
      reply_expiry_seconds: 600,
    });
  });

  it("添加与移除黑名单", async () => {
    render(<RulesPage />);
    await screen.findByDisplayValue("deepseek-chat");
    await userEvent.click(screen.getByRole("tab", { name: "黑名单" }));
    expect(await screen.findByText("buyer1")).toBeInTheDocument();

    await userEvent.type(
      screen.getByPlaceholderText("用户ID / 商品ID / 关键词"),
      "buyer9"
    );
    await userEvent.click(screen.getByRole("button", { name: /添\s*加/ }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/accounts/1/blocks", {
        kind: "user",
        value: "buyer9",
      })
    );

    await userEvent.click(screen.getByRole("button", { name: /^移\s*除$/ }));
    await userEvent.click(await screen.findByRole("button", { name: "OK" }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/api/blocks/7"));
  });

  it("没有账号时提示先扫码登录", async () => {
    vi.mocked(api.get).mockResolvedValue([]);

    render(<RulesPage />);

    expect(await screen.findByText("请先在「账号」页面扫码登录。")).toBeInTheDocument();
  });
});
