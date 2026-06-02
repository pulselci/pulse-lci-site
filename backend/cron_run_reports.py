import os
import requests

API_URL = os.environ.get("API_BASE_URL", "https://pulse-lci-api.onrender.com")
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")

resp = requests.post(
    f"{API_URL}/cron/run-scheduled-reports",
    headers={"X-Admin-Key": ADMIN_KEY},
    timeout=60,
)
print(f"[cron-reports] status={resp.status_code} body={resp.text}")
