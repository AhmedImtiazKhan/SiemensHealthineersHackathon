import os, time
from contextlib import asynccontextmanager
from typing import Annotated
from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func, cast, Text
from sqlalchemy.dialects.postgresql import JSONB
from .db import engine, products, coverage
from .parser import norm, imprint_key

ALIASES={'circular':'round','circle':'round','egg shaped':'oval','oblong':'oval','grey':'gray','off white':'white','whitish':'white'}
def canonical(v): return ALIASES.get(norm(v),norm(v))
class Search(BaseModel):
    colour: str | None = Field(default=None,max_length=80)
    shape: str | None = Field(default=None,max_length=80)
    imprint: str | None = Field(default=None,max_length=100)
    free_text: str | None = Field(default=None,max_length=300)
    limit: int = Field(default=5,ge=1,le=50)
    @model_validator(mode='after')
    def nonempty(self):
        if not any(norm(v) for v in [self.colour,self.shape,self.imprint,self.free_text]): raise ValueError('Supply at least one search attribute')
        return self

def auth(x_api_key: Annotated[str | None, Header()] = None):
    import secrets
    key=os.getenv('API_KEY')
    if key and (not x_api_key or not secrets.compare_digest(key,x_api_key)):
        raise HTTPException(401,'Invalid API key')

def create_app(db=None):
    db = db or engine()
    app=FastAPI(title='PillTalk Catalogue',version='0.1.0',dependencies=[Depends(auth)])
    @app.get('/health')
    def health():
        with db.connect() as c: c.execute(select(1))
        return {'status':'ok'}
    @app.get('/coverage')
    def report(): return coverage(db)
    @app.post('/search')
    def search(q: Search):
        start=time.perf_counter(); stmt=select(products.c.data).where(products.c.active.is_(True))
        if q.shape: stmt=stmt.where(products.c.shape==canonical(q.shape))
        if q.imprint:
            key=imprint_key(q.imprint)
            if not key: raise HTTPException(422,'Imprint must contain letters or digits')
            stmt=stmt.where(products.c.imprint_normalized==key)
        if q.colour:
            colour=canonical(q.colour)
            if db.dialect.name=='postgresql': stmt=stmt.where(cast(products.c.data,JSONB)['colours'].contains([colour]))
            else: stmt=stmt.where(func.lower(cast(products.c.data['colours'],Text)).contains('"'+colour+'"',autoescape=True))
        if q.free_text:
            term=norm(q.free_text)
            if db.dialect.name=='postgresql':
                similarity=func.similarity(products.c.search_text,term)
                stmt=stmt.where((similarity>0.08) | products.c.search_text.contains(term,autoescape=True)).order_by(similarity.desc(),products.c.id)
            else: stmt=stmt.where(products.c.search_text.contains(term,autoescape=True))
        stmt=stmt.order_by(products.c.id).limit(q.limit)
        with db.connect() as c: results=[x[0] for x in c.execute(stmt)]
        return {'candidates':results,'returned':len(results),'query_ms':round((time.perf_counter()-start)*1000,2),
                'requires_confirmation':True,'ranking':'attribute filters then text similarity; not a clinical probability'}
    return app
app=create_app()
