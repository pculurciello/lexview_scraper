# Download all listed Lexview temporal versions as annotated HTML

Python 3.10+ and lxml are required for the default HTTP backend:

```sh
python -m pip install -r requirements.txt
python lexview_html_versions.py --year 2024 --number 7 --output downloads
```

The scraper searches the actual law number/year, opens **Visualizza legge intera**,
reads every link in `PageBody_PanelMultivigenza`, submits its ASP.NET postback
with current hidden fields and cookies, selects **Testo annotato**, and fetches
`PageBody_aHtml`. It follows the site's real export URL rather than inventing
historical query parameters. The default backend uses Python's standard HTTPS
client with system CA verification and inherited proxy configuration; no browser
is needed. Export bytes are preserved, including their original character encoding.
The converter in this repository handles the supplied Windows-1252 exports.

```sh
python lexview_html_versions.py --year 2001 --number 3 --output downloads
# Optionally bypass search with an actual law URL:
python lexview_html_versions.py --year 2024 --number 7 \
  --law-url 'https://lexview-int.regione.fvg.it/FontiNormative/xml/IndiceLex.aspx?anno=2024&legge=0007'
```

## Coverage and verification

“All versions” means all temporal entries **exposed by Lexview for the law**.
Law 3/2001 currently exposes only `TESTO VIGENTE` and `TESTO STORICO`, not a dated
entry for every intermediate amendment. Law 7/2024 exposes 14 dated entries.
The scraper does not reconstruct or claim to retrieve unavailable intermediate
versions. The dated export heading must confirm the selected version start date;
current/historical selections must confirm their status on the law page. Historical
exports use a “VIGENTE dal … al …” heading and must contain that validity interval. Error pages or a
latest-version response for an older dated selection are rejected.

Output is written under `downloads/LR_YEAR_NUMBER/`, one original HTML file per
entry. `manifest.json` records the complete list, individual success/failure,
source URL, heading, encoding, article/annotation counts and SHA-256 of each file.
`complete` becomes true only after every listed export has been saved.
Failures leave an incomplete manifest and a nonzero exit status. Error responses
are saved as `failure_NNN.html` for diagnosis. Rerunning refreshes the same output
files and manifest. One-second minimum request spacing is the default; adjust
`--delay` and `--timeout` if necessary. Styles/images are not mirrored.

## Optional legacy browser adapter

The original dropdown-based adapter is retained for compatible layouts:

```sh
python -m pip install -r requirements-scraper.txt
python -m playwright install chromium
python lexview_html_versions.py --backend browser --year 2001 --number 3
```

It supports `--version-selector`, `--display-selector`, `--annotation-label`,
`--headed`, and `--browser-executable`. These options apply only to the browser
adapter. The current live site uses historical **links**, so use the default HTTP
backend. Chromium must trust any organization proxy CA; TLS checks are never disabled.

## Tests

```sh
python -m unittest discover -s tests -v
# If using an existing system browser for the browser-fixture test:
TEST_CHROMIUM=/usr/bin/chromium python -m unittest discover -s tests -v
```

Browser tests use intercepted fixture responses, not live requests. The HTTP
adapter also has tests for ASP.NET fields, temporal links, export validation,
and annotation preservation. Live verification results are recorded separately
in `LIVE_VERIFICATION.md`.
