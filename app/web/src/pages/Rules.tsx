import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
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
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { api } from "../api";
import { accountLabel, pickAccountId, rememberAccountId } from "../lib/account";

const BLOCK_KINDS = [
  { value: "user", label: "用户" },
  { value: "item", label: "商品" },
  { value: "keyword", label: "关键词" },
];

export default function RulesPage() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [bundle, setBundle] = useState<any>({ rules: [], config: {}, blocks: [] });
  const [ruleModal, setRuleModal] = useState<{ open: boolean; editing: any }>({ open: false, editing: null });
  const [ruleForm] = Form.useForm();
  const [configForm] = Form.useForm();
  const [blockForm] = Form.useForm();
  const [aiForm] = Form.useForm();
  const [aiConfig, setAiConfig] = useState<any>(null);
  const [aiTestText, setAiTestText] = useState("这个能便宜点吗？最低多少钱？");
  const [aiTestResult, setAiTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [aiTesting, setAiTesting] = useState(false);
  const [itemQuery, setItemQuery] = useState("");
  const [itemInfo, setItemInfo] = useState<any>(null);
  const [itemLoading, setItemLoading] = useState(false);
  const aiEnabled = Form.useWatch("enabled", aiForm);
  const aiListing = Form.useWatch("listing_price_yuan", aiForm);
  const aiFloor = Form.useWatch("price_floor_yuan", aiForm);
  const aiProductInfo = Form.useWatch("product_info", aiForm);

  const loadAi = useCallback(() => {
    if (accountId === null) return;
    api
      .get(`/api/accounts/${accountId}/ai`)
      .then((config: any) => {
        setAiConfig(config);
        aiForm.setFieldsValue({
          ...config,
          listing_price_yuan: (config.listing_price_cents || 0) / 100,
          price_floor_yuan: (config.price_floor_cents || 0) / 100,
        });
      })
      .catch((error) => message.error(String(error)));
  }, [accountId, aiForm]);

  useEffect(() => {
    loadAi();
  }, [loadAi]);

  const loadAccounts = useCallback(() => {
    api
      .get("/api/accounts")
      .then((rows: any[]) => {
        setAccounts(rows);
        setAccountId((prev) => pickAccountId(rows, prev));
      })
      .catch((error) => message.error(String(error)));
  }, []);

  const loadBundle = useCallback(() => {
    if (accountId === null) return;
    api
      .get(`/api/accounts/${accountId}/rules`)
      .then(setBundle)
      .catch((error) => message.error(String(error)));
  }, [accountId]);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    loadBundle();
  }, [loadBundle]);

  useEffect(() => {
    if (bundle.config) {
      configForm.setFieldsValue(bundle.config);
    }
  }, [bundle, configForm]);

  const saveRule = async (values: any) => {
    try {
      if (ruleModal.editing) {
        await api.patch(`/api/rules/${ruleModal.editing.id}`, values);
      } else {
        await api.post(`/api/accounts/${accountId}/rules`, values);
      }
      message.success("已保存");
      setRuleModal({ open: false, editing: null });
      loadBundle();
    } catch (error) {
      message.error(String(error));
    }
  };

  const saveConfig = async (values: any) => {
    try {
      await api.put(`/api/accounts/${accountId}/reply-config`, values);
      message.success("已保存");
    } catch (error) {
      message.error(String(error));
    }
  };

  const saveAi = async (values: any) => {
    try {
      const payload = {
        enabled: values.enabled,
        base_url: values.base_url,
        model: values.model,
        persona: values.persona ?? "",
        product_info: values.product_info ?? "",
        listing_price_cents: Math.round((values.listing_price_yuan || 0) * 100),
        price_floor_cents: Math.round((values.price_floor_yuan || 0) * 100),
        max_history: values.max_history,
        timeout_seconds: values.timeout_seconds,
        api_key: values.api_key ? values.api_key : undefined,
      };
      const saved = await api.put(`/api/accounts/${accountId}/ai`, payload);
      setAiConfig(saved);
      aiForm.setFieldsValue({ ...saved, api_key: "" });
      message.success("AI 配置已保存");
    } catch (error) {
      message.error(String(error));
    }
  };

  const testAi = async () => {
    setAiTesting(true);
    setAiTestResult(null);
    try {
      const result: any = await api.post(`/api/accounts/${accountId}/ai/test`, {
        text: aiTestText,
      });
      setAiTestResult({
        ok: result.ok,
        text: result.ok ? result.text : `调用失败：${result.error}`,
      });
    } catch (error) {
      setAiTestResult({ ok: false, text: String(error) });
    } finally {
      setAiTesting(false);
    }
  };

  const loadItem = async () => {
    if (!itemQuery.trim()) {
      message.warning("请输入商品 ID");
      return;
    }
    setItemLoading(true);
    setItemInfo(null);
    try {
      const info: any = await api.get(`/api/accounts/${accountId}/items/${itemQuery.trim()}`);
      setItemInfo(info);
    } catch (error) {
      message.error(`读取失败：${error}`);
    } finally {
      setItemLoading(false);
    }
  };

  const addBlock = async (values: any) => {
    try {
      await api.post(`/api/accounts/${accountId}/blocks`, values);
      blockForm.resetFields();
      loadBundle();
    } catch (error) {
      message.error(String(error));
    }
  };

  if (accounts.length === 0) {
    return <Card>请先在「账号」页面扫码登录。</Card>;
  }

  return (
    <Card
      title="自动回复规则"
      extra={
        <Select
          style={{ width: 220, maxWidth: "100%" }}
          value={accountId}
          onChange={(value) => {
            setAccountId(value);
            rememberAccountId(value);
          }}
          options={accounts.map((a) => ({ value: a.id, label: accountLabel(a) }))}
        />
      }
    >
      <Tabs
        items={[
          {
            key: "ai",
            label: "AI 回复",
            children: (
              <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 560 }}>
                <Alert
                  type="info"
                  showIcon
                  message="AI 优先、关键词兜底"
                  description="AI 启用且 Key 有效时由 AI 回复；超时、报错或报价越界时自动回退到关键词规则与默认回复。"
                />
                {aiEnabled &&
                  (!aiListing || !aiFloor || !(aiProductInfo || "").trim()) && (
                    <Alert
                      type="warning"
                      showIcon
                      message="配置不完整，AI 回复会受限"
                      description={[
                        !aiListing && !aiFloor
                          ? "未填写标价/底价：AI 不知道售价，不会报任何价格，只会向买家询问心理价位。"
                          : !aiFloor
                            ? "未设置底价：AI 不会给出低于标价的报价，砍价时只能说明按标价出售。"
                            : "",
                        !(aiProductInfo || "").trim()
                          ? "未填写商品补充信息：AI 默认使用自动读取的商品标题与描述，补充信息可提高回答准确度。"
                          : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                    />
                  )}
                <Form form={aiForm} layout="vertical" onFinish={saveAi}>
                  <Form.Item name="enabled" label="启用 AI 回复" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  <Form.Item name="base_url" label="接口地址（OpenAI 兼容）" rules={[{ required: true }]}>
                    <Input placeholder="https://api.deepseek.com" />
                  </Form.Item>
                  <Form.Item name="model" label="模型" rules={[{ required: true }]}>
                    <Input placeholder="deepseek-chat" />
                  </Form.Item>
                  <Form.Item
                    name="api_key"
                    label={`API Key${aiConfig?.has_api_key ? "（已配置，留空则不修改）" : ""}`}
                  >
                    <Input.Password placeholder={aiConfig?.has_api_key ? "••••••••" : "sk-..."} />
                  </Form.Item>
                  <Form.Item name="persona" label="店铺/卖家信息">
                    <Input.TextArea rows={2} maxLength={2000} placeholder="例如：个人自用数码，只做正品，不议价太狠" />
                  </Form.Item>
                  <Form.Item
                    name="product_info"
                    label="商品补充信息（可选，自动读取商品标题/描述后追加）"
                  >
                    <Input.TextArea
                      rows={4}
                      maxLength={4000}
                      placeholder="例如：国行正品，电池 88%，配件齐全；或补充自动描述里没有的成色/发货方式"
                    />
                  </Form.Item>
                  <Space size="large" wrap>
                    <Form.Item name="listing_price_yuan" label="标价（元），买家看到的售价">
                      <InputNumber min={0} max={1000000} style={{ width: 180 }} />
                    </Form.Item>
                    <Form.Item name="price_floor_yuan" label="底价（元），0 表示不降价">
                      <InputNumber min={0} max={1000000} style={{ width: 180 }} />
                    </Form.Item>
                    <Form.Item name="max_history" label="上下文条数">
                      <InputNumber min={0} max={50} style={{ width: 120 }} />
                    </Form.Item>
                    <Form.Item name="timeout_seconds" label="超时（秒）">
                      <InputNumber min={3} max={120} style={{ width: 120 }} />
                    </Form.Item>
                  </Space>
                  <Button type="primary" htmlType="submit">
                    保存
                  </Button>
                </Form>
                <Card size="small" title="商品详情（自动读取，AI 回答依据）">
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Space.Compact style={{ width: "100%" }}>
                      <Input
                        value={itemQuery}
                        onChange={(event) => setItemQuery(event.target.value)}
                        placeholder="商品 ID，例如 1096185526203"
                      />
                      <Button onClick={loadItem} loading={itemLoading}>
                        读取
                      </Button>
                    </Space.Compact>
                    {itemInfo && (
                      <Alert
                        type="success"
                        showIcon
                        message={`${itemInfo.title}（标价 ${(itemInfo.price_cents / 100).toFixed(2)} 元，${itemInfo.status}）`}
                        description={itemInfo.description || "无描述"}
                      />
                    )}
                  </Space>
                </Card>
                <Card size="small" title="连通性测试">
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Input.TextArea
                      rows={2}
                      value={aiTestText}
                      onChange={(event) => setAiTestText(event.target.value)}
                      maxLength={500}
                    />
                    <Button onClick={testAi} loading={aiTesting}>
                      发送测试
                    </Button>
                    {aiTestResult && (
                      <Alert
                        type={aiTestResult.ok ? "success" : "error"}
                        message={aiTestResult.text}
                        showIcon
                      />
                    )}
                  </Space>
                </Card>
              </Space>
            ),
          },
          {
            key: "rules",
            label: "关键词规则",
            children: (
              <>
                <Button
                  type="primary"
                  icon={<PlusOutlined />}
                  style={{ marginBottom: 12 }}
                  onClick={() => {
                    ruleForm.resetFields();
                    ruleForm.setFieldsValue({ priority: 100 });
                    setRuleModal({ open: true, editing: null });
                  }}
                >
                  新增规则
                </Button>
                <Table
                  scroll={{ x: "max-content" }}
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={bundle.rules}
                  columns={[
                    { title: "优先级", dataIndex: "priority", width: 80 },
                    { title: "关键词", dataIndex: "keyword" },
                    { title: "回复", dataIndex: "reply", ellipsis: true },
                    {
                      title: "启用",
                      dataIndex: "enabled",
                      width: 90,
                      render: (v: boolean, row: any) => (
                        <Switch
                          checked={v}
                          onChange={async (checked) => {
                            await api.patch(`/api/rules/${row.id}`, { enabled: checked });
                            loadBundle();
                          }}
                        />
                      ),
                    },
                    {
                      title: "操作",
                      width: 140,
                      render: (_, row: any) => (
                        <Space>
                          <Button
                            size="small"
                            onClick={() => {
                              setRuleModal({ open: true, editing: row });
                              ruleForm.setFieldsValue(row);
                            }}
                          >
                            编辑
                          </Button>
                          <Popconfirm title="删除该规则？" onConfirm={async () => {
                            await api.delete(`/api/rules/${row.id}`);
                            loadBundle();
                          }}>
                            <Button size="small" danger>
                              删除
                            </Button>
                          </Popconfirm>
                        </Space>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
          {
            key: "config",
            label: "默认回复",
            children: (
              <Form form={configForm} layout="vertical" onFinish={saveConfig} style={{ maxWidth: 480 }}>
                <Form.Item name="default_reply" label="默认回复（无关键词命中时发送）">
                  <Input.TextArea rows={3} maxLength={2000} showCount />
                </Form.Item>
                <Form.Item name="debounce_seconds" label="同会话防抖（秒），0 为关闭">
                  <InputNumber min={0} max={86400} style={{ width: 200, maxWidth: "100%" }} />
                </Form.Item>
                <Form.Item name="reply_expiry_seconds" label="消息过期时间（秒），0 为不过期">
                  <InputNumber min={0} max={2592000} style={{ width: 200, maxWidth: "100%" }} />
                </Form.Item>
                <Button type="primary" htmlType="submit">
                  保存
                </Button>
              </Form>
            ),
          },
          {
            key: "blocks",
            label: "黑名单",
            children: (
              <>
                <Form form={blockForm} layout="inline" onFinish={addBlock} style={{ marginBottom: 12 }}>
                  <Form.Item name="kind" rules={[{ required: true }]} initialValue="user">
                    <Select style={{ width: 110 }} options={BLOCK_KINDS} />
                  </Form.Item>
                  <Form.Item name="value" rules={[{ required: true, message: "请输入值" }]}>
                    <Input placeholder="用户ID / 商品ID / 关键词" style={{ width: 240 }} />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit" icon={<PlusOutlined />}>
                      添加
                    </Button>
                  </Form.Item>
                </Form>
                <Table
                  scroll={{ x: "max-content" }}
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={bundle.blocks}
                  columns={[
                    { title: "类型", dataIndex: "kind", width: 100, render: (v: string) => BLOCK_KINDS.find((k) => k.value === v)?.label ?? v },
                    { title: "值", dataIndex: "value" },
                    {
                      title: "操作",
                      width: 100,
                      render: (_, row: any) => (
                        <Popconfirm title="移除该黑名单？" onConfirm={async () => {
                          await api.delete(`/api/blocks/${row.id}`);
                          loadBundle();
                        }}>
                          <Button size="small" danger>
                            移除
                          </Button>
                        </Popconfirm>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
        ]}
      />
      <Modal
        open={ruleModal.open}
        title={ruleModal.editing ? "编辑规则" : "新增规则"}
        onCancel={() => setRuleModal({ open: false, editing: null })}
        onOk={() => ruleForm.submit()}
        destroyOnClose
      >
        <Form form={ruleForm} layout="vertical" onFinish={saveRule} preserve={false}>
          <Form.Item name="keyword" label="关键词（消息包含即命中）" rules={[{ required: true }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="reply" label="回复内容" rules={[{ required: true }]}>
            <Input.TextArea rows={3} maxLength={2000} />
          </Form.Item>
          <Form.Item name="priority" label="优先级（数字越小越优先）" rules={[{ required: true }]}>
            <InputNumber min={0} max={10000} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
