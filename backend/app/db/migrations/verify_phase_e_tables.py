from app.core.db import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "select to_regclass('public.report_schedules') as rs, "
            "to_regclass('public.generated_reports') as gr;"
        )
        print(cur.fetchone())
