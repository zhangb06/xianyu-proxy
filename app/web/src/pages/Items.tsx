import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Form,
  Image,
  Input,
  Popconfirm,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { ReloadOutlined, SettingOutlined, SyncOutlined, ThunderboltOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { api } from "../api";
import TemplateEditor from "../components/TemplateEditor";
import { accountLabel, pickAccountId, rememberAccountId } from "../lib/account";
import { useIsMobile } from "../lib/useMobile";
import { FIXED_TEMPLATE_VARIABLES, sampleValues, TEMPLATE_VARIABLES } from "../lib/template";

const MODE_META: Record<string, { label: string; color: string }> = {
  card: { label: "卡密池", color: "blue" },
  fixed: { label: "固定内容", color: "purple" },
  manual: { label: "人工处理", color: "orange" },
};

const DEFAULT_CARD_TEMPLATE = "已收到付款，卡密如下：\n{secret}\n按说明使用，有问题随时找我。";
const DEFAULT_FIXED_CONTENT = "感谢购买！下载链接：\n（请填写链接或提取码）";

export default function ItemsPage() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [items, setItems] = useState<any[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [lastSyncedAt, setLastSyncedAt] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [cards, setCards] = useState<any[]>([]);
  const [active, setActive] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [polishing, setPolishing] = useState("");
  const [polishingAll, setPolishingAll] = useState(false);
  const [form] = Form.useForm();
  const mobile = useIsMobile();
  const mode = Form.useWatch("mode", form);
  const delivery = active?.delivery;

  const loadAccounts = useCallback(() => {
    api
      .get("/api/accounts")
      .then((rows: any[]) => {
        setAccounts(rows);
        setAccountId((prev) => pickAccountId(rows, prev));
      })
      .catch((error) => message.error(String(error)));
  }, []);

  const loadItems = useCallback(() => {
    if (accountId === null) return;
    setLoading(true);
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    const suffix = params.toString() ? `?${params.toString()}` : "";
    api
      .get(`/api/accounts/${accountId}/items${suffix}`)
      .then((data: any) => {
        setItems(data.items);
        setCounts(data.counts || {});
        setLastSyncedAt(data.last_synced_at || "");
      })
      .catch((error) => message.error(String(error)))
      .finally(() => setLoading(false));
  }, [accountId, query]);

  const loadCards = useCallback(() => {
    if (accountId === null) return;
    api
      .get(`/api/accounts/${accountId}/cards`)
      .then((data: any) => setCards(data.cards))
      .catch((error) => message.error(String(error)));
  }, [accountId]);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    loadItems();
    loadCards();
  }, [loadItems, loadCards]);

  const polishAll = async () => {
    if (accountId === null) return;
    setPolishingAll(true);
    try {
      const result: any = await api.post(`/api/accounts/${accountId}/items/polish-all`);
      message.success(
        `一键擦亮完成：成功 ${result.polished}，已擦 ${result.already}，失败 ${result.failed}`
      );
      loadItems();
    } catch (error) {
      message.error(String(error));
    } finally {
      setPolishingAll(false);
    }
  };

  const syncItems = async () => {
    if (accountId === null) return;
    setSyncing(true);
    try {
      const result: any = await api.post(`/api/accounts/${accountId}/items/sync`);
      const onSale = result.counts?.["在售"] ?? 0;
      message.success(`同步完成，当前在售 ${onSale} 个商品`);
      loadItems();
    } catch (error) {
      message.error(String(error));
    } finally {
      setSyncing(false);
    }
  };

  const polish = async (item: any) => {
    if (accountId === null) return;
    setPolishing(item.item_id);
    try {
      const result: any = await api.post(
        `/api/accounts/${accountId}/items/${item.item_id}/polish`
      );
      message.success(result.message || "擦亮成功");
      loadItems();
    } catch (error) {
      message.error(String(error));
    } finally {
      setPolishing("");
    }
  };

  const openConfig = (item: any) => {
    const config = item.delivery;
    const legacyManual = config?.mode === "manual";
    form.setFieldsValue({
      enabled: legacyManual ? false : config?.enabled ?? false,
      mode: legacyManual ? "card" : config?.mode ?? "card",
      card_id: config?.card_id ?? undefined,
      template: config?.template || DEFAULT_CARD_TEMPLATE,
      content: config?.content || DEFAULT_FIXED_CONTENT,
      auto_consign: config?.auto_consign ?? true,
      remark: config?.remark || "",
    });
    setActive(item);
  };

  const saveConfig = async (values: any) => {
    if (!active || accountId === null) return;
    setSaving(true);
    try {
      await api.put(
        `/api/accounts/${accountId}/items/${active.item_id}/delivery`,
        values
      );
      message.success("发货配置已保存");
      setActive(null);
      loadItems();
    } catch (error) {
      message.error(String(error));
    } finally {
      setSaving(false);
    }
  };

  const removeConfig = async () => {
    if (!active || accountId === null) return;
    try {
      await api.delete(`/api/accounts/${accountId}/items/${active.item_id}/delivery`);
      message.success("已恢复为未配置");
      setActive(null);
      loadItems();
    } catch (error) {
      message.error(String(error));
    }
  };

  const cardOptions = useMemo(
    () =>
      cards.map((card) => ({
        value: card.id,
        label: `${card.name}（可用 ${card.available}${card.enabled ? "" : "，已停用"}）`,
        disabled: !card.enabled || card.available === 0,
      })),
    [cards]
  );

  const summary = `在售 ${counts["在售"] || 0} 个商品`;

  return (
    <Card
      title="商品管理"
      extra={
        <Select
          style={{ width: 200, maxWidth: "100%" }}
          placeholder="选择账号"
          value={accountId}
          onChange={(value) => {
            setAccountId(value);
            rememberAccountId(value);
          }}
          options={accounts.map((account) => ({
            value: account.id,
            label: accountLabel(account),
          }))}
        />
      }
    >
      {accountId === null ? (
        <Alert type="warning" showIcon message="请先在「账号」页添加并登录账号" />
      ) : (
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Space wrap>
            <Button
              type="primary"
              icon={<SyncOutlined />}
              loading={syncing}
              onClick={syncItems}
            >
              同步商品
            </Button>
            <Button icon={<ReloadOutlined />} onClick={loadItems} loading={loading}>
              刷新
            </Button>
            <Button icon={<ThunderboltOutlined />} onClick={polishAll} loading={polishingAll}>
              一键擦亮
            </Button>
            <Input.Search
              allowClear
              placeholder="搜索标题或商品 ID"
              style={{ width: 260 }}
              onSearch={(value) => setQuery(value)}
            />
            <Typography.Text type="secondary">
              {summary}
              {lastSyncedAt
                ? ` · 最近同步 ${dayjs(lastSyncedAt).format("MM-DD HH:mm")}`
                : ""}
            </Typography.Text>
          </Space>
          <Alert
            type="info"
            showIcon
            message="这里只展示你正在出售的商品，已售出和已下架的商品不会出现。后台会定时自动同步，新上架/下架自动更新；也可点「同步商品」立即刷新。"
          />
          <Table
            scroll={{ x: "max-content" }}
            rowKey="id"
            size="small"
            dataSource={items}
            loading={loading}
            pagination={{ pageSize: 20, showSizeChanger: false }}
            locale={{ emptyText: "没有在售商品，点击「同步商品」从闲鱼拉取" }}
            columns={[
              {
                title: "封面",
                dataIndex: "cover_url",
                width: 72,
                render: (value: string) =>
                  value ? (
                    <Image src={value} width={48} height={48} style={{ objectFit: "cover", borderRadius: 4 }} />
                  ) : (
                    <div
                      style={{
                        width: 48,
                        height: 48,
                        borderRadius: 4,
                        background: "#f0f0f0",
                      }}
                    />
                  ),
              },
              {
                title: "商品",
                render: (_, row: any) => (
                  <Space direction="vertical" size={0}>
                    <Typography.Text strong ellipsis style={{ maxWidth: 420 }}>
                      {row.title}
                    </Typography.Text>
                    <Typography.Text type="secondary" copyable={{ text: row.item_id }} style={{ fontSize: 12 }}>
                      {row.item_id}
                    </Typography.Text>
                  </Space>
                ),
              },
              {
                title: "售价",
                dataIndex: "price_cents",
                width: 110,
                render: (value: number) => `¥${(value / 100).toFixed(2)}`,
              },
              {
                title: "自动发货",
                width: 190,
                render: (_, row: any) => {
                  if (!row.delivery) {
                    return <Tag>未配置</Tag>;
                  }
                  if (!row.delivery.enabled) {
                    return <Tag color="default">已关闭</Tag>;
                  }
                  const meta = MODE_META[row.delivery.mode] || {
                    label: row.delivery.mode,
                    color: "default",
                  };
                  return (
                    <Space direction="vertical" size={0}>
                      <Tag color={meta.color}>{meta.label}</Tag>
                      {row.delivery.mode === "card" ? (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {row.delivery.card_name
                            ? `${row.delivery.card_name} · 可用 ${row.delivery.card_available}${
                                cards.find((card) => card.id === row.delivery.card_id)?.enabled === false
                                  ? " · 已停用"
                                  : ""
                              }`
                            : "未选择卡密池"}
                        </Typography.Text>
                      ) : null}
                    </Space>
                  );
                },
              },
              {
                title: "擦亮",
                width: 170,
                render: (_, row: any) => (
                  <Space direction="vertical" size={0}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {row.polished_at
                        ? `最近 ${dayjs(row.polished_at).format("MM-DD HH:mm")}`
                        : "未擦亮"}
                    </Typography.Text>
                    {row.polish_error ? (
                      <Typography.Text
                        type="danger"
                        ellipsis={{ tooltip: row.polish_error }}
                        style={{ fontSize: 12, maxWidth: 160 }}
                      >
                        {row.polish_error}
                      </Typography.Text>
                    ) : null}
                  </Space>
                ),
              },
              {
                title: "操作",
                width: 210,
                render: (_, row: any) => (
                  <Space size={0} wrap>
                    <Button
                      size="small"
                      type="link"
                      loading={polishing === row.item_id}
                      onClick={() => polish(row)}
                    >
                      擦亮
                    </Button>
                    <Button
                      size="small"
                      type="link"
                      icon={<SettingOutlined />}
                      onClick={() => openConfig(row)}
                    >
                      配置发货
                    </Button>
                  </Space>
                ),
              },
            ]}
          />
        </Space>
      )}

      <Drawer
        open={active !== null}
        width={mobile ? "100%" : 560}
        title={
          <Space direction="vertical" size={0}>
            <span>{active?.title}</span>
            <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {active?.item_id} · ¥{((active?.price_cents || 0) / 100).toFixed(2)}
            </Typography.Text>
          </Space>
        }
        onClose={() => setActive(null)}
        extra={
          delivery ? (
            <Popconfirm title="删除后该商品将回退为未配置，确定？" onConfirm={removeConfig}>
              <Button danger size="small">
                删除配置
              </Button>
            </Popconfirm>
          ) : null
        }
        footer={
          <Space style={{ display: "flex", justifyContent: "flex-end" }}>
            <Button onClick={() => setActive(null)}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => form.submit()}>
              保存
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical" onFinish={saveConfig}>
          <Form.Item
            name="enabled"
            label="启用自动发货"
            valuePropName="checked"
            extra="默认关闭；开启并保存后，该商品付款才会自动发送"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="mode" label="发货方式">
            <Radio.Group>
              <Radio.Button value="card">卡密池</Radio.Button>
              <Radio.Button value="fixed">固定内容</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={
              mode === "card"
                ? "从卡密池领取一条卡密，套用模板后发送。适合提取码、账号密码等虚拟商品。"
                : "每次都发送同一段内容。适合固定网盘链接、使用说明等不需要库存的场景。"
            }
          />

          {mode === "card" ? (
            <>
              <Form.Item
                name="card_id"
                label="卡密池"
                extra={
                  cards.length === 0
                    ? "还没有卡密池，请先到「自动发货 → 卡密池」新建并导入卡密"
                    : undefined
                }
              >
                <Select
                  allowClear
                  placeholder="选择卡密池"
                  options={cardOptions}
                  notFoundContent="还没有卡密池，请到「自动发货 → 卡密池」新建并导入"
                />
              </Form.Item>
              <Form.Item name="template" label="发货模板">
                <TemplateEditor
                  rows={5}
                  variables={TEMPLATE_VARIABLES}
                  values={sampleValues(active?.title)}
                />
              </Form.Item>
            </>
          ) : null}

          {mode === "fixed" ? (
            <Form.Item
              name="content"
              label="固定发货内容"
              rules={[{ required: true, message: "请输入固定发货内容" }]}
            >
              <TemplateEditor
                rows={5}
                variables={FIXED_TEMPLATE_VARIABLES}
                values={sampleValues(active?.title, { secret: "" })}
              />
            </Form.Item>
          ) : null}

          <Form.Item
            name="auto_consign"
            label="发送后在闲鱼上自动标记为【已发货】"
            valuePropName="checked"
            extra="仅对真实订单号生效；失败会记录日志，可在订单页手动补标。"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input maxLength={200} placeholder="仅自己可见，如：网盘资料，售前确认成色" />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}
