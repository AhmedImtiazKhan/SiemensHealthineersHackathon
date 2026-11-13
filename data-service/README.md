# PillTalk data service

A runnable Python service for importing the full available DailyMed catalogue, selecting human solid oral products, and searching product appearance in PostgreSQL. No patient-list restriction or default product limit.

## Hackathon demo: cardiovascular and OTC oral medicines

Stop any full import with Ctrl+C in its terminal. Run the commands below from
`data-service` with Docker Desktop running. The separate Compose project
`pilltalk-demo` has its own Postgres volume, and port 8001 lets the original API
stay on port 8000. Use a fresh demo project for this scope.

```bash
export PILLTALK_PORT=8001
docker compose -p pilltalk-demo up -d db
docker compose -p pilltalk-demo build api
docker compose -p pilltalk-demo run --rm api pilltalk discover-demo
docker compose -p pilltalk-demo run --rm api pilltalk ingest --retry-failed
docker compose -p pilltalk-demo up -d api
curl -fsS http://localhost:8001/coverage
```

Open http://localhost:8001/docs to test POST /search. The importer initializes
the schema automatically. Discovery queries 10 cardiovascular generics in human
prescription labels and 10 common generics in human OTC labels, taking up to 50
labels per generic. That is up to 1,000 labels before deduplication and oral dosage
form filtering. A label may yield zero or multiple product records. This is a
demo sample. Keep the same project name and port when rerunning commands.
The general scope text in /coverage describes the parser rules.

RxNorm and C3PI enrichment can be run later using the same Compose project.

## Full catalogue (Docker Desktop)

Open a terminal in this folder. Docker Desktop must be running.

```bash
cp .env.example .env
docker compose up -d db
docker compose build api
docker compose run --rm api pilltalk init
docker compose up -d api
```

API documentation: http://localhost:8000/docs

Run the full pipeline in this order:

```bash
# 1. Discover ALL pages of labels and persist a resumable download queue.
docker compose run --rm api pilltalk discover

# 2. Fetch and parse ALL queued labels. No default cap.
docker compose run --rm api pilltalk ingest --retry-failed

# 3. Resolve package NDCs against RxNorm. Save exact source status and mappings.
docker compose run --rm api pilltalk enrich

# 4. Download the small C3PI reference index and link historical image candidates.
docker compose run --rm api pilltalk index-c3pi

# 5. Report actual coverage, including failures and missing attributes.
docker compose run --rm api pilltalk coverage
```

These commands run in the foreground. Leave the terminal open. If interrupted, rerun the same command: completed labels are skipped and a label replacement is transactional. Discovery commits its queue and next-page checkpoint together. Run only one importer at a time. An import failure is retained for retry, never counted as successful. Full ingestion requires many upstream requests and may take hours; no full-catalogue runtime or row count is claimed here. Data volumes persist when containers stop. Do not use `docker compose down -v` unless you intend to delete the database.

API uses the imported database during calls; it does not fetch upstream labels during search. Before ingestion, search correctly returns no candidates.

## Search contract for Person 3

```bash
curl -X POST http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"colour":"white","shape":"round","imprint":null,"free_text":null,"limit":5}'
```

Response: `candidates`, `returned`, `query_ms`, `requires_confirmation`, and a ranking description. Candidate records include product ID, SETID, product NDC, package NDCs, labeler, dosage form, ingredient strengths, colours, shape, raw imprint, size with unit, SPL score code, image URLs, and source link. After enrichment, they also contain `rxcuis`, `rxnorm_mappings` and `c3pi_candidates`.

Missing attributes stay missing. SQL filters enforce supplied colour, shape and imprint. Text similarity ranks remaining candidates using PostgreSQL pg_trgm; it is not a probability. No fabricated confidence value is returned. `limit` is 1–50. Blank searches and imprints containing no letters/digits are rejected. Imprints are compared without punctuation/spacing; original values are retained. This is candidate retrieval for staff confirmation, not autonomous drug identification.

Other endpoints:
- `GET /health`: database connectivity.
- `GET /coverage`: product and attribute coverage, RxNorm coverage, label statuses, C3PI candidate count.

API is bound to localhost by default. Set `API_KEY` in `.env` before shared deployment and send `X-API-Key` on requests. The docs page is public but API operations enforce the key. Put a TLS gateway in front before exposing outside your machine. Database password default is for local development only; use a URL-safe password or encode it in DATABASE_URL when configuring externally. No patient data is stored here.

## What “full” means

Discovery scans all DailyMed label pages. The parser includes human prescription/OTC labels with explicit ORAL route and dosage forms containing TABLET, CAPSULE, PELLET, LOZENGE or TROCHE. Other forms are excluded. Missing route/form data is excluded rather than inferred. This covers the configured solid-oral scope, not all global medicines and not a German product catalogue.

Product identity is `(SETID, product NDC)`. Multiple strengths stay separate. Multiple labels for the same NDC can exist; counts are catalogue records, not guaranteed distinct commercial products. The `active` database flag means included in this local catalogue, NOT currently marketed. Labeler is explicitly called labeler: a repackager is not necessarily the physical manufacturer.

SPLSCORE is retained as the source code/value; do not treat `1` as one score line. See the source vocabulary before making patient-readable statements. Appearance, images and RxNorm mappings are not universally present. Packaging marketing end dates and withdrawn-label reconciliation are not yet implemented; do not advertise this as a current-market inventory. Coverage reports source processing completeness, not clinical completeness.

## C3PI images

`index-c3pi` imports the actual official reference XLS structure and matches only normalized package NDCs. It does not invent image URLs or use name-only matching. It adds archived metadata and the exact path inside the C3PI reference archive. Historical matches are marked `verified_current_appearance: false` and kept separate from DailyMed `image_urls`.

To serve C3PI photos, download the optional reference ZIP, extract the indexed paths, inspect/validate the historical image against the product, then host approved images in object storage. That separate image-hosting/verification step is not automated in this version. The entire photo archive is not necessary for database ingestion. The 133,000-image training collection is not needed. C3PI ended in 2018; identifiers are not maintained.

DailyMed `image_urls` are generated only from product-scoped SPLIMAGE references. General label illustrations, graphs, logos and package pictures are not substituted for pill photos. Image link availability still needs validation when serving the frontend.

## RxNorm

The `enrich` command queries exact package NDCs with `altpkg=0`; no alternative packaging or fuzzy-name guessing. It stores the complete returned status and only puts mappings with NDC status ACTIVE into `rxcuis`. No match remains unmapped. This groups matching clinical/brand concepts, not automatically all strengths of an ingredient. Ingredient UNIIs are retained to support substance grouping separately. Converting SBD concepts to SCD and multi-ingredient grouping is a further extension.

## Refresh

```bash
# Refresh discovery from page one; queues new/changed date or version signatures.
docker compose run --rm api pilltalk discover --restart
docker compose run --rm api pilltalk ingest --retry-failed
docker compose run --rm api pilltalk enrich
docker compose run --rm api pilltalk index-c3pi

# Or discover labels published on/after a date (inclusive):
docker compose run --rm api pilltalk discover --since 2026-11-13 --restart

# Recheck cached RxNorm mappings explicitly:
docker compose run --rm api pilltalk enrich --refresh
```

A changed label replaces its old product rows, including derived enrichment; rerun enrichment and image indexing afterwards. Raw fetched SPL XML is kept in `data/cache` under a SHA-256 filename for audit. Full discovery is page-based, not an upstream snapshot: rerun discovery after a long initial import to catch source changes. Withdrawals need separate reconciliation; this version never silently deletes labels missing from a later listing.

## Local Python development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/pilltalk'
pilltalk init
uvicorn pilltalk.api:app --reload
pytest -q
```

Postgres user must be able to install `pg_trgm` during init, or an administrator must install it first. pgvector is intentionally not required: no embeddings exist in this version. Structured filters and trigram similarity are implemented. Add pgvector with a defined embedding model/dimension when semantic/image retrieval is actually introduced.

## Validation performed

- Automated tests cover product-boundary isolation, exclusions, missing attributes, transactional version replacement, search filters, retry/resume, API key enforcement and NDC normalization.
- Parsed a real DailyMed lisinopril label (SETID and version in `tests/live-check-results.json`) and retrieved it by colour/imprint through the API.
- Read the real C3PI reference XLS: 4,236 distinct normalized package NDCs. This is an archive-index figure, not a PillTalk product-coverage figure.
- Tests ran against SQLite. PostgreSQL query compilation is checked; PostgreSQL server/Docker integration must be run on your machine. No full catalogue import has been executed here.

## Sources

- DailyMed API: https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm
- Label pagination: https://dailymed.nlm.nih.gov/dailymed/webservices-help/v2/spls_api.cfm
- Label XML: https://dailymed.nlm.nih.gov/dailymed/webservices-help/v2/spls_setid_api.cfm
- RxNorm NDC status: https://lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.getNDCStatus.html
- C3PI catalogue and usage notice: https://catalog.data.gov/dataset/computational-photography-project-for-pill-identification-c3pi
- Reference image index: https://data.lhncbc.nlm.nih.gov/public/Pills/directory_reference_images.xls
- Optional reference ZIP: https://data.lhncbc.nlm.nih.gov/public/Pills/rximage.zip

Keep source provenance and applicable source attribution with redistributed datasets. Code and tests contain no patient information or API credentials.
