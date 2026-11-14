"""Authenticated, capability-scoped management workflows shared by Web and Mini App."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter,Depends,Query
from fastapi.responses import Response
from pydantic import Field
from billing_api import Reason,Message
from product_admin import Product,finance_csv

class Batch(Message):
    user_ids:list[int]=Field(min_length=1,max_length=200)


def router(store,actor,guarded,admin_id):
    api=APIRouter(prefix='/api/product',tags=['management'])
    def service():return Product(store.db,admin_id)
    @api.get('/overview')
    async def overview(uid:int=Depends(actor)):
        return await guarded(service().overview,uid)
    @api.get('/users')
    async def users(uid:int=Depends(actor),q:str='',filter:Literal['all','expired','expiring','zero_wallet','pending','inactive','banned']='all',page:int=Query(1,ge=1,le=10000)):
        return await guarded(service().users,uid,q,filter,page)
    @api.get('/users/{user_id}')
    async def dossier(user_id:int,uid:int=Depends(actor),page:int=Query(1,ge=1,le=10000)):
        return await guarded(service().dossier,uid,user_id,page)
    @api.get('/orders')
    async def orders(uid:int=Depends(actor),status:Literal['pending','paid','rejected','refunded']|None=None,
                     start:datetime|None=None,end:datetime|None=None,plan_id:int|None=None,user_id:int|None=None,
                     method:Literal['manual','wallet','grant']|None=None,q:str='',page:int=Query(1,ge=1,le=10000)):
        return await guarded(service().orders,uid,status=status,start=start,end=end,plan_id=plan_id,user_id=user_id,method=method,q=q,page=page)
    @api.get('/finances')
    async def finances(uid:int=Depends(actor),start:datetime|None=None,end:datetime|None=None,
                       plan_id:int|None=None,user_id:int|None=None,method:Literal['manual','wallet','grant']|None=None,
                       format:Literal['json','csv']='json'):
        result=await guarded(service().finances,uid,start,end,plan_id,user_id,method)
        if format=='csv':return Response(finance_csv(result),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="sonar-finance.csv"','Cache-Control':'no-store'})
        return result
    @api.get('/plans/{ident}/usage')
    async def usage(ident:int,uid:int=Depends(actor)):
        return await guarded(service().plan_usage,uid,ident)
    @api.post('/plans/{ident}/archive')
    async def archive(ident:int,body:Reason,uid:int=Depends(actor)):
        await guarded(service().archive,uid,ident,body.reason);return {'ok':True}
    @api.post('/messages/batch')
    async def batch(body:Batch,uid:int=Depends(actor)):
        if any(i<1 for i in body.user_ids):
            from user_errors import UserError
            raise UserError('شناسه گیرنده معتبر نیست')
        return {'ids':await guarded(service().bulk_message,uid,body.user_ids,body.text,body.request_key,body.reason),'state':'queued'}
    @api.get('/search')
    async def search(q:str=Query(min_length=2,max_length=100),uid:int=Depends(actor)):
        def read():
            owner=store.workspace(uid);term='%'+q+'%';out=[]
            for kind,table,extra in [('server','servers','ip'),('config','tunnel_configs','id::text'),('service','pro_services','target')]:
                rows=store.db.execute(f'SELECT id,name FROM {table} WHERE owner_id=%s AND (name ILIKE %s OR {extra} ILIKE %s) ORDER BY id LIMIT 10',(owner,term,term),'all')
                out.extend(dict(kind=kind,id=r['id'],name=r['name']) for r in rows)
            caps=service().billing.permissions(uid)
            if 'users.read' in caps:
                out.extend(dict(kind='user',id=r['user_id'],name=r['full_name']) for r in service().users(uid,q)[:10])
            if 'payments' in caps:
                rows=store.db.execute('SELECT id,user_id FROM sonar_orders WHERE id::text ILIKE %s ORDER BY created_at DESC LIMIT 10',(term,),'all')
                out.extend(dict(kind='order',id=str(r['id']),name=str(r['user_id'])) for r in rows)
            return out
        return await guarded(read)
    return api
