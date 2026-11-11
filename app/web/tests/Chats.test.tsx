import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import ChatsPage from "../src/pages/Chats";

vi.mock("../src/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

function chat(overrides: Record<string, unknown> = {}) {
  return {
    id: 3,
    account_id: 1,
    account_remark: "主号",
    chat_id: "c1",
    peer_id: "buyer1",
    peer_name: "买家",
    item_id: "42",
    paused: false,
    paused_until: "",
    last_message_at: "2026-11-06T10:00:00Z",
    ...overrides,
  };
}

const messages = [
  {
    id: 1,
    direction: "in",
    kind: "text",
    sender_id: "buyer1",
    text: "在吗",
    status: "sent",
    timestamp_ms: 1762400000000,
  },
];

function mockGet(row = chat()) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === "/api/accounts") {
      return Promise.resolve([{ id: 1, remark: "主号", xianyu_id: "10001" }]);
    }
    if (path === "/api/chats" || path.startsWith("/api/chats?")) {
      return Promise.resolve([row]);
    }
    if (path === "/api/chats/3/messages") {
      return Promise.resolve(messages);
    }
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
}

describe("会话", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.post).mockResolvedValue({ ok: true, msg_id: "m1" });
    mockGet();
  });

  it("列表展示会话并在抽屉里查看消息", async () => {
    render(<ChatsPage />);

    expect(await screen.findByText("买家")).toBeInTheDocument();
    expect(screen.getAllByText("主号").length).toBeGreaterThan(0);
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("开启")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^查\s*看$/ }));

    expect(await screen.findByText("在吗")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith("/api/chats/3/messages");
  });

  it("手动回复后提交文本并刷新消息", async () => {
    render(<ChatsPage />);
    await screen.findByText("买家");
    await userEvent.click(screen.getByRole("button", { name: /^查\s*看$/ }));
    await screen.findByText("在吗");

    await userEvent.type(
      screen.getByPlaceholderText("手动回复（以当前账号发送）"),
      "在的"
    );
    await userEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/chats/3/reply", { text: "在的" })
    );
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith("/api/chats/3/messages")
    );
  });

  it("暂停自动回复提交暂停时长", async () => {
    render(<ChatsPage />);
    await screen.findByText("买家");
    await userEvent.click(screen.getByRole("button", { name: /^查\s*看$/ }));
    await screen.findByText("在吗");

    await userEvent.click(screen.getByRole("button", { name: "暂停自动回复" }));
    await userEvent.click(await screen.findByRole("button", { name: /^暂\s*停$/ }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/chats/3/pause", { minutes: 60 })
    );
  });

  it("已暂停会话可恢复自动回复", async () => {
    mockGet(chat({ paused: true, paused_until: "2026-11-06T12:00:00Z" }));
    render(<ChatsPage />);

    expect(await screen.findByText(/已暂停至/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^查\s*看$/ }));
    await userEvent.click(await screen.findByRole("button", { name: "恢复自动回复" }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/chats/3/unpause")
    );
  });

  it("按账号筛选会话", async () => {
    render(<ChatsPage />);
    await screen.findByText("买家");

    await userEvent.click(screen.getByRole("combobox"));
    await screen.findByText("主号（10001）", { selector: ".ant-select-item-option-content" });
    await userEvent.click(
      document.querySelector('.ant-select-item-option[title="主号（10001）"]') as HTMLElement
    );

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith("/api/chats?account_id=1")
    );
  });
});
