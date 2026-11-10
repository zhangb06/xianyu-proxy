import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import OverviewPage from "../src/pages/Overview";

vi.mock("../src/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

function overview() {
  return {
    accounts: {
      online: 1,
      total: 2,
      items: [
        { id: 1, remark: "主号", xianyu_id: "10001", status: "online" },
        { id: 2, remark: "", xianyu_id: "10002", status: "error" },
      ],
    },
    today: { inbound: 5, outbound: 3 },
    issues: [
      {
        type: "ws.loop_error",
        level: "warning",
        detail: { error: "连接断开" },
        created_at: "2026-11-06T10:00:00Z",
      },
      {
        type: "reply.send_error",
        level: "error",
        detail: { reason: "被限流" },
        created_at: "2026-11-06T11:00:00Z",
      },
    ],
  };
}

describe("总览", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("展示账号统计、账号状态与告警", async () => {
    vi.mocked(api.get).mockResolvedValue(overview());

    render(<OverviewPage />);

    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/overview"));
    expect(await screen.findByText("在线账号")).toBeInTheDocument();
    expect(screen.getByText("/ 2")).toBeInTheDocument();
    expect(screen.getByText("今日收到")).toBeInTheDocument();
    expect(screen.getByText("今日发出")).toBeInTheDocument();
    expect(screen.getByText("待处理告警")).toBeInTheDocument();

    expect(screen.getByText("主号")).toBeInTheDocument();
    expect(screen.getByText("10001")).toBeInTheDocument();
    expect(screen.getByText("在线")).toBeInTheDocument();
    expect(screen.getByText("错误")).toBeInTheDocument();

    expect(screen.getByText("[ws.loop_error] 连接断开")).toBeInTheDocument();
    expect(screen.getByText("[reply.send_error] 被限流")).toBeInTheDocument();
  });

  it("没有告警时展示空状态", async () => {
    vi.mocked(api.get).mockResolvedValue({ ...overview(), issues: [] });

    render(<OverviewPage />);

    expect(await screen.findByText("暂无告警")).toBeInTheDocument();
  });
});
