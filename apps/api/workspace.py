import base64
import json
from datetime import datetime
from typing import Literal
from uuid import UUID,uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.access import analyst,user
from packages.contracts.stream import query
from packages.db.connect import engine

router=APIRouter()
TX=""" FROM transactions t LEFT JOIN decisions d ON d.transaction_id=t.id """
STATE="""CASE WHEN d.id IS NOT NULL THEN 'scored' WHEN EXISTS(SELECT 1 FROM quarantine q WHERE q.transaction_id=t.id AND resolved_at IS NULL) THEN 'failed' ELSE 'pending' END"""
CASES=""" FROM cases c JOIN decisions d ON d.id=c.opening_decision_id JOIN transactions t ON t.id=d.transaction_id LEFT JOIN users u ON u.id=c.assignee """
CASE_FIELDS="c.*,u.username AS owner,t.id AS transaction_id,t.run_id,d.action,d.score"


def filters(**fields):
    clauses,params=[],{}
    for idx,(expr,value) in enumerate(fields.items()):
        if value is not None:
            name=f"p{idx}"
            clauses.append(expr+" :"+name)
            params[name]=value
    return clauses,params


def scope(run,start,end):
    return filters(**{"t.run_id =":run,"t.received_at >=":start,"t.received_at <":end})


def where(clauses):
    return " WHERE "+" AND ".join(clauses) if clauses else ""


def page(sql,clauses,params,order,idcol,cursor,limit):
    if cursor:
        try:
            ts,uid=json.loads(base64.urlsafe_b64decode(cursor))
            params.update(ts=datetime.fromisoformat(ts),uid=UUID(uid))
        except (ValueError,TypeError,UnicodeError):
            raise HTTPException(422,"Invalid cursor")
        clauses.append(f"({order},{idcol}) < (:ts,:uid)")
    with engine.connect() as c:
        rows=query(c,sql+where(clauses)+f" ORDER BY {order} DESC,{idcol} DESC LIMIT :n",**params,n=limit+1).mappings().all()
    token=None
    if len(rows)>limit:
        last=rows[limit-1]
        token=base64.urlsafe_b64encode(json.dumps([last[order.split('.')[-1]].isoformat(),str(last["id"])]).encode()).decode()
    return dict(items=rows[:limit],next_cursor=token)


@router.get("/v1/runs")
def runs(u=Depends(user)):
    with engine.connect() as c:
        return query(c,"SELECT id,seed,generator_version,started_at FROM runs ORDER BY started_at DESC").mappings().all()


@router.get("/v1/summary")
def summary(run_id:UUID|None=None,start:datetime|None=Query(None,alias="from"),to:datetime|None=None,u=Depends(user)):
    cond,p=scope(run_id,start,to)
    with engine.connect() as c:
        row=query(c,"""SELECT count(*) accepted,count(*) FILTER(WHERE action='approve') approved,
            count(*) FILTER(WHERE action='review') reviewed,count(*) FILTER(WHERE action='block') blocked,
            count(*) FILTER(WHERE state='pending') pending,count(*) FILTER(WHERE state='failed') failed,
            extract(epoch FROM now()-min(received_at) FILTER(WHERE state='pending')) oldest_pending_seconds
            FROM (SELECT t.received_at,d.action,"""+STATE+" state "+TX+where(cond)+") scoped",**p).mappings().one()
        series=query(c,"SELECT date_trunc('minute',t.received_at) AS minute,count(*) total FROM transactions t"+
                     where(cond)+" GROUP BY 1 ORDER BY 1",**p).mappings().all()
    return dict(row,series=series)


@router.get("/v1/transactions")
def transactions(run_id:UUID|None=None,action:Literal["approve","review","block"]|None=None,
    status:Literal["pending","scored","failed"]|None=None,start:datetime|None=Query(None,alias="from"),to:datetime|None=None,
    cursor:str|None=None,limit:int=Query(25,ge=1,le=100),u=Depends(user)):
    clauses,p=scope(run_id,start,to)
    if action:
        clauses.append("d.action=:action")
        p["action"]=action
    if status:
        clauses.append(STATE+"=:status")
        p["status"]=status
    return page("SELECT t.id,t.amount_minor,t.currency,t.received_at,d.score,d.action,"+STATE+" state "+TX,
                clauses,p,"t.received_at","t.id",cursor,limit)


@router.get("/v1/cases")
def cases(run_id:UUID|None=None,status:Literal["open","investigating","resolved"]|None=None,
          priority:Literal["normal","high"]|None=None,cursor:str|None=None,limit:int=Query(25,ge=1,le=100),
          start:datetime|None=Query(None,alias="from"),to:datetime|None=None,u=Depends(user)):
    clauses,p=filters(**{"t.run_id =":run_id,"c.status =":status,"c.priority =":priority,"t.received_at >=":start,"t.received_at <":to})
    return page("SELECT "+CASE_FIELDS+CASES,clauses,p,"c.opened_at","c.id",cursor,limit)


@router.get("/v1/cases/{cid}")
def case_detail(cid:UUID,u=Depends(user)):
    with engine.connect() as c:
        row=query(c,"SELECT "+CASE_FIELDS+CASES+" WHERE c.id=:id",id=cid).mappings().one_or_none()
        if not row:
            raise HTTPException(404,"Case not found")
        notes=query(c,"SELECT n.*,u.username FROM case_notes n JOIN users u ON u.id=n.author_id WHERE case_id=:id ORDER BY created_at,n.id",id=cid).mappings().all()
        events=query(c,"SELECT e.*,u.username FROM case_events e LEFT JOIN users u ON u.id=e.actor_id WHERE case_id=:id ORDER BY occurred_at,e.id",id=cid).mappings().all()
        links=query(c,"SELECT transaction_id,relation FROM case_transactions WHERE case_id=:id ORDER BY added_at,transaction_id",id=cid).mappings().all()
        return dict(row,notes=notes,timeline=events,links=links)


class Edit(BaseModel):
    expected_version:int=Field(ge=1)
    status:Literal["investigating","resolved"]
    resolution:str|None=Field(None,max_length=2000)


class Note(BaseModel):
    id:UUID
    body:str=Field(min_length=1,max_length=2000)


def audit(c,cid,uid,kind,change):
    query(c,"INSERT INTO case_events(id,case_id,actor_id,kind,before_after,correlation_id) VALUES(:id,:cid,:uid,:kind,CAST(:data AS jsonb),:corr)",
          id=uuid4(),cid=cid,uid=uid,kind=kind,data=json.dumps(change),corr=uuid4())


@router.patch("/v1/cases/{cid}")
def edit(cid:UUID,data:Edit,u=Depends(analyst)):
    with engine.begin() as c:
        old=query(c,"SELECT status,version FROM cases WHERE id=:id FOR UPDATE",id=cid).mappings().one_or_none()
        if not old:
            raise HTTPException(404,"Case not found")
        if old["version"]!=data.expected_version:
            raise HTTPException(409,"Case changed. Review the latest version before retrying.")
        if (old["status"],data.status) not in {("open","investigating"),("investigating","resolved")}:
            raise HTTPException(422,"Invalid case transition")
        resolution=(data.resolution or "").strip()
        if data.status=="resolved" and not resolution:
            raise HTTPException(422,"Resolution required")
        query(c,"""UPDATE cases SET status=:s,assignee=coalesce(assignee,:u),version=version+1,updated_at=now(),
            resolution=:r,resolved_by=CASE WHEN :s='resolved' THEN CAST(:u AS uuid) ELSE NULL END
            WHERE id=:id AND version=:v""",s=data.status,u=u["id"],r=resolution or None,id=cid,v=data.expected_version)
        audit(c,cid,u["id"],data.status,{"before":old["status"],"after":data.status})
    return {"version":data.expected_version+1}


@router.post("/v1/cases/{cid}/notes")
def note(cid:UUID,data:Note,u=Depends(analyst)):
    if not data.body.strip():
        raise HTTPException(422,"Note cannot be blank")
    with engine.begin() as c:
        if not query(c,"SELECT id FROM cases WHERE id=:id FOR UPDATE",id=cid).scalar():
            raise HTTPException(404,"Case not found")
        created=query(c,"INSERT INTO case_notes(id,case_id,author_id,body) VALUES(:id,:cid,:u,:b) ON CONFLICT DO NOTHING RETURNING id",
                      id=data.id,cid=cid,u=u["id"],b=data.body).scalar()
        if created:
            audit(c,cid,u["id"],"note_added",{"note_id":str(data.id)})
            query(c,"UPDATE cases SET version=version+1,updated_at=now() WHERE id=:id",id=cid)
        else:
            old=query(c,"SELECT case_id,author_id,body FROM case_notes WHERE id=:id",id=data.id).one()
            if tuple(old)!=(cid,u["id"],data.body):
                raise HTTPException(409,"Note ID already used")
    return {"id":data.id}
