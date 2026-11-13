"""Queue a bounded set of DailyMed labels for the hackathon demo."""
from urllib.parse import urlencode

from .db import labels, put
from .ingest import BASE

CARDIOVASCULAR = (
    'lisinopril', 'losartan', 'amlodipine', 'metoprolol', 'carvedilol',
    'atorvastatin', 'rosuvastatin', 'furosemide', 'spironolactone', 'hydrochlorothiazide',
)
OTC = (
    'acetaminophen', 'ibuprofen', 'naproxen', 'aspirin', 'cetirizine',
    'loratadine', 'famotidine', 'omeprazole', 'loperamide', 'simethicone',
)


def discover_demo(db, fetch, per_drug):
    for names, doctype in ((CARDIOVASCULAR, '34391-3'), (OTC, '34390-5')):
        for name in names:
            params = urlencode({
                'drug_name': name, 'name_type': 'generic',
                'doctype': doctype, 'page': 1, 'pagesize': per_drug,
            })
            rows = fetch.json(f'{BASE}/spls.json?{params}')['data'] or []
            with db.begin() as connection:
                for row in rows:
                    sid = row['setid']
                    signature = row.get('published_date', '') + '|v' + str(row.get('spl_version', ''))
                    existing = connection.execute(
                        labels.select().where(labels.c.setid == sid)
                    ).mappings().first()
                    if existing is None or existing['published'] != signature:
                        put(connection, labels, 'setid', {
                            'setid': sid, 'published': signature,
                            'status': 'pending', 'error': None, 'version': None,
                        })
            print(f'{name}: {len(rows)} labels found', flush=True)
