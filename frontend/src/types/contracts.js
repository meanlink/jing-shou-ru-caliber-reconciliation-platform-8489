/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,name:string,month:string,dataset_id:number,basis:'revenue'|'visits',amount_cents:number,allocated_cents:number,remaining_cents:number,version:number}} FeePool */
/** @typedef {{id:number,name:string,status:'draft'|'trialed'|'confirmed'|'revoked',stale:boolean,zero_weight:boolean,total_fee_cents:number,lines:Array<Object>}} AllocationSheet */
export {};
