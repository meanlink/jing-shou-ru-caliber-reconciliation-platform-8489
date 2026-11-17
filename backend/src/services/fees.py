"""Monthly shared operating-fee pools and cent-exact allocation sheets.

Financial rules encoded here:

* a pool is funded in integer cents; ``used_cents`` only ever contains
  confirmed allocations and ``used + remaining == amount`` is an invariant;
* allocation weights come from the pool's selected source ledger for its
  month, either unified net revenue (gross-refunds-discount) or visits;
* every cent of a line is distributed with the largest-remainder method and
  deterministic dimension-name tie-breaks, so the rounding residue always
  lands on the same dimension for the same input;
* trial snapshots freeze the pool version, source ledger and weights; a later
  amount/ledger change invalidates the trial and confirmation is refused;
* confirmation takes quota from every pool in one database transaction with
  row locks. If any pool would exceed its capacity, no pool is charged;
* confirmation and revocation are idempotent; the append-only ledger has a
  unique (line, kind) key so a retried request can never move a line twice.
"""
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from src.models import Dataset, FeePool, FeePoolLedger, AllocationDetail, AllocationLine, AllocationSheet

BASIS_LABELS = {"revenue": "收入", "visits": "访问"}
STATUS_LABELS = {"draft": "待试算", "trialed": "已试算", "confirmed": "已确认", "revoked": "已撤销"}
MAX_CENTS = 100_000_000_000  # 10亿 CNY
_CENT = Decimal("0.01")


def _fail(status, message):
    raise HTTPException(status_code=status, detail=message)


def parse_month(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", text):
        _fail(422, "费用池月份必须为 YYYY-MM 格式")
    try:
        parsed = datetime.strptime(text + "-01", "%Y-%m-%d").date()
    except ValueError:
        _fail(422, "费用池月份不是有效日期")
    if not 2000 <= parsed.year <= 2100:
        _fail(422, "费用池月份需在 2000-01 至 2100-12 之间")
    return text


def parse_amount(value, label="额度"):
    if isinstance(value, bool):
        _fail(422, f"{label}必须为有限金额")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _fail(422, f"{label}必须为有限金额")
    if not amount.is_finite():
        _fail(422, f"{label}必须为有限金额")
    quantized = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    if amount != quantized:
        _fail(422, f"{label}最多保留两位小数，不能出现小于一分的金额")
    cents = int(quantized * 100)
    if cents <= 0:
        _fail(422, f"{label}必须大于 0")
    if cents > MAX_CENTS:
        _fail(422, f"{label}不能超过 10 亿元")
    return cents


def _yuan(cents):
    return round(cents / 100, 2)


def _decimal(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field}不是数值")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field}不是数值") from None
    if not number.is_finite():
        raise ValueError(f"{field}不是有限数值")
    return number


def _to_cents(number):
    return int(number.quantize(_CENT, rounding=ROUND_HALF_UP) * 100)


def month_weights(dataset, month, basis):
    """Return sorted per-channel weights, plus revenue/visit snapshots."""
    if basis not in BASIS_LABELS:
        _fail(422, "分摊基数只能是收入或访问")
    channels = {}
    for index, record in enumerate(dataset.records or []):
        try:
            day = date.fromisoformat(str(record["day"]))
        except (KeyError, TypeError, ValueError):
            _fail(422, f"源账第 {index + 1} 行日期无效，请先修复数据批次")
        if day.strftime("%Y-%m") != month:
            continue
        try:
            channel = str(record["channel"]).strip()
            currency = str(record["currency"])
            gross = _decimal(record["gross"], "毛额")
            refunds = _decimal(record["refunds"], "退款")
            discount = _decimal(record["discount"], "折扣")
            visits = _decimal(record["visits"], "访问数")
        except (KeyError, ValueError) as error:
            _fail(422, f"源账第 {index + 1} 行缺少或包含无效字段：{error}")
        if not channel:
            _fail(422, f"源账第 {index + 1} 行渠道为空")
        if currency != "CNY":
            _fail(422, "分摊源账必须统一为 CNY，混合币种请先换算")
        for label, value in (("毛额", gross), ("退款", refunds), ("折扣", discount), ("访问数", visits)):
            if not (0 <= value <= Decimal("1000000000")):
                _fail(422, f"源账第 {index + 1} 行{label}必须在 0 至 10 亿之间")
        if visits != visits.to_integral_value():
            _fail(422, f"源账第 {index + 1} 行访问数必须为整数")
        revenue_cents = _to_cents(gross - refunds - discount)
        if revenue_cents < 0:
            _fail(422, f"源账第 {index + 1} 行收入为负，无法作为分摊权重")
        item = channels.setdefault(channel, {"revenue_cents": 0, "visits": 0})
        item["revenue_cents"] += revenue_cents
        item["visits"] += int(visits)
    if not channels:
        _fail(409, f"所选源账在 {month} 没有日汇总记录，无法分摊")
    weights = []
    for channel in sorted(channels):
        item = channels[channel]
        weight = item["revenue_cents"] if basis == "revenue" else item["visits"]
        weights.append(
            {
                "dimension": channel,
                "weight": weight,
                "weight_kind": basis,
                "revenue_cents": item["revenue_cents"],
                "visits": item["visits"],
            }
        )
    return weights


def split_cents(amount_cents, rows):
    """Largest-remainder split, stable order by dimension name.

    Ties in the fractional remainder go to the lexicographically smaller
    dimension, not to input order, so the run is reproducible.
    """
    total = sum(row["weight"] for row in rows)
    if total <= 0:
        _fail(422, "分摊权重合计为 0，无法分摊任何费用")
    if amount_cents <= 0:
        _fail(422, "分摊额必须大于 0")
    shares, remainders = [], []
    for row in rows:
        share, remainder = divmod(amount_cents * row["weight"], total)
        shares.append(share)
        remainders.append(remainder)
    leftover = amount_cents - sum(shares)
    order = sorted(range(len(rows)), key=lambda i: (-remainders[i], rows[i]["dimension"]))
    for i in order[:leftover]:
        shares[i] += 1
    return shares


def _pool_remaining(db, pool):
    # ``used_cents`` is a maintained counter; pool_dict cross-checks it against
    # the derived sum of confirmed sheet lines so a balance can always be audited.
    return pool.amount_cents - pool.used_cents


def _dataset_for(db, pool):
    dataset = db.get(Dataset, pool.dataset_id)
    if not dataset:
        _fail(409, "费用池绑定的源账批次不存在，请重新选择账源")
    return dataset


def _line_stale(db, line, pool):
    if pool.version != line.pool_version:
        return True
    if pool.dataset_id != line.dataset_id or pool.basis != line.basis:
        return True
    if pool.amount_cents != line.pool_amount_cents_snapshot:
        return True
    dataset = db.get(Dataset, pool.dataset_id)
    return not dataset or dataset.checksum != line.dataset_checksum


# ---------------------------------------------------------------------------
# Pools


def create_pool(db, data, user):
    name = str(data.name).strip()
    if len(name) < 2:
        _fail(422, "费用池名称去除空格后至少需要两个字符")
    month = parse_month(data.month)
    basis = str(data.basis)
    if basis not in BASIS_LABELS:
        _fail(422, "分摊基数只能是收入或访问")
    dataset = db.get(Dataset, data.dataset_id)
    if not dataset:
        _fail(404, "源账数据批次不存在")
    amount_cents = parse_amount(data.amount, "费用池月度额度")
    if db.scalar(select(FeePool).where(FeePool.month == month, FeePool.name == name)):
        _fail(409, "该月份已经存在同名费用池，请修改名称")
    now = time.time()
    pool = FeePool(
        name=name, month=month, dataset_id=dataset.id, basis=basis,
        amount_cents=amount_cents, used_cents=0, version=1,
        created_by=user.id, created_at=now, updated_at=now,
    )
    db.add(pool)
    db.flush()
    return pool


def update_pool(db, pool_id, data, user):
    pool = db.scalar(select(FeePool).where(FeePool.id == pool_id).with_for_update())
    if not pool:
        _fail(404, "费用池不存在")
    changed_financials = False
    if data.name is not None:
        name = str(data.name).strip()
        if len(name) < 2:
            _fail(422, "费用池名称去除空格后至少需要两个字符")
        if name != pool.name:
            if db.scalar(select(FeePool).where(FeePool.month == pool.month, FeePool.name == name, FeePool.id != pool.id)):
                _fail(409, "该月份已经存在同名费用池，请修改名称")
            pool.name = name
    if data.amount is not None:
        amount_cents = parse_amount(data.amount, "费用池月度额度")
        if amount_cents < pool.used_cents:
            _fail(409, "调减后额度将低于已确认分摊合计，请先撤销相关分摊单")
        if amount_cents != pool.amount_cents:
            pool.amount_cents = amount_cents
            changed_financials = True
    if data.dataset_id is not None and data.dataset_id != pool.dataset_id:
        dataset = db.get(Dataset, data.dataset_id)
        if not dataset:
            _fail(404, "源账数据批次不存在")
        pool.dataset_id = dataset.id
        changed_financials = True
    if data.basis is not None and str(data.basis) != pool.basis:
        if str(data.basis) not in BASIS_LABELS:
            _fail(422, "分摊基数只能是收入或访问")
        pool.basis = str(data.basis)
        changed_financials = True
    if changed_financials:
        # Any amount/ledger/basis change makes prior trials stale.
        pool.version = pool.version + 1
    pool.updated_at = time.time()
    db.flush()
    return pool


# ---------------------------------------------------------------------------
# Sheets


def create_sheet(db, data, user):
    name = str(data.name).strip()
    if len(name) < 2:
        _fail(422, "分摊单名称去除空格后至少需要两个字符")
    lines = list(data.lines or [])
    if not 1 <= len(lines) <= 20:
        _fail(422, "分摊单至少选择一个费用池，最多 20 个")
    ids = [line.pool_id for line in lines]
    if len(ids) != len(set(ids)):
        _fail(422, "同一费用池不能在一张分摊单中重复出现")
    now = time.time()
    sheet = AllocationSheet(
        name=name, note=str(data.note or '').strip()[:500], status='draft',
        created_by=user.id, created_at=now, updated_at=now,
    )
    db.add(sheet)
    db.flush()
    for order, item in enumerate(lines):
        pool = db.get(FeePool, item.pool_id)
        if not pool:
            _fail(404, f"费用池 {item.pool_id} 不存在")
        requested = None if item.amount is None else parse_amount(item.amount, f"「{pool.name}」分摊额")
        line = AllocationLine(
            sheet_id=sheet.id, pool_id=pool.id, requested_amount_cents=requested,
            amount_cents=0, pool_version=0, pool_amount_cents_snapshot=0,
            dataset_id=pool.dataset_id, dataset_checksum='', basis=pool.basis,
            weight_total=0, zero_weight=False, reserved=False, returned=False,
            line_order=order,
        )
        db.add(line)
        db.flush()
    return sheet


def _load_lines(db, sheet_id):
    return list(db.scalars(select(AllocationLine).where(AllocationLine.sheet_id == sheet_id).order_by(AllocationLine.line_order, AllocationLine.id)))


def _lock_sheet(db, sheet_id):
    """Serialize confirm/revoke on the sheet row so a retried request sees the
    outcome of the previous attempt instead of charging or returning twice."""
    return db.scalar(select(AllocationSheet).where(AllocationSheet.id == sheet_id).with_for_update())


def trial(db, sheet_id, user):
    sheet = _lock_sheet(db, sheet_id)
    if not sheet:
        _fail(404, "分摊单不存在")
    if sheet.status in ("confirmed", "revoked"):
        _fail(409, "该分摊单已完成或已撤销，不能再次试算；请新建分摊单")
    lines = _load_lines(db, sheet.id)
    if not lines:
        _fail(409, "分摊单没有费用池行，请重新创建")
    planned = []
    for line in lines:
        pool = db.get(FeePool, line.pool_id)
        if not pool:
            _fail(409, "分摊单引用的费用池已不存在")
        remaining = _pool_remaining(db, pool)
        requested = remaining if line.requested_amount_cents is None else line.requested_amount_cents
        if requested <= 0:
            _fail(422, f"「{pool.name}」可分摊额度为 0，请先为费用池充值额度")
        if requested > remaining:
            _fail(409, f"「{pool.name}」当前剩余 {_yuan(remaining):,.2f} 元，分摊额 {_yuan(requested):,.2f} 元超出余额")
        dataset = _dataset_for(db, pool)
        rows = month_weights(dataset, pool.month, pool.basis)
        total = sum(row["weight"] for row in rows)
        if total <= 0:
            _fail(422, f"「{pool.name}」在 {pool.month} 的{BASIS_LABELS[pool.basis]}总权重为 0，无法分摊")
        zero_weight = any(row["weight"] <= 0 for row in rows)
        shares = split_cents(requested, rows)
        planned.append((line, pool, dataset, rows, requested, total, zero_weight, shares))
    for line, pool, dataset, rows, requested, total, zero_weight, shares in planned:
        db.execute(delete(AllocationDetail).where(AllocationDetail.line_id == line.id))
        line.amount_cents = requested
        line.pool_version = pool.version
        line.pool_amount_cents_snapshot = pool.amount_cents
        line.dataset_id = dataset.id
        line.dataset_checksum = dataset.checksum
        line.basis = pool.basis
        line.weight_total = total
        line.zero_weight = zero_weight
        line.reserved = False
        line.returned = False
        for row, share in zip(rows, shares):
            db.add(AllocationDetail(
                line_id=line.id, dimension=row["dimension"],
                weight_units=row["weight"], weight_kind=row["weight_kind"],
                fee_cents=share, revenue_cents=row["revenue_cents"], visits=row["visits"],
            ))
    sheet.total_fee_cents = sum(line.amount_cents for line in lines)
    sheet.status = "trialed"
    sheet.operation = "trial"
    sheet.trialed_at = time.time()
    sheet.updated_at = time.time()
    db.flush()
    return sheet


def _check_trial_fresh(db, line, pool):
    if _line_stale(db, line, pool):
        _fail(409, "费用池改额或换账后旧试算已失效，请重新试算后再确认")
    if pool.used_cents + line.amount_cents > pool.amount_cents:
        _fail(409, f"「{pool.name}」剩余额度不足：本次分摊 {_yuan(line.amount_cents):,.2f} 元，"
                   f"剩余 {_yuan(_pool_remaining(db, pool)):,.2f} 元。多池合单共享额度，任一池超额则本单不占用任何额度")
    if line.zero_weight:
        _fail(422, f"「{pool.name}」存在零权重渠道，按规则拒绝确认；请调整源账或改选分摊基数后重新试算")


def _sum_detail_fees(db, line):
    return sum(db.scalars(select(AllocationDetail.fee_cents).where(AllocationDetail.line_id == line.id)))


def confirm(db, sheet_id, user):
    sheet = _lock_sheet(db, sheet_id)
    if not sheet:
        _fail(404, "分摊单不存在")
    if sheet.status == "confirmed":
        # A retry after an interrupted request reuses the same sheet and the
        # already-committed reservation; nothing is charged again.
        return sheet
    if sheet.status == "revoked":
        _fail(409, "该分摊单已撤销，不能再次确认；如需恢复请新建分摊单并重新试算")
    if sheet.status != "trialed":
        _fail(409, "分摊单尚未试算，请先试算并核对明细")
    # Claim the sheet with a conditional status update first: only one
    # concurrent request can move trialed -> confirmed. The loser then returns
    # the winner's committed result instead of re-validating (whose intermediate
    # reads may race) or touching any pool.
    now = time.time()
    claimed = db.execute(
        update(AllocationSheet)
        .where(AllocationSheet.id == sheet.id, AllocationSheet.status == "trialed")
        .values(status="confirmed", operation="confirm", confirmed_at=now,
                confirmed_by=user.id, updated_at=now)
    )
    if claimed.rowcount != 1:
        db.refresh(sheet)
        if sheet.status == "confirmed":
            return sheet
        _fail(409, "分摊单正在被另一请求处理，请刷新后重试")
    db.refresh(sheet)
    lines = _load_lines(db, sheet.id)
    if not lines:
        _fail(409, "分摊单没有费用池行")
    pool_ids = sorted({line.pool_id for line in lines})
    # Lock every pool in a deterministic order. The whole confirmation is one
    # transaction: any failure below rolls back without charging any pool.
    pools = list(db.scalars(
        select(FeePool).where(FeePool.id.in_(pool_ids)).order_by(FeePool.id).with_for_update()
        .execution_options(populate_existing=True)
    ))
    pool_map = {pool.id: pool for pool in pools}
    if len(pool_map) != len(pool_ids):
        _fail(409, "分摊单引用的费用池已不存在")
    for line in lines:
        pool = pool_map[line.pool_id]
        if line.amount_cents <= 0:
            _fail(409, "分摊额必须大于 0，请重新试算")
        if _sum_detail_fees(db, line) != line.amount_cents:
            _fail(409, "试算明细合计与分摊额不一致，请重新试算")
        _check_trial_fresh(db, line, pool)
    # Each pool is charged with a conditional update so a concurrent sheet
    # racing for the same shared quota either fits entirely or fails the whole
    # transaction; nothing is partially reserved.
    for line in lines:
        pool = pool_map[line.pool_id]
        charged = db.execute(
            update(FeePool)
            .where(FeePool.id == pool.id, FeePool.used_cents + line.amount_cents <= FeePool.amount_cents)
            .values(used_cents=FeePool.used_cents + line.amount_cents, updated_at=now)
        )
        if charged.rowcount != 1:
            _fail(409, f"「{pool.name}」剩余额度不足：本次分摊 {_yuan(line.amount_cents):,.2f} 元，"
                       f"剩余 {_yuan(_pool_remaining(db, pool)):,.2f} 元。多池合单共享额度，任一池超额则本单不占用任何额度")
        db.refresh(pool)
        line.reserved = True
        line.returned = False
        db.add(FeePoolLedger(
            pool_id=pool.id, sheet_id=sheet.id, line_id=line.id, kind="reserve",
            amount_cents=line.amount_cents, balance_after=_pool_remaining(db, pool),
            actor=user.email, created_at=now,
        ))
    db.flush()
    # Belt-and-braces: encode the stated invariant for every pool in the sheet
    # (allocated across all sheets + remaining == original amount). A violation
    # aborts the whole transaction, so no pool keeps a partial reservation.
    for line in lines:
        pool = pool_map[line.pool_id]
        allocated = allocated_cents(db, pool.id)
        if allocated != pool.used_cents or allocated + (pool.amount_cents - pool.used_cents) != pool.amount_cents:
            _fail(409, f"「{pool.name}」余额校验失败：已分摊与剩余之和必须等于原额度，本次确认已回滚")
    if sum(line.amount_cents for line in lines) != sheet.total_fee_cents:
        _fail(409, "分摊单合计与各池分摊额不一致，本次确认已回滚")
    db.flush()
    return sheet


def revoke(db, sheet_id, user):
    sheet = _lock_sheet(db, sheet_id)
    if not sheet:
        _fail(404, "分摊单不存在")
    if sheet.status == "revoked":
        # Retry after an interrupted revocation returns the same sheet; the
        # already returned quota is not returned twice.
        return sheet
    if sheet.status != "confirmed":
        _fail(409, "只有已确认的分摊单可以整单撤销")
    now = time.time()
    # Claim confirmed -> revoked before touching lines or pools, for the same
    # reason as confirmation.
    claimed = db.execute(
        update(AllocationSheet)
        .where(AllocationSheet.id == sheet.id, AllocationSheet.status == "confirmed")
        .values(status="revoked", operation="revoke", reversed_at=now,
                reversed_by=user.id, updated_at=now)
    )
    if claimed.rowcount != 1:
        db.refresh(sheet)
        if sheet.status == "revoked":
            return sheet
        _fail(409, "分摊单正在被另一请求处理，请刷新后重试")
    db.refresh(sheet)
    lines = _load_lines(db, sheet.id)
    pool_ids = sorted({line.pool_id for line in lines})
    pools = list(db.scalars(
        select(FeePool).where(FeePool.id.in_(pool_ids)).order_by(FeePool.id).with_for_update()
        .execution_options(populate_existing=True)
    ))
    pool_map = {pool.id: pool for pool in pools}
    if len(pool_map) != len(pool_ids):
        _fail(409, "分摊单引用的费用池已不存在，无法返还额度")
    for line in lines:
        if not line.reserved or line.returned:
            _fail(409, "分摊行状态与费用池不一致，请联系管理员核对余额")
    for line in lines:
        pool = pool_map[line.pool_id]
        returned = db.execute(
            update(FeePool)
            .where(FeePool.id == pool.id, FeePool.used_cents >= line.amount_cents)
            .values(used_cents=FeePool.used_cents - line.amount_cents, updated_at=now)
        )
        if returned.rowcount != 1:
            _fail(409, "费用池已用额度小于本单分摊额，余额记录不一致，请联系管理员")
        db.refresh(pool)
        line.returned = True
        db.add(FeePoolLedger(
            pool_id=pool.id, sheet_id=sheet.id, line_id=line.id, kind="return",
            amount_cents=line.amount_cents, balance_after=_pool_remaining(db, pool),
            actor=user.email, created_at=now,
        ))
    db.flush()
    for line in lines:
        pool = pool_map[line.pool_id]
        allocated = allocated_cents(db, pool.id)
        if allocated != pool.used_cents:
            _fail(409, f"「{pool.name}」余额校验失败：返还后已分摊与余额不一致，本次撤销已回滚")
    db.flush()
    return sheet


# ---------------------------------------------------------------------------
# Read models


def line_dict(db, line, detail_rows=None, sheet_status=None):
    pool = db.get(FeePool, line.pool_id)
    # Once confirmed the frozen snapshot is authoritative; later pool edits do
    # not invalidate a booked amount, they only block new trials.
    stale = bool(pool and sheet_status == "trialed" and _line_stale(db, line, pool))
    details = detail_rows if detail_rows is not None else list(db.scalars(
        select(AllocationDetail).where(AllocationDetail.line_id == line.id).order_by(AllocationDetail.id)
    ))
    detail_payload = []
    revenue = visits = fee = 0
    for row in details:
        net_cents = row.revenue_cents - row.fee_cents
        revenue += row.revenue_cents
        visits += row.visits
        fee += row.fee_cents
        detail_payload.append({
            "dimension": row.dimension, "weight": row.weight_units, "weight_kind": row.weight_kind,
            "weight_label": BASIS_LABELS.get(row.weight_kind, row.weight_kind),
            "weight_display": _yuan(row.weight_units) if row.weight_kind == "revenue" else row.weight_units,
            "fee_cents": row.fee_cents, "fee": _yuan(row.fee_cents),
            "revenue_cents": row.revenue_cents, "revenue": _yuan(row.revenue_cents),
            "visits": row.visits, "net_cents": net_cents, "net_revenue": _yuan(net_cents),
            "net_per_visit": round(net_cents / 100 / row.visits, 4) if row.visits else None,
        })
    net_cents = revenue - fee
    return {
        "id": line.id, "pool_id": line.pool_id,
        "pool_name": pool.name if pool else "（已删除）",
        "month": pool.month if pool else "", "basis": line.basis,
        "basis_label": BASIS_LABELS.get(line.basis, line.basis),
        "requested_amount": None if line.requested_amount_cents is None else _yuan(line.requested_amount_cents),
        "amount_cents": line.amount_cents, "amount": _yuan(line.amount_cents),
        "pool_version": line.pool_version,
        "pool_amount_snapshot": _yuan(line.pool_amount_cents_snapshot),
        "dataset_id": line.dataset_id, "dataset_checksum": line.dataset_checksum,
        "weight_total": line.weight_total, "zero_weight": line.zero_weight,
        "reserved": line.reserved, "returned": line.returned, "stale": stale,
        "details": detail_payload,
        "economics": {
            "revenue": _yuan(revenue), "visits": visits, "fee": _yuan(fee),
            "net_revenue": _yuan(net_cents),
            "net_per_visit": round(net_cents / 100 / visits, 4) if visits else None,
        },
        "allocation_check": {"detail_sum_cents": fee, "amount_cents": line.amount_cents, "balanced": fee == line.amount_cents},
    }


def sheet_dict(db, sheet):
    lines = _load_lines(db, sheet.id)
    payload = [line_dict(db, line, sheet_status=sheet.status) for line in lines]
    stale = sheet.status == "trialed" and any(line["stale"] for line in payload)
    zero_weight = any(line["zero_weight"] for line in payload)
    return {
        "id": sheet.id, "name": sheet.name, "note": sheet.note, "status": sheet.status,
        "status_label": STATUS_LABELS.get(sheet.status, sheet.status),
        "operation": sheet.operation,
        "total_fee_cents": sheet.total_fee_cents, "total_fee": _yuan(sheet.total_fee_cents),
        "created_at": sheet.created_at, "updated_at": sheet.updated_at,
        "trialed_at": sheet.trialed_at, "confirmed_at": sheet.confirmed_at, "reversed_at": sheet.reversed_at,
        "created_by": sheet.created_by, "confirmed_by": sheet.confirmed_by, "reversed_by": sheet.reversed_by,
        "stale": stale, "zero_weight": zero_weight,
        "lines": payload,
    }


def allocated_cents(db, pool_id):
    """Confirmed and not-yet-reversed allocations, derived from sheet lines."""
    total = db.scalar(
        select(func.coalesce(func.sum(AllocationLine.amount_cents), 0)).where(
            AllocationLine.pool_id == pool_id,
            AllocationLine.reserved.is_(True),
            AllocationLine.returned.is_(False),
        )
    )
    return int(total or 0)


def pool_dict(db, pool):
    allocated = allocated_cents(db, pool.id)
    remaining = pool.amount_cents - allocated
    return {
        "id": pool.id, "name": pool.name, "month": pool.month,
        "dataset_id": pool.dataset_id, "basis": pool.basis,
        "basis_label": BASIS_LABELS.get(pool.basis, pool.basis),
        "amount_cents": pool.amount_cents, "amount": _yuan(pool.amount_cents),
        "allocated_cents": allocated, "allocated": _yuan(allocated),
        "used_cents": pool.used_cents, "used": _yuan(pool.used_cents),
        "remaining_cents": remaining, "remaining": _yuan(remaining),
        "version": pool.version, "created_at": pool.created_at, "updated_at": pool.updated_at,
        "invariant": {
            "allocated_plus_remaining_cents": allocated + remaining,
            "amount_cents": pool.amount_cents,
            "balanced": allocated + remaining == pool.amount_cents and allocated == pool.used_cents,
        },
    }


def pool_balance(db, pool_id):
    pool = db.get(FeePool, pool_id)
    if not pool:
        _fail(404, "费用池不存在")
    ledger = list(db.scalars(select(FeePoolLedger).where(FeePoolLedger.pool_id == pool.id).order_by(FeePoolLedger.id)))
    sheets = []
    for line in db.scalars(select(AllocationLine).where(AllocationLine.pool_id == pool.id).order_by(AllocationLine.id)):
        sheet = db.get(AllocationSheet, line.sheet_id)
        if not sheet:
            continue
        sheets.append({
            "sheet_id": sheet.id, "sheet_name": sheet.name, "sheet_status": sheet.status,
            "sheet_status_label": STATUS_LABELS.get(sheet.status, sheet.status),
            "amount": _yuan(line.amount_cents), "amount_cents": line.amount_cents,
            "reserved": line.reserved, "returned": line.returned, "confirmed_at": sheet.confirmed_at,
        })
    return {
        "pool": pool_dict(db, pool),
        "ledger": [{
            "id": entry.id, "kind": entry.kind, "kind_label": "占用" if entry.kind == "reserve" else "返还",
            "amount": _yuan(entry.amount_cents), "amount_cents": entry.amount_cents,
            "balance_after": _yuan(entry.balance_after), "balance_after_cents": entry.balance_after,
            "sheet_id": entry.sheet_id, "line_id": entry.line_id, "actor": entry.actor, "created_at": entry.created_at,
        } for entry in ledger],
        "sheets": sheets,
    }
