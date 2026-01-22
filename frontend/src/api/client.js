const BASE_URL = "http://127.0.0.1:8000";
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (!res.ok) {
    throw new Error(
      (data && data.detail && JSON.stringify(data.detail)) ||
        data ||
        `Request failed: ${res.status}`
    );
  }

  return data;
}

export const api = {
  health: () => request("/health"),

  businesses: () => request("/businesses"),
  business: (id) => request(`/business/${id}`),

  // Phase 1 snapshots (raw list)
  snapshots: (businessId) => request(`/snapshots?business_id=${businessId}`),
  snapshotDetail: (snapshotId) => request(`/snapshot/${snapshotId}`),

  // Phase 2 analytics
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

  deleteSnapshot: (snapshotId) =>
    request(`/snapshot/${snapshotId}`, {
      method: "DELETE",
    }),
};
