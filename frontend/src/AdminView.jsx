import { useEffect, useMemo, useState } from "react";
import { api } from "./api/client";

export default function AdminView() {
  const [health, setHealth] = useState(null);
  const [businesses, setBusinesses] = useState([]);
  const [selectedId, setSelectedId] = useState(null);

  const [selectedBusiness, setSelectedBusiness] = useState(null); // { business, competitors }
  const [snapshots, setSnapshots] = useState([]);
  const [latestReport, setLatestReport] = useState(null);
  const [reports, setReports] = useState([]);
  const [reportHistory, setReportHistory] = useState([]);
  const [selectedReportId, setSelectedReportId] = useState(null);
  const [selectedReport, setSelectedReport] = useState(null);

  const [selectedSnapshotId, setSelectedSnapshotId] = useState(null);
  const [selectedSnapshotDetail, setSelectedSnapshotDetail] = useState(null);

  const [loadingDetail, setLoadingDetail] = useState(false);
  const [demoWorking, setDemoWorking] = useState(false);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [deletingSnapshotId, setDeletingSnapshotId] = useState(null);
  const [snapshotCompetitorFilter, setSnapshotCompetitorFilter] = useState("all");

  function formatSnapshotTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  // Manual snapshot form state
  const [formCompetitorId, setFormCompetitorId] = useState("");
  const [formData, setFormData] = useState({
    google_rating: "",
    google_review_count: "",
    visibility_score: "",
    price_hint: "$$",
    notes: "",
    raw: `{
  "source": "manual"
}`,
  });

  const [error, setError] = useState("");
  const [reportForm, setReportForm] = useState({
    period_start: "",
    period_end: "",
    title: "",
    summary: "",
  });

  // Initial load
  useEffect(() => {
    async function load() {
      try {
        setError("");
        const h = await api.health();
        setHealth(h);

        const b = await api.businesses();
        setBusinesses(b);

        // auto-select first business for convenience
        if (b.length > 0) setSelectedId(b[0].id);
      } catch (e) {
        setError(String(e?.message || e));
      }
    }
    load();
  }, []);

  async function refreshSelected() {
    if (!selectedId) return;
    try {
      setError("");
      setLoadingDetail(true);

      // reset right-side data for clean loading state
      setSelectedBusiness(null);
      setSnapshots([]);
      setLatestReport(null);
      setReports([]);
      setReportHistory([]);

      // reset snapshot detail when switching businesses
      setSelectedSnapshotId(null);
      setSelectedSnapshotDetail(null);

      const detail = await api.business(selectedId);
      setSelectedBusiness(detail);

      const snaps = await api.snapshots(selectedId);
      setSnapshots(snaps);

      const report = await api.latestReport(selectedId);
      setLatestReport(report);

      const repList = await api.reports(selectedId);
      setReports(repList);

      const history = await api.reports(selectedId);
      setReportHistory(history);
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setLoadingDetail(false);
    }
  }

  // Load business detail, snapshots, report whenever selectedId changes
  useEffect(() => {
    refreshSelected();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  async function loadSnapshotDetail(snapshotId) {
    if (!snapshotId) return;
    try {
      setError("");
      setSnapshotLoading(true);
      setSelectedSnapshotDetail(null);

      const detail = await api.snapshotDetail(snapshotId);
      setSelectedSnapshotDetail(detail);
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setSnapshotLoading(false);
    }
  }

  async function deleteSnapshotFromList(snapshotId) {
    if (!snapshotId) return;
    if (!selectedId) return;
    if (!window.confirm("Delete this snapshot? This cannot be undone.")) return;

    try {
      setError("");
      setSnapshotLoading(true);

      setDeletingSnapshotId(snapshotId);
      await api.deleteSnapshot(snapshotId);

      const prevIndex = snapshots.findIndex((x) => x.id === snapshotId);

      const snaps = await api.snapshots(selectedId);
      setSnapshots(snaps);

      if (selectedSnapshotId === snapshotId) {
        if (snaps.length === 0) {
          setSelectedSnapshotId(null);
          setSelectedSnapshotDetail(null);
        } else {
          const nextIndex = Math.min(Math.max(prevIndex, 0), snaps.length - 1);
          const nextId = snaps[nextIndex].id;

          setSelectedSnapshotId(nextId);
          await loadSnapshotDetail(nextId);
        }
      }
    } catch (e) {
      alert(String(e?.message || e || "Failed to delete snapshot"));
    } finally {
      setSnapshotLoading(false);
      setDeletingSnapshotId(null);
    }
  }

  async function deleteSelectedSnapshot() {
    if (!selectedSnapshotId) return;
    if (!window.confirm("Delete this snapshot? This cannot be undone.")) return;

    try {
      setError("");
      setSnapshotLoading(true);

      await api.deleteSnapshot(selectedSnapshotId);

      const snaps = await api.snapshots(selectedId);
      setSnapshots(snaps);

      if (snaps.length > 0) {
        setSelectedSnapshotId(snaps[0].id);
        await loadSnapshotDetail(snaps[0].id);
      } else {
        setSelectedSnapshotId(null);
        setSelectedSnapshotDetail(null);
      }

      alert("Snapshot deleted.");
      setError("");
    } catch (e) {
      alert(String(e?.message || e || "Failed to delete snapshot"));
    } finally {
      setSnapshotLoading(false);
    }
  }

  async function submitManualSnapshot() {
    if (!selectedBusiness || !formCompetitorId) {
      alert("Select a competitor first");
      return;
    }

    let rawParsed = null;
    try {
      rawParsed = formData.raw ? JSON.parse(formData.raw) : null;
    } catch {
      alert("Raw JSON is invalid");
      return;
    }

    try {
      setSnapshotLoading(true);

      await api.snapshotBulk({
        snapshots: [
          {
            business_id: selectedBusiness.business.id,
            competitor_id: formCompetitorId,
            google_rating: formData.google_rating ? Number(formData.google_rating) : null,
            google_review_count: formData.google_review_count
              ? Number(formData.google_review_count)
              : null,
            visibility_score: formData.visibility_score ? Number(formData.visibility_score) : null,
            price_hint: formData.price_hint || null,
            notes: formData.notes || null,
            raw: rawParsed,
          },
        ],
      });

      const snaps = await api.snapshots(selectedBusiness.business.id);
      setSnapshots(snaps);

      if (snaps.length > 0) {
        setSelectedSnapshotId(snaps[0].id);
        await loadSnapshotDetail(snaps[0].id);
      }

      setFormCompetitorId("");
      setFormData({
        google_rating: "",
        google_review_count: "",
        visibility_score: "",
        price_hint: "$$",
        notes: "",
        raw: `{
  "source": "manual"
}`,
      });
    } catch (e) {
      alert(e?.message || e);
    } finally {
      setSnapshotLoading(false);
    }
  }

  const leftStyle = {
    width: 320,
    borderRight: "1px solid #ddd",
    paddingRight: 16,
  };

  const rightStyle = {
    flex: 1,
    paddingLeft: 16,
  };

  const competitors = selectedBusiness?.competitors || [];
  const filteredSnapshots =
    snapshotCompetitorFilter === "all"
      ? snapshots
      : snapshots.filter((s) => s.competitor_id === snapshotCompetitorFilter);

  const snapshotPayloadTemplate = useMemo(() => {
    if (!selectedId || competitors.length === 0) return null;

    const now = new Date();
    const times = [0, 60, 120].map((minsAgo) => {
      const d = new Date(now.getTime() - minsAgo * 60 * 1000);
      return d.toISOString();
    });

    const snapshotsOut = [];
    for (const c of competitors) {
      for (const t of times) {
        snapshotsOut.push({
          business_id: selectedId,
          competitor_id: c.id,
          observed_at: t,
          google_rating: Number((3.8 + Math.random() * 1.0).toFixed(1)),
          google_review_count: Math.floor(20 + Math.random() * 250),
          offer_summary: "Demo offer: seasonal promo",
          price_hint: "$$",
          visibility_score: Math.floor(40 + Math.random() * 60),
          notes: "Generated demo snapshot",
          raw: { source: "demo", ts: t },
        });
      }
    }

    return { snapshots: snapshotsOut };
  }, [selectedId, competitors]);

  async function addSampleSnapshots() {
    if (!snapshotPayloadTemplate) return;
    try {
      setError("");
      setDemoWorking(true);
      await api.snapshotBulk(snapshotPayloadTemplate);
      await refreshSelected();
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setDemoWorking(false);
    }
  }

  async function submitReport() {
    if (!selectedId) {
      alert("No business selected");
      return;
    }

    if (!reportForm.title || !reportForm.period_start || !reportForm.period_end) {
      alert("Title, period start, and period end are required");
      return;
    }

    try {
      setError("");

      await api.registerReport({
        business_id: selectedId,
        period_start: reportForm.period_start,
        period_end: reportForm.period_end,
        title: reportForm.title,
        summary: reportForm.summary || null,
      });

      const report = await api.latestReport(selectedId);
      setLatestReport(report);

      setReportForm({
        period_start: "",
        period_end: "",
        title: "",
        summary: "",
      });

      alert("Report registered");
    } catch (e) {
      alert(e?.message || "Failed to register report");
    }
  }

  const snapshotDetailBoxStyle = {
    border: "1px solid #ddd",
    borderRadius: 10,
    padding: 12,
    background: "#fff",
    marginTop: 10,
  };

  return (
    <div style={{ fontFamily: "system-ui", padding: 24 }}>
      <h1 style={{ marginTop: 0 }}>LCI Frontend</h1>

      {error && <pre style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{error}</pre>}

      <div style={{ display: "flex", gap: 16 }}>
        {/* Left: Businesses list */}
        <div style={leftStyle}>
          <h2 style={{ marginTop: 0 }}>Businesses</h2>

          <div style={{ fontSize: 12, color: "#666", marginBottom: 12 }}>
            Backend: {health ? "ok" : "loading..."}
          </div>

          {businesses.length === 0 ? (
            <div>Loading businesses...</div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {businesses.map((b) => {
                const active = b.id === selectedId;
                return (
                  <li key={b.id} style={{ marginBottom: 8 }}>
                    <button
                      onClick={() => setSelectedId(b.id)}
                      style={{
                        width: "100%",
                        textAlign: "left",
                        padding: "10px 12px",
                        borderRadius: 8,
                        border: "1px solid #ddd",
                        background: active ? "#f3f4f6" : "white",
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                        <div style={{ fontWeight: 700 }}>{b.name}</div>
                        {active ? (
                          <div
                            style={{
                              fontSize: 11,
                              padding: "2px 8px",
                              borderRadius: 999,
                              border: "1px solid #ddd",
                              background: "#fff",
                              color: "#444",
                              height: "fit-content",
                            }}
                          >
                            Selected
                          </div>
                        ) : null}
                      </div>

                      <div style={{ fontSize: 12, color: "#666" }}>
                        {b.city || "?"}, {b.state || "?"}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Right: Selected business details */}
        <div style={rightStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
            <h2 style={{ marginTop: 0, marginBottom: 8 }}>Business Detail</h2>

            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button
                onClick={refreshSelected}
                disabled={!selectedId || loadingDetail}
                style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid #ddd",
                  background: "white",
                  cursor: "pointer",
                }}
              >
                {loadingDetail ? "Refreshing..." : "Refresh"}
              </button>

              <button
                onClick={addSampleSnapshots}
                disabled={!snapshotPayloadTemplate || demoWorking}
                style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid #ddd",
                  background: snapshotPayloadTemplate ? "#f3f4f6" : "#fafafa",
                  cursor: snapshotPayloadTemplate ? "pointer" : "not-allowed",
                }}
                title={
                  snapshotPayloadTemplate
                    ? "Creates demo snapshots for each competitor"
                    : "Select a business with competitors"
                }
              >
                {demoWorking ? "Adding..." : "Add sample snapshots"}
              </button>
            </div>
          </div>

          {!selectedId ? (
            <div>Select a business.</div>
          ) : loadingDetail && !selectedBusiness ? (
            <div>Loading business detail...</div>
          ) : !selectedBusiness ? (
            <div>Loading business detail...</div>
          ) : (
            <>
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 22, fontWeight: 800 }}>{selectedBusiness.business.name}</div>
                <div style={{ color: "#666" }}>{selectedBusiness.business.primary_domain || ""}</div>
                <div style={{ fontSize: 12, color: "#777", marginTop: 6 }}>
                  {selectedBusiness.business.id}
                </div>
              </div>

              <section style={{ marginBottom: 20 }}>
                <section style={{ marginBottom: 20 }}>
                  <h3 style={{ marginBottom: 8 }}>Register Report</h3>

                  <div
                    style={{
                      border: "1px solid #ddd",
                      borderRadius: 10,
                      padding: 12,
                      background: "#fff",
                    }}
                  >
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      <div>
                        <div style={{ fontSize: 12, color: "#777" }}>Period Start</div>
                        <input
                          type="date"
                          value={reportForm.period_start}
                          onChange={(e) =>
                            setReportForm((p) => ({ ...p, period_start: e.target.value }))
                          }
                          style={{
                            padding: "6px 8px",
                            borderRadius: 6,
                            border: "1px solid #ddd",
                            background: "white",
                          }}
                        />
                      </div>

                      <div>
                        <div style={{ fontSize: 12, color: "#777" }}>Period End</div>
                        <input
                          type="date"
                          value={reportForm.period_end}
                          onChange={(e) =>
                            setReportForm((p) => ({ ...p, period_end: e.target.value }))
                          }
                          style={{
                            padding: "6px 8px",
                            borderRadius: 6,
                            border: "1px solid #ddd",
                            background: "white",
                          }}
                        />
                      </div>

                      <div style={{ flex: 1, minWidth: 240 }}>
                        <div style={{ fontSize: 12, color: "#777" }}>Title</div>
                        <input
                          value={reportForm.title}
                          onChange={(e) =>
                            setReportForm((p) => ({ ...p, title: e.target.value }))
                          }
                          placeholder="January Competitive Report"
                          style={{
                            width: "100%",
                            padding: "6px 8px",
                            borderRadius: 6,
                            border: "1px solid #ddd",
                            background: "white",
                          }}
                        />
                      </div>
                    </div>

                    <div style={{ marginTop: 10 }}>
                      <div style={{ fontSize: 12, color: "#777" }}>Summary (optional)</div>
                      <textarea
                        rows={3}
                        value={reportForm.summary}
                        onChange={(e) =>
                          setReportForm((p) => ({ ...p, summary: e.target.value }))
                        }
                        style={{
                          width: "100%",
                          padding: "6px 8px",
                          borderRadius: 6,
                          border: "1px solid #ddd",
                          background: "white",
                        }}
                      />
                    </div>

                    <button
                      onClick={submitReport}
                      style={{
                        marginTop: 10,
                        padding: "8px 12px",
                        borderRadius: 8,
                        border: "1px solid #ddd",
                        background: "#f3f4f6",
                        cursor: "pointer",
                      }}
                    >
                      Register report
                    </button>
                  </div>
                </section>

                <h3 style={{ marginBottom: 8 }}>Competitors</h3>
                {selectedBusiness.competitors?.length ? (
                  <ul>
                    {selectedBusiness.competitors.map((c) => (
                      <li key={c.id}>
                        <strong>{c.name}</strong>
                        {c.website_url ? (
                          <>
                            <span style={{ color: "#777" }}> — </span>
                            <a href={c.website_url} target="_blank" rel="noreferrer">
                              {c.website_url}
                            </a>
                          </>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div style={{ color: "#666" }}>No competitors found.</div>
                )}
              </section>

              <section style={{ marginBottom: 20 }}>
                <h3 style={{ marginBottom: 8 }}>Snapshots</h3>

                <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
                  <div style={{ fontSize: 12, color: "#777" }}>Filter:</div>

                  <select
                    value={snapshotCompetitorFilter}
                    onChange={(e) => {
                      const v = e.target.value;
                      setSnapshotCompetitorFilter(v);

                      if (v !== "all") {
                        const stillVisible = snapshots.some(
                          (x) => x.id === selectedSnapshotId && x.competitor_id === v
                        );
                        if (!stillVisible) {
                          setSelectedSnapshotId(null);
                          setSelectedSnapshotDetail(null);
                        }
                      }
                    }}
                    style={{
                      padding: "6px 10px",
                      borderRadius: 8,
                      border: "1px solid #ddd",
                      background: "white",
                      cursor: "pointer",
                    }}
                  >
                    <option value="all">All competitors</option>
                    {competitors.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>

                  <div style={{ fontSize: 12, color: "#777" }}>
                    Showing {filteredSnapshots.length} / {snapshots.length}
                  </div>
                </div>

                {/* Manual Snapshot Entry */}
                <div
                  style={{
                    border: "1px solid #ddd",
                    borderRadius: 10,
                    padding: 12,
                    background: "#fff",
                    marginBottom: 12,
                  }}
                >
                  <div style={{ fontWeight: 800, marginBottom: 8 }}>Add Snapshot (manual)</div>

                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <div style={{ minWidth: 240 }}>
                      <div style={{ fontSize: 12, color: "#777" }}>Competitor</div>
                      <select
                        value={formCompetitorId}
                        onChange={(e) => setFormCompetitorId(e.target.value)}
                        style={{
                          width: "100%",
                          padding: "8px 10px",
                          borderRadius: 8,
                          border: "1px solid #ddd",
                          marginTop: 4,
                        }}
                      >
                        <option value="">Select competitor...</option>
                        {selectedBusiness?.competitors?.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <div style={{ fontSize: 12, color: "#777" }}>Rating</div>
                      <input
                        value={formData.google_rating}
                        onChange={(e) =>
                          setFormData((p) => ({ ...p, google_rating: e.target.value }))
                        }
                        placeholder="4.2"
                        style={{
                          width: 110,
                          padding: "8px 10px",
                          borderRadius: 8,
                          border: "1px solid #ddd",
                          marginTop: 4,
                        }}
                      />
                    </div>

                    <div>
                      <div style={{ fontSize: 12, color: "#777" }}>Reviews</div>
                      <input
                        value={formData.google_review_count}
                        onChange={(e) =>
                          setFormData((p) => ({ ...p, google_review_count: e.target.value }))
                        }
                        placeholder="120"
                        style={{
                          width: 110,
                          padding: "8px 10px",
                          borderRadius: 8,
                          border: "1px solid #ddd",
                          marginTop: 4,
                        }}
                      />
                    </div>

                    <div>
                      <div style={{ fontSize: 12, color: "#777" }}>Visibility</div>
                      <input
                        value={formData.visibility_score}
                        onChange={(e) =>
                          setFormData((p) => ({ ...p, visibility_score: e.target.value }))
                        }
                        placeholder="85"
                        style={{
                          width: 110,
                          padding: "8px 10px",
                          borderRadius: 8,
                          border: "1px solid #ddd",
                          marginTop: 4,
                        }}
                      />
                    </div>

                    <div>
                      <div style={{ fontSize: 12, color: "#777" }}>Price</div>
                      <select
                        value={formData.price_hint}
                        onChange={(e) =>
                          setFormData((p) => ({ ...p, price_hint: e.target.value }))
                        }
                        style={{
                          width: 110,
                          padding: "8px 10px",
                          borderRadius: 8,
                          border: "1px solid #ddd",
                          marginTop: 4,
                        }}
                      >
                        <option value="$">$</option>
                        <option value="$$">$$</option>
                        <option value="$$$">$$$</option>
                        <option value="$$$$">$$$$</option>
                      </select>
                    </div>
                  </div>

                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: 12, color: "#777" }}>Notes</div>
                    <input
                      value={formData.notes}
                      onChange={(e) => setFormData((p) => ({ ...p, notes: e.target.value }))}
                      placeholder="Optional notes for this snapshot"
                      style={{
                        width: "100%",
                        padding: "8px 10px",
                        borderRadius: 8,
                        border: "1px solid #ddd",
                        marginTop: 4,
                      }}
                    />
                  </div>

                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: 12, color: "#777" }}>Raw JSON</div>
                    <textarea
                      value={formData.raw}
                      onChange={(e) => setFormData((p) => ({ ...p, raw: e.target.value }))}
                      rows={4}
                      style={{
                        width: "100%",
                        padding: "8px 10px",
                        borderRadius: 8,
                        border: "1px solid #ddd",
                        marginTop: 4,
                        fontFamily: "monospace",
                        fontSize: 12,
                      }}
                    />
                  </div>

                  <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
                    <button
                      onClick={submitManualSnapshot}
                      disabled={snapshotLoading}
                      style={{
                        padding: "8px 12px",
                        borderRadius: 8,
                        border: "1px solid #ddd",
                        background: "#f3f4f6",
                        cursor: "pointer",
                      }}
                    >
                      {snapshotLoading ? "Saving..." : "Save snapshot"}
                    </button>

                    <button
                      onClick={() => {
                        setFormCompetitorId("");
                        setFormData({
                          google_rating: "",
                          google_review_count: "",
                          visibility_score: "",
                          price_hint: "$$",
                          notes: "",
                          raw: `{
  "source": "manual"
}`,
                        });
                      }}
                      disabled={snapshotLoading}
                      style={{
                        padding: "8px 12px",
                        borderRadius: 8,
                        border: "1px solid #ddd",
                        background: "white",
                        cursor: "pointer",
                      }}
                    >
                      Clear
                    </button>
                  </div>
                </div>

                {filteredSnapshots.length ? (
                  <>
                    <div style={{ fontSize: 12, color: "#666", marginBottom: 8 }}>
                      Click a snapshot to view details.
                    </div>

                    <ul style={{ paddingLeft: 16 }}>
                      {filteredSnapshots.map((s) => {
                        const active = s.id === selectedSnapshotId;

                        return (
                          <li key={s.id} style={{ marginBottom: 6 }}>
                            <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
                              <button
                                onClick={() => {
                                  setSelectedSnapshotId(s.id);
                                  loadSnapshotDetail(s.id);
                                }}
                                style={{
                                  border: active ? "2px solid #111" : "1px solid #ddd",
                                  background: active ? "#f3f4f6" : "white",
                                  padding: "6px 10px",
                                  borderRadius: 8,
                                  cursor: "pointer",
                                  width: "100%",
                                  textAlign: "left",
                                  flex: 1,
                                }}
                              >
                                <div
                                  style={{
                                    display: "flex",
                                    gap: 8,
                                    alignItems: "center",
                                    flexWrap: "wrap",
                                  }}
                                >
                                  <span style={{ fontWeight: 700 }}>
                                    {s.competitor_name || "Competitor"}
                                  </span>

                                  <span style={{ color: "#777" }}>·</span>

                                  <span style={{ fontFamily: "monospace", fontSize: 12 }}>
                                    {s.google_rating != null ? `★ ${s.google_rating}` : "★ —"}
                                  </span>

                                  <span style={{ fontFamily: "monospace", fontSize: 12, color: "#555" }}>
                                    {s.google_review_count != null ? `(${s.google_review_count})` : "(—)"}
                                  </span>

                                  <span style={{ color: "#777" }}>·</span>

                                  <span style={{ fontSize: 12, color: "#666" }}>
                                    {formatSnapshotTime(s.created_at)}
                                  </span>

                                  {active ? (
                                    <span
                                      style={{
                                        fontSize: 11,
                                        padding: "2px 8px",
                                        borderRadius: 999,
                                        border: "1px solid #ddd",
                                        background: "#fff",
                                        color: "#444",
                                        marginLeft: 6,
                                      }}
                                    >
                                      Selected
                                    </span>
                                  ) : null}
                                </div>
                              </button>

                              <button
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  deleteSnapshotFromList(s.id);
                                }}
                                disabled={snapshotLoading || deletingSnapshotId === s.id}
                                title="Delete snapshot"
                                style={{
                                  border: "1px solid #ddd",
                                  background: "white",
                                  padding: "6px 10px",
                                  borderRadius: 8,
                                  cursor: "pointer",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {deletingSnapshotId === s.id ? "Deleting..." : "Delete"}
                              </button>
                            </div>
                          </li>
                        );
                      })}
                    </ul>

                    {/* Snapshot Detail Panel */}
                    <div style={snapshotDetailBoxStyle}>
                      <div style={{ fontWeight: 800, marginBottom: 6 }}>Snapshot Detail</div>

                      {!selectedSnapshotId ? (
                        <div style={{ color: "#666" }}>Select a snapshot above.</div>
                      ) : snapshotLoading ? (
                        <div style={{ color: "#666" }}>Loading snapshot detail...</div>
                      ) : !selectedSnapshotDetail ? (
                        <div style={{ color: "#666" }}>Click a snapshot to load its detail.</div>
                      ) : (
                        <>
                          <button
                            onClick={deleteSelectedSnapshot}
                            disabled={!selectedSnapshotId || snapshotLoading}
                            style={{
                              padding: "8px 10px",
                              borderRadius: 6,
                              border: "1px solid #ccc",
                              cursor: "pointer",
                              marginBottom: 10,
                            }}
                          >
                            {snapshotLoading ? "Working..." : "Delete snapshot"}
                          </button>

                          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                            <div>
                              <div style={{ fontSize: 12, color: "#777" }}>Rating</div>
                              <div style={{ fontWeight: 700 }}>
                                {selectedSnapshotDetail.google_rating ?? "—"}
                              </div>
                            </div>

                            <div>
                              <div style={{ fontSize: 12, color: "#777" }}>Reviews</div>
                              <div style={{ fontWeight: 700 }}>
                                {selectedSnapshotDetail.google_review_count ?? "—"}
                              </div>
                            </div>

                            <div>
                              <div style={{ fontSize: 12, color: "#777" }}>Visibility</div>
                              <div style={{ fontWeight: 700 }}>
                                {selectedSnapshotDetail.visibility_score ?? "—"}
                              </div>
                            </div>

                            <div>
                              <div style={{ fontSize: 12, color: "#777" }}>Price</div>
                              <div style={{ fontWeight: 700 }}>
                                {selectedSnapshotDetail.price_hint ?? "—"}
                              </div>
                            </div>
                          </div>

                          {selectedSnapshotDetail.notes ? (
                            <div style={{ marginTop: 10 }}>
                              <div style={{ fontSize: 12, color: "#777" }}>Notes</div>
                              <div>{selectedSnapshotDetail.notes}</div>
                            </div>
                          ) : null}

                          <div style={{ marginTop: 10 }}>
                            <div style={{ fontSize: 12, color: "#777" }}>Raw</div>
                            <pre
                              style={{
                                background: "#f7f7f7",
                                padding: 10,
                                borderRadius: 8,
                                overflowX: "auto",
                                marginTop: 6,
                              }}
                            >
                              {JSON.stringify(selectedSnapshotDetail.raw, null, 2)}
                            </pre>
                          </div>
                        </>
                      )}
                    </div>
                  </>
                ) : (
                  <div style={{ color: "#666" }}>
                    No snapshots yet. Click <strong>Add sample snapshots</strong> to generate demo data.
                  </div>
                )}
              </section>

              <section>
                <h3 style={{ marginBottom: 8 }}>Latest Report</h3>

                {latestReport ? (
                  <>
                    {latestReport.signed_url_placeholder ? (
                      <div style={{ marginBottom: 8 }}>
                        <a href={latestReport.signed_url_placeholder} target="_blank" rel="noreferrer">
                          Open report (placeholder URL)
                        </a>
                      </div>
                    ) : null}

                    <pre style={{ background: "#f7f7f7", padding: 12, borderRadius: 8 }}>
                      {JSON.stringify(latestReport, null, 2)}
                    </pre>
                  </>
                ) : (
                  <div style={{ color: "#666" }}>No report found.</div>
                )}

                <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid #eee" }} />

                <h3 style={{ marginBottom: 8 }}>Report History</h3>

                {reports.length === 0 ? (
                  <div style={{ color: "#666" }}>No reports yet.</div>
                ) : (
                  <ul style={{ paddingLeft: 16, marginTop: 6 }}>
                    {reports.map((r) => (
                      <li key={r.id} style={{ marginBottom: 8 }}>
                        <div style={{ fontWeight: 700 }}>{r.title}</div>

                        <div style={{ fontSize: 12, color: "#666" }}>
                          {r.period_start} → {r.period_end} • {r.status}
                        </div>

                        <div style={{ fontSize: 12, fontFamily: "monospace", color: "#777" }}>{r.id}</div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
