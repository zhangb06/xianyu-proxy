import { useCallback, useEffect, useState } from "react";
import {
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

  const loadAccounts = useCallback(() => {
    api
      .get("/api/accounts")
      .then((rows: any[]) => {
        setAccounts(rows);
        setAccountId((prev) => prev ?? rows[0]?.id ?? null);
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
          style={{ width: 220 }}
          value={accountId}
          onChange={setAccountId}
          options={accounts.map((a) => ({ value: a.id, label: a.remark || a.xianyu_id }))}
        />
      }
    >
      <Tabs
        items={[
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
                  <InputNumber min={0} max={86400} style={{ width: 200 }} />
                </Form.Item>
                <Form.Item name="reply_expiry_seconds" label="消息过期时间（秒），0 为不过期">
                  <InputNumber min={0} max={2592000} style={{ width: 200 }} />
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
