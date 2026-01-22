from uuid import UUID
from typing import List

from fastapi import APIRouter, HTTPException

from app.core.db import get_conn

from app.models.schemas import (
    BusinessIntakeIn,
    BusinessOut,
    BusinessWithCompetitorsOut,
    SnapshotBulkIn,
    SnapshotBulkOut,
    SnapshotListItemOut,
    SnapshotDetailOut,
    ReportRegisterIn,
    ReportOut,
    ReportLatestOut,
)

from app.services.business_service import (
    create_business_and_competitors,
    get_business_with_competitors,
    list_businesses,
)

from app.services.snapshot_service import (
    insert_snapshots_bulk,
    list_snapshots_by_business,
    get_snapshot_by_id,
)

from app.services.report_service import (
    register_report,
    get_latest_report,
    list_reports_by_business,
    get_report_by_id,
)

from app.api.analytics import router as analytics_router
router = APIRouter()
router.include_router(analytics_router)




# --------------------
# Health
# --------------------

@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/db")
def health_db():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select 1;")
        return {"db": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db error: {str(e)}")


# --------------------
# Business
# --------------------

@router.post("/intake", response_model=BusinessWithCompetitorsOut)
def intake(payload: BusinessIntakeIn):
    try:
        return create_business_and_competitors(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed intake: {str(e)}")


@router.get("/business/{business_id}", response_model=BusinessWithCompetitorsOut)
def get_business(business_id: UUID):
    result = get_business_with_competitors(business_id)
    if not result:
        raise HTTPException(status_code=404, detail="Business not found")
    return result


@router.get("/businesses", response_model=List[BusinessOut])
def get_businesses():
    return list_businesses()


# --------------------
# Snapshots
# --------------------

@router.post("/snapshot/bulk", response_model=SnapshotBulkOut)
def snapshot_bulk(payload: SnapshotBulkIn):
    try:
        return insert_snapshots_bulk(payload)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to insert snapshots: {str(e)}",
        )


@router.get("/snapshots", response_model=List[SnapshotListItemOut])
def get_snapshots(business_id: UUID):
    return list_snapshots_by_business(business_id)


@router.get("/snapshot/{snapshot_id}", response_model=SnapshotDetailOut)
def get_snapshot(snapshot_id: UUID):
    snap = get_snapshot_by_id(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap


@router.delete("/snapshot/{snapshot_id}")
def delete_snapshot(snapshot_id: UUID):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM snapshots WHERE id = %s RETURNING id",
                    (snapshot_id,),
                )
                row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Snapshot not found")

            conn.commit()

        return {"deleted_snapshot_id": str(snapshot_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete snapshot: {str(e)}",
        )


# --------------------
# Reports
# --------------------

# --------------------
# Reports
# --------------------

@router.post("/report/register", response_model=ReportOut)
def report_register(payload: ReportRegisterIn):
    try:
        return register_report(payload)
    except Exception as e:
        msg = str(e)
        if "uq_reports_business_period" in msg or "duplicate key value" in msg:
            raise HTTPException(
                status_code=409,
                detail="Report for that business + period already exists",
            )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to register report: {msg}",
        )


@router.get("/reports/{business_id}/latest", response_model=ReportLatestOut)
def reports_latest(business_id: UUID):
    try:
        return get_latest_report(business_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch latest report: {str(e)}",
        )


@router.get("/reports", response_model=List[ReportOut])
def reports_list(business_id: UUID):
    from app.services.report_service import list_reports_by_business

    try:
        return list_reports_by_business(business_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch reports: {str(e)}",
        )
@router.get("/report/{report_id}", response_model=ReportOut)
def report_detail(report_id: UUID):
    r = get_report_by_id(report_id)
    if not r:
        raise HTTPException(status_code=404, detail="Report not found")
    return r


