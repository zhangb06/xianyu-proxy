import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import DeliveryPage from "../src/pages/Delivery";

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
  { id: 10, name: "电子书池", note: "网盘链接", available: 3, used: 1, total: 4, enabled: true },
  { id: 11, name: "备用池", note: "", available: 0, used: 0, total: 0, enabled: true },
];

function mockGet() {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === "/api/accounts") {
      return Promise.resolve([{ id: 1, remark: "主号", xianyu_id: "10001" }]);
    }
    if (path === "/api/accounts/1/orders") {
      return Promise.resolve({ orders: [] });
    }
    if (path === "/api/accounts/1/outbox") {
      return Promise.resolve({ tasks: [] });
    }
    if (path === "/api/accounts/1/cards") {
      return Promise.resolve({ cards });
    }
    if (path === "/api/accounts/1/delivery-rules") {
      return Promise.resolve({ rules: [] });
    }
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
}

describe("自动发货 - 卡密池", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.post).mockResolvedValue({ inserted: 2, duplicates: 0 });
    vi.mocked(api.patch).mockResolvedValue({});
    mockGet();
  });

  it("订单页展示小红花请求状态与失败原因", async () => {
    const baseOrder = {
      chat_id: "c1",
      buyer_id: "b1",
      item_id: "1096",
      title: "电子书",
      amount_cents: 100,
      paid_at: "2026-11-09T01:00:00Z",
      delivered_at: "2026-11-09T01:01:00Z",
      detail: "",
      card_secret_id: 1,
      created_at: "",
    };
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/api/accounts") {
        return Promise.resolve([{ id: 1, remark: "主号", xianyu_id: "10001" }]);
      }
      if (path === "/api/accounts/1/orders") {
        return Promise.resolve({
          orders: [
            {
              ...baseOrder,
              id: 1,
              order_no: "202611090001",
              status: "delivered",
              flower_status: "sent",
              flower_at: "2026-11-09T01:31:00Z",
              flower_error: "",
            },
            {
              ...baseOrder,
              id: 2,
              order_no: "202611090002",
              status: "delivered",
              flower_status: "skipped",
              flower_at: "",
              flower_error: "买家已拉黑",
            },
          ],
        });
      }
      if (path === "/api/accounts/1/outbox") {
        return Promise.resolve({ tasks: [] });
      }
      if (path === "/api/accounts/1/cards") {
        return Promise.resolve({ cards });
      }
      if (path === "/api/accounts/1/delivery-rules") {
        return Promise.resolve({ rules: [] });
      }
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });

    render(<DeliveryPage />);

    expect(await screen.findByText("已请求")).toBeInTheDocument();
    expect(screen.getByText("已跳过")).toBeInTheDocument();
    expect(screen.getByText("买家已拉黑")).toBeInTheDocument();
  });

  it("卡密池页展示库存与已用数量", async () => {
    render(<DeliveryPage />);

    await userEvent.click(await screen.findByRole("tab", { name: "卡密池" }));

    expect(await screen.findByText("电子书池")).toBeInTheDocument();
    expect(screen.getByText("可用 3")).toBeInTheDocument();
    expect(screen.getByText("已发 1")).toBeInTheDocument();
  });

  it("新建卡密池提交名称和备注", async () => {
    render(<DeliveryPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "卡密池" }));
    await screen.findByText("电子书池");

    await userEvent.click(screen.getByRole("button", { name: /新建卡密池/ }));
    await userEvent.type(await screen.findByPlaceholderText("如：电子书提取码"), "电子书");
    await userEvent.type(screen.getByPlaceholderText("使用说明、网盘地址等"), "网盘链接");
    await userEvent.click(screen.getByRole("button", { name: /创\s*建/ }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe("/api/accounts/1/cards");
    expect(vi.mocked(api.post).mock.calls[0][1]).toMatchObject({
      name: "电子书",
      note: "网盘链接",
    });
  });

  it("导入卡密按行提交原始文本", async () => {
    render(<DeliveryPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "卡密池" }));
    await screen.findByText("电子书池");

    await userEvent.click(screen.getAllByRole("button", { name: "导入卡密" })[0]);
    await screen.findByText(
      "每行一条，自动跳过空行与重复卡密；也可以从 txt/csv 文件导入，CSV 取第一列。"
    );
    const textarea = document.querySelector(".ant-modal textarea") as HTMLTextAreaElement;
    await userEvent.type(textarea, "A\nB");
    await userEvent.click(screen.getByRole("button", { name: /^导\s*入$/ }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe("/api/cards/10/secrets");
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({ text: "A\nB" });
  });

  it("从 txt 文件读取卡密，去重后填入并提交", async () => {
    render(<DeliveryPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "卡密池" }));
    await screen.findByText("电子书池");

    await userEvent.click(screen.getAllByRole("button", { name: "导入卡密" })[0]);
    await screen.findByText(
      "每行一条，自动跳过空行与重复卡密；也可以从 txt/csv 文件导入，CSV 取第一列。"
    );
    const input = document.querySelector(".ant-modal input[type='file']") as HTMLInputElement;
    await userEvent.upload(input, new File(["X\nY\nX\n"], "cards.txt", { type: "text/plain" }));

    const textarea = document.querySelector(".ant-modal textarea") as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe("X\nY"));

    await userEvent.click(screen.getByRole("button", { name: /^导\s*入$/ }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe("/api/cards/10/secrets");
    expect(vi.mocked(api.post).mock.calls[0][1]).toEqual({ text: "X\nY" });
  });

  it("切换卡密池启用开关会调用接口", async () => {
    render(<DeliveryPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "卡密池" }));
    await screen.findByText("电子书池");

    await userEvent.click(screen.getAllByRole("switch")[0]);

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.patch).mock.calls[0][0]).toBe("/api/cards/10");
    expect(vi.mocked(api.patch).mock.calls[0][1]).toEqual({ enabled: false });
  });

  it("发货规则弹窗可点选变量并实时预览", async () => {
    render(<DeliveryPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "发货规则" }));
    await userEvent.click(await screen.findByRole("button", { name: /新建规则/ }));

    const modal = document.querySelector(".ant-modal") as HTMLElement;
    expect(within(modal).getByText("实时预览")).toBeInTheDocument();

    const textarea = modal.querySelector("textarea") as HTMLTextAreaElement;
    await userEvent.clear(textarea);
    await userEvent.click(within(modal).getByRole("button", { name: /卡\s*密/ }));

    expect(textarea).toHaveValue("{secret}");
    expect(within(modal).getByText(/CARD-8F2K-9Q1Z/)).toBeInTheDocument();

    await userEvent.type(within(modal).getByPlaceholderText("唯一编码，如 book-2024"), "book");
    await userEvent.type(within(modal).getByPlaceholderText("如：电子书默认模板"), "电子书");
    await userEvent.click(within(modal).getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe("/api/accounts/1/delivery-rules");
    expect(vi.mocked(api.post).mock.calls[0][1]).toMatchObject({
      code: "book",
      name: "电子书",
      template: "{secret}",
    });
  });
});
