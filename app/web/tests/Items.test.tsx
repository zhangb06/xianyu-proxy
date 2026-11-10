import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import ItemsPage from "../src/pages/Items";

vi.mock("../src/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const cards = [
  { id: 10, name: "电子书池", available: 3, used: 1, total: 4, enabled: true },
  { id: 11, name: "空池", available: 0, used: 0, total: 0, enabled: true },
  { id: 12, name: "停用池", available: 5, used: 0, total: 5, enabled: false },
];

function configuredItem() {
  return {
    id: 1,
    account_id: 1,
    item_id: "1096",
    title: "电子书",
    price_cents: 990,
    status: "在售",
    cover_url: "",
    synced_at: "",
    polished_at: "",
    polish_error: "",
    delivery: {
      enabled: true,
      mode: "card",
      card_id: 10,
      card_name: "电子书池",
      card_available: 3,
      template: "卡密 {secret}",
      content: "",
      auto_consign: true,
      remark: "",
    },
  };
}

function mockGet(item: ReturnType<typeof configuredItem> | null) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === "/api/accounts") {
      return Promise.resolve([{ id: 1, remark: "主号", xianyu_id: "10001" }]);
    }
    if (path === "/api/accounts/1/items") {
      return Promise.resolve({
        items: [item ?? { ...configuredItem(), delivery: null }],
        counts: { 在售: 1 },
        last_synced_at: "2026-11-06T10:00:00Z",
      });
    }
    if (path === "/api/accounts/1/cards") {
      return Promise.resolve({ cards });
    }
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
}

function drawerSwitch() {
  const label = screen.getByText("启用自动发货");
  return label.closest(".ant-form-item")?.querySelector('[role="switch"]') as HTMLElement;
}

describe("商品管理 - 卡密池与默认关闭", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.put).mockResolvedValue({ delivery: {} });
    vi.mocked(api.post).mockResolvedValue({
      ok: true,
      already: false,
      message: "擦亮成功",
      polished_at: "2026-11-09T02:00:00Z",
    });
    vi.mocked(api.delete).mockResolvedValue({ ok: true });
  });

  it("擦亮商品调用接口并展示最近擦亮时间", async () => {
    mockGet({
      ...configuredItem(),
      polished_at: "2026-11-09T02:00:00Z",
    });

    render(<ItemsPage />);
    expect(await screen.findByText(/最近 11-09/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /擦\s*亮/ }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/accounts/1/items/1096/polish")
    );
  });

  it("展示擦亮失败原因", async () => {
    mockGet({ ...configuredItem(), polish_error: "FAIL_BIZ_SYSTEM_ERROR::系统繁忙" });

    render(<ItemsPage />);
    expect(await screen.findByText(/系统繁忙/)).toBeInTheDocument();
    expect(screen.getByText("未擦亮")).toBeInTheDocument();
  });

  it("商品行展示所选卡密池的可用库存", async () => {
    mockGet(configuredItem());

    render(<ItemsPage />);

    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/accounts/1/items"));
    expect(await screen.findByText("电子书")).toBeInTheDocument();
    expect(screen.getByText("电子书池 · 可用 3")).toBeInTheDocument();
  });

  it("未配置商品打开配置时默认关闭自动发货", async () => {
    mockGet(null);

    render(<ItemsPage />);
    await userEvent.click(await screen.findByText("配置发货"));

    expect(drawerSwitch()).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("默认关闭；开启并保存后，该商品付款才会自动发送")).toBeInTheDocument();
  });

  it("保存未开启的配置时显式提交 enabled=false", async () => {
    mockGet(null);

    render(<ItemsPage />);
    await userEvent.click(await screen.findByText("配置发货"));
    await userEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.put).mock.calls[0][0]).toBe(
      "/api/accounts/1/items/1096/delivery"
    );
    expect(vi.mocked(api.put).mock.calls[0][1]).toMatchObject({
      enabled: false,
      mode: "card",
    });
  });

  it("空池和已停用的池在商品配置里不可选", async () => {
    mockGet(configuredItem());

    render(<ItemsPage />);
    await userEvent.click(await screen.findByText("配置发货"));
    const drawer = document.querySelector(".ant-drawer") as HTMLElement;
    await userEvent.click(within(drawer).getByRole("combobox"));

    const empty = await screen.findByText("空池（可用 0）");
    const disabled = screen.getByText("停用池（可用 5，已停用）");
    expect(empty.closest(".ant-select-item-option")).toHaveClass(
      "ant-select-item-option-disabled"
    );
    expect(disabled.closest(".ant-select-item-option")).toHaveClass(
      "ant-select-item-option-disabled"
    );
  });

  it("发货方式只提供卡密池和固定内容，关闭开关即人工处理", async () => {
    mockGet(configuredItem());

    render(<ItemsPage />);
    await userEvent.click(await screen.findByText("配置发货"));
    const drawer = document.querySelector(".ant-drawer") as HTMLElement;

    expect(within(drawer).getAllByRole("radio")).toHaveLength(2);
    expect(within(drawer).getByRole("radio", { name: "卡密池" })).toBeInTheDocument();
    expect(within(drawer).getByRole("radio", { name: "固定内容" })).toBeInTheDocument();
    expect(within(drawer).queryByRole("radio", { name: "人工处理" })).not.toBeInTheDocument();
    expect(
      within(drawer).getByText("发送后在闲鱼上自动标记为【已发货】")
    ).toBeInTheDocument();
  });

  it("历史人工处理配置打开时按关闭自动发货处理", async () => {
    const item = configuredItem();
    mockGet({
      ...item,
      delivery: { ...item.delivery, mode: "manual", enabled: true },
    });

    render(<ItemsPage />);
    await userEvent.click(await screen.findByText("配置发货"));
    const drawer = document.querySelector(".ant-drawer") as HTMLElement;

    expect(drawerSwitch()).toHaveAttribute("aria-checked", "false");
    expect(within(drawer).getByRole("radio", { name: "卡密池" })).toBeChecked();
    expect(within(drawer).queryByRole("radio", { name: "人工处理" })).not.toBeInTheDocument();
  });

  it("卡密模板可选变量，预览随选择实时更新", async () => {
    mockGet(configuredItem());

    render(<ItemsPage />);
    await userEvent.click(await screen.findByText("配置发货"));
    const drawer = document.querySelector(".ant-drawer") as HTMLElement;

    expect(within(drawer).getByRole("button", { name: /卡\s*密/ })).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "订单号" })).toBeInTheDocument();

    await userEvent.click(within(drawer).getByRole("button", { name: "商品标题" }));

    expect(drawer.querySelector("textarea")).toHaveValue("卡密 {secret}{item_title}");
    const preview = within(drawer).getByText("实时预览").closest(".ant-card") as HTMLElement;
    expect(preview).toHaveTextContent("卡密 CARD-8F2K-9Q1Z电子书");
  });

  it("固定内容模式不提供卡密变量，变量可点选并实时预览", async () => {
    mockGet(configuredItem());

    render(<ItemsPage />);
    await userEvent.click(await screen.findByText("配置发货"));
    const drawer = document.querySelector(".ant-drawer") as HTMLElement;
    await userEvent.click(screen.getByText("固定内容"));

    await waitFor(() =>
      expect(within(drawer).queryByRole("button", { name: /卡\s*密/ })).not.toBeInTheDocument()
    );
    const textarea = drawer.querySelector("textarea") as HTMLTextAreaElement;
    await userEvent.clear(textarea);
    await userEvent.click(within(drawer).getByRole("button", { name: "商品标题" }));

    expect(textarea).toHaveValue("{item_title}");
    const preview = within(drawer).getByText("实时预览").closest(".ant-card") as HTMLElement;
    expect(preview).toHaveTextContent("电子书");
  });
});
