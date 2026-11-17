import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, FeePool, AllocationSheet
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum
from src.services import fees as fee_service
from src.schemas.fees import PoolInput, SheetInput, SheetLineInput
def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        seed_fee_pools(db, d)
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,FeePool,AllocationSheet]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
def seed_fee_pools(db, dataset):
    """Create a September fee pool and a trialed example sheet once.

    The demo deliberately leaves the sheet trialed rather than confirmed so a
    reviewer can walk through confirm/revoke themselves.
    """
    seed_admin = db.scalar(select(User).where(User.role=='admin'))
    if not seed_admin:
        return
    month = '2026-09'
    shared = db.scalar(select(FeePool).where(FeePool.month==month, FeePool.name=='共享运营费池 · 9月'))
    if not shared:
        shared = fee_service.create_pool(db, PoolInput(name='共享运营费池 · 9月', month=month, dataset_id=dataset.id, basis='revenue', amount=5000.0), seed_admin)
        db.add(Audit(actor='seed', action='fee_pool.create', detail={'id': shared.id, 'month': month}, at=time.time()))
    visits = db.scalar(select(FeePool).where(FeePool.month==month, FeePool.name=='访问保障费池 · 9月'))
    if not visits:
        visits = fee_service.create_pool(db, PoolInput(name='访问保障费池 · 9月', month=month, dataset_id=dataset.id, basis='visits', amount=1200.0), seed_admin)
        db.add(Audit(actor='seed', action='fee_pool.create', detail={'id': visits.id, 'month': month}, at=time.time()))
    if not db.scalar(select(AllocationSheet).where(AllocationSheet.name=='9月共享运营费首次分摊')):
        sheet = fee_service.create_sheet(db, SheetInput(name='9月共享运营费首次分摊', note='演示：按 9 月渠道净收入与访问占比分摊，试算后由负责人确认。', lines=[SheetLineInput(pool_id=shared.id, amount=1800.0), SheetLineInput(pool_id=visits.id, amount=400.0)]), seed_admin)
        fee_service.trial(db, sheet.id, seed_admin)
        db.add(Audit(actor='seed', action='fee_sheet.trial', detail={'id': sheet.id}, at=time.time()))
if __name__=='__main__':seed()
