"""烘箱余量 / 退役批次放行的用例服务。

所有判定都在“产线行锁 + 事务”内重算可用数，保证同一产线两批并发时
恰好放行一批；不同产线锁不同行，互不阻塞。
"""
from time import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from src.domain import release as R
from src.models.retirement import (
    oven_acceptances, ovens, production_lines, retirement_batches, retirement_items,
)
from .audit import record


def now_ts():
    return int(time())


# ---------------------------------------------------------------------------
# 序列化 / 读模型
# ---------------------------------------------------------------------------

def _oven_view(row):
    oven = dict(row)
    oven["state_label"] = R.OVEN_STATE_LABELS.get(oven["state"], oven["state"])
    oven["kind_label"] = "替换炉" if oven["kind"] == "replacement" else "旧炉"
    oven["usable"] = oven["state"] in R.COUNTED_OVEN_STATES
    return oven


def counts_for(db, line_id):
    rows = db.execute(
        select(ovens.c.state).where(ovens.c.line_id == line_id)
    ).scalars().all()
    counts = {state: 0 for state in R.OVEN_STATES}
    counts["total"] = len(rows)
    for state in rows:
        counts[state] = counts.get(state, 0) + 1
    counts["available"] = counts.get(R.AVAILABLE, 0)
    counts["usable"] = counts["available"]
    return counts


def _item_ovens(db, batch_id):
    rows = db.execute(
        select(ovens).join(retirement_items, retirement_items.c.oven_id == ovens.c.id)
        .where(retirement_items.c.batch_id == batch_id)
        .order_by(ovens.c.code)
    ).mappings().all()
    return [_oven_view(r) for r in rows]


def batch_view(db, row, line=None):
    batch = dict(row)
    items = _item_ovens(db, batch["id"])
    codes = [item["code"] for item in items]
    status = batch["status"]
    # 待办批次的缺口按当前台账实时重算：替换炉验收、旧炉恢复后立即反映“补足”，
    # 不必等下一次显式判定；放行/交卷等终态仍以持久化状态为准。
    if status == R.PENDING:
        live_usable = counts_for(db, batch["line_id"])["available"]
        retire_count = len(codes) or batch.get("retire_count") or 0
        v, s, rem, text = R.verdict_for(live_usable, retire_count)
        batch.update({
            "usable_count": live_usable, "retire_count": retire_count,
            "shortage": s, "remaining_count": rem, "release_verdict": v,
            "verdict_text": text,
        })
    shortage = batch.get("shortage") or 0
    allowed = shortage == 0
    released = status == R.RELEASED_BATCH
    if status == R.PENDING:
        release_code = R.ALLOWED if allowed else R.BLOCKED
        release_label = R.VERDICT_LABELS[release_code]
        if not allowed:
            release_label = f"暂不放行·还缺{shortage}台"
        release_status_text = release_label
    elif status == R.RELEASED_BATCH:
        release_code = R.RELEASED_BATCH
        release_status_text = "已放行（待交卷）"
    else:
        release_code = status
        release_status_text = R.BATCH_STATE_LABELS.get(status, status)
    line_code = line["code"] if line else batch.get("_line_code")
    line_name = line["name"] if line else batch.get("_line_name")
    batch.update({
        "status_label": R.BATCH_STATE_LABELS.get(status, status),
        "state": status,
        "release_status": release_code,
        "release_status_label": release_status_text,
        "release_status_text": release_status_text,
        "released": released,
        "allowed": allowed and status == R.PENDING,
        "can_release": status == R.PENDING,
        "shortage": shortage,
        "missing": shortage,
        "missing_count": shortage,
        "lack_count": shortage,
        "still_need": shortage,
        "shortage_text": f"还缺{shortage}台" if shortage > 0 else "余量足够",
        "todo": status in R.TODO_BATCH_STATES,
        "oven_codes": codes,
        "assets": codes,
        "ovens": items,
        "line_code": line_code,
        "line_name": line_name,
        "retire_count": batch.get("retire_count") or len(codes),
    })
    return batch


def _line_batches(db, line_id):
    rows = db.execute(
        select(retirement_batches).where(retirement_batches.c.line_id == line_id)
        .order_by(retirement_batches.c.id)
    ).mappings().all()
    line = db.execute(select(production_lines).where(production_lines.c.id == line_id)).mappings().first()
    return [batch_view(db, row, line) for row in rows]


def line_view(db, row, with_batches=True):
    line = dict(row)
    line["counts"] = counts_for(db, line["id"])
    line["usable_count"] = line["counts"]["available"]
    line["available_count"] = line["counts"]["available"]
    line["ovens"] = [_oven_view(o) for o in db.execute(
        select(ovens).where(ovens.c.line_id == line["id"]).order_by(ovens.c.kind.desc(), ovens.c.code)
    ).mappings().all()]
    if with_batches:
        line["batches"] = _line_batches(db, line["id"])
    return line


def board(db):
    lines = [line_view(db, row) for row in db.execute(
        select(production_lines).order_by(production_lines.c.id)
    ).mappings().all()]
    batches = [batch for line in lines for batch in line["batches"]]
    todo = [b for b in batches if b["status"] in R.TODO_BATCH_STATES]
    return {
        "lines": lines,
        "batches": batches,
        "todo_batches": todo,
        "summary": {
            "line_count": len(lines),
            "batch_count": len(batches),
            "todo_count": len(todo),
            "released_count": sum(b["status"] == R.RELEASED_BATCH for b in batches),
            "shortage_batch_count": sum(b["status"] == R.PENDING and b["shortage"] > 0 for b in batches),
        },
        "rule": f"每条产线必须保留至少 {R.FLOOR_USABLE} 台可用烘箱",
    }


# ---------------------------------------------------------------------------
# 解析辅助（请求体字段名做宽松兼容）
# ---------------------------------------------------------------------------

def pick(data, *keys, default=None):
    if not isinstance(data, dict):
        return default
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def resolve_line(db, data=None, line_ref=None):
    ref = line_ref if line_ref is not None else pick(
        data or {}, "line_id", "line", "line_code", "production_line", "production_line_code", "产线",
    )
    if ref is None:
        raise HTTPException(422, "请指定产线")
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        row = db.execute(select(production_lines).where(production_lines.c.id == int(ref))).mappings().first()
    else:
        code = R.normalize_code(ref)
        row = db.execute(select(production_lines).where(
            (production_lines.c.code == code) | (production_lines.c.name == str(ref))
        )).mappings().first()
    if row is None:
        raise HTTPException(404, f"产线不存在：{ref}")
    return row


def resolve_oven(db, oven_id=None, code=None):
    if oven_id is not None:
        row = db.execute(select(ovens).where(ovens.c.id == oven_id)).mappings().first()
        if row is None:
            raise HTTPException(404, f"烘箱不存在：{oven_id}")
        return row
    if code is not None:
        normalized = R.normalize_code(code)
        row = db.execute(select(ovens).where(ovens.c.code == normalized)).mappings().first()
        if row is None:
            raise HTTPException(404, f"烘箱不存在：{code}")
        return row
    raise HTTPException(422, "请指定烘箱")


def get_batch_row(db, batch_id):
    # 非纯数字引用一定是批次编号，避免在 PostgreSQL 上用字符串比整数列报错。
    if isinstance(batch_id, str) and not batch_id.isdigit():
        code = R.normalize_code(batch_id)
        row = db.execute(select(retirement_batches).where(
            retirement_batches.c.code == code
        )).mappings().first()
        if row is None:
            raise HTTPException(404, "退役批次不存在")
        return row
    row = db.execute(
        select(retirement_batches).where(retirement_batches.c.id == batch_id)
    ).mappings().first()
    if row is None:
        # 也允许用形如 “301” 之外的编号兜底（理论上前面已处理非数字）。
        code = R.normalize_code(batch_id) if isinstance(batch_id, str) else None
        if code:
            row = db.execute(select(retirement_batches).where(
                retirement_batches.c.code == code
            )).mappings().first()
    if row is None:
        raise HTTPException(404, "退役批次不存在")
    return row


def get_batch(db, batch_id):
    row = get_batch_row(db, batch_id)
    line = db.execute(select(production_lines).where(production_lines.c.id == row["line_id"])).mappings().first()
    return batch_view(db, row, line)


def list_batches(db, status=None, line_ref=None):
    stmt = select(retirement_batches)
    if status:
        stmt = stmt.where(retirement_batches.c.status == status)
    rows = db.execute(stmt.order_by(retirement_batches.c.id.desc()).limit(200)).mappings().all()
    result = []
    for row in rows:
        line = db.execute(select(production_lines).where(
            production_lines.c.id == row["line_id"]
        )).mappings().first()
        view = batch_view(db, row, line)
        if line_ref is not None:
            ref = str(line_ref)
            if ref not in (str(line["id"]), line["code"], line["name"]):
                continue
        result.append(view)
    return result


# ---------------------------------------------------------------------------
# 产线 / 烘箱 / 批次登记
# ---------------------------------------------------------------------------

def create_line(db, data, actor):
    code = R.normalize_code(pick(data, "code", "line_code", "production_line_code", "产线编号"))
    name = (pick(data, "name", "line_name", "production_line_name", "产线名称") or code or "").strip()
    if not code:
        raise HTTPException(422, "产线需要 code 编号")
    if not name:
        name = code
    exists = db.execute(select(production_lines.c.id).where(production_lines.c.code == code)).first()
    if exists:
        return line_view(db, db.execute(select(production_lines).where(
            production_lines.c.code == code)).mappings().first())
    change = db.execute(insert(production_lines).values(
        code=code, name=str(name)[:120], note=str(pick(data, "note", "description", default="") or "")[:420],
        created_by=actor, created_at=now_ts(),
    ))
    record(db, "create_line", actor, "production_line", change.inserted_primary_key[0])
    return line_view(db, db.execute(select(production_lines).where(
        production_lines.c.id == change.inserted_primary_key[0])).mappings().first())


def create_oven(db, data, actor, force_kind=None):
    code = R.normalize_code(pick(data, "code", "oven_code", "asset", "asset_code", "炉号", "烘箱编号"))
    if not code:
        raise HTTPException(422, "烘箱需要 code/炉号")
    existing = db.execute(select(ovens).where(ovens.c.code == code)).mappings().first()
    if existing:
        return _oven_view(existing)
    line = resolve_line(db, data)
    kind = force_kind or pick(data, "kind", "type", "oven_kind", default="old")
    kind = "replacement" if str(kind).lower() in ("replacement", "new", "替换", "替换炉") else "old"
    wanted_state = pick(data, "state", "status", "oven_state")
    if wanted_state:
        state = wanted_state if wanted_state in R.OVEN_STATES else {
            "可用": R.AVAILABLE, "待验收": R.PENDING_ACCEPTANCE,
        }.get(wanted_state)
        if state is None:
            raise HTTPException(422, f"未知烘箱状态：{wanted_state}")
    else:
        state = R.PENDING_ACCEPTANCE if kind == "replacement" else R.AVAILABLE
    change = db.execute(insert(ovens).values(
        code=code, line_id=line["id"], kind=kind, state=state,
        note=str(pick(data, "note", default="") or "")[:420],
        accepted_by=actor if state == R.AVAILABLE and kind == "replacement" else None,
        accepted_at=now_ts() if state == R.AVAILABLE and kind == "replacement" else None,
        created_at=now_ts(),
    ))
    oven_id = change.inserted_primary_key[0]
    record(db, "create_oven", actor, "oven", oven_id)
    return _oven_view(db.execute(select(ovens).where(ovens.c.id == oven_id)).mappings().first())


def _extract_oven_codes(data):
    """把各种常见请求形态归一成 code/id 列表（保留 int 表示主键 id）。"""
    raw = []
    if not isinstance(data, dict):
        return raw
    for key in ("oven_codes", "oven_ids", "ovens", "assets", "asset_codes", "furnaces",
                "furnace_codes", "炉号", "烘箱编号", "oven_code"):
        if key in data and data[key] is not None:
            raw.extend(_as_list(data[key]))
    for key in ("items", "retirement_items", "details"):
        raw.extend(_as_list(data.get(key)))

    out = []
    for entry in raw:
        if isinstance(entry, int):
            out.append(entry)
        elif isinstance(entry, str):
            text = entry.strip()
            if text:
                out.append(text)
        elif isinstance(entry, dict):
            value = pick(entry, "oven_id", "id", "oven_code", "code", "asset", "asset_code")
            if value is not None:
                out.append(value)
    return out


def create_batch(db, data, actor):
    if not isinstance(data, dict):
        raise HTTPException(422, "批次内容需要是对象")
    line = None
    line_ref = pick(data, "line_id", "line", "line_code", "production_line", "production_line_code")
    refs = _extract_oven_codes(data)
    resolved_ovens = []
    for ref in refs:
        if isinstance(ref, int):
            resolved_ovens.append(resolve_oven(db, oven_id=ref))
        elif isinstance(ref, str) and ref.isdigit():
            maybe = db.execute(select(ovens).where(ovens.c.id == int(ref))).mappings().first()
            resolved_ovens.append(dict(maybe) if maybe else resolve_oven(db, code=ref))
        else:
            resolved_ovens.append(resolve_oven(db, code=ref))
    if line_ref is None and resolved_ovens:
        line = db.execute(select(production_lines).where(
            production_lines.c.id == resolved_ovens[0]["line_id"]
        )).mappings().first()
    else:
        line = resolve_line(db, data, line_ref)
    if line is None:
        raise HTTPException(422, "请指定产线或至少一台本线烘箱")
    for oven in resolved_ovens:
        if oven["line_id"] != line["id"]:
            raise HTTPException(422, f"烘箱 {oven['code']} 不属于产线 {line['code']}")

    quantity = pick(data, "retire_count", "quantity", "count", "oven_count", "台数")
    retire_count = len(resolved_ovens) or (int(quantity) if quantity is not None else 0)
    if retire_count <= 0:
        raise HTTPException(422, "退役批次至少要退 1 台炉（提供 oven_codes 或 retire_count）")

    code = R.normalize_code(pick(data, "code", "batch_code", "no", "编号", "批次号"))
    if not code:
        code = f"RTB-{uuid4().hex[:10].upper()}"
    exists = db.execute(select(retirement_batches.c.id).where(
        retirement_batches.c.code == code)).first()
    if exists:
        return get_batch(db, exists[0])

    usable = counts_for(db, line["id"])["available"]
    verdict, shortage, remaining, text = R.verdict_for(usable, retire_count)
    change = db.execute(insert(retirement_batches).values(
        code=code, line_id=line["id"],
        title=str(pick(data, "title", "name", default="") or "")[:160],
        status=R.PENDING, release_verdict=verdict, shortage=shortage,
        usable_count=usable, retire_count=retire_count, remaining_count=remaining,
        verdict_text=text, extra={"quantity_only": not resolved_ovens} if not resolved_ovens else None,
        created_by=actor, created_at=now_ts(),
    ))
    batch_id = change.inserted_primary_key[0]
    if resolved_ovens:
        db.execute(insert(retirement_items), [
            {"batch_id": batch_id, "oven_id": oven["id"]} for oven in resolved_ovens
        ])
    record(db, "create_release_batch", actor, "release_batch", batch_id)
    return get_batch(db, batch_id)


# ---------------------------------------------------------------------------
# 放行判定（并发核心）
# ---------------------------------------------------------------------------

def _lock_line(db, line_id):
    stmt = select(production_lines).where(production_lines.c.id == line_id)
    if db.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    row = db.execute(stmt).mappings().first()
    if row is None:
        raise HTTPException(404, "产线不存在")
    return row


def _ensure_items_assigned(db, batch, usable_ovens):
    """数量型批次在放行时，按炉号顺序把若干台可用旧炉挂到本批。"""
    existing = db.execute(select(retirement_items.c.oven_id).where(
        retirement_items.c.batch_id == batch["id"]
    )).scalars().all()
    if existing:
        return
    need = batch["retire_count"]
    candidates = [o for o in usable_ovens if o["kind"] == "old"] or usable_ovens
    chosen = candidates[:need]
    if chosen:
        db.execute(insert(retirement_items), [
            {"batch_id": batch["id"], "oven_id": o["id"]} for o in chosen
        ])


def decide(db, batch_id, actor, *, release=True):
    """在产线行锁内重算并（可选）执行放行。

    返回 dict：released / allowed / shortage / batch / line。
    余量不足时不抛业务冲突，而是返回 ``released=False`` 与缺口，
    便于调用方与界面直接展示“还缺 N 台”。
    """
    row = get_batch_row(db, batch_id)
    line = _lock_line(db, row["line_id"])
    row = get_batch_row(db, batch_id)  # 锁后重读

    # 已放行 / 已交卷 / 已恢复：幂等返回，重试不产生第二次放行效果。
    if row["status"] != R.PENDING:
        view = batch_view(db, row, line)
        return {
            "released": row["status"] == R.RELEASED_BATCH,
            "allowed": view["shortage"] == 0,
            "idempotent": True,
            "shortage": view["shortage"],
            "batch": view,
            "line": line_view(db, line),
        }

    usable_rows = db.execute(
        select(ovens).where(ovens.c.line_id == line["id"], ovens.c.state == R.AVAILABLE)
        .order_by(ovens.c.code)
    ).mappings().all()
    usable_ovens = [dict(o) for o in usable_rows]

    _ensure_items_assigned(db, row, usable_ovens)
    item_rows = db.execute(
        select(ovens).join(retirement_items, retirement_items.c.oven_id == ovens.c.id)
        .where(retirement_items.c.batch_id == row["id"])
    ).mappings().all()
    items = [dict(o) for o in item_rows]
    retire_count = len(items) or row["retire_count"]

    # 并发下的重复炉保护：申请炉若已被“别的批次”放行占用，则阻止本批。
    item_ids = [i["id"] for i in items]
    conflicts = []
    if item_ids:
        conflicts = db.execute(
            select(ovens.c.code)
            .join(retirement_items, retirement_items.c.oven_id == ovens.c.id)
            .where(
                retirement_items.c.batch_id != row["id"],
                ovens.c.state == R.RELEASED,
                ovens.c.id.in_(item_ids),
            )
        ).scalars().all()
    if conflicts:
        text = f"炉号 {'、'.join(conflicts)} 已被其它已放行批次占用，不能重复退役。"
        db.execute(update(retirement_batches).where(retirement_batches.c.id == row["id"]).values(
            release_verdict=R.BLOCKED, verdict_text=text, decided_at=now_ts(),
        ))
        view = get_batch(db, row["id"])
        return {"released": False, "allowed": False, "shortage": view["shortage"],
                "conflict": True, "batch": view, "line": line_view(db, line)}

    usable_count = len(usable_ovens)
    verdict, shortage, remaining, text = R.verdict_for(usable_count, retire_count)
    values = {
        "release_verdict": verdict, "shortage": shortage, "usable_count": usable_count,
        "retire_count": retire_count, "remaining_count": remaining, "verdict_text": text,
        "decided_at": now_ts(),
    }

    did_release = False
    if release and shortage == 0:
        values.update(status=R.RELEASED_BATCH, released_at=now_ts())
        db.execute(update(retirement_batches).where(
            retirement_batches.c.id == row["id"]).values(**values))
        # 关键口径：放行即刻把旧炉移出可用数（交卷前也不算可用）。
        if items:
            db.execute(update(ovens).where(ovens.c.id.in_([i["id"] for i in items])).values(
                state=R.RELEASED))
        did_release = True
        record(db, "release_batch", actor, "release_batch", row["id"])
    else:
        db.execute(update(retirement_batches).where(
            retirement_batches.c.id == row["id"]).values(**values))
        if release:
            record(db, "block_batch", actor, "release_batch", row["id"])
        else:
            record(db, "judge_batch", actor, "release_batch", row["id"])

    view = get_batch(db, row["id"])
    return {
        "released": did_release,
        "allowed": shortage == 0,
        "idempotent": False,
        "shortage": shortage,
        "batch": view,
        "line": line_view(db, line),
    }


# ---------------------------------------------------------------------------
# 替换炉验收（幂等）
# ---------------------------------------------------------------------------

def accept_oven(db, data=None, *, oven_id=None, code=None, idempotency_key=None, actor="operator"):
    if data:
        oven_id = oven_id if oven_id is not None else pick(data, "oven_id", "id")
        code = code or pick(data, "code", "oven_code", "asset")
        idempotency_key = idempotency_key or pick(data, "idempotency_key", "key", "request_id")
    oven = resolve_oven(db, oven_id=oven_id, code=code)

    receipt = db.execute(select(oven_acceptances).where(
        oven_acceptances.c.oven_id == oven["id"])).mappings().first()
    if receipt is not None:
        # 验收重试：只累加尝试次数，不新增收据、不重复计数。
        db.execute(update(oven_acceptances).where(oven_acceptances.c.id == receipt["id"]).values(
            attempts=oven_acceptances.c.attempts + 1))
        if oven["state"] != R.AVAILABLE:
            db.execute(update(ovens).where(ovens.c.id == oven["id"]).values(
                state=R.AVAILABLE, accepted_by=actor, accepted_at=now_ts()))
        record(db, "accept_retry", actor, "oven", oven["id"])
        fresh = resolve_oven(db, oven_id=oven["id"])
        return {"oven": _oven_view(fresh), "idempotent": True, "duplicate": True,
                "attempts": (receipt["attempts"] or 1) + 1,
                "line": line_view(db, db.execute(select(production_lines).where(
                    production_lines.c.id == oven["line_id"])).mappings().first())}

    if idempotency_key:
        key_owner = db.execute(select(oven_acceptances.c.id).where(
            oven_acceptances.c.idempotency_key == idempotency_key)).first()
        if key_owner:
            existing = db.execute(select(oven_acceptances).where(
                oven_acceptances.c.id == key_owner[0])).mappings().first()
            ov = resolve_oven(db, oven_id=existing["oven_id"])
            return {"oven": _oven_view(ov), "idempotent": True, "duplicate": True,
                    "attempts": existing["attempts"], "line": None}

    try:
        with db.begin_nested():
            db.execute(insert(oven_acceptances).values(
                oven_id=oven["id"], idempotency_key=idempotency_key, attempts=1,
                accepted_by=actor, accepted_at=now_ts()))
            db.execute(update(ovens).where(ovens.c.id == oven["id"]).values(
                state=R.AVAILABLE, accepted_by=actor, accepted_at=now_ts()))
    except IntegrityError:
        # 并发/重试撞上唯一收据：保存点回滚后按幂等返回。
        existing = db.execute(select(oven_acceptances).where(
            oven_acceptances.c.oven_id == oven["id"])).mappings().one()
        fresh = resolve_oven(db, oven_id=oven["id"])
        return {"oven": _oven_view(fresh), "idempotent": True, "duplicate": True,
                "attempts": existing["attempts"], "line": None}

    record(db, "accept_oven", actor, "oven", oven["id"])
    fresh = resolve_oven(db, oven_id=oven["id"])
    line = db.execute(select(production_lines).where(
        production_lines.c.id == oven["line_id"])).mappings().first()
    return {"oven": _oven_view(fresh), "idempotent": False, "duplicate": False,
            "attempts": 1, "line": line_view(db, line)}


# ---------------------------------------------------------------------------
# 交卷 / 失败 / 设备组确认恢复
# ---------------------------------------------------------------------------

def submit_batch(db, batch_id, data=None, actor="operator", *, force_fail=None):
    row = get_batch_row(db, batch_id)
    line = _lock_line(db, row["line_id"])
    row = get_batch_row(db, batch_id)

    if row["status"] in (R.DELIVERED, R.RESTORED):
        view = batch_view(db, row, line)
        return {"batch": view, "idempotent": True, "delivered": row["status"] == R.DELIVERED}
    if row["status"] == R.FAILED:
        raise HTTPException(409, "该批交卷失败，需设备组确认旧炉恢复可用后再办理")
    if row["status"] != R.RELEASED_BATCH:
        raise HTTPException(409, "批次尚未放行，不能交卷")

    should_fail = force_fail
    if should_fail is None and isinstance(data, dict):
        fail_flag = pick(data, "fail", "failed", "simulate_failure", "delivery_failed")
        if fail_flag is not None:
            should_fail = bool(fail_flag) if not isinstance(fail_flag, str) else fail_flag.lower() not in ("0", "false", "no", "")
        stage = pick(data, "failed_stage", "fault", "stage")
        if stage in ("before_ack", "after_receipt", "before_enqueue", "fail", "failed"):
            should_fail = True
    if should_fail is None:
        extra = row.get("extra") or {}
        should_fail = bool(extra.get("fail_on_submit")) if isinstance(extra, dict) else False

    item_ids = db.execute(select(retirement_items.c.oven_id).where(
        retirement_items.c.batch_id == row["id"])).scalars().all()

    if should_fail:
        reason = "模拟交卷失败：回执未获确认，旧炉保持不可用，等待设备组确认恢复。"
        if isinstance(data, dict) and pick(data, "reason", "error"):
            reason = str(pick(data, "reason", "error"))[:300]
        db.execute(update(retirement_batches).where(retirement_batches.c.id == row["id"]).values(
            status=R.FAILED, submit_error=reason, submitted_at=now_ts()))
        if item_ids:
            db.execute(update(ovens).where(ovens.c.id.in_(item_ids)).values(
                state=R.DELIVERY_FAILED))
        record(db, "submit_failed", actor, "release_batch", row["id"])
    else:
        db.execute(update(retirement_batches).where(retirement_batches.c.id == row["id"]).values(
            status=R.DELIVERED, submit_error=None, submitted_at=now_ts()))
        if item_ids:
            db.execute(update(ovens).where(ovens.c.id.in_(item_ids)).values(state=R.RETIRED))
        record(db, "submit_batch", actor, "release_batch", row["id"])

    view = get_batch(db, row["id"])
    return {"batch": view, "idempotent": False, "delivered": not should_fail,
            "failed": bool(should_fail), "line": line_view(db, line)}


def confirm_restore(db, data=None, *, batch_id=None, oven_id=None, code=None, actor="operator"):
    if data:
        batch_id = batch_id or pick(data, "batch_id", "id")
        oven_id = oven_id if oven_id is not None else pick(data, "oven_id")
        code = code or pick(data, "code", "oven_code", "asset")

    affected_batch_id = batch_id
    oven_ids = []
    if batch_id is not None:
        row = get_batch_row(db, batch_id)
        affected_batch_id = row["id"]
        _lock_line(db, row["line_id"])
        oven_ids = db.execute(select(retirement_items.c.oven_id).where(
            retirement_items.c.batch_id == row["id"])).scalars().all()
    elif oven_id is not None or code is not None:
        oven = resolve_oven(db, oven_id=oven_id, code=code)
        _lock_line(db, oven["line_id"])
        oven_ids = [oven["id"]]
        link = db.execute(select(retirement_items.c.batch_id).where(
            retirement_items.c.oven_id == oven["id"]).order_by(
            retirement_items.c.batch_id.desc())).scalars().first()
        affected_batch_id = link
    else:
        raise HTTPException(422, "请指定要确认恢复的烘箱或批次")

    restored = []
    for oid in oven_ids:
        oven = db.execute(select(ovens).where(ovens.c.id == oid)).mappings().one()
        if oven["state"] == R.DELIVERY_FAILED:
            db.execute(update(ovens).where(ovens.c.id == oid).values(
                state=R.AVAILABLE, restored_by=actor, restored_at=now_ts()))
            restored.append(oid)
            record(db, "confirm_restore", actor, "oven", oid)

    batch_view_result = None
    if affected_batch_id is not None:
        brow = get_batch_row(db, affected_batch_id)
        # 该批所有旧炉都已回到可用（或已正常退役）后，收尾为 restored。
        states = db.execute(select(ovens.c.state).join(
            retirement_items, retirement_items.c.oven_id == ovens.c.id
        ).where(retirement_items.c.batch_id == affected_batch_id)).scalars().all()
        if brow["status"] == R.FAILED and states and all(
            s in (R.AVAILABLE, R.RETIRED) for s in states
        ):
            db.execute(update(retirement_batches).where(
                retirement_batches.c.id == affected_batch_id).values(
                status=R.RESTORED, restored_at=now_ts()))
            record(db, "batch_restored", actor, "release_batch", affected_batch_id)
        batch_view_result = get_batch(db, affected_batch_id)

    line_id = None
    if oven_ids:
        line_id = db.execute(select(ovens.c.line_id).where(ovens.c.id == oven_ids[0])).scalar_one()
    elif affected_batch_id is not None:
        line_id = get_batch_row(db, affected_batch_id)["line_id"]
    line = line_view(db, db.execute(select(production_lines).where(
        production_lines.c.id == line_id)).mappings().first()) if line_id else None
    return {"restored_oven_ids": restored, "restored_count": len(restored),
            "batch": batch_view_result, "line": line, "idempotent": len(restored) == 0}
