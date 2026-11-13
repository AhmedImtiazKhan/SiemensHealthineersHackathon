"""Historical image candidates only; never substitute an archive image as verified current appearance."""
import re
from pathlib import PurePosixPath
import xlrd
from sqlalchemy import select
from .db import products
INDEX='https://data.lhncbc.nlm.nih.gov/public/Pills/directory_reference_images.xls'

def ndc11(value):
    parts=value.split('-')
    if len(parts)==3 and tuple(map(len,parts)) in {(5,3,2),(5,4,1),(4,4,2),(5,4,2)}:
        value=''.join(p.zfill(n) for p,n in zip(parts,(5,4,2)))
    return value if re.fullmatch(r'\d{11}',value) else None

def read_index(raw):
    sheet=xlrd.open_workbook(file_contents=raw).sheet_by_index(0)
    headers=sheet.row_values(0)
    if not {'ndc11','rxcui','RXBASE 300'}.issubset(headers): raise ValueError('Unexpected C3PI index format')
    mapping={}
    for i in range(1,sheet.nrows):
        row=dict(zip(headers,sheet.row_values(i))); key=ndc11(str(row['ndc11']))
        path=str(row['RXBASE 300']); rx=row['rxcui']
        if not key or not path or '..' in PurePosixPath(path).parts or path.startswith('/'): continue
        mapping.setdefault(key,[]).append({'ndc11':key,'archived_rxcui':str(int(rx)) if isinstance(rx,float) else str(rx),
            'archive_path':path,'name':row.get('name'),'source':'C3PI (discontinued 2018)',
            'verified_current_appearance':False})
    return mapping

def attach(db,fetch):
    mapping=read_index(fetch.get(INDEX,cached=True)); last=''; matched=0
    while True:
        with db.connect() as c: rows=c.execute(select(products).where(products.c.id>last).order_by(products.c.id).limit(100)).mappings().all()
        if not rows: break
        for row in rows:
            last=row['id']; p=dict(row['data']); candidates=[]
            for ndc in p['package_ndcs']: candidates.extend(mapping.get(ndc11(ndc),[]))
            p['c3pi_candidates']=candidates
            matched+=bool(candidates)
            with db.begin() as c: c.execute(products.update().where(products.c.id==last).values(data=p))
    print(f'{len(mapping)} archive NDCs indexed; {matched} product records with historical image candidates')
