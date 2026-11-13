import os
from sqlalchemy import create_engine, MetaData, Table, Column, String, Text, JSON, Integer, Boolean, Index, select, delete, func
metadata = MetaData()
products = Table('products', metadata,
    Column('id', String, primary_key=True), Column('setid', String, nullable=False, index=True),
    Column('name', Text, nullable=False), Column('shape', String, index=True),
    Column('imprint_normalized', String, index=True), Column('search_text', Text),
    Column('active', Boolean, nullable=False, default=True), Column('data', JSON, nullable=False))
labels = Table('labels', metadata, Column('setid', String, primary_key=True),
    Column('published', String), Column('status', String, index=True), Column('error', Text), Column('version', String))
state = Table('state', metadata, Column('key', String, primary_key=True), Column('value', JSON))
rxcache = Table('rxcache', metadata, Column('ndc', String, primary_key=True), Column('data', JSON))

def engine():
    return create_engine(os.getenv('DATABASE_URL', 'postgresql+psycopg://pilltalk:pilltalk@localhost:5432/pilltalk'), pool_pre_ping=True)

def init(db):
    metadata.create_all(db)
    if db.dialect.name == 'postgresql':
        with db.begin() as c:
            c.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            c.exec_driver_sql('CREATE INDEX IF NOT EXISTS product_text_trgm ON products USING gin (search_text gin_trgm_ops)')
            c.exec_driver_sql("CREATE INDEX IF NOT EXISTS product_colour_gin ON products USING gin ((data::jsonb -> 'colours'))")

def put(c, table, key, values):
    existing = c.execute(select(table).where(table.c[key] == values[key])).first()
    if existing:
        c.execute(table.update().where(table.c[key] == values[key]).values(**values))
    else:
        c.execute(table.insert().values(**values))

def save_label(db, sid, version, items):
    with db.begin() as c:
        c.execute(delete(products).where(products.c.setid == sid))
        for p in items:
            search_text = ' '.join([p['name'],p['labeler'] or '', p['dosage_form'],p['imprint'] or '', *p['colours'],p['shape'] or '',*[i['name'] or '' for i in p['ingredients']]]).lower()
            c.execute(products.insert().values(id=p['id'],setid=sid,name=p['name'],shape=p['shape'],imprint_normalized=p['imprint_normalized'],search_text=search_text,active=True,data=p))
        c.execute(labels.update().where(labels.c.setid == sid).values(status='done',error=None,version=version))

def coverage(db):
    with db.connect() as c:
        counts = dict(c.execute(select(labels.c.status,func.count()).group_by(labels.c.status)).all())
        total = images = appearance = rx = archived = 0
        for (p,) in c.execute(select(products.c.data).where(products.c.active.is_(True))):
            archived += bool(p.get('c3pi_candidates'))
            total += 1; images += bool(p.get('image_urls')); appearance += bool(p.get('colours') and p.get('shape') and p.get('imprint')); rx += bool(p.get('rxcuis'))
        return dict(products_indexed=total,with_images=images,with_c3pi_candidates=archived,with_colour_shape_imprint=appearance,with_active_rxnorm=rx,label_status=counts,
                    scope='DailyMed human oral tablets/capsules/pellets/lozenges/troches with explicit oral route; source coverage, not all global medicines')
