import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Drawer, Layout, Menu, Button, message } from "antd";
import {
  DashboardOutlined,
  UserOutlined,
  MessageOutlined,
  ControlOutlined,
  ShoppingOutlined,
  ThunderboltOutlined,
  LogoutOutlined,
  MenuOutlined,
} from "@ant-design/icons";
import { api, setCsrf } from "./api";
import { BASE_PATH, withBase } from "./lib/base";
import { useIsMobile } from "./lib/useMobile";
import LoginPage from "./pages/Login";
import OverviewPage from "./pages/Overview";
import AccountsPage from "./pages/Accounts";
import RulesPage from "./pages/Rules";
import ChatsPage from "./pages/Chats";
import DeliveryPage from "./pages/Delivery";
import ItemsPage from "./pages/Items";

type AuthState = "loading" | "setup" | "login" | "ok";

const MENU_ITEMS = [
  { key: "/", icon: <DashboardOutlined />, label: "总览" },
  { key: "/accounts", icon: <UserOutlined />, label: "账号" },
  { key: "/items", icon: <ShoppingOutlined />, label: "商品" },
  { key: "/rules", icon: <ControlOutlined />, label: "规则" },
  { key: "/chats", icon: <MessageOutlined />, label: "会话" },
  { key: "/delivery", icon: <ThunderboltOutlined />, label: "自动发货" },
];

export default function App() {
  const [auth, setAuth] = useState<AuthState>("loading");
  const [menuOpen, setMenuOpen] = useState(false);
  const mobile = useIsMobile();

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
    <BrowserRouter basename={BASE_PATH || "/"}>
      <Layout style={{ minHeight: "100vh" }}>
        {!mobile && (
          <Layout.Sider theme="light" width={200}>
            <div style={{ padding: 16, fontWeight: 600 }}>闲鱼 Lite</div>
            <Menu
              items={MENU_ITEMS}
              defaultSelectedKeys={[window.location.pathname.slice(BASE_PATH.length) || "/"]}
              onClick={({ key }) => (window.location.href = withBase(key))}
            />
          </Layout.Sider>
        )}
        <Layout>
          <Layout.Header
            style={{
              background: "#fff",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: mobile ? "0 12px" : "0 24px",
              gap: 8,
            }}
          >
            {mobile ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Button
                  aria-label="菜单"
                  icon={<MenuOutlined />}
                  onClick={() => setMenuOpen(true)}
                />
                <span style={{ fontWeight: 600 }}>闲鱼 Lite</span>
              </div>
            ) : (
              <span />
            )}
            <Button
              icon={<LogoutOutlined />}
              onClick={() => handleLogout().catch((e) => message.error(String(e)))}
            >
              {mobile ? "退出" : "退出登录"}
            </Button>
          </Layout.Header>
          <Layout.Content style={{ padding: mobile ? 12 : 24 }}>
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/accounts" element={<AccountsPage />} />
              <Route path="/items" element={<ItemsPage />} />
              <Route path="/rules" element={<RulesPage />} />
              <Route path="/chats" element={<ChatsPage />} />
              <Route path="/delivery" element={<DeliveryPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Layout.Content>
        </Layout>
        {mobile && (
          <Drawer
            placement="left"
            open={menuOpen}
            width={240}
            title="闲鱼 Lite"
            onClose={() => setMenuOpen(false)}
            styles={{ body: { padding: 0 } }}
          >
            <Menu
              items={MENU_ITEMS}
              defaultSelectedKeys={[window.location.pathname.slice(BASE_PATH.length) || "/"]}
              onClick={({ key }) => {
                setMenuOpen(false);
                window.location.href = withBase(key);
              }}
            />
          </Drawer>
        )}
      </Layout>
    </BrowserRouter>
  );
}
