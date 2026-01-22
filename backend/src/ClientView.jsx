import { useEffect, useMemo, useState } from "react";
import { api } from "./api/client";

export default function ClientView() {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const [businesses, setBusinesses] = useState([]);
  const [selectedId, setSelectedId] = useState("");

  const [deltas, setDeltas] = useState([]);
  const [insights, setInsights] = useState(null);

  const [days, setDays] = useState(30);

  useEffect(() => {
    let mounted = true;

    async function boot() {
      setLoading(true);
      setErr("");
      try {
        const list = await api.businesses();
        if (!mounted) return;

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
    try {
      const [d, i] = await Promise.all([
        api.snapshotDeltas(selectedId, days),
        api.insights(selectedId, days),
      ]);
      setDeltas(d || []);
      setInsights(i || null);
    } catch (e) {
      setErr(String(e?.message || e));
      setDeltas([]);
      setInsights(null);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, days]);

  const selectedBusiness = useMemo(
    () => businesses.find((b) => b.id === selectedId),
    [businesses, selectedId]
  );

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: "0 auto" }}>
      <h2 style={{ marginBottom: 6 }}>LCI — Client View</h2>
      <div style={{ opacity: 0.8, marginBottom: 16 }}>
        {selectedBusiness ? (
          <>
            <strong>{selectedBusiness.name}</strong>{" "}
            <span>— {selectedBusiness.address || "No address on file"}</span>
          </>
        ) : (
          <span>Select a business</span>
        )}
      </div>

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

        {loading ? <span>Loading…</span> : null}
        {err ? (
          <span style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{err}</span>
        ) : null}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
          <h3 style={{ marginTop: 0 }}>Competitors (latest day)</h3>
          <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 8 }}>
            {deltas?.[0]?.observed_day_utc
              ? `As of: ${deltas[0].observed_day_utc}`
              : "As of: —"}
          </div>

          <div style={{ overflowX: "auto" }}>
            <table width="100%" cellPadding="8" style={{ borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid #eee" }}>
                  <th>Competitor</th>
                  <th>Rating</th>
                  <th>Reviews</th>
                  <th>Δ 1d Reviews</th>
                  <th>Δ 7d Reviews</th>
                </tr>
              </thead>
              <tbody>
                {deltas.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ padding: 12, opacity: 0.75 }}>
                      No data yet.
                    </td>
                  </tr>
                ) : (
                  deltas.map((r) => (
                    <tr key={r.competitor_id} style={{ borderBottom: "1px solid #f2f2f2" }}>
                      <td>{r.competitor_name}</td>
                      <td>{r.google_rating ?? "—"}</td>
                      <td>{r.google_review_count ?? "—"}</td>
                      <td>{r.reviews_delta_1d ?? "—"}</td>
                      <td>{r.reviews_delta_7d ?? "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

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
            <div style={{ opacity: 0.75 }}>No insights yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
