# FVG presidential decree and Giunta deliberation scrapers

Two command-line entry points collect the full PDF labelled **Testo integrale**
and every discoverable **Allegato** link for the selected year/act. They share
HTTP collection and PDF verification code, without modifying source documents.

Python 3.10+, lxml and Poppler (`pdftotext`) are required. Cover OCR additionally
requires `pdftoppm`, Tesseract and an installed language model. The supplied
Giunta covers need OCR because their text layers are corrupt.

```sh
cd act_scrapers
python -m pip install -r requirements.txt
# Debian/Ubuntu:
sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-ita

python scrape_presidential_decrees.py --year 2026 --number 13 --output downloads/dpreg-2026-13
python scrape_deliberations.py --year 2024 --number 1506 --output downloads/dgr-2024-1506 --ocr-cover

# Omit --number to traverse the year's discoverable result pages:
python scrape_presidential_decrees.py --year 2026 --output downloads/dpreg-2026
python scrape_deliberations.py --year 2024 --output downloads/dgr-2024 --ocr-cover

# Independent second pass (also detects changed bytes):
python audit_downloads.py downloads/dgr-2024/manifest.json \
  --output downloads/dgr-2024/audit.json --ocr-cover
python -m unittest -v test_act_archive
```

## Files and checks

Files have deterministic names such as
`DPREG-2026-13-testo-integrale-SHA256PREFIX.pdf` and
`DGR-2024-1506-allegato-2-SHA256PREFIX.pdf`. Hash suffixes preserve distinct versions
and prevent different attachments with the same display name from overwriting one
another. Existing bytes are downloaded and checked again on reruns; this is not a
resume/checkpoint protocol. Each run rebuilds `manifest.json`; use separate output
directories to keep historical manifests. Temporary downloads use `.part` files.

The manifest records listing and final download URLs, link text, original server
filename when supplied, download time, SHA-256, local filename, PDF page count,
cover transcript, extracted act identity and all mismatch/review reasons. Listing
HTML snapshots are saved under `pages/` for diagnosing changed layouts. HTTP
sessions retain cookies; retryable failures have bounded retries and delays.
Defaults are one second between requests, three retries, 60-second request timeout,
100 MiB per response and 10,000 listing pages (`--delay`, `--retries`, `--max-pages`).
A reached page limit is recorded, never called complete.

The independent checks are:

1. Compare the publisher's link/card number and year with the downloaded cover's
   explicit act heading and date. References to older acts in recitals cannot
   establish identity. Also check the original server filename and URL basename
   whenever they encode a recognizable identity.
2. Compare a locally discoverable listing subject with the cover subject using
   normalized word overlap. Scores below 0.65 require review. Missing title
   evidence also requires review; a filename alone never proves a match.
3. For full texts, check for both recital/preamble text and a standalone
   `Decreta` or `Delibera` heading after the cover. This catches many accidentally
   downloaded operative-only extracts, but is not proof every motivation is present.
4. For annexes, compare any explicit `ALLEGATO ALLA DELIBERA N. ...` cover identity,
   date and standalone annex number. An annex without a parent identity remains
   `REVIEW_REQUIRED`, even if the publication page links it to the act.
5. Recompute SHA-256 during an independent audit, detecting file changes after
   collection. Identical bytes assigned to distinct acts/attachments are flagged.

Explicit contradictions produce `MISMATCH` and move the file to `quarantine/`.
Missing evidence, extraction failures and uncertain OCR produce review reasons.
`MATCH` means the implemented checks passed, not legal authenticity or verified
archive completeness. Non-PDF attachments (including ZIP, P7M and office files)
are retained, but require review; their internal contents are not parsed.
No signature or legal-effective-date verification is performed.

Exit status 0 means all discovered files passed the checks and no collection
issues arose. Status 2 means review or collection failure; inspect `manifest.json`.
No-download runs, unresolved link identities, HTML error pages, missing full text,
repeated listing content and failed requests are explicitly recorded. The scraper
does not infer that an act has no attachments merely because none were discovered.

## Discovery and current verification limits

Entry points:

- https://decreti.regione.fvg.it/elencoDecreti.asp
- https://www.regione.fvg.it/asp/delibere/layout2008_2.asp

The adapters use `anno`, `numero` (decrees) / `num` (deliberations), `cerca=true`
and `pag`. Explicit pagination links are followed, retaining filters. Direct
hrefs and simple JavaScript window-open URLs are recognized. Ambiguous downloads
are recorded rather than assigned to a neighbouring act. Full-text links and
annex links must be present in returned HTML. Arbitrary JavaScript-generated
results, POST-only searches, nested detail pages and authenticated downloads are
not supported by the present adapters. If query parameters change, use
`--start-url 'FILTERED_RESULTS_URL'` and inspect saved HTML. The year/number filter
still applies to parsed identities.

**Live end-to-end validation is pending.** The development environment's outbound
proxy returned 403 for both hosts. Public browser indexing confirmed decree link
labels, pagination and direct PDF links under `Storage/`, but the deliberation
page could not be inspected live. The discovery tests therefore use representative
synthetic HTML; they are not captured production fixtures. This version should be
piloted on one known act per site before a bulk run. A successful pilot must find
one full text, the expected attachments, and agree with the publication page.

PDF identity checks were additionally exercised against the ten previously
supplied decree/deliberation and annex PDFs. They distinguish explicit parent
headings from generic annex titles. Unit tests cover swapped PDFs, filename
conflicts, missing preambles, unassignable links, three attachments, JavaScript
URLs, pagination filters, HTML responses, quarantine and changed downloaded bytes.

## XML conversion

Use the downloaded main PDF and annexes with the converters in `fvg_decrees/`:

```sh
python ../fvg_decrees/pdf_decree_to_xml.py MAIN.pdf output.xml --annex ANNEX.pdf
python ../fvg_decrees/pdf_deliberation_to_xml.py MAIN.pdf output.xml \
  --ocr-cover --annex ANNEX1.pdf --annex ANNEX2.pdf
```

Inspect the manifest/audit and resolve quarantined files before ingestion.
