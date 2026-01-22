import { useState } from "react";
import AdminView from "./AdminView";
import ClientView from "./ClientView";

export default function App() {
  const [viewMode, setViewMode] = useState("admin"); // "admin" | "client"

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <button
          onClick={() => setViewMode("admin")}
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid #ccc",
            background: viewMode === "admin" ? "#111" : "#fff",
            color: viewMode === "admin" ? "#fff" : "#111",
            cursor: "pointer",
          }}
        >
          Admin View
        </button>

        <button
          onClick={() => setViewMode("client")}
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid #ccc",
            background: viewMode === "client" ? "#111" : "#fff",
            color: viewMode === "client" ? "#fff" : "#111",
            cursor: "pointer",
          }}
        >
          Client View
        </button>
      </div>

      {viewMode === "admin" ? <AdminView /> : <ClientView />}
    </div>
  );
}
