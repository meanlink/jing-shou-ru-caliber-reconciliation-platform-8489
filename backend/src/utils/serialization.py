import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}

def sheet_summary(s):
    return {'id': s.id, 'name': s.name, 'status': s.status, 'operation': s.operation,
            'total_fee_cents': s.total_fee_cents, 'total_fee': round(s.total_fee_cents / 100, 2),
            'created_at': s.created_at, 'updated_at': s.updated_at,
            'trialed_at': s.trialed_at, 'confirmed_at': s.confirmed_at, 'reversed_at': s.reversed_at}
