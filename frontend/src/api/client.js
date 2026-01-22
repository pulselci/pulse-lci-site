const BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const url = `${BASE_URL}${path}`;

  const method = (options.method || "GET").toUpperCase();
  const hasBody = options.body != null;

  const headers = {
    Accept: "application/json",
    ...(hasBody ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };

  let res;
  try {
    res = await fetch(url, {
      ...options,
      method,
      headers,
    });
  } catch (e) {
    // Network/CORS/backend-down level failure (no HTTP status available)
    throw new Error(`Fetch failed for ${method} ${url}\n${String(e?.message || e)}`);
  }

  const text = await res.text();

  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text; // keep raw text if not JSON
  }

  if (!res.ok) {
    const detail =
      (data && data.detail && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) ||
      (typeof data === "string" ? data : JSON.stringify(data)) ||
      "";

    throw new Error(`HTTP ${res.status} ${res.statusText} for ${method} ${url}\n${detail}`);
  }

  return data;
}

export const api = {
  // Health
  health: () => request("/health"),
  healthDb: () => request("/health/db"),

  // Businesses
  businesses: () => request("/businesses"),
  business: (id) => request(`/business/${id}`),

  // Snapshots (Phase 1)
  snapshots: (businessId) => request(`/snapshots?business_id=${businessId}`),
  snapshotDetail: (snapshotId) => request(`/snapshot/${snapshotId}`),

  // Analytics (Phase 2)
  snapshotDeltas: (businessId, days = 30) =>
    request(`/snapshots/deltas?business_id=${businessId}&days=${days}`),

  insights: (businessId, days = 30) =>
    request(`/insights?business_id=${businessId}&days=${days}`),

  // Reports
  latestReport: (businessId) => request(`/reports/${businessId}/latest`),
  reports: (businessId) => request(`/reports?business_id=${businessId}`),
  registerReport: (payload) =>
    request("/report/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // Create snapshots
  snapshotBulk: (payload) =>
    request("/snapshot/bulk", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // Delete snapshot
  deleteSnapshot: (snapshotId) =>
    request(`/snapshot/${snapshotId}`, {
      method: "DELETE",
    }),
};
