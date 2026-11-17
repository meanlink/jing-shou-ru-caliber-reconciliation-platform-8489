from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.config.database import get_db
from src.middlewares.auth import current_user, can_write, admin_only
from src.models import AllocationSheet, FeePool
from src.schemas.fees import PoolInput, PoolUpdate, SheetInput
from src.services import fees
from src.services.audit import record
from src.utils.serialization import sheet_summary

router = APIRouter(prefix='/fee-pools')


@router.get('/meta')
def meta():
    return {
        "basis": [{"value": "revenue", "label": "按选定源账的净收入占比"}, {"value": "visits", "label": "按选定源账的访问占比"}],
        "statuses": [{"value": key, "label": label} for key, label in fees.STATUS_LABELS.items()],
        "rules": [
            "分摊以分为最小单位，最大余数法分配尾差，同余时按渠道名排序固定归属。",
            "多池合单确认在同一事务内完成：任一池额度不足则不占用任何额度。",
            "费用池改额、换账或换分摊基数后，旧试算失效，需重新试算才能确认。",
            "确认固定分摊额、源账指纹与权重；之后池子变化不影响已确认金额。",
            "确认与撤销按分摊单幂等：中断后重试沿用同一单，不重复扣减或返还。",
        ],
    }


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    pools = list(db.scalars(select(FeePool).order_by(FeePool.month.desc(), FeePool.id.desc())))
    return [fees.pool_dict(db, pool) for pool in pools]


@router.post('', status_code=201)
def create(data: PoolInput, db=Depends(get_db), u=Depends(admin_only)):
    pool = fees.create_pool(db, data, u)
    record(db, u.email, 'fee_pool.create', {'id': pool.id, 'month': pool.month, 'amount_cents': pool.amount_cents})
    db.commit()
    return fees.pool_dict(db, pool)


@router.patch('/{id}')
def update(id: int, data: PoolUpdate, db=Depends(get_db), u=Depends(admin_only)):
    pool = fees.update_pool(db, id, data, u)
    record(db, u.email, 'fee_pool.update', {'id': pool.id, 'amount_cents': pool.amount_cents, 'dataset_id': pool.dataset_id, 'basis': pool.basis, 'version': pool.version})
    db.commit()
    return fees.pool_dict(db, pool)


@router.get('/{id}/balance')
def balance(id: int, db=Depends(get_db), u=Depends(current_user)):
    return fees.pool_balance(db, id)


@router.get('/sheets')
def sheet_listing(db=Depends(get_db), u=Depends(current_user)):
    sheets = list(db.scalars(select(AllocationSheet).order_by(AllocationSheet.id.desc()).limit(100)))
    return [sheet_summary(sheet) for sheet in sheets]


@router.post('/sheets', status_code=201)
def create_sheet(data: SheetInput, db=Depends(get_db), u=Depends(can_write)):
    sheet = fees.create_sheet(db, data, u)
    record(db, u.email, 'fee_sheet.create', {'id': sheet.id, 'lines': len(data.lines)})
    db.commit()
    return fees.sheet_dict(db, sheet)


@router.get('/sheets/{id}')
def sheet_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    sheet = db.get(AllocationSheet, id)
    if not sheet:
        raise HTTPException(404, '分摊单不存在')
    return fees.sheet_dict(db, sheet)


@router.post('/sheets/{id}/trial')
def sheet_trial(id: int, db=Depends(get_db), u=Depends(can_write)):
    sheet = fees.trial(db, id, u)
    record(db, u.email, 'fee_sheet.trial', {'id': sheet.id, 'total_fee_cents': sheet.total_fee_cents})
    db.commit()
    return fees.sheet_dict(db, sheet)


@router.post('/sheets/{id}/confirm')
def sheet_confirm(id: int, db=Depends(get_db), u=Depends(admin_only)):
    sheet = fees.confirm(db, id, u)
    record(db, u.email, 'fee_sheet.confirm', {'id': sheet.id, 'total_fee_cents': sheet.total_fee_cents, 'status': sheet.status})
    db.commit()
    return fees.sheet_dict(db, sheet)


@router.post('/sheets/{id}/revoke')
def sheet_revoke(id: int, db=Depends(get_db), u=Depends(admin_only)):
    sheet = fees.revoke(db, id, u)
    record(db, u.email, 'fee_sheet.revoke', {'id': sheet.id, 'total_fee_cents': sheet.total_fee_cents, 'status': sheet.status})
    db.commit()
    return fees.sheet_dict(db, sheet)
