# Live Lexview verification

Verified on 21 October 2026 against the live HTTPS site with certificate verification enabled.

The default HTTP adapter was used, including the real search form, whole-law navigation,
ASP.NET temporal postbacks, Testo annotato selection, and the original HTML export link.

| Law | Listed entries | Downloaded | Annotation counts |
| --- | ---: | ---: | --- |
| 3/2001 | 2 | 2 | 70, 63 |
| 7/2024 | 14 | 14 | 139, 132, 131, 129, 117, 107, 102, 93, 86, 77, 74, 73, 48, 0 |

All 16 saved exports were independently rechecked against their requested temporal labels
and SHA-256 hashes. Dated entries match both interval boundaries where provided.
Each export contains a complete law; each law has distinct hashes for all its listed versions.
Both manifests report `complete: true`.

Law 3/2001 exposes only current and historical entries: the current version starts
7 March 2023, and the historical export covers 3 March 2001–23 June 2004. The site
does not expose every intermediate amendment as a separate entry for this law.

Law 7/2024 exposes 14 dated versions from 10 August 2024 through the current version
starting 8 August 2026. The original 10 August 2024 version contains no editorial
notes; the other 13 exports contain 48–139 notes each.

The scraper retrieves all entries exposed by the site; it does not reconstruct unavailable
intermediate versions. Ten automated tests also passed, including browser-fixture
navigation, ASP.NET field handling, export validation and wrong-version rejection.

Raw law exports and manifests remain under the ignored `downloads/live-http/` directory,
and are packaged in `/workspace/lexview-live-exports.zip`. They are not committed as source code.

Commands:

```sh
python lexview_html_versions.py --year 2001 --number 3 --output downloads/live-http
python lexview_html_versions.py --year 2024 --number 7 --output downloads/live-http
TEST_CHROMIUM=/usr/bin/chromium python -m unittest discover -s tests -v
```
