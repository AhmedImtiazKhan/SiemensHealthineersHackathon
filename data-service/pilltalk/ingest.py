import hashlib, json, time
from pathlib import Path
import httpx
from sqlalchemy import select
from .db import labels, state, products, rxcache, put, save_label
from .parser import parse_spl
BASE = 'https://dailymed.nlm.nih.gov/dailymed/services/v2'

class Fetcher:
    def __init__(self, cache='data/cache', delay=0.3):
        self.cache = Path(cache); self.cache.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.client = httpx.Client(timeout=60, follow_redirects=True, headers={'User-Agent':'PillTalkResearch/0.1'})
    def get(self, url, cached=False):
        path = self.cache / hashlib.sha256(url.encode()).hexdigest()
        if cached and path.exists(): return path.read_bytes()
        for attempt in range(5):
            time.sleep(self.delay)
            try:
                r = self.client.get(url)
                if r.status_code == 429 or r.status_code >= 500:
                    retry = r.headers.get('Retry-After','')
                    time.sleep(min(float(retry) if retry.isdigit() else 2**attempt, 30)); continue
                r.raise_for_status()
                if cached:
                    tmp = path.with_suffix('.tmp'); tmp.write_bytes(r.content); tmp.replace(path)
                return r.content
            except httpx.TransportError:
                if attempt == 4: raise
                time.sleep(2**attempt)
        raise RuntimeError(f'Upstream unavailable: {url}')
    def json(self, url, cached=False): return json.loads(self.get(url,cached))

def discover(db, fetch, restart=False, since=None):
    """Atomic per-page queue/checkpoint. A restart also discovers newer label revisions."""
    key = 'discovery:' + (since or 'all')
    with db.connect() as c:
        current = c.execute(select(state.c.value).where(state.c.key == key)).scalar()
    page = 1 if restart or not current else current['page']
    if current and current.get('complete') and not restart:
        print('Discovery already complete; use --restart to refresh.',flush=True); return
    while True:
        suffix = f'&published_date={since}&published_date_comparison=gte' if since else ''
        data = fetch.json(f'{BASE}/spls.json?pagesize=100&page={page}{suffix}')
        rows = data.get('data', [])
        if not rows and page == 1: raise RuntimeError('Empty catalogue response; refusing to mark discovery complete')
        last = not rows or page >= int(data['metadata']['total_pages'])
        with db.begin() as c:
            for row in rows:
                sid = row['setid']; old = c.execute(select(labels).where(labels.c.setid == sid)).mappings().first()
                published = row.get('published_date','') + '|v' + str(row.get('spl_version',''))
                if not old or old['published'] != published:
                    put(c,labels,'setid',dict(setid=sid,published=published,status='pending',error=None,version=None))
            put(c,state,'key',dict(key=key,value={'page':page+1,'complete':last}))
        print(f'Discovered page {page}; {len(rows)} labels',flush=True)
        if last: break
        page += 1

def ingest(db, fetch, retry=False, limit=None):
    # Keyset batches avoid loading the catalogue into RAM. Each failure is retained.
    last = ''; count = 0
    while True:
        with db.connect() as c:
            statuses = ['pending','failed'] if retry else ['pending']
            batch = c.execute(select(labels).where(labels.c.status.in_(statuses), labels.c.setid > last).order_by(labels.c.setid).limit(100)).mappings().all()
        if not batch: break
        for row in batch:
            if limit is not None and count >= limit: return
            sid=row['setid']; last=sid; count += 1
            try:
                raw = fetch.get(f'{BASE}/spls/{sid}.xml')
                parsed_sid, version, items = parse_spl(raw)
                if parsed_sid.lower() != sid.lower(): raise ValueError('Returned SETID differs from requested SETID')
                # Preserve raw XML for audit/reprocessing, named by its content hash.
                (fetch.cache / (hashlib.sha256(raw).hexdigest()+'.xml')).write_bytes(raw)
                save_label(db,sid,version,items)
                print(f'{sid}: {len(items)} products',flush=True)
            except Exception as e:
                with db.begin() as c:
                    c.execute(labels.update().where(labels.c.setid==sid).values(status='failed',error=str(e)[:2000]))
                print(f'{sid}: FAILED {e}',flush=True)

def enrich(db, fetch, refresh=False):
    last=''
    while True:
        with db.connect() as c:
            rows=c.execute(select(products).where(products.c.id>last).order_by(products.c.id).limit(100)).mappings().all()
        if not rows: break
        for row in rows:
            last=row['id']; p=dict(row['data'])
            if p.get('rxnorm_checked') and not refresh: continue
            mappings=[]
            try:
                for ndc in p['package_ndcs']:
                    with db.connect() as c: cached=c.execute(select(rxcache.c.data).where(rxcache.c.ndc==ndc)).scalar()
                    result = cached if cached is not None and not refresh else fetch.json(f'https://rxnav.nlm.nih.gov/REST/ndcstatus.json?ndc={ndc}&history=1&altpkg=0').get('ndcStatus',{})
                    with db.begin() as c: put(c,rxcache,'ndc',{'ndc':ndc,'data':result})
                    mappings.append({'ndc':ndc,**result})
                p['rxnorm_mappings']=mappings
                p['rxcuis']=sorted({m['rxcui'] for m in mappings if m.get('status')=='ACTIVE' and m.get('rxcui')})
                p['rxnorm_checked']=True
                with db.begin() as c: c.execute(products.update().where(products.c.id==last).values(data=p))
            except Exception as e: print(f'RxNorm failed {last}: {e}',flush=True)
