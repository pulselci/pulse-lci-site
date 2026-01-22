import { useEffect, useMemo, useState } from "react";
import { api } from "./api/client";

// Uses the same BASE_URL behavior as api client.js (env-aware).
async function checkBackend() {
  try {
    const res = await api.healthRaw?.() ?? fetch("http://127.0.0.1:8000/health", {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    // If api.healthRaw exists, it returns { ok, status, body }. Otherwise we used fetch Response.
    if (res?.ok !== undefined && res?.status !== undefined && res?.body !== undefined) return res;

    const text = await res.text();
    return { ok: res.ok, status: res.status, body: text };
  } catch (e) {
    return { ok: false, status: 0, body: String(e?.message || e) };
  }
}

function pillStyle(ok) {
  return {
    padding: "2px 8px",
    borderRadius: 999,
    border: ok ? "1px solid #cfe9d6" : "1px solid #f3c2c2",
    background: "#fff",
  };
}

export default function ClientView() {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const [businesses, setBusinesses] = useState([]);
  const [selectedId, setSelectedId] = useState(""); // client-scoped business

  const [deltas, setDeltas] = useState([]);
  const [insights, setInsights] = useState(null);

  const [days, setDays] = useState(30);

  const [backendCheck, setBackendCheck] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  // Boot: check backend + load businesses once
  useEffect(() => {
    let mounted = true;

    async function boot() {
      setLoading(true);
      setErr("");

      const hc = await checkBackend();
      if (!mounted) return;
      setBackendCheck(hc);

      try {
        const list = await api.businesses();
        if (!mounted) return;

        const safeList = list || [];
        setBusinesses(safeList);

        // ✅ Client scope: always pick the first business (Phase C)
        if (safeList.length) setSelectedId(safeList[0].id);
        else setSelectedId("");
      } catch (e) {
        if (!mounted) return;
        setErr(String(e?.message || e));
      } finally {
        if (!mounted) return;
        setLoading(false);
      }
    }

    boot();
    return () => {
      mounted = false;
    };
  }, []);

  async function refresh() {
    if (!selectedId) return;
    setErr("");
    try {
      const [d, i] = await Promise.all([
        api.snapshotDeltas(selectedId, days),
        api.insights(selectedId, days),
      ]);
      setDeltas(d || []);
      setInsights(i || null);
      setLastUpdated(new Date());
    } catch (e) {
      setErr(String(e?.message || e));
      setDeltas([]);
      setInsights(null);
    }
  }

  // Refresh whenever selectedId or days changes
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, days]);

  // Optional: auto-refresh every 5 minutes (client-friendly)
  useEffect(() => {
    if (!selectedId) return;
    const t = setInterval(() => refresh(), 5 * 60 * 1000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, days]);

  const selectedBusiness = useMemo(
    () => businesses.find((b) => b.id === selectedId),
    [businesses, selectedId]
  );

  const hasData = deltas && deltas.length > 0;

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: "0 auto" }}>
      <h2 style={{ marginBottom: 6 }}>LCI — Client Portal</h2>

      {/* Backend badge */}
      <div
        style={{
          display: "flex",
          gap: 10,
          alignItems: "center",
          marginBottom: 10,
          fontSize: 12,
          flexWrap: "wrap",
        }}
      >
        <div style={{ opacity: 0.7 }}>Backend:</div>

        {backendCheck ? (
          backendCheck.ok ? (
            <div style={pillStyle(true)}>✅ OK (HTTP {backendCheck.status})</div>
          ) : (
            <div style={pillStyle(false)}>
              ❌ FAIL {backendCheck.status ? `(HTTP ${backendCheck.status})` : ""}
            </div>
          )
        ) : (
          <div style={{ padding: "2px 8px", borderRadius: 999, border: "1px solid #ddd" }}>
            Checking…
          </div>
        )}

        {lastUpdated ? (
          <div style={{ opacity: 0.7 }}>
            Last updated: {lastUpdated.toLocaleString()}
          </div>
        ) : null}
      </div>

      {/* If backend fails, show the message prominently */}
      {backendCheck && !backendCheck.ok ? (
        <div
          style={{
            border: "1px solid #f3c2c2",
            background: "#fff",
            borderRadius: 10,
            padding: 12,
            marginBottom: 12,
          }}
        >
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Can’t reach the server</div>
          <div style={{ fontSize: 12, opacity: 0.85, whiteSpace: "pre-wrap" }}>
            {backendCheck.body}
          </div>
          <div style={{ marginTop: 10 }}>
            <button onClick={() => window.location.reload()} style={{ padding: "6px 10px" }}>
              Reload page
            </button>
          </div>
        </div>
      ) : null}

      {/* Header business info */}
      <div style={{ opacity: 0.9, marginBottom: 16 }}>
        {selectedBusiness ? (
          <>
            <div style={{ fontSize: 18, fontWeight: 800 }}>{selectedBusiness.name}</div>
            <div style={{ opacity: 0.75 }}>
              {selectedBusiness.address || "No address on file"}
            </div>

            {/* If there are multiple businesses, we still lock to first (Phase C), but show a note */}
            {businesses.length > 1 ? (
              <div style={{ fontSize: 12, opacity: 0.65, marginTop: 6 }}>
                (Client scope active: showing the first business on file.)
              </div>
            ) : null}
          </>
        ) : loading ? (
          <div>Loading business…</div>
        ) : (
          <div>No business found for this account yet.</div>
        )}
      </div>

      {/* Controls */}
      <div
        style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          flexWrap: "wrap",
          marginBottom: 12,
        }}
      >
        <label>
          Days{" "}
          <input
            type="number"
            min={2}
            max={365}
            value={days}
            onChange={(e) => setDays(Number(e.target.value || 30))}
            style={{ padding: 6, width: 90 }}
          />
        </label>

        <button onClick={refresh} style={{ padding: "6px 10px" }} disabled={!selectedId}>
          Refresh
        </button>

        {loading ? <span>Loading…</span> : null}
        {err ? (
          <span style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{err}</span>
        ) : null}
      </div>

      {/* Main grid */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
        {/* Deltas */}
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
          <h3 style={{ marginTop: 0 }}>Competitors (latest day)</h3>

          <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 8 }}>
            {hasData && deltas?.[0]?.observed_day_utc
              ? `As of: ${deltas[0].observed_day_utc}`
              : "As of: —"}
          </div>

          {!hasData ? (
            <div style={{ opacity: 0.8 }}>
              No competitor movement to show yet.
              <div style={{ fontSize: 12, opacity: 0.7, marginTop: 8 }}>
                If this is a new account, we may need a few daily snapshots before trends appear.
              </div>
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table width="100%" cellPadding="8" style={{ borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid #eee" }}>
                    <th>Competitor</th>
                    <th>Rating</th>
                    <th>Reviews</th>
                    <th>Δ 1d</th>
                    <th>Δ 7d</th>
                  </tr>
                </thead>
                <tbody>
                  {deltas.map((r) => (
                    <tr key={r.competitor_id} style={{ borderBottom: "1px solid #f2f2f2" }}>
                      <td>{r.competitor_name}</td>
                      <td>{r.google_rating ?? "—"}</td>
                      <td>{r.google_review_count ?? "—"}</td>
                      <td>{r.reviews_delta_1d ?? "—"}</td>
                      <td>{r.reviews_delta_7d ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Insights */}
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
          <h3 style={{ marginTop: 0 }}>Insights</h3>
          <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 8 }}>
            {insights?.as_of ? `As of: ${insights.as_of}` : "As of: —"}
          </div>

          {insights?.insights?.length ? (
            <ul style={{ paddingLeft: 18, margin: 0 }}>
              {insights.insights.map((it, idx) => (
                <li key={`${it.type}-${idx}`} style={{ marginBottom: 8 }}>
                  {it.message}
                </li>
              ))}
            </ul>
          ) : (
            <div style={{ opacity: 0.75 }}>
              No insights yet.
              <div style={{ fontSize: 12, opacity: 0.7, marginTop: 8 }}>
                Insights appear once we have enough history and detectable movement.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
