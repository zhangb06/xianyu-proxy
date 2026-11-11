import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Typography,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { api } from "../api";
import { StatusTag } from "./Overview";

const VERIFICATION_OPTIONS = [
  { value: "", label: "跟随全局（默认人工）" },
  { value: "manual", label: "人工验证" },
  { value: "auto_fallback", label: "自动验证（失败后转人工）" },
];

function QrLoginModal({
  open,
  account,
  onClose,
}: {
  open: boolean;
  account: any | null;
  onClose: (changed: boolean) => void;
}) {
  const [qr, setQr] = useState("");
  const [state, setState] = useState("waiting");
  const [verifyUrl, setVerifyUrl] = useState("");
  const [errorText, setErrorText] = useState("");
  const [attempt, setAttempt] = useState(0);
  const timerRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => {
    if (!open) {
      stopPolling();
      return;
    }
    let cancelled = false;
    setQr("");
    setState("waiting");
    setVerifyUrl("");
    setErrorText("");

    api
      .post("/api/accounts/qr", account ? { account_id: account.id } : {})
      .then((result: any) => {
        if (cancelled) return;
        setQr(result.qr);
        timerRef.current = window.setInterval(async () => {
          try {
            const status: any = await api.get(`/api/accounts/qr/${result.session_id}`);
            setState(status.state);
            if (status.state === "verification_required") {
              setVerifyUrl(status.url || "");
              stopPolling();
            } else if (status.state === "success") {
              stopPolling();
              message.success(`已登录账号 ${status.account?.xianyu_id ?? ""}`);
              onClose(true);
            } else if (status.state === "expired" || status.state === "error") {
              stopPolling();
              setErrorText(status.message ?? "二维码已过期，请重新获取");
            }
          } catch {
            // 保持轮询
          }
        }, 1500);
      })
      .catch((error) => {
        message.error(String(error));
        onClose(false);
      });

    return () => {
      cancelled = true;
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, account?.id, attempt]);

  return (
    <Modal
      open={open}
      title={account ? `重新扫码登录 ${account.xianyu_id}` : "扫码添加账号"}
      footer={null}
      onCancel={() => onClose(false)}
      maskClosable={false}
    >
      <div style={{ textAlign: "center", minHeight: 260 }}>
        {qr && <img src={qr} alt="登录二维码" style={{ width: 220, height: 220 }} />}
        {!qr && <Typography.Text>正在获取二维码…</Typography.Text>}
        <div style={{ marginTop: 12 }}>
          {account && (
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              请用闲鱼号 {account.xianyu_id} 扫码；扫其他账号会被拒绝，不会新增账号或覆盖登录。
            </Typography.Paragraph>
          )}
          {state === "waiting" && <Typography.Text>请使用闲鱼 App 扫码</Typography.Text>}
          {state === "scanned" && <Typography.Text type="success">已扫码，请在手机上确认</Typography.Text>}
          {state === "verification_required" && (
            <>
              <Typography.Text type="warning">需要人脸/滑块验证：</Typography.Text>
              {verifyUrl && (
                <Typography.Paragraph copyable={{ text: verifyUrl }} style={{ wordBreak: "break-all" }}>
                  {verifyUrl}
                </Typography.Paragraph>
              )}
            </>
          )}
          {errorText && (
            <Alert
              type="error"
              showIcon
              message="登录未完成"
              description={errorText}
              style={{ marginTop: 12, textAlign: "left" }}
              action={
                <Button size="small" onClick={() => setAttempt((value) => value + 1)}>
                  重新获取二维码
                </Button>
              }
            />
          )}
        </div>
      </div>
    </Modal>
  );
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [qrOpen, setQrOpen] = useState(false);
  const [qrTarget, setQrTarget] = useState<any | null>(null);
  const [loadingNick, setLoadingNick] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [form] = Form.useForm();

  const load = useCallback(() => {
    api
      .get("/api/accounts")
      .then((rows: any[]) => setAccounts(rows))
      .catch((error) => message.error(String(error)));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 4000);
    return () => window.clearInterval(timer);
  }, [load]);

  const toggle = async (account: any) => {
    try {
      await api.post(`/api/accounts/${account.id}/${account.enabled ? "disable" : "enable"}`);
      load();
    } catch (error) {
      message.error(String(error));
    }
  };

  const remove = async (account: any) => {
    try {
      await api.delete(`/api/accounts/${account.id}`);
      message.success("已删除");
      load();
    } catch (error) {
      message.error(String(error));
    }
  };

  const openEdit = async (row: any) => {
    setEditing({ ...row, auto_polish: false, flower_enabled: false });
    form.setFieldsValue(row);
    try {
      const ops: any = await api.get(`/api/accounts/${row.id}/ops`);
      form.setFieldsValue(ops);
      setEditing((prev: any) => (prev ? { ...prev, ...ops } : prev));
    } catch (error) {
      message.error(String(error));
    }
  };

  const loadNickname = async () => {
    if (!editing) return;
    setLoadingNick(true);
    try {
      const result: any = await api.post(`/api/accounts/${editing.id}/nickname`);
      form.setFieldsValue({ remark: result.nickname });
      message.success(`已读取闲鱼昵称：${result.nickname}`);
    } catch (error) {
      message.warning(String(error));
    } finally {
      setLoadingNick(false);
    }
  };

  const saveEdit = async (values: any) => {
    try {
      const { auto_polish, flower_enabled, ...account } = values;
      await api.patch(`/api/accounts/${editing.id}`, account);
      await api.put(`/api/accounts/${editing.id}/ops`, { auto_polish, flower_enabled });
      message.success("已保存");
      setEditing(null);
      load();
    } catch (error) {
      message.error(String(error));
    }
  };

  return (
    <Card
      title="账号管理"
      extra={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setQrTarget(null);
            setQrOpen(true);
          }}
        >
          扫码添加
        </Button>
      }
    >
      <Table
        rowKey="id"
        dataSource={accounts}
        pagination={false}
        columns={[
          { title: "ID", dataIndex: "id", width: 64 },
          { title: "备注", dataIndex: "remark", render: (v: string) => v || "未命名（编辑可设置）" },
          { title: "闲鱼 ID", dataIndex: "xianyu_id" },
          {
            title: "状态",
            dataIndex: "status",
            render: (v: string, row: any) => (
              <Space>
                <StatusTag status={v} />
                {row.last_error && (
                  <Typography.Text type="danger" style={{ fontSize: 12 }}>
                    {row.last_error}
                  </Typography.Text>
                )}
              </Space>
            ),
          },
          { title: "代理", dataIndex: "proxy_url", render: (v: string) => v || "-" },
          {
            title: "验证模式",
            dataIndex: "verification_mode",
            render: (v: string) => (v ? (v === "manual" ? "人工" : "自动兜底") : "全局"),
          },
          {
            title: "操作",
            render: (_, row: any) => (
              <Space>
                <Button size="small" onClick={() => toggle(row)}>
                  {row.enabled ? "停用" : "启用"}
                </Button>
                <Button
                  size="small"
                  onClick={() => {
                    setQrTarget(row);
                    setQrOpen(true);
                  }}
                >
                  重新扫码
                </Button>
                <Button size="small" onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm title="删除账号及其凭据、会话与消息记录？" onConfirm={() => remove(row)}>
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <QrLoginModal
        open={qrOpen}
        account={qrTarget}
        onClose={(changed) => {
          setQrOpen(false);
          if (changed) load();
        }}
      />
      <Modal
        open={editing !== null}
        title={`编辑账号 ${editing?.xianyu_id ?? ""}`}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={saveEdit} preserve={false}>
          <Form.Item name="remark" label="备注">
            <Input
              maxLength={64}
              placeholder="便于识别的名称，如：主号 / 18号当铺"
              addonAfter={
                <Button type="link" size="small" loading={loadingNick} onClick={loadNickname}>
                  读取闲鱼昵称
                </Button>
              }
            />
          </Form.Item>
          <Form.Item name="proxy_url" label="代理地址" rules={[{ type: "url", message: "格式如 http://127.0.0.1:7890" }]}>
            <Input placeholder="http://127.0.0.1:7890，留空为直连" />
          </Form.Item>
          <Form.Item name="verification_mode" label="验证模式">
            <Select options={VERIFICATION_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="auto_polish"
            label="每天自动擦亮在售商品"
            valuePropName="checked"
            extra="按北京时间每天擦亮一次；请求间隔 2 秒，登录失效时自动暂停并退避。"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="flower_enabled"
            label="发货后自动请求小红花"
            valuePropName="checked"
            extra="发货消息发送成功 30 分钟后私聊买家一次；人工标记发货不触发。"
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
