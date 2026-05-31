import { useEffect, useMemo, useState } from "react";
import { api } from "./api/client";

function pillStyle(ok) {
  return {
    padding: "2px 8px",
    borderRadius: 999,
    border: ok ? "1px solid #cfe9d6" : "1px solid #f3c2c2",
    background: "#fff",
  };
}

function cardStyle(borderColor = "#ddd") {
  return {
    border: `1px solid ${borderColor}`,
    borderRadius: 10,
    padding: 12,
    background: "#fff",
  };
}

function apiBaseUrl() {
  // Prefer env var if you have it; fallback to local backend.
  return (
    import.meta?.env?.VITE_API_URL ||
    import.meta?.env?.VITE_BACKEND_URL ||
    "http://127.0.0.1:8000"
  );
}

async function fetchGeneratedReport(businessId) {
  const url = `${apiBaseUrl()}/business/${businessId}/reports/generate`;
  const res = await fetch(url, { method: "POST" });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(text || `Failed to generate report (HTTP ${res.status})`);
  }
  return JSON.parse(text);
}

function fmtPct(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  return `${Number(x).toFixed(1)}%`;
}

export default function ClientView() {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const [businesses, setBusinesses] = useState([]);
  const [selectedId, setSelectedId] = useState(""); // client-scoped business

  const [deltas, setDeltas] = useState([]);
  const [report, setReport] = useState(null); // NEW: generated report payload

  const [days, setDays] = useState(30);

  const [backendCheck, setBackendCheck] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  async function runBackendCheck() {
    const hc = await api.healthRaw();
    setBackendCheck(hc);
    return hc;
  }

  // Boot: check backend + load businesses once
  useEffect(() => {
    let mounted = true;

    async function boot() {
      setLoading(true);
      setErr("");

      const hc = await api.healthRaw();
      if (!mounted) return;
      setBackendCheck(hc);

      try {
        const list = await api.businesses();
        if (!mounted) return;

        const safeList = list || [];
        setBusinesses(safeList);

        // ✅ Client scope (Phase C/D): always pick the first business
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
    setRefreshing(true);

    try {
      // NOTE: report generate is always 30d server-side (your backend currently hardcodes days=30)
      const [d, r] = await Promise.all([
        api.snapshotDeltas(selectedId, days),
        fetchGeneratedReport(selectedId),
      ]);

      setDeltas(d || []);
      setReport(r || null);
      setLastUpdated(new Date());
    } catch (e) {
      setErr(String(e?.message || e));
      setDeltas([]);
      setReport(null);
    } finally {
      setRefreshing(false);
    }
  }

  // Refresh whenever selectedId or days changes
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, days]);

  // Auto-refresh every 5 minutes (client-friendly)
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

  const hasDeltas = deltas && deltas.length > 0;

  const sections = report?.sections || {};
  const insightsList = Array.isArray(sections?.insights) ? sections.insights : [];
  const hasInsights = insightsList.length > 0;

  const sov = sections?.share_of_voice || null;
  const sovRows = Array.isArray(sov?.rows) ? sov.rows : [];
  const marketTotalReviews = sov?.market_total_reviews;

  const weeklyActions = useMemo(() => {
    const wa = insightsList.find((x) => x?.type === "weekly_actions");
    if (!wa) return [];
    return Array.isArray(wa.items) ? wa.items : [];
  }, [insightsList]);

  const otherInsights = useMemo(() => {
    // show everything EXCEPT weekly_actions as the "Insights" bullets
    return insightsList.filter((x) => x?.type !== "weekly_actions");
  }, [insightsList]);

  const isBackendDown = backendCheck && !backendCheck.ok;

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: "0 auto" }}>
      <h2 style={{ marginBottom: 6 }}>LCI — Client Portal</h2>

      {/* Backend badge row */}
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

        <button
          onClick={runBackendCheck}
          style={{
            padding: "4px 8px",
            borderRadius: 8,
            border: "1px solid #ddd",
            background: "white",
            cursor: "pointer",
          }}
        >
          Re-check backend
        </button>

        {lastUpdated ? (
          <div style={{ opacity: 0.7 }}>Last updated: {lastUpdated.toLocaleString()}</div>
        ) : null}
      </div>

      {/* Backend down panel */}
      {isBackendDown ? (
        <div style={{ ...cardStyle("#f3c2c2"), marginBottom: 12 }}>
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Can’t reach the server</div>
          <div style={{ fontSize: 12, opacity: 0.85, whiteSpace: "pre-wrap" }}>
            {backendCheck.body}
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              onClick={runBackendCheck}
              style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #ddd" }}
            >
              Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #ddd" }}
            >
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
            <div style={{ opacity: 0.75 }}>{selectedBusiness.address || "No address on file"}</div>

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

        <button onClick={refresh} style={{ padding: "6px 10px" }} disabled={!selectedId || refreshing}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>

        {loading ? <span>Loading…</span> : null}
        {err ? <span style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{err}</span> : null}
      </div>

      {/* Getting started card (no data yet) */}
      {!isBackendDown && selectedId && !hasDeltas && !hasInsights ? (
        <div style={{ ...cardStyle("#ddd"), marginBottom: 12 }}>
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Getting started</div>
          <div style={{ fontSize: 13, opacity: 0.85, lineHeight: 1.4 }}>
            We’ll start showing competitor movement and insights once we have enough daily snapshots. If this is a
            brand-new account, check back tomorrow after the next snapshot run.
          </div>
        </div>
      ) : null}

      {/* Main grid */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
        {/* Left column */}
        <div style={{ display: "grid", gap: 16 }}>
          {/* Deltas */}
          <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
            <h3 style={{ marginTop: 0 }}>Competitors (latest day)</h3>

            <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 8 }}>
              {hasDeltas && deltas?.[0]?.observed_day_utc ? `As of: ${deltas[0].observed_day_utc}` : "As of: —"}
            </div>

            {!hasDeltas ? (
              <div style={{ opacity: 0.8 }}>
                No competitor movement to show yet.
                <div style={{ fontSize: 12, opacity: 0.7, marginTop: 8 }}>
                  We may need a few daily snapshots before trends appear.
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
                      <tr key={r.competitor_id || r.competitor_name} style={{ borderBottom: "1px solid #f2f2f2" }}>
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

          {/* Share of Voice */}
          <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
            <h3 style={{ marginTop: 0 }}>Share of Voice (total reviews)</h3>

            <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 8 }}>
              {marketTotalReviews != null ? `Market total: ${marketTotalReviews}` : "Market total: —"}
              {report?.generated_at ? (
                <span style={{ marginLeft: 10 }}>Report: {new Date(report.generated_at).toLocaleString()}</span>
              ) : null}
            </div>

            {!sovRows.length ? (
              <div style={{ opacity: 0.75 }}>
                No share-of-voice data yet.
                <div style={{ fontSize: 12, opacity: 0.7, marginTop: 8 }}>
                  Once snapshots exist, we’ll compute how review volume is distributed across competitors.
                </div>
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table width="100%" cellPadding="8" style={{ borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ textAlign: "left", borderBottom: "1px solid #eee" }}>
                      <th>#</th>
                      <th>Competitor</th>
                      <th>Reviews</th>
                      <th>Share</th>
                      <th>Δ share (7d)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sovRows.map((r) => {
                      const isBiz = !!r.is_business;
                      return (
                        <tr
                          key={`${r.rank}-${r.competitor_name}`}
                          style={{
                            borderBottom: "1px solid #f2f2f2",
                            background: isBiz ? "#f3f4f6" : "transparent",
                          }}
                        >
                          <td style={{ width: 40 }}>{r.rank ?? "—"}</td>
                          <td style={{ fontWeight: isBiz ? 800 : 600 }}>
                            {r.competitor_name}
                            {isBiz ? <span style={{ marginLeft: 8, fontSize: 12, opacity: 0.7 }}>(you)</span> : null}
                          </td>
                          <td style={{ fontFamily: "monospace" }}>{r.reviews_total ?? "—"}</td>
                          <td style={{ fontFamily: "monospace" }}>{fmtPct(r.share_pct)}</td>
                          <td style={{ fontFamily: "monospace", opacity: 0.85 }}>
                            {r.share_change_7d_pct != null ? fmtPct(r.share_change_7d_pct) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Right column */}
        <div style={{ display: "grid", gap: 16 }}>
          {/* Insights */}
          <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
            <h3 style={{ marginTop: 0 }}>Insights</h3>

            <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 8 }}>
              {report?.generated_at ? `As of: ${new Date(report.generated_at).toLocaleString()}` : "As of: —"}
            </div>

            {otherInsights.length ? (
              <ul style={{ paddingLeft: 18, margin: 0 }}>
                {otherInsights.map((it, idx) => (
                  <li key={`${it.type}-${idx}`} style={{ marginBottom: 8 }}>
                    {it.message || it.summary || (
                      <span style={{ fontFamily: "monospace", fontSize: 12 }}>
                        {it.type}
                      </span>
                    )}
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

          {/* What to do this week (weekly_actions) */}
          <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
            <h3 style={{ marginTop: 0 }}>What to do this week</h3>

            {!weeklyActions.length ? (
              <div style={{ opacity: 0.75 }}>
                No weekly action recommendations yet.
                <div style={{ fontSize: 12, opacity: 0.7, marginTop: 8 }}>
                  Once the report can be generated, we’ll show clear actions here.
                </div>
              </div>
            ) : (
              <div style={{ display: "grid", gap: 10 }}>
                {weeklyActions.map((a, idx) => (
                  <div key={`${a.title}-${idx}`} style={cardStyle("#eee")}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                      <div style={{ fontWeight: 800 }}>{a.title || "Action"}</div>
                      {a.severity ? (
                        <div style={{ fontSize: 12, opacity: 0.75, textTransform: "uppercase" }}>
                          {a.severity}
                        </div>
                      ) : null}
                    </div>

                    {a.why ? <div style={{ fontSize: 13, opacity: 0.9, marginTop: 6 }}>{a.why}</div> : null}
                    {a.metric ? (
                      <div style={{ fontSize: 12, opacity: 0.85, marginTop: 8, fontFamily: "monospace" }}>
                        {a.metric}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Report summary (optional small box) */}
          {report?.summary_text ? (
            <div style={{ ...cardStyle("#ddd") }}>
              <div style={{ fontWeight: 800, marginBottom: 6 }}>Report summary</div>
              <div style={{ fontSize: 13, opacity: 0.85, lineHeight: 1.4 }}>{report.summary_text}</div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
