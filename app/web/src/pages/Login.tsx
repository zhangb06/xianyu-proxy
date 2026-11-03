import { useState } from "react";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { api, setCsrf } from "../api";

export default function LoginPage({
  setupMode,
  onSuccess,
}: {
  setupMode: boolean;
  onSuccess: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);

  const onFinish = async (values: { password: string }) => {
    setSubmitting(true);
    try {
      const result: any = await api.post(
        setupMode ? "/api/auth/setup" : "/api/auth/login",
        values
      );
      setCsrf(result.csrf || "");
      message.success(setupMode ? "口令已设置" : "登录成功");
      onSuccess();
    } catch (error) {
      message.error(String(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}>
      <Card style={{ width: 380 }}>
        <Typography.Title level={4}>
          {setupMode ? "首次设置管理员口令" : "登录闲鱼 Lite"}
        </Typography.Title>
        {setupMode && (
          <Typography.Paragraph type="secondary">
            检测到尚未设置口令。请设置至少 8 位的管理口令，仅本机可访问此页面。
          </Typography.Paragraph>
        )}
        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item
            name="password"
            label="管理口令"
            rules={[
              { required: true, message: "请输入口令" },
              { min: 8, message: "口令至少 8 位" },
            ]}
          >
            <Input.Password placeholder="至少 8 位" autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            {setupMode ? "设置并进入" : "登录"}
          </Button>
        </Form>
      </Card>
    </div>
  );
}
