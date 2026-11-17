import {api} from '../api/client.js';
export const feePools={
 list:()=>api('/fee-pools'),
 meta:()=>api('/fee-pools/meta'),
 create:data=>api('/fee-pools',{method:'POST',body:JSON.stringify(data)}),
 update:(id,data)=>api('/fee-pools/'+id,{method:'PATCH',body:JSON.stringify(data)}),
 balance:id=>api('/fee-pools/'+id+'/balance'),
 sheets:()=>api('/fee-pools/sheets'),
 sheet:id=>api('/fee-pools/sheets/'+id),
 createSheet:data=>api('/fee-pools/sheets',{method:'POST',body:JSON.stringify(data)}),
 trial:id=>api('/fee-pools/sheets/'+id+'/trial',{method:'POST'}),
 confirm:id=>api('/fee-pools/sheets/'+id+'/confirm',{method:'POST'}),
 revoke:id=>api('/fee-pools/sheets/'+id+'/revoke',{method:'POST'})
};
