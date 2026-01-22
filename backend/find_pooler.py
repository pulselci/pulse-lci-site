import re
import psycopg
from app.core.config import settings

# Uses your current DATABASE_URL (already loaded from your root .env)
base = settings.DATABASE_URL

# Pull user:pass + dbname from the current URL
m = re.match(r"^(postgresql://[^@]+@)([^:/]+)(:\d+)(/[^?]+)(\?.*)?$", base)
if not m:
    raise SystemExit(f"Could not parse DATABASE_URL: {base}")

prefix, host, port, path, qs = m.groups()
qs = qs or ""
if "sslmode=" not in qs:
    qs = (qs + "&" if qs else "?") + "sslmode=require"

# Candidates to try (both aws-0 and aws-1 exist in the wild)
regions = [
    "us-east-1","us-east-2","us-west-1","us-west-2",
    "eu-west-1","eu-central-1",
    "ap-southeast-1","ap-southeast-2",
    "ap-northeast-1","ap-northeast-2",
    "ap-south-1","sa-east-1",
]
prefixes = ["aws-0", "aws-1"]
ports = ["5432", "6543"]  # try both session and transaction entrypoints

def try_one(h, p):
    url = f"{prefix}{h}:{p}{path}{qs}"
    try:
        conn = psycopg.connect(url, connect_timeout=5)
        conn.close()
        return True, url
    except Exception as e:
        return False, str(e).splitlines()[-1]

print("Testing pooler endpoints...")
for pre in prefixes:
    for reg in regions:
        h = f"{pre}-{reg}.pooler.supabase.com"
        for p in ports:
            ok, msg = try_one(h, p)
            if ok:
                print("\n✅ FOUND WORKING POOLER URL:")
                print(msg)
                raise SystemExit(0)
            else:
                # Print only the useful tail error
                if "Tenant or user not found" not in msg and "getaddrinfo" not in msg:
                    print(f"{h}:{p} -> {msg}")

print("\n❌ No working pooler found in candidate list.")
print("If this happens, your region is outside this list or network is blocking the pooler.")
