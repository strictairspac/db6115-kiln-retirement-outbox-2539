"""炉体退役放行台账的幂等演示数据。

用几组小场景把验收口径直接摆在「炉体退役批次」页上：

- LINE-A：两批各退一台，单独都够余量，并发放行时只放一批；
- LINE-B：当前只有一台可用旧炉，待验收替换炉验收后补足；
- LINE-C：放行 → 交卷（成功/失败）→ 设备组确认恢复；
- LINE-D：已有一批交卷失败，旧炉未计回，确认恢复后余量回来；
- LINE-E：已有一批放行待交卷，旧炉即刻移出可用数；
- LINE-F：已交卷终态示例。
"""
from sqlalchemy import select

from src.domain import release as R
from src.models.retirement import (
    ovens, production_lines, retirement_batches, retirement_items,
)
from src.services import retirement as svc

DEMO = [
    {
        "line": {"code": "LINE-A", "name": "焙烧一线", "note": "两批并发退不同炉的余量临界场景"},
        "ovens": [
            {"code": "OV-301", "kind": "old", "state": R.AVAILABLE},
            {"code": "OV-302", "kind": "old", "state": R.AVAILABLE},
        ],
        "batches": [
            {"code": "RTB-301", "title": "OV-301 封板退役", "ovens": ["OV-301"]},
            {"code": "RTB-302", "title": "OV-302 封板退役", "ovens": ["OV-302"]},
        ],
    },
    {
        "line": {"code": "LINE-B", "name": "焙烧二线", "note": "替换炉验收后才计入可用数"},
        "ovens": [
            {"code": "OV-311", "kind": "old", "state": R.AVAILABLE},
            {"code": "OV-312R", "kind": "replacement", "state": R.PENDING_ACCEPTANCE},
        ],
        "batches": [
            {"code": "RTB-311", "title": "OV-311 退役，OV-312R 替换", "ovens": ["OV-311"]},
        ],
    },
    {
        "line": {"code": "LINE-C", "name": "焙烧三线", "note": "放行后交卷，可演练交卷失败与恢复确认"},
        "ovens": [
            {"code": "OV-321", "kind": "old", "state": R.AVAILABLE},
            {"code": "OV-322", "kind": "old", "state": R.AVAILABLE},
        ],
        "batches": [
            {"code": "RTB-321", "title": "OV-321 封板退役", "ovens": ["OV-321"]},
        ],
    },
    {
        "line": {"code": "LINE-D", "name": "焙烧四线", "note": "交卷失败后，设备组确认恢复才计回"},
        "ovens": [
            {"code": "OV-331", "kind": "old", "state": R.DELIVERY_FAILED},
            {"code": "OV-332", "kind": "old", "state": R.AVAILABLE},
        ],
        "batches": [
            {"code": "RTB-331", "title": "OV-331 退役证书交卷失败", "ovens": ["OV-331"],
             "status": R.FAILED, "error": "模拟档案室回执未确认，交卷失败。"},
            {"code": "RTB-332", "title": "OV-332 封板退役", "ovens": ["OV-332"]},
        ],
    },
    {
        "line": {"code": "LINE-E", "name": "焙烧五线", "note": "已放行旧炉在交卷前也不算可用"},
        "ovens": [
            {"code": "OV-341", "kind": "old", "state": R.RELEASED},
            {"code": "OV-342", "kind": "old", "state": R.AVAILABLE},
        ],
        "batches": [
            {"code": "RTB-341", "title": "OV-341 已放行待交卷", "ovens": ["OV-341"],
             "status": R.RELEASED_BATCH},
        ],
    },
    {
        "line": {"code": "LINE-F", "name": "焙烧六线", "note": "正常交卷终态"},
        "ovens": [
            {"code": "OV-351", "kind": "old", "state": R.RETIRED},
            {"code": "OV-352", "kind": "old", "state": R.AVAILABLE},
        ],
        "batches": [
            {"code": "RTB-351", "title": "OV-351 已交卷退役", "ovens": ["OV-351"],
             "status": R.DELIVERED},
        ],
    },
]


def seed_retirement(db):
    if db.execute(select(production_lines.c.id).limit(1)).first():
        return {"skipped": True}
    actor = "seed"
    for spec in DEMO:
        line_info = spec["line"]
        line_id = db.execute(production_lines.insert().values(
            code=line_info["code"], name=line_info["name"], note=line_info.get("note", ""),
            created_by=actor, created_at=svc.now_ts(),
        )).inserted_primary_key[0]
        oven_ids = {}
        for oven in spec["ovens"]:
            oid = db.execute(ovens.insert().values(
                code=oven["code"], line_id=line_id, kind=oven["kind"], state=oven["state"],
                accepted_by=actor if oven["state"] == R.AVAILABLE and oven["kind"] == "replacement" else None,
                accepted_at=svc.now_ts() if oven["state"] == R.AVAILABLE and oven["kind"] == "replacement" else None,
                created_at=svc.now_ts(),
            )).inserted_primary_key[0]
            oven_ids[oven["code"]] = oid
        for batch in spec["batches"]:
            codes = batch["ovens"]
            usable = svc.counts_for(db, line_id)["available"]
            retire_count = len(codes)
            verdict, shortage, remaining, text = R.verdict_for(usable, retire_count)
            status = batch.get("status", R.PENDING)
            bid = db.execute(retirement_batches.insert().values(
                code=batch["code"], line_id=line_id, title=batch.get("title", ""),
                status=status,
                release_verdict=status if status != R.PENDING else verdict,
                shortage=shortage if status == R.PENDING else (1 if status == R.FAILED else 0),
                usable_count=usable, retire_count=retire_count, remaining_count=remaining,
                verdict_text=batch.get("error") or text,
                submit_error=batch.get("error"),
                created_by=actor, created_at=svc.now_ts(),
                decided_at=svc.now_ts(),
                released_at=svc.now_ts() if status in (R.RELEASED_BATCH, R.DELIVERED, R.FAILED) else None,
                submitted_at=svc.now_ts() if status in (R.DELIVERED, R.FAILED) else None,
            )).inserted_primary_key[0]
            db.execute(retirement_items.insert(), [
                {"batch_id": bid, "oven_id": oven_ids[code]} for code in codes
            ])
    return {"seeded": len(DEMO)}
