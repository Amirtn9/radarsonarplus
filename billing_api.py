"""One authenticated billing API for standalone Web and Telegram Mini Apps."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,Field,ConfigDict,field_validator
from billing import Billing

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Reason(Strict):
    reason:str=Field(min_length=3,max_length=1000)
class PlanPolicy(Strict):
    min_interval_seconds:int=Field(default=60,ge=60,le=3600)
    retention_days:int=Field(default=30,ge=1,le=30)

class Plan(Reason):
    name:str=Field(min_length=1,max_length=100)
    description:str=Field(default='',max_length=2000)
    price:int=Field(ge=0,le=10**14)
    days:int=Field(ge=1,le=3660)
    limits:dict[str,int]
    features:dict[str,bool]=Field(default_factory=dict)
    consumption:dict[str,int]=Field(default_factory=dict)
    policy:PlanPolicy=Field(default_factory=PlanPolicy)
    enabled:bool=True
    publish_at:datetime|None=None
    @field_validator('publish_at')
    @classmethod
    def publish_aware(cls,value):
        if value is not None and value.tzinfo is None:raise ValueError('Timezone required')
        return value
    priority:int=Field(default=0,ge=-10000,le=10000)
    apply:Literal['new','current','renewal']='new'
class Purchase(Strict):
    plan_id:int=Field(gt=0)
    method:Literal['wallet','manual']
    idempotency_key:str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    destination_id:int|None=Field(default=None,gt=0)
    discount_code:str|None=Field(default=None,max_length=40)
class Evidence(Strict):
    text:str=Field(min_length=5,max_length=1000)
class Decision(Reason):
    approve:bool
class Wallet(Reason):
    amount:int=Field(ge=-10**14,le=10**14)
    idempotency_key:str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
class Subscription(Reason):
    status:Literal['active','suspended','cancelled']|None=None
    days:int=Field(default=0,ge=-3660,le=3660)
    overrides:dict[str,int]|None=None
    plan_id:int|None=Field(default=None,gt=0)
class User(Reason):
    full_name:str=Field(min_length=1,max_length=120)
    banned:bool=False
class Destination(Reason):
    kind:Literal['card','crypto']
    name:str=Field(min_length=1,max_length=100)
    network:str=Field(default='',max_length=80)
    address:str=Field(min_length=5,max_length=250)
    holder:str=Field(default='',max_length=100)
    instructions:str=Field(default='',max_length=1000)
    enabled:bool=True
class Discount(Reason):
    code:str=Field(min_length=3,max_length=40)
    kind:Literal['percent','fixed']
    value:int=Field(gt=0,le=10**14)
    starts_at:datetime
    ends_at:datetime
    max_uses:int=Field(gt=0,le=1000000)
    user_id:int|None=Field(default=None,gt=0)
    plan_id:int|None=Field(default=None,gt=0)
    first_only:bool=False
    renewal_only:bool=False
    enabled:bool=True
    @field_validator('starts_at','ends_at')
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None:raise ValueError('Timezone required')
        return value


class Message(Reason):
    text:str=Field(min_length=1,max_length=3500)
    request_key:str=Field(min_length=8,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')

class Staff(Reason):
    label:str=Field(min_length=1,max_length=80)
    capabilities:list[str]=Field(max_length=11)
    enabled:bool=True


def router(store,actor,guarded,admin_id):
    api=APIRouter(prefix='/api/billing',tags=['billing'])
    # Capture the current store database on every call (also supports DB recovery).
    def service():return Billing(store.db,admin_id)
    @api.get('/plans')
    async def plans(uid:int=Depends(actor),all:bool=False):return await guarded(service().plans,uid,all)
    @api.get('/account')
    async def account(uid:int=Depends(actor)):return await guarded(service().account,uid)
    @api.get('/destinations')
    async def destinations(uid:int=Depends(actor),all:bool=False):return await guarded(service().destinations,uid,all)
    @api.post('/orders')
    async def checkout(body:Purchase,uid:int=Depends(actor)):
        return await guarded(service().checkout,uid,body.plan_id,body.method,body.idempotency_key,body.destination_id,body.discount_code)
    @api.post('/orders/{ident}/cancel')
    async def cancel(ident:UUID,uid:int=Depends(actor)):
        await guarded(service().cancel,uid,str(ident));return {'ok':True}
    @api.post('/orders/{ident}/evidence')
    async def evidence(ident:UUID,body:Evidence,uid:int=Depends(actor)):
        await guarded(service().evidence,uid,str(ident),body.text);return {'ok':True}
    @api.post('/plans')
    async def plan_create(body:Plan,uid:int=Depends(actor)):
        return {'id':await guarded(service().save_plan,uid,body.model_dump(exclude={'reason','apply'}),None,body.apply,body.reason)}
    @api.put('/plans/{ident}')
    async def plan_update(ident:int,body:Plan,uid:int=Depends(actor)):
        data=body.model_dump(exclude={'reason','apply'})
        for optional in ('consumption','publish_at'):
            if optional not in body.model_fields_set:data.pop(optional,None)
        return {'id':await guarded(service().save_plan,uid,data,ident,body.apply,body.reason)}
    @api.delete('/plans/{ident}')
    async def plan_delete(ident:int,body:Reason,uid:int=Depends(actor)):
        await guarded(service().delete_plan,uid,ident,body.reason);return {'ok':True}
    @api.post('/destinations')
    async def destination_create(body:Destination,uid:int=Depends(actor)):
        return {'id':await guarded(service().save_destination,uid,body.model_dump(exclude={'reason'}),None,body.reason)}
    @api.put('/destinations/{ident}')
    async def destination_update(ident:int,body:Destination,uid:int=Depends(actor)):
        return {'id':await guarded(service().save_destination,uid,body.model_dump(exclude={'reason'}),ident,body.reason)}
    @api.post('/discounts')
    async def discount(body:Discount,uid:int=Depends(actor)):
        await guarded(service().save_discount,uid,body.model_dump(exclude={'reason'}),body.reason);return {'ok':True}
    @api.get('/discounts')
    async def discounts(uid:int=Depends(actor)):
        def read():
            service().admin(uid,'discounts');return store.db.execute('SELECT * FROM sonar_discounts ORDER BY code',fetch='all')
        return await guarded(read)
    @api.get('/users')
    async def users(uid:int=Depends(actor),q:str='',page:int=Query(default=1,ge=1,le=10000)):
        return await guarded(service().users,uid,q,page)
    @api.get('/users/{user_id}')
    async def user(user_id:int,uid:int=Depends(actor)):
        def read():service().admin(uid,'users.read');return service().account(user_id)
        return await guarded(read)
    @api.put('/users/{user_id}')
    async def manage_user(user_id:int,body:User,uid:int=Depends(actor)):
        await guarded(service().set_user,uid,user_id,body.full_name,body.banned,body.reason);return {'ok':True}
    @api.post('/users/{user_id}/wallet')
    async def wallet(user_id:int,body:Wallet,uid:int=Depends(actor)):
        return {'balance':await guarded(service().adjust_wallet,uid,user_id,body.amount,body.idempotency_key,body.reason)}
    @api.put('/users/{user_id}/subscription')
    async def subscription(user_id:int,body:Subscription,uid:int=Depends(actor)):
        return await guarded(service().manage,uid,user_id,body.status,body.days,body.overrides,body.reason,body.plan_id)
    @api.get('/orders')
    async def orders(uid:int=Depends(actor),status:Literal['pending','paid','rejected','refunded']='pending',page:int=Query(default=1,ge=1,le=10000)):
        def read():
            service().admin(uid,'payments');return store.db.execute('SELECT * FROM sonar_orders WHERE status=%s ORDER BY created_at DESC LIMIT 50 OFFSET %s',(status,(page-1)*50),fetch='all')
        return await guarded(read)
    @api.get('/orders/{ident}/receipt')
    async def receipt(ident:UUID,uid:int=Depends(actor)):
        from fastapi import HTTPException
        from fastapi.responses import Response
        import asyncio
        from telegram import Bot
        from pro_portal import get_bot_token
        def read():
            service().admin(uid,'payments')
            row=store.db.execute('SELECT evidence FROM sonar_orders WHERE id=%s',(str(ident),),fetch='one')
            if not row or not row['evidence'].startswith('telegram-photo:'):raise HTTPException(status_code=404,detail='تصویر رسید ثبت نشده است')
            return row['evidence'].split(':',1)[1]
        file_id=await guarded(read)
        token=get_bot_token()
        if not token:raise HTTPException(status_code=503,detail='ربات برای دریافت رسید تنظیم نشده است')
        try:
            async with Bot(token) as bot:
                file=await asyncio.wait_for(bot.get_file(file_id),timeout=15)
                if file.file_size is None or file.file_size>10*1024*1024:raise HTTPException(status_code=400,detail='اندازه رسید مجاز نیست')
                payload=await asyncio.wait_for(file.download_as_bytearray(),timeout=20)
        except HTTPException:raise
        except Exception:raise HTTPException(status_code=503,detail='دریافت تصویر رسید از تلگرام ممکن نیست') from None
        return Response(bytes(payload),media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="receipt.jpg"','Cache-Control':'no-store'})
    @api.post('/orders/{ident}/decision')
    async def decide(ident:UUID,body:Decision,uid:int=Depends(actor)):
        return await guarded(service().decide,uid,str(ident),body.approve,body.reason)
    @api.post('/orders/{ident}/refund')
    async def refund(ident:UUID,body:Reason,uid:int=Depends(actor)):
        await guarded(service().refund,uid,str(ident),body.reason);return {'ok':True}
    @api.get('/finances')
    async def finances(uid:int=Depends(actor),days:int=Query(default=30,ge=1,le=366)):
        return await guarded(service().finances,uid,days)
    @api.get('/audit')
    async def audit(uid:int=Depends(actor),user_id:int|None=None):
        def read():
            service().admin(uid,'audit');return store.db.execute('SELECT * FROM sonar_billing_audit WHERE (%s IS NULL OR user_id=%s) ORDER BY id DESC LIMIT 200',(user_id,user_id),fetch='all')
        return await guarded(read)
    @api.post('/users/{user_id}/message')
    async def message(user_id:int,body:Message,uid:int=Depends(actor)):
        return {'id':await guarded(service().message,uid,user_id,body.text,body.request_key,body.reason),'state':'queued'}
    @api.get('/messages')
    async def messages(uid:int=Depends(actor),user_id:int|None=None):
        def read():
            service().admin(uid,'messages')
            return store.db.execute('SELECT id,user_id,state,attempts,error_code,sent_at,created_at FROM sonar_billing_messages WHERE (%s IS NULL OR user_id=%s) ORDER BY id DESC LIMIT 100',(user_id,user_id),fetch='all')
        return await guarded(read)
    @api.post('/messages/{ident}/retry')
    async def retry_message(ident:int,uid:int=Depends(actor)):
        def write():
            service().admin(uid,'messages')
            store.db.execute("UPDATE sonar_billing_messages SET state='pending',attempts=0,next_attempt_at=NOW(),error_code=NULL WHERE id=%s AND state='failed'",(ident,))
            return {'ok':True}
        return await guarded(write)
    @api.get('/staff')
    async def staff(uid:int=Depends(actor)):
        def read():
            if uid!=admin_id:raise PermissionError('Platform administrator required')
            return store.db.execute('SELECT * FROM sonar_billing_staff ORDER BY user_id',fetch='all')
        return await guarded(read)
    @api.put('/staff/{user_id}')
    async def set_staff(user_id:int,body:Staff,uid:int=Depends(actor)):
        await guarded(service().staff,uid,user_id,body.label,body.capabilities,body.enabled,body.reason);return {'ok':True}
    return api
