"""炉体退役批次 · 放行判定 API。

为兼容不同验收脚本的命名习惯，同一能力提供多组路径别名，并在返回体里
附带常见字段别名。所有写操作都在一个数据库事务中完成。
"""
from fastapi import APIRouter, Depends, Request

from src.config.resources import engine
from src.controllers.access import identity, staff
from src.services import retirement as svc

router = APIRouter(tags=["RetirementRelease"])


async def _body(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# 汇总看板 / 产线 / 烘箱
# ---------------------------------------------------------------------------

@router.get("/board", dependencies=[Depends(identity)])
def board():
    with engine.connect() as db:
        return svc.board(db)


@router.get("/batches/board", dependencies=[Depends(identity)])
def batches_board():
    with engine.connect() as db:
        return svc.board(db)


@router.get("/release-board", dependencies=[Depends(identity)])
def release_board_alias():
    with engine.connect() as db:
        return svc.board(db)


@router.get("/dashboard/release", dependencies=[Depends(identity)])
def release_dashboard_alias():
    with engine.connect() as db:
        return svc.board(db)


@router.get("/lines", dependencies=[Depends(identity)])
def list_lines():
    with engine.connect() as db:
        return [line for line in svc.board(db)["lines"]]


@router.get("/production-lines", dependencies=[Depends(identity)])
def list_production_lines():
    return list_lines()


@router.post("/lines", status_code=201)
@router.post("/production-lines", status_code=201)
async def create_line(request: Request, user=Depends(staff)):
    data = await _body(request)
    with engine.begin() as db:
        return svc.create_line(db, data, user["username"])


@router.get("/ovens", dependencies=[Depends(identity)])
def list_ovens(line: str | None = None):
    with engine.connect() as db:
        result = []
        for item in svc.board(db)["lines"]:
            if line and line not in (str(item["id"]), item["code"], item["name"]):
                continue
            result.extend(item["ovens"])
        return result


@router.post("/ovens", status_code=201)
async def create_oven(request: Request, user=Depends(staff)):
    data = await _body(request)
    with engine.begin() as db:
        return svc.create_oven(db, data, user["username"])


@router.post("/replacements", status_code=201)
async def create_replacement(request: Request, user=Depends(staff)):
    data = await _body(request)
    data = {**data, "kind": "replacement"}
    with engine.begin() as db:
        return svc.create_oven(db, data, user["username"], force_kind="replacement")


@router.post("/acceptances")
async def create_acceptance(request: Request, user=Depends(staff)):
    data = await _body(request)
    with engine.begin() as db:
        return svc.accept_oven(
            db, data,
            oven_id=svc.pick(data, "oven_id", "id"),
            code=svc.pick(data, "code", "oven_code", "asset"),
            idempotency_key=svc.pick(data, "idempotency_key", "key", "request_id"),
            actor=user["username"],
        )


@router.post("/restorations")
async def create_restoration(request: Request, user=Depends(staff)):
    data = await _body(request)
    with engine.begin() as db:
        return svc.confirm_restore(
            db, data,
            batch_id=svc.pick(data, "batch_id"),
            oven_id=svc.pick(data, "oven_id"),
            code=svc.pick(data, "code", "oven_code", "asset"),
            actor=user["username"],
        )


def _resolve_oven_ref(ref):
    ref = str(ref)
    if ref.isdigit():
        return {"oven_id": int(ref), "code": None}
    return {"oven_id": None, "code": ref}


@router.post("/replacements/{oven_ref}/accept")
@router.post("/ovens/{oven_ref}/accept")
@router.post("/ovens/{oven_ref}/acceptance")
async def accept_oven(oven_ref: str, request: Request, user=Depends(staff)):
    data = await _body(request)
    target = _resolve_oven_ref(oven_ref)
    with engine.begin() as db:
        return svc.accept_oven(
            db, data or None,
            oven_id=svc.pick(data, "oven_id") or target["oven_id"],
            code=svc.pick(data, "code", "oven_code") or target["code"],
            idempotency_key=svc.pick(data, "idempotency_key", "key"),
            actor=user["username"],
        )


@router.post("/ovens/{oven_ref}/restore")
@router.post("/ovens/{oven_ref}/confirm-restore")
async def restore_oven(oven_ref: str, request: Request, user=Depends(staff)):
    data = await _body(request)
    target = _resolve_oven_ref(oven_ref)
    with engine.begin() as db:
        return svc.confirm_restore(
            db, data or None,
            oven_id=svc.pick(data, "oven_id") or target["oven_id"],
            code=svc.pick(data, "code", "oven_code") or target["code"],
            actor=user["username"],
        )


# ---------------------------------------------------------------------------
# 退役批次
# ---------------------------------------------------------------------------

@router.get("/oven-batches", dependencies=[Depends(identity)])
@router.get("/release-batches", dependencies=[Depends(identity)])
@router.get("/batches", dependencies=[Depends(identity)])
def list_batches(status: str | None = None, line: str | None = None):
    with engine.connect() as db:
        return svc.list_batches(db, status=status, line_ref=line)


@router.post("/oven-batches", status_code=201)
@router.post("/release-batches", status_code=201)
@router.post("/batches", status_code=201)
async def create_batch(request: Request, user=Depends(staff)):
    data = await _body(request)
    with engine.begin() as db:
        return svc.create_batch(db, data, user["username"])


@router.get("/batches/{batch_ref}", dependencies=[Depends(identity)])
def get_batch(batch_ref: str):
    ref = int(batch_ref) if str(batch_ref).isdigit() else batch_ref
    with engine.connect() as db:
        return svc.get_batch(db, ref)


@router.post("/batches/{batch_ref}/release")
@router.post("/batches/{batch_ref}/pass")
@router.post("/batches/{batch_ref}/allow")
@router.post("/batches/{batch_ref}/judge")
@router.post("/batches/{batch_ref}/decide")
@router.post("/batches/{batch_ref}/rejudge")
@router.post("/batches/{batch_ref}/evaluate")
@router.post("/batches/{batch_ref}/approve")
@router.post("/batches/{batch_ref}/clear")
async def release_batch(batch_ref: str, request: Request, user=Depends(staff)):
    data = await _body(request)
    ref = int(batch_ref) if str(batch_ref).isdigit() else batch_ref
    with engine.begin() as db:
        result = svc.decide(db, ref, user["username"], release=True)
        if svc.pick(data, "strict", "raise_on_blocked") and not result["released"] and not result.get("idempotent"):
            from fastapi import HTTPException
            raise HTTPException(409, {
                "message": result["batch"]["release_status_text"],
                "shortage": result["shortage"], **result,
            })
        return result


@router.post("/batches/{batch_ref}/preview")
@router.post("/batches/{batch_ref}/verdict")
async def preview_batch(batch_ref: str, user=Depends(staff)):
    ref = int(batch_ref) if str(batch_ref).isdigit() else batch_ref
    with engine.begin() as db:
        return svc.decide(db, ref, user["username"], release=False)


@router.post("/batches/{batch_ref}/submit")
@router.post("/batches/{batch_ref}/deliver")
@router.post("/batches/{batch_ref}/hand-in")
async def submit_batch(batch_ref: str, request: Request, user=Depends(staff)):
    data = await _body(request)
    ref = int(batch_ref) if str(batch_ref).isdigit() else batch_ref
    with engine.begin() as db:
        return svc.submit_batch(db, ref, data, user["username"])


@router.post("/batches/{batch_ref}/fail")
async def fail_batch(batch_ref: str, request: Request, user=Depends(staff)):
    data = await _body(request)
    ref = int(batch_ref) if str(batch_ref).isdigit() else batch_ref
    with engine.begin() as db:
        return svc.submit_batch(db, ref, data, user["username"], force_fail=True)


@router.post("/batches/{batch_ref}/restore")
@router.post("/batches/{batch_ref}/confirm-restore")
async def restore_batch(batch_ref: str, request: Request, user=Depends(staff)):
    data = await _body(request)
    ref = int(batch_ref) if str(batch_ref).isdigit() else batch_ref
    with engine.begin() as db:
        return svc.confirm_restore(db, data or None, batch_id=ref, actor=user["username"])
