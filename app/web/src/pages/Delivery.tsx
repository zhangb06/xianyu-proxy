import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { PlusOutlined, ReloadOutlined, UploadOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { api } from "../api";
import TemplateEditor from "../components/TemplateEditor";
import { sampleValues, TEMPLATE_VARIABLES } from "../lib/template";
import { parseSecretFile } from "../lib/secretFile";

const ORDER_STATUS: Record<string, { label: string; color: string }> = {
  paid: { label: "待发货", color: "processing" },
  delivering: { label: "发货中", color: "processing" },
  delivered: { label: "已发货", color: "success" },
  failed: { label: "失败", color: "error" },
  manual: { label: "需人工", color: "warning" },
};

const FLOWER_STATUS: Record<string, { label: string; color: string }> = {
  pending: { label: "待请求", color: "processing" },
  sent: { label: "已请求", color: "success" },
  skipped: { label: "已跳过", color: "default" },
  failed: { label: "失败", color: "error" },
};

const SECRET_STATUS: Record<string, { label: string; color: string }> = {
  available: { label: "未使用", color: "default" },
  claimed: { label: "已锁定", color: "processing" },
  used: { label: "已发放", color: "success" },
  manual: { label: "人工", color: "warning" },
};

const DEFAULT_TEMPLATE = "已收到付款，卡密如下：\n{secret}\n按说明使用，有问题随时找我。";

function statusTag(value: string, mapping: Record<string, { label: string; color: string }>) {
  const meta = mapping[value] || { label: value, color: "default" };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

export default function DeliveryPage() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);

  const [orders, setOrders] = useState<any[]>([]);
  const [orderStatus, setOrderStatus] = useState<string>("");
  const [outbox, setOutbox] = useState<any[]>([]);
  const [loadingOrders, setLoadingOrders] = useState(false);

  const [cards, setCards] = useState<any[]>([]);
  const [rules, setRules] = useState<any[]>([]);

  const [cardModal, setCardModal] = useState(false);
  const [cardForm] = Form.useForm();
  const [secretModal, setSecretModal] = useState<any>(null);
  const [secretText, setSecretText] = useState("");
  const [secretsDrawer, setSecretsDrawer] = useState<{ open: boolean; card: any; rows: any[] }>({
    open: false,
    card: null,
    rows: [],
  });
  const [ruleModal, setRuleModal] = useState<{ open: boolean; editing: any }>({
    open: false,
    editing: null,
  });
  const [ruleForm] = Form.useForm();

  const loadAccounts = useCallback(() => {
    api
      .get("/api/accounts")
      .then((rows: any[]) => {
        setAccounts(rows);
        setAccountId((prev) => prev ?? rows[0]?.id ?? null);
      })
      .catch((error) => message.error(String(error)));
  }, []);

  const loadOrders = useCallback(() => {
    if (accountId === null) return;
    setLoadingOrders(true);
    const query = orderStatus ? `?status=${orderStatus}` : "";
    Promise.all([
      api.get(`/api/accounts/${accountId}/orders${query}`),
      api.get(`/api/accounts/${accountId}/outbox`),
    ])
      .then(([orderRows, tasks]: any[]) => {
        setOrders(orderRows.orders);
        setOutbox(tasks.tasks);
      })
      .catch((error) => message.error(String(error)))
      .finally(() => setLoadingOrders(false));
  }, [accountId, orderStatus]);

  const loadCards = useCallback(() => {
    if (accountId === null) return;
    api
      .get(`/api/accounts/${accountId}/cards`)
      .then((data: any) => setCards(data.cards))
      .catch((error) => message.error(String(error)));
  }, [accountId]);

  const loadRules = useCallback(() => {
    if (accountId === null) return;
    api
      .get(`/api/accounts/${accountId}/delivery-rules`)
      .then((data: any) => setRules(data.rules))
      .catch((error) => message.error(String(error)));
  }, [accountId]);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    loadCards();
    loadRules();
    loadOrders();
  }, [loadCards, loadRules, loadOrders]);

  useEffect(() => {
    const timer = window.setInterval(loadOrders, 5000);
    return () => window.clearInterval(timer);
  }, [loadOrders]);

  const orderAction = async (path: string, successText: string) => {
    try {
      const result: any = await api.post(path);
      if (result && result.ok === false) {
        message.warning(result.reason || "操作失败");
      } else {
        message.success(successText);
      }
      loadOrders();
    } catch (error) {
      message.error(String(error));
    }
  };

  const createCard = async (values: any) => {
    try {
      await api.post(`/api/accounts/${accountId}/cards`, values);
      message.success("卡密池已创建");
      setCardModal(false);
      cardForm.resetFields();
      loadCards();
    } catch (error) {
      message.error(String(error));
    }
  };

  const importSecrets = async () => {
    if (!secretModal || !secretText.trim()) return;
    try {
      const result: any = await api.post(`/api/cards/${secretModal.id}/secrets`, {
        text: secretText,
      });
      message.success(`导入 ${result.inserted} 条，跳过重复 ${result.duplicates} 条`);
      setSecretModal(null);
      setSecretText("");
      loadCards();
    } catch (error) {
      message.error(String(error));
    }
  };

  const importFromFile = async (file: File) => {
    try {
      const result = parseSecretFile(file.name, await file.text());
      if (!result.ok) {
        message.error(result.error);
        return;
      }
      setSecretText(result.text);
      message.success(`已读取 ${result.count} 条，确认后点「导入」`);
    } catch (error) {
      message.error(`读取文件失败：${error}`);
    }
  };

  const openSecrets = async (card: any) => {
    try {
      const data: any = await api.get(`/api/cards/${card.id}/secrets`);
      setSecretsDrawer({ open: true, card, rows: data.secrets });
    } catch (error) {
      message.error(String(error));
    }
  };

  const saveRule = async (values: any) => {
    try {
      if (ruleModal.editing) {
        await api.patch(`/api/delivery-rules/${ruleModal.editing.id}`, values);
      } else {
        await api.post(`/api/accounts/${accountId}/delivery-rules`, values);
      }
      message.success("已保存");
      setRuleModal({ open: false, editing: null });
      ruleForm.resetFields();
      loadRules();
    } catch (error) {
      message.error(String(error));
    }
  };

  const ordersTab = (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Alert
        type="info"
        showIcon
        message="订单来自会话中的系统付款通知。只有「商品管理」中已启用自动发货的商品才会自动发送；未配置或已关闭的商品转为“需人工”。"
      />
      <Space>
        <Select
          allowClear
          placeholder="全部状态"
          style={{ width: 160 }}
          value={orderStatus || undefined}
          onChange={(value) => setOrderStatus(value ?? "")}
          options={Object.entries(ORDER_STATUS).map(([value, meta]) => ({
            value,
            label: meta.label,
          }))}
        />
        <Button icon={<ReloadOutlined />} onClick={loadOrders} loading={loadingOrders}>
          刷新
        </Button>
      </Space>
      <Table
        rowKey="id"
        size="small"
        dataSource={orders}
        loading={loadingOrders}
        pagination={{ pageSize: 20 }}
        columns={[
          {
            title: "订单号",
            dataIndex: "order_no",
            width: 200,
            render: (value: string) => (
              <Typography.Text copyable={{ text: value }} ellipsis style={{ maxWidth: 180 }}>
                {value}
              </Typography.Text>
            ),
          },
          {
            title: "商品",
            render: (_, row: any) => (
              <Space direction="vertical" size={0}>
                <span>{row.title || "-"}</span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {row.item_id ? `Item ${row.item_id}` : "未识别商品"}
                </Typography.Text>
              </Space>
            ),
          },
          { title: "买家", dataIndex: "buyer_id", width: 140, render: (v: string, row: any) => v || row.chat_id },
          {
            title: "状态",
            dataIndex: "status",
            width: 130,
            render: (value: string, row: any) => (
              <Space direction="vertical" size={0}>
                {statusTag(value, ORDER_STATUS)}
                {row.detail ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {row.detail}
                  </Typography.Text>
                ) : null}
              </Space>
            ),
          },
          {
            title: "付款时间",
            dataIndex: "paid_at",
            width: 160,
            render: (value: string) => (value ? dayjs(value).format("MM-DD HH:mm:ss") : "-"),
          },
          {
            title: "发货时间",
            dataIndex: "delivered_at",
            width: 160,
            render: (value: string) => (value ? dayjs(value).format("MM-DD HH:mm:ss") : "-"),
          },
          {
            title: "小红花",
            width: 150,
            render: (_, row: any) => {
              if (!row.flower_status) return "-";
              return (
                <Space direction="vertical" size={0}>
                  {statusTag(row.flower_status, FLOWER_STATUS)}
                  {row.flower_status === "sent" && row.flower_at ? (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {dayjs(row.flower_at).format("MM-DD HH:mm")}
                    </Typography.Text>
                  ) : null}
                  {row.flower_error ? (
                    <Typography.Text
                      type="danger"
                      ellipsis={{ tooltip: row.flower_error }}
                      style={{ fontSize: 12, maxWidth: 140 }}
                    >
                      {row.flower_error}
                    </Typography.Text>
                  ) : null}
                </Space>
              );
            },
          },
          {
            title: "操作",
            width: 230,
            render: (_, row: any) => (
              <Space size={4} wrap>
                {row.status !== "delivered" ? (
                  <Button
                    size="small"
                    type="link"
                    onClick={() => orderAction(`/api/orders/${row.id}/redeliver`, "已加入发货队列")}
                  >
                    重新发货
                  </Button>
                ) : null}
                {row.status !== "delivered" ? (
                  <Button
                    size="small"
                    type="link"
                    onClick={() =>
                      orderAction(`/api/orders/${row.id}/mark-delivered`, "已标记为已发货")
                    }
                  >
                    标记已发货
                  </Button>
                ) : (
                  <Button
                    size="small"
                    type="link"
                    onClick={() => orderAction(`/api/orders/${row.id}/consign`, "已调用虚拟发货")}
                  >
                    补标发货
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Card size="small" title="发货任务队列（outbox）">
        <Table
          rowKey="id"
          size="small"
          dataSource={outbox}
          pagination={false}
          locale={{ emptyText: "暂无待执行任务" }}
          columns={[
            { title: "任务", dataIndex: "dedup_key" },
            {
              title: "状态",
              dataIndex: "status",
              width: 100,
              render: (value: string) => {
                const color =
                  value === "sent" ? "success" : value === "dead" ? "error" : "processing";
                return <Tag color={color}>{value}</Tag>;
              },
            },
            { title: "尝试", dataIndex: "attempts", width: 80 },
            {
              title: "下次执行",
              dataIndex: "run_at",
              width: 160,
              render: (value: string) => dayjs(value).format("MM-DD HH:mm:ss"),
            },
            { title: "最近错误", dataIndex: "last_error", render: (value: string) => value || "-" },
          ]}
        />
      </Card>
    </Space>
  );

  const cardsTab = (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCardModal(true)}>
          新建卡密池
        </Button>
        <Button icon={<ReloadOutlined />} onClick={loadCards}>
          刷新
        </Button>
        <Typography.Text type="secondary">
          卡密池在「商品管理」页按商品选用；一个卡密池可被多个商品共用。
        </Typography.Text>
      </Space>
      <Table
        rowKey="id"
        size="small"
        dataSource={cards}
        pagination={false}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            render: (value: string, row: any) => (
              <Space direction="vertical" size={0}>
                <span>{value}</span>
                {row.note ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {row.note}
                  </Typography.Text>
                ) : null}
              </Space>
            ),
          },
          {
            title: "库存",
            width: 180,
            render: (_, row: any) => (
              <Space size={8}>
                <Tag color={row.available > 0 ? "success" : "error"}>可用 {row.available}</Tag>
                <Tag>已发 {row.used}</Tag>
                <Tag>共 {row.total}</Tag>
              </Space>
            ),
          },
          {
            title: "启用",
            dataIndex: "enabled",
            width: 90,
            render: (value: boolean, row: any) => (
              <Switch
                size="small"
                checked={value}
                onChange={async (checked) => {
                  try {
                    await api.patch(`/api/cards/${row.id}`, { enabled: checked });
                    loadCards();
                  } catch (error) {
                    message.error(String(error));
                  }
                }}
              />
            ),
          },
          {
            title: "操作",
            width: 300,
            render: (_, row: any) => (
              <Space size={4} wrap>
                <Button size="small" type="link" onClick={() => setSecretModal(row)}>
                  导入卡密
                </Button>
                <Button size="small" type="link" onClick={() => openSecrets(row)}>
                  查看卡密
                </Button>
                <Popconfirm
                  title="删除后卡密与绑定关系一并移除，确定？"
                  onConfirm={async () => {
                    try {
                      await api.delete(`/api/cards/${row.id}`);
                      message.success("已删除");
                      loadCards();
                    } catch (error) {
                      message.error(String(error));
                    }
                  }}
                >
                  <Button size="small" type="link" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
    </Space>
  );

  const rulesTab = (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            ruleForm.setFieldsValue({
              code: "",
              name: "",
              item_id: "",
              template: DEFAULT_TEMPLATE,
              priority: 100,
              enabled: true,
            });
            setRuleModal({ open: true, editing: null });
          }}
        >
          新建规则
        </Button>
        <Typography.Text type="secondary">
          旧版兜底：仅在商品存在旧版卡券绑定且规则启用时生效；建议优先在「商品管理」按商品配置。
        </Typography.Text>
      </Space>
      <Table
        rowKey="id"
        size="small"
        dataSource={rules}
        pagination={false}
        columns={[
          { title: "编码", dataIndex: "code", width: 120 },
          { title: "名称", dataIndex: "name", width: 140 },
          {
            title: "适用商品",
            dataIndex: "item_id",
            width: 140,
            render: (value: string) => value || <Tag>默认</Tag>,
          },
          {
            title: "模板",
            dataIndex: "template",
            render: (value: string) => (
              <Typography.Paragraph style={{ margin: 0 }} ellipsis={{ rows: 2 }}>
                {value}
              </Typography.Paragraph>
            ),
          },
          { title: "优先级", dataIndex: "priority", width: 90 },
          {
            title: "启用",
            dataIndex: "enabled",
            width: 80,
            render: (value: boolean, row: any) => (
              <Switch
                size="small"
                checked={value}
                onChange={async (checked) => {
                  try {
                    await api.patch(`/api/delivery-rules/${row.id}`, { enabled: checked });
                    loadRules();
                  } catch (error) {
                    message.error(String(error));
                  }
                }}
              />
            ),
          },
          {
            title: "操作",
            width: 140,
            render: (_, row: any) => (
              <Space size={4}>
                <Button
                  size="small"
                  type="link"
                  onClick={() => {
                    ruleForm.setFieldsValue(row);
                    setRuleModal({ open: true, editing: row });
                  }}
                >
                  编辑
                </Button>
                <Popconfirm
                  title="确定删除该规则？"
                  onConfirm={async () => {
                    try {
                      await api.delete(`/api/delivery-rules/${row.id}`);
                      loadRules();
                    } catch (error) {
                      message.error(String(error));
                    }
                  }}
                >
                  <Button size="small" type="link" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
    </Space>
  );

  return (
    <Card
      title="自动发货"
      extra={
        <Select
          style={{ width: 200 }}
          placeholder="选择账号"
          value={accountId}
          onChange={(value) => setAccountId(value)}
          options={accounts.map((account) => ({
            value: account.id,
            label: account.remark || account.xianyu_id,
          }))}
        />
      }
    >
      {accountId === null ? (
        <Alert type="warning" showIcon message="请先在「账号」页添加并登录账号" />
      ) : (
        <Tabs
          items={[
            { key: "orders", label: "订单", children: ordersTab },
            { key: "cards", label: "卡密池", children: cardsTab },
            { key: "rules", label: "发货规则", children: rulesTab },
          ]}
        />
      )}

      <Modal
        open={cardModal}
        title="新建卡密池"
        onCancel={() => setCardModal(false)}
        onOk={() => cardForm.submit()}
        okText="创建"
      >
        <Form form={cardForm} layout="vertical" onFinish={createCard}>
          <Form.Item name="name" label="名称" rules={[{ required: true, min: 1, max: 100 }]}>
            <Input placeholder="如：电子书提取码" />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input.TextArea rows={2} placeholder="使用说明、网盘地址等" maxLength={2000} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={secretModal !== null}
        title={`导入卡密 - ${secretModal?.name || ""}`}
        onCancel={() => setSecretModal(null)}
        onOk={importSecrets}
        okText="导入"
      >
        <Typography.Paragraph type="secondary">
          每行一条，自动跳过空行与重复卡密；也可以从 txt/csv 文件导入，CSV 取第一列。
        </Typography.Paragraph>
        <Space style={{ marginBottom: 8 }}>
          <Upload
            accept=".txt,.csv,text/plain,text/csv"
            showUploadList={false}
            beforeUpload={(file) => {
              void importFromFile(file);
              return false;
            }}
          >
            <Button icon={<UploadOutlined />}>从文件导入</Button>
          </Upload>
          <Typography.Text type="secondary">支持 .txt / .csv</Typography.Text>
        </Space>
        <Input.TextArea
          rows={10}
          value={secretText}
          onChange={(e) => setSecretText(e.target.value)}
          placeholder={"SECRET-0001\nSECRET-0002"}
        />
      </Modal>

      <Drawer
        open={secretsDrawer.open}
        width={720}
        title={`卡密 - ${secretsDrawer.card?.name || ""}`}
        onClose={() => setSecretsDrawer({ open: false, card: null, rows: [] })}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="卡密仅展示脱敏结果；发放记录以订单与卡密状态为准。"
        />
        <Table
          rowKey="id"
          size="small"
          dataSource={secretsDrawer.rows}
          pagination={{ pageSize: 20 }}
          columns={[
            { title: "卡密", dataIndex: "masked" },
            {
              title: "状态",
              dataIndex: "status",
              width: 100,
              render: (value: string) => statusTag(value, SECRET_STATUS),
            },
            { title: "订单号", dataIndex: "order_no", render: (value: string) => value || "-" },
            {
              title: "发放时间",
              dataIndex: "used_at",
              width: 160,
              render: (value: string) => (value ? dayjs(value).format("MM-DD HH:mm:ss") : "-"),
            },
          ]}
        />
      </Drawer>

      <Modal
        open={ruleModal.open}
        title={ruleModal.editing ? "编辑发货规则" : "新建发货规则"}
        onCancel={() => setRuleModal({ open: false, editing: null })}
        onOk={() => ruleForm.submit()}
        okText="保存"
      >
        <Form form={ruleForm} layout="vertical" onFinish={saveRule}>
          <Form.Item
            name="code"
            label="编码"
            rules={[{ required: true, min: 1, max: 100 }]}
          >
            <Input disabled={Boolean(ruleModal.editing)} placeholder="唯一编码，如 book-2024" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, min: 1, max: 100 }]}>
            <Input placeholder="如：电子书默认模板" />
          </Form.Item>
          <Form.Item name="item_id" label="商品 ID（留空表示该账号默认）">
            <Input placeholder="闲鱼商品 ID" />
          </Form.Item>
          <Form.Item
            name="template"
            label="发货模板"
            rules={[{ required: true, min: 1, max: 4000 }]}
            extra="请插入「卡密」变量，否则买家收不到卡密。"
          >
            <TemplateEditor rows={6} variables={TEMPLATE_VARIABLES} values={sampleValues()} />
          </Form.Item>
          <Space size="large">
            <Form.Item name="priority" label="优先级（越小越优先）">
              <InputNumber min={0} max={10000} />
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </Card>
  );
}
