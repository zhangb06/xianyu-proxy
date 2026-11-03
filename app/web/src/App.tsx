import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout, Menu, Button, message } from "antd";
import {
  DashboardOutlined,
  UserOutlined,
  MessageOutlined,
  ControlOutlined,
  LogoutOutlined,
} from "@ant-design/icons";
import { api, setCsrf } from "./api";
import LoginPage from "./pages/Login";
import OverviewPage from "./pages/Overview";
import AccountsPage from "./pages/Accounts";
import RulesPage from "./pages/Rules";
import ChatsPage from "./pages/Chats";

type AuthState = "loading" | "setup" | "login" | "ok";

const MENU_ITEMS = [
  { key: "/", icon: <DashboardOutlined />, label: "总览" },
  { key: "/accounts", icon: <UserOutlined />, label: "账号" },
  { key: "/rules", icon: <ControlOutlined />, label: "规则" },
  { key: "/chats", icon: <MessageOutlined />, label: "会话" },
];

export default function App() {
  const [auth, setAuth] = useState<AuthState>("loading");

  useEffect(() => {
    api
      .get("/api/auth/status")
      .then((status: any) => {
        setCsrf(status.csrf || "");
        setAuth(!status.password_set ? "setup" : status.authenticated ? "ok" : "login");
      })
      .catch(() => setAuth("login"));
    const onUnauthorized = () => {
      setCsrf("");
      setAuth((prev) => (prev === "ok" ? "login" : prev));
    };
    window.addEventListener("xy-unauthorized", onUnauthorized);
    return () => window.removeEventListener("xy-unauthorized", onUnauthorized);
  }, []);

  const handleLogout = async () => {
    try {
      await api.post("/api/auth/logout");
    } catch {
      // 会话可能已失效
    }
    setCsrf("");
    setAuth("login");
  };

  if (auth === "loading") {
    return null;
  }
  if (auth !== "ok") {
    return <LoginPage setupMode={auth === "setup"} onSuccess={() => setAuth("ok")} />;
  }

  return (
    <BrowserRouter>
      <Layout style={{ minHeight: "100vh" }}>
        <Layout.Sider theme="light" width={200}>
          <div style={{ padding: 16, fontWeight: 600 }}>闲鱼 Lite</div>
          <Menu
            items={MENU_ITEMS}
            defaultSelectedKeys={[window.location.pathname]}
            onClick={({ key }) => (window.location.href = key)}
          />
        </Layout.Sider>
        <Layout>
          <Layout.Header
            style={{
              background: "#fff",
              display: "flex",
              justifyContent: "flex-end",
              alignItems: "center",
              padding: "0 24px",
            }}
          >
            <Button icon={<LogoutOutlined />} onClick={() => handleLogout().catch((e) => message.error(String(e)))}>
              退出登录
            </Button>
          </Layout.Header>
          <Layout.Content style={{ padding: 24 }}>
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/accounts" element={<AccountsPage />} />
              <Route path="/rules" element={<RulesPage />} />
              <Route path="/chats" element={<ChatsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Layout.Content>
        </Layout>
      </Layout>
    </BrowserRouter>
  );
}
