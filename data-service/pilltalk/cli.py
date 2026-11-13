import argparse, json
from datetime import date
from .db import engine, init, coverage
from .ingest import Fetcher, discover, ingest, enrich

def main():
    p=argparse.ArgumentParser(description='PillTalk full-source catalogue pipeline')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('index-c3pi'); sub.add_parser('init'); sub.add_parser('coverage')
    demo=sub.add_parser('discover-demo'); demo.add_argument('--per-drug',type=int,choices=range(1,101),default=50)
    d=sub.add_parser('discover'); d.add_argument('--restart',action='store_true'); d.add_argument('--since',type=date.fromisoformat)
    i=sub.add_parser('ingest'); i.add_argument('--retry-failed',action='store_true'); i.add_argument('--limit',type=int,help='Optional development limit; absent means all queued labels')
    r=sub.add_parser('enrich'); r.add_argument('--refresh',action='store_true')
    a=p.parse_args(); db=engine(); init(db)
    if a.command=='init': print('Schema ready')
    elif a.command=='coverage': print(json.dumps(coverage(db),indent=2))
    elif a.command=='index-c3pi':
        from .c3pi import attach
        attach(db,Fetcher())
    elif a.command=='discover-demo':
        from .demo import discover_demo
        discover_demo(db,Fetcher(),a.per_drug)
    elif a.command=='discover': discover(db,Fetcher(),a.restart,str(a.since) if a.since else None)
    elif a.command=='ingest': ingest(db,Fetcher(),a.retry_failed,a.limit)
    elif a.command=='enrich': enrich(db,Fetcher(),a.refresh)
if __name__=='__main__': main()
