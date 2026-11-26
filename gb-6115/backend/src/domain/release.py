"""产线烘箱余量与退役批次放行规则。

口径（与验收需求逐条对应）：

- 每条产线任何时候都必须保留至少 ``FLOOR_USABLE`` 台可用烘箱；
- 已放行的批次，其旧炉立即移出可用数（交卷前也不算可用）；
- 替换炉验收后才计入可用数，验收幂等、重试不重复计数；
- 交卷失败后，旧炉仍不计入可用，直到设备组确认恢复可用；
- 判定/放行在产线行锁内进行，同一产线的并发申请串行化，合起来会
  突破保留下限的第二批会被判为不放行；不同产线互不阻塞。

本模块只放不依赖数据库的纯计算，方便单测与其它领域代码复用。
"""

FLOOR_USABLE = 1

# ---- 烘箱（炉）状态 -------------------------------------------------------
AVAILABLE = "available"                 # 可用（在役旧炉 / 已验收替换炉 / 已确认恢复旧炉）
PENDING_ACCEPTANCE = "pending_acceptance"  # 替换炉已到场但未验收，不计入可用
RELEASED = "released"                   # 所属批次已放行、尚未交卷：不计入可用
RETIRED = "retired"                     # 已交卷退役：不计入可用
DELIVERY_FAILED = "delivery_failed"     # 交卷失败、待设备组确认恢复：仍不计入可用

OVEN_STATES = (
    AVAILABLE, PENDING_ACCEPTANCE, RELEASED, RETIRED, DELIVERY_FAILED,
)

COUNTED_OVEN_STATES = (AVAILABLE,)

OVEN_STATE_LABELS = {
    AVAILABLE: "可用",
    PENDING_ACCEPTANCE: "待验收",
    RELEASED: "已放行·待交卷",
    RETIRED: "已交卷退役",
    DELIVERY_FAILED: "交卷失败·待恢复",
}

# ---- 退役批次状态 ---------------------------------------------------------
PENDING = "pending"        # 待判定 / 待办（shortage 为最近一次判定的缺口）
RELEASED_BATCH = "released"  # 已放行，待交卷
DELIVERED = "delivered"    # 交卷成功
FAILED = "failed"          # 交卷失败，等待恢复确认
RESTORED = "restored"      # 设备组已确认旧炉恢复可用，批次收尾

BATCH_STATES = (PENDING, RELEASED_BATCH, DELIVERED, FAILED, RESTORED)

# 仍需要现场处理的批次（页面“待办批次”口径）
TODO_BATCH_STATES = (PENDING, RELEASED_BATCH, FAILED)

BATCH_STATE_LABELS = {
    PENDING: "待判定",
    RELEASED_BATCH: "已放行",
    DELIVERED: "已交卷",
    FAILED: "交卷失败",
    RESTORED: "已恢复可用",
}

# 放行判定结论
ALLOWED = "allowed"
BLOCKED = "blocked"

VERDICT_LABELS = {
    ALLOWED: "可放行",
    BLOCKED: "暂不放行",
}


def shortage_for(usable_count, retire_count, floor=FLOOR_USABLE):
    """放行 ``retire_count`` 台后仍要保留 ``floor`` 台时，还缺几台可用炉。

    返回 0 表示余量足够；>0 即“还缺 N 台”。
    """
    usable = max(0, int(usable_count or 0))
    retire = max(0, int(retire_count or 0))
    return max(0, retire + floor - usable)


def remaining_after(usable_count, retire_count):
    return max(0, int(usable_count or 0) - max(0, int(retire_count or 0)))


def verdict_for(usable_count, retire_count, floor=FLOOR_USABLE):
    """返回 (结论, 缺口, 放行后剩余, 说明)。"""
    usable = max(0, int(usable_count or 0))
    retire = max(0, int(retire_count or 0))
    shortage = shortage_for(usable, retire, floor)
    remaining = int(usable) - retire
    if shortage == 0:
        text = f"余量 {usable} 台，退 {retire} 台后剩 {max(remaining, 0)} 台，满足保留 {floor} 台，可放行。"
        return ALLOWED, 0, max(remaining, 0), text
    text = (
        f"当前可用 {usable} 台，申请退 {retire} 台；为保留 {floor} 台可用烘箱，"
        f"还缺 {shortage} 台，补足并验收后重新判定。"
    )
    return BLOCKED, shortage, max(remaining, 0), text


def normalize_code(value):
    if value is None:
        return None
    code = str(value).strip().upper()
    return code or None
