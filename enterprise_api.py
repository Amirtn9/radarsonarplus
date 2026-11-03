"""Authenticated API surface for RadarSonarPlus enterprise services."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
try:  # FastAPI 0.115 supports both Pydantic generations.
    from pydantic import model_validator
except ImportError:  # Pydantic v1 compatibility
    model_validator = None
    from pydantic import root_validator
from typing import Literal

class OperationCreate(BaseModel):
    owner: int | None = None
    kind: Literal['agent_update','service_restart','diagnostic','maintenance']
    server_ids: list[int] = Field(min_length=1,max_length=100)
    idempotency_key: str = Field(min_length=8,max_length=120)
    service: str = Field(default='sonar-agent',max_length=40)

class IncidentState(BaseModel):
    owner: int | None = None
    status: Literal['acknowledged','investigating','resolved']
class IncidentNote(BaseModel):
    owner: int | None = None
    note: str = Field(min_length=1,max_length=2000)
class IncidentAssign(BaseModel):
    owner: int | None = None
    assignee_id: int = Field(gt=0)
class RoleUpdate(BaseModel):
    owner: int | None = None
    member_id: int = Field(gt=0)
    role: Literal['manager','operator','viewer','auditor','remove']
class BackupSchedule(BaseModel):
    owner: int | None = None
    name: str = Field(min_length=1,max_length=100)
    cadence: Literal['daily','weekly']
    destination: str = Field(min_length=1,max_length=500)
    retention: int = Field(default=7,ge=1,le=90)
class AutomationRule(BaseModel):
    owner: int | None = None
    event: Literal['agent_unreachable','service_down','disk_pressure']
    action: Literal['notify','restart_managed_service']
    max_attempts: int = Field(default=1,ge=1,le=3)

    if model_validator is not None:
        @model_validator(mode='after')
        def validate_event_action_pair(self):
            if self.event == 'service_down' and self.action != 'notify':
                raise ValueError('service_down supports notify only until a monitored-service-to-server mapping exists')
            if self.event == 'disk_pressure' and self.action != 'notify':
                raise ValueError('disk_pressure supports notify only')
            return self
    else:
        @root_validator(skip_on_failure=True)
        def validate_event_action_pair(cls, values):
            event, action = values.get('event'), values.get('action')
            if event == 'service_down' and action != 'notify':
                raise ValueError('service_down supports notify only until a monitored-service-to-server mapping exists')
            if event == 'disk_pressure' and action != 'notify':
                raise ValueError('disk_pressure supports notify only')
            return values
class TokenCreate(BaseModel):
    owner: int | None = None
    label: str = Field(min_length=1,max_length=80)
    scopes: list[str] = Field(min_length=1,max_length=20)
    expires_in_days: int | None = Field(default=None,ge=1,le=3650)

def make_router(store, actor, guarded):
    from enterprise_services import EnterpriseStore
    svc=EnterpriseStore(store)
    router=APIRouter(prefix='/api/enterprise',tags=['enterprise'])
    def own(uid, requested): return int(requested) if requested is not None else int(uid)
    def require_scope(scope):
        async def check(request: Request):
            scopes=getattr(request.state,'api_scopes',None)
            if scopes is not None and scope not in scopes and '*' not in scopes:
                raise HTTPException(status_code=403,detail='API token scope is insufficient')
        return check

    @router.get('/health')
    async def health(uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('servers:read'))):
        return await guarded(svc.health,uid,own(uid,owner))

    @router.post('/operations')
    async def create_operation(body:OperationCreate,uid:int=Depends(actor),_scope=Depends(require_scope('operations:write'))):
        return await guarded(svc.create_operation,uid,own(uid,body.owner),body.kind,body.server_ids,body.idempotency_key,{'service':body.service})

    @router.get('/operations')
    async def list_operations(uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('operations:read'))):
        return await guarded(svc.operations,uid,own(uid,owner))

    @router.post('/incidents/{incident_id}/state')
    async def incident_state(incident_id:int,body:IncidentState,uid:int=Depends(actor),_scope=Depends(require_scope('incidents:write'))):
        return {'ok':await guarded(svc.transition_incident,uid,own(uid,body.owner),incident_id,body.status)}

    @router.post('/incidents/{incident_id}/notes')
    async def incident_note(incident_id:int,body:IncidentNote,uid:int=Depends(actor),_scope=Depends(require_scope('incidents:write'))):
        await guarded(svc.incident_note,uid,own(uid,body.owner),incident_id,body.note);return {'ok':True}

    @router.post('/incidents/{incident_id}/assignment')
    async def incident_assign(incident_id:int,body:IncidentAssign,uid:int=Depends(actor),_scope=Depends(require_scope('incidents:write'))):
        await guarded(svc.assign_incident,uid,own(uid,body.owner),incident_id,body.assignee_id);return {'ok':True}

    @router.put('/team/role')
    async def team_role(body:RoleUpdate,uid:int=Depends(actor),_scope=Depends(require_scope('workspace:admin'))):
        await guarded(svc.set_role,uid,own(uid,body.owner),body.member_id,body.role);return {'ok':True}

    @router.post('/backups/schedules')
    async def backup_schedule(body:BackupSchedule,uid:int=Depends(actor),_scope=Depends(require_scope('backups:write'))):
        return await guarded(svc.create_backup_schedule,uid,own(uid,body.owner),body.name,body.cadence,body.destination,body.retention)

    @router.post('/automation/rules')
    async def automation_rule(body:AutomationRule,uid:int=Depends(actor),_scope=Depends(require_scope('automation:write'))):
        return await guarded(svc.create_automation_rule,uid,own(uid,body.owner),body.event,body.action,body.max_attempts)

    @router.post('/tokens')
    async def api_token(body:TokenCreate,uid:int=Depends(actor),_scope=Depends(require_scope('tokens:write'))):
        return await guarded(svc.create_api_token,uid,own(uid,body.owner),body.label,body.scopes,body.expires_in_days)

    class WebhookCreate(BaseModel):
        owner: int | None = None
        url: str = Field(min_length=10,max_length=500)
        events: list[str] = Field(min_length=1,max_length=8)
    class TokenRevoke(BaseModel):
        owner: int | None = None
        token_id: int = Field(gt=0)
    @router.post('/webhooks')
    async def webhook_create(body:WebhookCreate,uid:int=Depends(actor),_scope=Depends(require_scope('webhooks:write'))):
        return await guarded(svc.create_webhook,uid,own(uid,body.owner),body.url,body.events)
    @router.post('/tokens/revoke')
    async def token_revoke(body:TokenRevoke,uid:int=Depends(actor),_scope=Depends(require_scope('tokens:write'))):
        return {'ok':await guarded(svc.revoke_api_token,uid,own(uid,body.owner),body.token_id)}
    @router.get('/analytics')
    async def analytics(uid:int=Depends(actor),owner:int|None=None,days:int=30,_scope=Depends(require_scope('analytics:read'))):
        return await guarded(svc.analytics,uid,own(uid,owner),days)
    class AgentRelease(BaseModel):
        owner: int | None = None
        version: str = Field(min_length=1,max_length=40)
        sha256: str = Field(min_length=64,max_length=64)
        artifact_base64: str = Field(min_length=100,max_length=2_800_000)
        signature: str = Field(min_length=80,max_length=200)
        channel: Literal['stable','beta'] = 'stable'
    class AgentRollout(BaseModel):
        owner: int | None = None
        release_id: int = Field(gt=0)
        server_ids: list[int] = Field(min_length=1,max_length=100)
        batch_size: int = Field(default=5,ge=1,le=20)
        canary_count: int = Field(default=1,ge=1,le=5)
    @router.get('/agent/source')
    async def agent_source(uid:int=Depends(actor),_scope=Depends(require_scope('agents:read'))):
        import hashlib
        from pathlib import Path
        source=Path(__file__).resolve().parent/'monitor_agent.py'
        return {'version':source.read_text(encoding='utf-8').split("AGENT_VERSION = ",1)[1].splitlines()[0].strip(" \'\"") if "AGENT_VERSION = " in source.read_text(encoding='utf-8') else 'unknown',
                'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'release_mode':'signed Ed25519 artifacts are required; upload the exact Agent file and signature'}
    @router.post('/agent/releases')
    async def agent_release(body:AgentRelease,uid:int=Depends(actor),_scope=Depends(require_scope('agents:write'))):
        return await guarded(svc.create_agent_release,uid,own(uid,body.owner),body.version,body.sha256,body.artifact_base64,body.signature,body.channel)
    @router.post('/agent/rollouts')
    async def agent_rollout(body:AgentRollout,uid:int=Depends(actor),_scope=Depends(require_scope('agents:write'))):
        return await guarded(svc.create_agent_rollout,uid,own(uid,body.owner),body.release_id,body.server_ids,body.batch_size,body.canary_count)
    class RolloutControl(BaseModel):
        owner: int | None = None
        action: Literal['pause','resume','cancel','rollback']
    @router.post('/agent/rollouts/{rollout_id}/control')
    async def rollout_control(rollout_id:int,body:RolloutControl,uid:int=Depends(actor),_scope=Depends(require_scope('agents:write'))):
        return await guarded(svc.control_agent_rollout,uid,own(uid,body.owner),rollout_id,body.action)
    @router.get('/agent/rollouts')
    async def agent_rollouts(uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('agents:read'))):
        return await guarded(svc.agent_rollouts,uid,own(uid,owner))

    class OperationAction(BaseModel):
        owner: int | None = None
        action: Literal['pause','resume','cancel','retry']
    class HealthPolicy(BaseModel):
        owner: int | None = None
        weights: dict[str,int]
        thresholds: dict[str,int]
        stale_seconds: int = Field(default=180,ge=30,le=3600)
    class WebhookUpdate(BaseModel):
        owner: int | None = None
        url: str | None = Field(default=None,min_length=10,max_length=500)
        events: list[str] | None = Field(default=None,min_length=1,max_length=8)
        enabled: bool | None = None
        rotate_secret: bool = False
    class WebhookRetry(BaseModel):
        owner: int | None = None
        delivery_id: int = Field(gt=0)
    class BackupUpdate(BaseModel):
        owner: int | None = None
        name: str | None = Field(default=None,min_length=1,max_length=100)
        cadence: Literal['daily','weekly'] | None = None
        destination: str | None = Field(default=None,min_length=1,max_length=120)
        retention: int | None = Field(default=None,ge=1,le=90)
    class BackupControl(BaseModel):
        owner: int | None = None
        action: Literal['enable','disable','run_now','delete']
    @router.get('/operations/{operation_id}')
    async def operation_detail(operation_id:str,uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('operations:read'))):
        return await guarded(svc.operation_detail,uid,own(uid,owner),operation_id)
    @router.post('/operations/{operation_id}/control')
    async def operation_control(operation_id:str,body:OperationAction,uid:int=Depends(actor),_scope=Depends(require_scope('operations:write'))):
        return await guarded(svc.control_operation,uid,own(uid,body.owner),operation_id,body.action)
    @router.get('/incidents')
    async def incidents(uid:int=Depends(actor),owner:int|None=None,status:str='open',limit:int=50,_scope=Depends(require_scope('incidents:read'))):
        return await guarded(svc.list_incidents,uid,own(uid,owner),status,limit)
    @router.get('/incidents/{incident_id}')
    async def incident_detail(incident_id:int,uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('incidents:read'))):
        return await guarded(svc.incident_detail,uid,own(uid,owner),incident_id)
    @router.get('/servers/{server_id}/metrics')
    async def server_metrics(server_id:int,uid:int=Depends(actor),owner:int|None=None,days:int=7,_scope=Depends(require_scope('metrics:read'))):
        return await guarded(svc.server_metrics,uid,own(uid,owner),server_id,days)
    @router.get('/health/policy')
    async def get_health_policy(uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('servers:read'))):
        return await guarded(svc.health_policy,uid,own(uid,owner))
    @router.put('/health/policy')
    async def put_health_policy(body:HealthPolicy,uid:int=Depends(actor),_scope=Depends(require_scope('health:write'))):
        return await guarded(svc.set_health_policy,uid,own(uid,body.owner),body.weights,body.thresholds,body.stale_seconds)
    @router.get('/backups/schedules')
    async def backup_schedules(uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('backups:read'))):
        return await guarded(svc.backup_schedules,uid,own(uid,owner))
    @router.get('/backups/schedules/{schedule_id}/runs')
    async def backup_runs(schedule_id:int,uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('backups:read'))):
        return await guarded(svc.backup_runs,uid,own(uid,owner),schedule_id)
    @router.put('/backups/schedules/{schedule_id}')
    async def backup_update(schedule_id:int,body:BackupUpdate,uid:int=Depends(actor),_scope=Depends(require_scope('backups:write'))):
        return await guarded(svc.update_backup_schedule,uid,own(uid,body.owner),schedule_id,body.name,body.cadence,body.destination,body.retention)
    @router.post('/backups/schedules/{schedule_id}/control')
    async def backup_control(schedule_id:int,body:BackupControl,uid:int=Depends(actor),_scope=Depends(require_scope('backups:write'))):
        return await guarded(svc.control_backup_schedule,uid,own(uid,body.owner),schedule_id,body.action)
    @router.get('/tokens')
    async def token_list(uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('tokens:read'))):
        return await guarded(svc.list_api_tokens,uid,own(uid,owner))
    @router.get('/webhooks')
    async def webhook_list(uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('webhooks:read'))):
        return await guarded(svc.list_webhooks,uid,own(uid,owner))
    @router.put('/webhooks/{webhook_id}')
    async def webhook_update(webhook_id:int,body:WebhookUpdate,uid:int=Depends(actor),_scope=Depends(require_scope('webhooks:write'))):
        return await guarded(svc.update_webhook,uid,own(uid,body.owner),webhook_id,body.url,body.events,body.enabled,body.rotate_secret)
    @router.delete('/webhooks/{webhook_id}')
    async def webhook_delete(webhook_id:int,uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('webhooks:write'))):
        return {'ok':await guarded(svc.delete_webhook,uid,own(uid,owner),webhook_id)}
    @router.get('/webhooks/{webhook_id}/deliveries')
    async def webhook_deliveries(webhook_id:int,uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('webhooks:read'))):
        return await guarded(svc.webhook_deliveries,uid,own(uid,owner),webhook_id)
    @router.post('/webhooks/{webhook_id}/retry')
    async def webhook_retry(webhook_id:int,body:WebhookRetry,uid:int=Depends(actor),_scope=Depends(require_scope('webhooks:write'))):
        return {'ok':await guarded(svc.retry_webhook_delivery,uid,own(uid,body.owner),webhook_id,body.delivery_id)}
    @router.delete('/tokens/{token_id}')
    async def token_delete(token_id:int,uid:int=Depends(actor),owner:int|None=None,_scope=Depends(require_scope('tokens:write'))):
        return {'ok':await guarded(svc.revoke_api_token,uid,own(uid,owner),token_id)}
    return router
