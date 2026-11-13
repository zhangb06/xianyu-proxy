import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Input,
  InputNumber,
  List,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { SendOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { api } from "../api";
import { accountLabel } from "../lib/account";
import { useIsMobile } from "../lib/useMobile";

export default function ChatsPage() {
  const mobile = useIsMobile();
  const [accounts, setAccounts] = useState<any[]>([]);
  const [accountFilter, setAccountFilter] = useState<number | null>(null);
  const [chats, setChats] = useState<any[]>([]);
  const [active, setActive] = useState<any>(null);
  const [messages, setMessages] = useState<any[]>([]);
  const [replyText, setReplyText] = useState("");
  const [pauseOpen, setPauseOpen] = useState(false);
  const [pauseMinutes, setPauseMinutes] = useState(60);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const loadChats = useCallback(() => {
    const path = accountFilter ? `/api/chats?account_id=${accountFilter}` : "/api/chats";
    api
      .get(path)
      .then(setChats)
      .catch((error) => message.error(String(error)));
  }, [accountFilter]);

  useEffect(() => {
    api.get("/api/accounts").then(setAccounts).catch(() => undefined);
  }, []);

  useEffect(() => {
    loadChats();
    const timer = window.setInterval(loadChats, 4000);
    return () => window.clearInterval(timer);
  }, [loadChats]);

  const openChat = async (chat: any) => {
    setActive(chat);
    try {
      const rows = await api.get(`/api/chats/${chat.id}/messages`);
      setMessages(rows);
    } catch (error) {
      message.error(String(error));
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const refreshMessages = async () => {
    if (!active) return;
    const rows = await api.get(`/api/chats/${active.id}/messages`);
    setMessages(rows);
  };

  const sendReply = async () => {
    if (!active || !replyText.trim()) return;
    try {
      await api.post(`/api/chats/${active.id}/reply`, { text: replyText.trim() });
      setReplyText("");
      await refreshMessages();
    } catch (error) {
      message.error(String(error));
    }
  };

  const pauseChat = async () => {
    if (!active) return;
    try {
      await api.post(`/api/chats/${active.id}/pause`, { minutes: pauseMinutes });
      message.success(`已暂停 ${pauseMinutes} 分钟`);
      setPauseOpen(false);
      loadChats();
    } catch (error) {
      message.error(String(error));
    }
  };

  const unpauseChat = async () => {
    if (!active) return;
    try {
      await api.post(`/api/chats/${active.id}/unpause`);
      message.success("已恢复自动回复");
      loadChats();
      setActive({ ...active, paused: false });
    } catch (error) {
      message.error(String(error));
    }
  };

  return (
    <Card
      title="会话"
      extra={
        <Select
          allowClear
          placeholder="全部账号"
          style={{ width: 200, maxWidth: "100%" }}
          value={accountFilter}
          onChange={(v) => setAccountFilter(v ?? null)}
          options={accounts.map((a) => ({ value: a.id, label: accountLabel(a) }))}
        />
      }
    >
      <Table
        scroll={{ x: "max-content" }}
        rowKey="id"
        size="small"
        dataSource={chats}
        pagination={{ pageSize: 20 }}
        columns={[
          { title: "买家", dataIndex: "peer_name", render: (v: string, row: any) => v || row.peer_id || row.chat_id },
          { title: "账号", dataIndex: "account_remark", render: (v: string) => v || "-" },
          { title: "商品", dataIndex: "item_id", render: (v: string) => v || "-" },
          {
            title: "自动回复",
            dataIndex: "paused",
            width: 100,
            render: (v: boolean, row: any) =>
              v ? <Tag color="warning">已暂停至 {dayjs(row.paused_until).format("HH:mm")}</Tag> : <Tag color="success">开启</Tag>,
          },
          {
            title: "最近消息",
            dataIndex: "last_message_at",
            width: 160,
            render: (v: string) => dayjs(v).format("MM-DD HH:mm:ss"),
          },
          {
            title: "操作",
            width: 100,
            render: (_, row: any) => (
              <Button size="small" type="link" onClick={() => openChat(row)}>
                查看
              </Button>
            ),
          },
        ]}
      />
      <Drawer
        width={mobile ? "100%" : 480}
        open={active !== null}
        title={
          <Space>
            {active?.peer_name || active?.chat_id}
            {active?.paused ? <Tag color="warning">已暂停</Tag> : null}
          </Space>
        }
        onClose={() => setActive(null)}
        extra={
          <Space>
            {active?.paused ? (
              <Button size="small" onClick={unpauseChat}>
                恢复自动回复
              </Button>
            ) : (
              <Button size="small" onClick={() => setPauseOpen(true)}>
                暂停自动回复
              </Button>
            )}
          </Space>
        }
      >
        <List
          size="small"
          dataSource={messages}
          style={{ minHeight: "60vh" }}
          renderItem={(item: any) => (
            <List.Item style={{ justifyContent: item.direction === "out" ? "flex-end" : "flex-start" }}>
              <div
                style={{
                  maxWidth: "75%",
                  padding: "8px 12px",
                  borderRadius: 8,
                  background: item.direction === "out" ? "#1677ff" : "#f5f5f5",
                  color: item.direction === "out" ? "#fff" : "inherit",
                }}
              >
                <div>{item.text}</div>
                <div style={{ fontSize: 11, opacity: 0.7 }}>
                  {item.status === "failed" ? "发送失败 " : ""}
                  {dayjs(item.timestamp_ms).format("MM-DD HH:mm:ss")}
                </div>
              </div>
            </List.Item>
          )}
        />
        <div ref={messagesEndRef} />
        <Space.Compact style={{ width: "100%", marginTop: 12 }}>
          <Input
            placeholder="手动回复（以当前账号发送）"
            value={replyText}
            onChange={(e) => setReplyText(e.target.value)}
            onPressEnter={sendReply}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={sendReply}>
            发送
          </Button>
        </Space.Compact>
      </Drawer>
      <Modal
        open={pauseOpen}
        title="暂停自动回复"
        onCancel={() => setPauseOpen(false)}
        onOk={pauseChat}
        okText="暂停"
      >
        <Typography.Paragraph>暂停期间该会话不再自动回复，可随时恢复。</Typography.Paragraph>
        <Space>
          <span>暂停时长（分钟）</span>
          <InputNumber min={1} max={10080} value={pauseMinutes} onChange={(v) => setPauseMinutes(v ?? 60)} />
        </Space>
      </Modal>
    </Card>
  );
}
