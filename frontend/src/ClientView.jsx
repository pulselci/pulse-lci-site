import { useEffect, useMemo, useState } from "react";
import { api } from "./api/client";

function fmtLocal(dt) {
  if (!dt) return "—";
  try {
    return dt.toLocaleString();
  } catch {
    return String(dt);
  }
}

export default function ClientView() {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const [health, setHealth] = useState(null);
  const [businesses, setBusinesses] = useState([]);
  const [selectedId, setSelectedId] = useState("");

  const [deltas, setDeltas] = useState([]);
  const [insights, setInsights] = useState(null);

  const [days, setDays] = useState(30);
  const [lastRefreshedAt, setLastRefreshedAt] = useState(null);

  // Boot: health + businesses
  useEffect(() => {
    let mounted = true;

    async function boot() {
      setLoading(true);
      setErr("");
      try {
        const [h, list] = await Promise.all([api.health(), api.businesses()]);
        if (!mounted) return;

        setHealth(h || null);
        setBusinesses(list || []);
        if (list?.length) setSelectedId(list[0].id);
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
    setLoading(true);

    try {
      const [d, i] = await Promise.all([
        api.snapshotDeltas(selectedId, days),
        api.insights(selectedId, days),
      ]);
      setDeltas(d || []);
      setInsights(i || null);
      setLastRefreshedAt(new Date());
    } catch (e) {
      setErr(String(e?.message || e));
      setDeltas([]);
      setInsights(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // Only auto-refresh after we have a selection
    if (!selectedId) return;
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, days]);

  const selectedBusiness = useMemo(
    () => businesses.find((b) => b.id === selectedId),
    [businesses, selectedId]
  );

  const asOfObservedDay =
    deltas?.[0]?.observed_day_utc || deltas?.[0]?.observed_day || null;

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: "0 auto" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          alignItems: "flex-start",
          flexWrap: "wrap",
          marginBottom: 12,
        }}
      >
        <div>
          <h2 style={{ margin: 0 }}>LCI — Client View</h2>
          <div style={{ opacity: 0.8, marginTop: 6 }}>
            {selectedBusiness ? (
              <>
                <strong>{selectedBusiness.name}</strong>{" "}
                <span>— {selectedBusiness.address || "No address on file"}</span>
              </>
            ) : (
              <span>Select a business</span>
            )}
          </div>
        </div>

        <div
          style={{
            border: "1px solid #e6e6e6",
            borderRadius: 10,
            padding: "10px 12px",
            minWidth: 260,
            background: "#fff",
          }}
        >
          <div style={{ fontSize: 12, opacity: 0.75 }}>Status</div>
          <div style={{ marginTop: 4 }}>
            Backend:{" "}
            <span style={{ fontWeight: 700 }}>
              {health ? "ok" : err ? "error" : "checking..."}
            </span>
          </div>
          <div style={{ fontSize: 12, opacity: 0.75, marginTop: 4 }}>
            Last refresh: {fmtLocal(lastRefreshedAt)}
          </div>
        </div>
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
          Business{" "}
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            style={{ padding: 6, minWidth: 320 }}
          >
            {businesses.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>

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

        <button onClick={refresh} style={{ padding: "6px 10px" }}>
          Refresh
        </button>

        {err ? (
          <span style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{err}</span>
        ) : null}
      </div>

      {/* Loading strip */}
      {loading ? (
        <div
          style={{
            marginBottom: 12,
            padding: "8px 10px",
            borderRadius: 10,
            border: "1px solid #eee",
            background: "#fafafa",
          }}
        >
          Loading…
        </div>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
        {/* Table */}
        <div style={{ border: "1px solid #ddd", borderRadius: 12, padding: 12, background: "#fff" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <h3 style={{ marginTop: 0, marginBottom: 6 }}>Competitors</h3>
            <div style={{ fontSize: 12, opacity: 0.75 }}>
              {asOfObservedDay ? `As of: ${asOfObservedDay}` : "As of: —"}
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table width="100%" cellPadding="8" style={{ borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid #eee" }}>
                  <th>Competitor</th>
                  <th style={{ textAlign: "right" }}>Rating</th>
                  <th style={{ textAlign: "right" }}>Reviews</th>
                  <th style={{ textAlign: "right" }}>Δ 1d</th>
                  <th style={{ textAlign: "right" }}>Δ 7d</th>
                </tr>
              </thead>
              <tbody>
                {deltas.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ padding: 12, opacity: 0.75 }}>
                      No competitor movement found for the selected window.
                    </td>
                  </tr>
                ) : (
                  deltas.map((r) => (
                    <tr key={r.competitor_id} style={{ borderBottom: "1px solid #f2f2f2" }}>
                      <td>{r.competitor_name}</td>
                      <td style={{ textAlign: "right" }}>{r.google_rating ?? "—"}</td>
                      <td style={{ textAlign: "right" }}>{r.google_review_count ?? "—"}</td>
                      <td style={{ textAlign: "right" }}>{r.reviews_delta_1d ?? "—"}</td>
                      <td style={{ textAlign: "right" }}>{r.reviews_delta_7d ?? "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Insights */}
        <div style={{ border: "1px solid #ddd", borderRadius: 12, padding: 12, background: "#fff" }}>
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
            <div style={{ opacity: 0.75 }}>No insights yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
