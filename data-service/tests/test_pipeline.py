import os
os.environ['DATABASE_URL']='sqlite://'
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from pilltalk.parser import parse_spl
from pilltalk.db import init, labels, products, save_label, coverage
from pilltalk.ingest import discover, ingest
from pilltalk.api import create_app


def product(ndc, colour, imprint, route='C38288'):
    return f'''<manufacturedProduct><manufacturedProduct>
    <code code="{ndc}" codeSystem="2.16.840.1.113883.6.69"/><name>Test Drug</name><formCode displayName="TABLET"/>
    <ingredient classCode="ACTIB"><quantity><numerator value="5" unit="mg"/><denominator value="1"/></quantity><ingredientSubstance><name>Example ingredient</name></ingredientSubstance></ingredient>
    <asContent><containerPackagedProduct><code code="{ndc}-01" codeSystem="2.16.840.1.113883.6.69"/></containerPackagedProduct></asContent>
    </manufacturedProduct><consumedIn><substanceAdministration><routeCode code="{route}"/></substanceAdministration></consumedIn>
    <subjectOf><characteristic><code code="SPLCOLOR"/><value displayName="{colour}"/></characteristic></subjectOf>
    <subjectOf><characteristic><code code="SPLSHAPE"/><value displayName="ROUND"/></characteristic></subjectOf>
    <subjectOf><characteristic><code code="SPLIMPRINT"/><value>{imprint}</value></characteristic></subjectOf>
    </manufacturedProduct>'''

def xml(body=None, sid='test'):
    return f'''<document xmlns="urn:hl7-org:v3"><code code="34391-3"/><setId root="{sid}"/><versionNumber value="1"/>
    <author><assignedEntity><representedOrganization><name>Test labeler</name></representedOrganization></assignedEntity></author>
    <component>{body if body is not None else product('12345-001','WHITE','A;1')+product('12345-002','PINK','B;2')}</component></document>'''.encode()

@pytest.fixture
def db():
    e=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool); init(e); return e

def test_product_scope_and_strength():
    _,_,rows=parse_spl(xml())
    assert len(rows)==2
    assert rows[0]['colours']==['white'] and rows[1]['colours']==['pink']
    assert rows[0]['imprint_normalized']=='A1'
    assert rows[0]['ingredients'][0]['numerator']['value']=='5'
    assert rows[0]['package_ndcs']==['12345-001-01']

def test_excludes_nonoral_and_animal():
    assert not parse_spl(xml(product('12345-001','WHITE','X','C38216')))[2]
    assert not parse_spl(xml().replace(b'34391-3',b'34390-6'))[2]

def test_unknown_attributes_are_missing():
    _,_,rows=parse_spl(xml(product('12345-001','','')))
    assert rows[0]['colours']==[] and rows[0]['imprint'] is None

def test_version_replace_is_atomic_and_removes_old_products(db):
    sid,v,rows=parse_spl(xml()); save_label(db,sid,v,rows)
    save_label(db,sid,'2',rows[:1])
    assert coverage(db)['products_indexed']==1
    with pytest.raises(Exception): save_label(db,sid,'3',[rows[0],rows[0]])
    assert coverage(db)['products_indexed']==1

def test_search_strict_filters_no_fabricated_confidence(db):
    sid,v,rows=parse_spl(xml()); save_label(db,sid,v,rows)
    c=TestClient(create_app(db))
    r=c.post('/search',json={'colour':'white','shape':'circular'}).json()
    assert len(r['candidates'])==1
    assert r['candidates'][0]['imprint_normalized']=='A1'
    assert 'confidence' not in r['candidates'][0]
    assert c.post('/search',json={'colour':'blue'}).json()['candidates']==[]
    assert c.post('/search',json={'imprint':'A 1'}).json()['returned']==1
    assert c.post('/search',json={}).status_code==422
    assert c.post('/search',json={'imprint':'!!!'}).status_code==422
    assert c.post('/search',json={'free_text':'%'}).json()['returned']==0

class Fake:
    def __init__(self, tmp_path): self.cache=tmp_path; self.calls=[]
    def json(self,url):
        self.calls.append(url)
        page=int(url.split('page=')[-1])
        return {'data':[{'setid':f's{page}','published_date':'2026-11-13'}], 'metadata':{'total_pages':2}}
    def get(self,url):
        sid=url.split('/')[-1].split('.')[0]
        if sid=='s2': raise RuntimeError('Temporary failure')
        return xml(sid=sid)

def test_discovery_resume_and_failed_retry(db,tmp_path):
    f=Fake(tmp_path); discover(db,f); discover(db,f)
    assert len(f.calls)==2
    ingest(db,f)
    assert coverage(db)['label_status']=={'done':1,'failed':1}
    f.get=lambda url:xml(sid=url.split('/')[-1].split('.')[0])
    ingest(db,f,retry=True)
    assert coverage(db)['label_status']=={'done':2}
    assert coverage(db)['products_indexed']==4

def test_auth(db,monkeypatch):
    monkeypatch.setenv('API_KEY','test-secret'); c=TestClient(create_app(db))
    assert c.get('/coverage').status_code==401
    assert c.get('/coverage',headers={'x-api-key':'test-secret'}).status_code==200

def test_ndc_normalization_preserves_leading_zeros():
    from pilltalk.c3pi import ndc11
    assert ndc11('1234-5678-90')=='01234567890'
    assert ndc11('12345-678-90')=='12345067890'
    assert ndc11('12345-6789-0')=='12345678900'
    assert ndc11('1234567890') is None  # ambiguous unhyphenated 10-digit code

def test_postgres_colour_query_compiles():
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB, dialect
    stmt=select(products.c.data).where(cast(products.c.data,JSONB)['colours'].contains(['white']))
    compiled=str(stmt.compile(dialect=dialect()))
    assert '@>' in compiled and 'JSONB' in compiled
