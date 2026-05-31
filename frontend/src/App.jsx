import { useEffect, useState } from "react";
import "./App.css";

import AdminView from "./AdminView";
import ClientView from "./ClientView";
import Login from "./Login";

function readSession() {
  try {
    const raw = localStorage.getItem("lci_session");
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export default function App() {
  const [session, setSession] = useState(null);

  useEffect(() => {
    setSession(readSession());
  }, []);

  function logout() {
    localStorage.removeItem("lci_session");
    setSession(null);
  }

  if (!session) {
    return <Login onLoggedIn={setSession} />;
  }

  return (
    <div style={{ fontFamily: "system-ui" }}>
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          alignItems: "center",
          padding: "10px 16px",
          borderBottom: "1px solid #eee",
        }}
      >
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <strong>LCI</strong>
          <span style={{ opacity: 0.7, fontSize: 12 }}>
            Mode: {session.role}
            {session.role === "client" && session.business_id ? ` • business_id: ${session.business_id}` : ""}
          </span>
        </div>

        <button
          onClick={logout}
          style={{
            padding: "6px 10px",
            borderRadius: 8,
            border: "1px solid #ddd",
            background: "white",
            cursor: "pointer",
          }}
        >
          Logout
        </button>
      </div>

      {/* Views */}
      {session.role === "admin" ? <AdminView /> : <ClientView />}
    </div>
  );
}
