import { useEffect, useState } from "react";
import { Card, Col, List, Row, Statistic, Table, Tag, Typography, message } from "antd";
import dayjs from "dayjs";
import { api } from "../api";

const STATUS_COLORS: Record<string, string> = {
  online: "success",
  connecting: "processing",
  reconnecting: "warning",
  verification_required: "warning",
  error: "error",
  disabled: "default",
  offline: "default",
  new: "default",
};

const STATUS_LABELS: Record<string, string> = {
  online: "在线",
  connecting: "连接中",
  reconnecting: "重连中",
  verification_required: "需验证",
  error: "错误",
  disabled: "已停用",
  offline: "离线",
  new: "未连接",
};

export function StatusTag({ status }: { status: string }) {
  return (
    <Tag color={STATUS_COLORS[status] ?? "default"}>
      {STATUS_LABELS[status] ?? status}
    </Tag>
  );
}

export default function OverviewPage() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api
      .get("/api/overview")
      .then(setData)
      .catch((error) => message.error(String(error)));
    const timer = setInterval(() => {
      api.get("/api/overview").then(setData).catch(() => undefined);
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  if (!data) {
    return null;
  }

  return (
    <Row gutter={[16, 16]}>
      <Col xs={12} md={6}>
        <Card>
          <Statistic title="在线账号" value={data.accounts.online} suffix={`/ ${data.accounts.total}`} />
        </Card>
      </Col>
      <Col xs={12} md={6}>
        <Card>
          <Statistic title="今日收到" value={data.today.inbound} />
        </Card>
      </Col>
      <Col xs={12} md={6}>
        <Card>
          <Statistic title="今日发出" value={data.today.outbound} />
        </Card>
      </Col>
      <Col xs={12} md={6}>
        <Card>
          <Statistic title="待处理告警" value={data.issues.length} />
        </Card>
      </Col>
      <Col xs={24} md={14}>
        <Card title="账号状态">
          <Table
            scroll={{ x: "max-content" }}
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={data.accounts.items}
            columns={[
              { title: "备注", dataIndex: "remark", render: (v: string) => v || "-" },
              { title: "闲鱼 ID", dataIndex: "xianyu_id" },
              { title: "状态", dataIndex: "status", render: (v: string) => <StatusTag status={v} /> },
            ]}
          />
        </Card>
      </Col>
      <Col xs={24} md={10}>
        <Card title="最近告警">
          <List
            size="small"
            dataSource={data.issues}
            locale={{ emptyText: "暂无告警" }}
            renderItem={(item: any) => (
              <List.Item>
                <Typography.Text type={item.level === "error" ? "danger" : "warning"}>
                  [{item.type}] {item.detail?.error || item.detail?.reason || ""}
                </Typography.Text>
                <Typography.Text type="secondary">
                  {dayjs(item.created_at).format("MM-DD HH:mm:ss")}
                </Typography.Text>
              </List.Item>
            )}
          />
        </Card>
      </Col>
    </Row>
  );
}
