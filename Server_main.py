#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Server_main.py  ―  Culture-Fest 受付システム 2025
---------------------------------------------------
▶ 依存:  pip install fastapi uvicorn sqlmodel
▶ 起動:  python3 Server_main.py
"""

from datetime import datetime, timezone
from typing import List, Optional, Generator

from fastapi import (
    FastAPI, HTTPException, Depends,
    WebSocket, WebSocketDisconnect, Header, status
)
from pydantic import BaseModel
from sqlmodel import (
    Field, SQLModel, Session, select, create_engine
)

# ───────────────────────────────────────────────
# DB 初期化
# ───────────────────────────────────────────────
DATABASE_URL = "sqlite:///./queue.db"
engine = create_engine(DATABASE_URL, echo=False)

# ───────────────────────────────────────────────
# テーブル定義
# ───────────────────────────────────────────────
class Service(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, nullable=False)
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Ticket(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, nullable=False)
    service_id: int = Field(foreign_key="service.id")
    called: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    called_at: Optional[datetime] = None

# ───────────────────────────────────────────────
# スキーマ
# ───────────────────────────────────────────────
class ServiceCreate(BaseModel):
    name: str
    description: Optional[str] = None


class TicketCreate(BaseModel):
    name: str
    service_id: int


class TicketRead(BaseModel):
    id: int
    name: str
    service_id: int
    position: int
    called: bool
    created_at: datetime
    called_at: Optional[datetime] = None


class QueueRead(BaseModel):
    service_id: int
    service_name: str
    waiting: int
    tickets: List[TicketRead]


class QueueSummary(BaseModel):
    service_id: int
    service_name: str
    waiting: int

# ───────────────────────────────────────────────
# 依存
# ───────────────────────────────────────────────
def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


ADMIN_API_KEY = "CHANGE_ME"


def verify_admin(x_api_key: str = Header(...)) -> None:
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

# ───────────────────────────────────────────────
# FastAPI 本体
# ───────────────────────────────────────────────
app = FastAPI(
    title="受付キューサーバー",
    description="文化祭などで使える簡易受付 / 順番待ち API",
    version="1.1.0"
)


@app.on_event("startup")
def on_startup() -> None:
    SQLModel.metadata.create_all(engine)

# ───────────────────────────────────────────────
# サービス
# ───────────────────────────────────────────────
@app.post("/services", response_model=Service, status_code=status.HTTP_201_CREATED)
def create_service(data: ServiceCreate, session: Session = Depends(get_session)):
    if session.exec(select(Service).where(Service.name == data.name)).first():
        raise HTTPException(400, f"Service '{data.name}' already exists")
    svc = Service.model_validate(data)          # from_orm は非推奨
    session.add(svc)
    session.commit()
    session.refresh(svc)
    return svc


@app.get("/services", response_model=List[Service])
def list_services(session: Session = Depends(get_session)):
    return session.exec(select(Service).order_by(Service.id)).all()

# ───────────────────────────────────────────────
# チケット
# ───────────────────────────────────────────────
@app.post("/tickets", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
def register_ticket(data: TicketCreate, session: Session = Depends(get_session)):
    service = session.get(Service, data.service_id)
    if not service:
        raise HTTPException(404, "Service not found")

    ticket = Ticket(name=data.name, service_id=service.id)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    pos = _position_in_queue(ticket, session)
    return _ticket_to_read(ticket, position=pos)


@app.get("/tickets/{ticket_id}", response_model=TicketRead)
def get_ticket(ticket_id: int, session: Session = Depends(get_session)):
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    pos = _position_in_queue(ticket, session)
    return _ticket_to_read(ticket, position=pos)


@app.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_ticket(ticket_id: int, session: Session = Depends(get_session)):
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket.called:
        raise HTTPException(400, "Already called; cannot cancel")
    session.delete(ticket)
    session.commit()

# ───────────────────────────────────────────────
# キュー詳細
# ───────────────────────────────────────────────
@app.get("/queues/{service_id}", response_model=QueueRead)
def queue_detail(service_id: int, session: Session = Depends(get_session)):
    service = session.get(Service, service_id)
    if not service:
        raise HTTPException(404, "Service not found")

    # called==Trueも含めて、全チケットを古い順で返す
    all_tickets = session.exec(
        select(Ticket)
        .where(Ticket.service_id == service_id)
        .order_by(Ticket.created_at)
    ).all()

    waiting = len([t for t in all_tickets if not t.called])

    read_list = [
        _ticket_to_read(t, position=(i + 1) if not t.called else 0)
        for i, t in enumerate([t for t in all_tickets if not t.called])
    ]
    # 全チケット分を返す
    tickets_list = []
    idx = 1
    for t in all_tickets:
        pos = idx if not t.called else 0
        if not t.called:
            idx += 1
        tickets_list.append(_ticket_to_read(t, position=pos))
    return QueueRead(
        service_id=service.id,
        service_name=service.name,
        waiting=waiting,
        tickets=tickets_list
    )


# ───────────────────────────────────────────────
# 次の人を呼ぶ
# ───────────────────────────────────────────────
@app.post("/admin/next/{service_id}", response_model=TicketRead,
          dependencies=[Depends(verify_admin)])
def call_next(service_id: int, session: Session = Depends(get_session)):
    ticket = session.exec(
        select(Ticket)
        .where(Ticket.service_id == service_id, Ticket.called == False)
        .order_by(Ticket.created_at)
        .limit(1)
    ).first()
    if not ticket:
        raise HTTPException(404, "No one waiting")

    ticket.called = True
    ticket.called_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return _ticket_to_read(ticket, position=0)

# ───────────────────────────────────────────────
# 待ち人数サマリ（修正版）
# ───────────────────────────────────────────────
@app.get("/stats", response_model=List[QueueSummary])
def stats(session: Session = Depends(get_session)):
    services = session.exec(select(Service)).all()
    summaries: List[QueueSummary] = []
    for svc in services:
        waiting_rows = session.exec(
            select(Ticket).where(
                Ticket.service_id == svc.id,
                Ticket.called == False
            )
        ).all()
        summaries.append(
            QueueSummary(
                service_id=svc.id,
                service_name=svc.name,
                waiting=len(waiting_rows)
            )
        )
    return summaries

# ───────────────────────────────────────────────
# WebSocket（任意購読）
# ───────────────────────────────────────────────
subscribers: dict[int, list[WebSocket]] = {}


@app.websocket("/ws/queues/{service_id}")
async def queue_ws(websocket: WebSocket, service_id: int):
    await websocket.accept()
    subscribers.setdefault(service_id, []).append(websocket)
    try:
        await _push_queue_update(service_id)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers[service_id].remove(websocket)


async def _push_queue_update(service_id: int):
    if not subscribers.get(service_id):
        return
    async with Session(engine) as session:
        data = queue_detail(service_id, session)  # type: ignore[arg-type]
    for ws in list(subscribers[service_id]):
        try:
            await ws.send_json(data.model_dump())
        except RuntimeError:
            subscribers[service_id].remove(ws)

# ───────────────────────────────────────────────
# 内部ユーティリティ（修正版）
# ───────────────────────────────────────────────
def _position_in_queue(ticket: Ticket, session: Session) -> int:
    """待機中なら 1 以上、呼出済なら 0"""
    if ticket.called:
        return 0
    earlier_rows = session.exec(
        select(Ticket).where(
            Ticket.service_id == ticket.service_id,
            Ticket.called == False,
            Ticket.created_at < ticket.created_at
        )
    ).all()
    return len(earlier_rows) + 1


def _ticket_to_read(ticket: Ticket, position: int) -> TicketRead:
    return TicketRead(
        id=ticket.id,
        name=ticket.name,
        service_id=ticket.service_id,
        position=position,
        called=ticket.called,
        created_at=ticket.created_at,
        called_at=ticket.called_at
    )

# ───────────────────────────────────────────────
# エントリポイント
# ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)