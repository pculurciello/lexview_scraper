#!/usr/bin/env python3
"""Download every listed temporal version of one FVG law as annotated HTML.

Uses the site's ASP.NET controls and original HTML download endpoint.
See SCRAPER.md for setup, selector overrides, and verification limitations.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from lxml import etree, html

BASE = 'https://lexview-int.regione.fvg.it/FontiNormative/xml/'
SEARCH = BASE + 'ricerca.aspx?db=DBC'
DATE_RE = re.compile(r'\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b')


def normalize(s):
    return re.sub(r'\s+', ' ', s).strip()


def date_key(label):
    match = DATE_RE.search(label)
    if match:
        day, month, year = map(int, match.groups())
        return datetime(year, month, day).date().isoformat()
    return None


def law_identity(url):
    q = {k.lower(): v for k, v in parse_qs(urlsplit(url).query).items()}
    try:
        return int(q['anno'][0]), int(q['legge'][0])
    except (KeyError, ValueError):
        return None


def version_options(options):
    """Retain actual version entries; never select an 'all versions' placeholder."""
    result = []
    seen = set()
    for option in options:
        label, value = normalize(option['label']), option['value']
        placeholder = re.search(r'seleziona|scegli|tutte le versioni|tutti i testi', label, re.I)
        version = DATE_RE.search(label) or re.search(r'originari|originale|vigente|storico', label, re.I)
        if not option.get('disabled') and not placeholder and version and value not in seen:
            result.append({'label': label, 'value': value, 'date': date_key(label)})
            seen.add(value)
    return result


def find_version_select(controls, selector=None):
    if selector:
        matches = [c for c in controls if c['selector'] == selector]
    else:
        matches = [c for c in controls if version_options(c['options'])
                   and c['id'] != 'PageBody_ddlVisualizza']
    if len(matches) != 1:
        raise ValueError('Cannot uniquely identify the temporal-version dropdown. '
                         'Inspect diagnostics.json and pass --version-selector (a CSS selector). '
                         'The script will not silently download only the latest version.')
    return matches[0]


def annotated_option(options):
    matches = [o for o in options if not o.get('disabled')
               and re.search(r'annotat|annotazion|con\s+(?:le\s+)?note|testo\s+e\s+note', o['label'], re.I)
               and not re.search(r'senza|solo\s+testo', o['label'], re.I)]
    if len(matches) != 1:
        raise ValueError('Cannot identify one annotated-text display option; '
                         'inspect diagnostics.json and set --annotation-label exactly.')
    return matches[0]


async def controls(page):
    return await page.locator('select').evaluate_all('''els => els.map((e, i) => ({
        id: e.id, name: e.name,
        selector: e.id ? '#' + CSS.escape(e.id) : 'select >> nth=' + i,
        options: Array.from(e.options).map(o => ({value:o.value,
            label:o.textContent.trim(), disabled:o.disabled}))
    }))''')


async def settle(page, delay):
    # ASP.NET AutoPostBack and UpdatePanel updates can start after select_option returns.
    await page.wait_for_timeout(max(delay, 0.5) * 1000)
    await page.wait_for_load_state('domcontentloaded')
    await page.wait_for_load_state('networkidle')


async def navigate(page, url, delay):
    response = await page.goto(url, wait_until='domcontentloaded')
    if response is not None and not response.ok:
        raise RuntimeError(f'HTTP {response.status} for {url}')
    await settle(page, delay)


async def select(page, selector, value, delay):
    await page.locator(selector).select_option(value=value)
    await settle(page, delay)
    if await page.locator(selector).input_value() != value:
        raise RuntimeError(f'Site did not retain selection {value!r} in {selector}')


async def locate_law(page, args):
    if args.law_url:
        if law_identity(args.law_url) != (args.year, args.number):
            raise ValueError('--law-url must identify the requested anno and legge.')
        await navigate(page, args.law_url, args.delay)
    else:
        await navigate(page, SEARCH, args.delay)
        await page.locator('input[id="PageBody_tbAnno"], input[name*="Anno"]').first.fill(str(args.year))
        number = page.locator('input[id="PageBody_tbNumero"], input[name*="Numero"]')
        if await number.count():
            await number.first.fill(str(args.number))
        # Unlike the supplied PDF scraper, retain repealed laws in search results.
        exclude = page.locator('#PageBody_lbAbrogati')
        if await exclude.count() and await exclude.get_attribute('type') == 'checkbox':
            await exclude.uncheck()
        submit = page.locator('input[type="submit"]')
        search = submit.filter(has_text=re.compile('Cerca|Ricerca', re.I))
        if await search.count() == 1:
            await search.click()
        elif await submit.count() == 1:
            await submit.click()
        else:
            # input labels are values, not textContent.
            button = page.locator('input[type="submit"][value*="Cerca" i], input[type="submit"][value*="Ricerca" i]')
            if await button.count() != 1:
                raise ValueError('Cannot identify search button; supply --law-url.')
            await button.click()
        await settle(page, args.delay)
        scope = page.locator('select[name="selectoroscopo"]')
        if await scope.count():
            await scope.select_option(label='tutti')
            await settle(page, args.delay)
        links = await page.locator('a[href]').evaluate_all('els => els.map(e => e.href)')
        matches = list(dict.fromkeys(u for u in links if law_identity(u) == (args.year, args.number)
                                    and re.search(r'(?:indicelex|xmllex)\.aspx', u, re.I)))
        if not matches:
            raise ValueError('Law not found in search results; supply its --law-url if necessary.')
        matches.sort(key=lambda u: ('indicelex.aspx' not in u.lower(), len(u)))
        await navigate(page, matches[0], args.delay)
    whole = page.locator('a[title*="legge intera" i]')
    if await whole.count():
        href = await whole.first.get_attribute('href')
        if href and not href.lower().startswith('javascript:'):
            await navigate(page, urljoin(page.url, href), args.delay)
        else:
            await whole.first.click()
            await settle(page, args.delay)
    return page.url


def document_html(raw, source_url, expected_date=None):
    """Save the law, not the ASP.NET form/navigation, with annotations intact."""
    doc = html.fromstring(raw)
    headers = doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," intestazione ")]')
    laws = doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," NIR ")]')
    if len(laws) != 1 or not laws[0].xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," articolo ")]'):
        raise ValueError('Response is not a complete law: expected one .NIR containing .articolo.')
    if not headers:
        raise ValueError('Missing .intestazione; cannot verify the returned version.')
    heading = normalize(' '.join(headers[0].itertext()))
    # Guard against a failed postback returning the latest version for every selection.
    if expected_date:
        dates = [datetime(int(y), int(m), int(d)).date().isoformat()
                 for d, m, y in DATE_RE.findall(heading)]
        if expected_date not in dates:
            raise ValueError(f'Returned heading does not confirm version date {expected_date}: {heading}')
    out = html.Element('html', lang='it')
    head = etree.SubElement(out, 'head')
    etree.SubElement(head, 'meta', charset='utf-8')
    etree.SubElement(head, 'base', href=source_url)
    etree.SubElement(head, 'title').text = heading
    body = etree.SubElement(out, 'body')
    body.append(headers[0])
    body.append(laws[0])
    return html.tostring(out, encoding='utf-8', doctype='<!DOCTYPE html>'), heading


async def diagnostics(page, directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'diagnostics.html').write_text(await page.content(), encoding='utf-8')
    links = await page.locator('a[href]').evaluate_all('els => els.map(e => ({text:e.textContent.trim(), title:e.title, href:e.href}))')
    (directory / 'diagnostics.json').write_text(json.dumps(
        {'url': page.url, 'selects': await controls(page), 'links': links}, ensure_ascii=False, indent=2), encoding='utf-8')


def proxy_config():
    # Playwright does not automatically use the inherited HTTP proxy variables.
    proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if not proxy:
        return None
    u = urlsplit(proxy)
    config = {'server': f'{u.scheme}://{u.hostname}:{u.port or 8080}'}
    if u.username or u.password:
        raise ValueError('Authenticated proxy URLs are not supported; use a browser proxy configuration.')
    return config


async def scrape(args):
    from playwright.async_api import async_playwright
    directory = args.output / f'LR_{args.year}_{args.number:04d}'
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {'year': args.year, 'number': args.number, 'complete': False,
                'started_at': datetime.now(timezone.utc).isoformat(), 'versions': []}
    manifest_path = directory / 'manifest.json'

    def save_manifest():
        temp = manifest_path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(manifest_path)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed, proxy=proxy_config(),
                                           executable_path=args.browser_executable)
        try:
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            page.set_default_timeout(args.timeout * 1000)
            page.set_default_navigation_timeout(args.timeout * 1000)
            try:
                whole_url = await locate_law(page, args)
                all_controls = await controls(page)
                if args.version_selector:
                    loc = page.locator(args.version_selector)
                    if await loc.count() != 1:
                        raise ValueError('--version-selector must match exactly one select.')
                    opts = await loc.locator('option').evaluate_all('es => es.map(e => ({value:e.value,label:e.textContent,disabled:e.disabled}))')
                    version_control = {'selector': args.version_selector, 'options': opts}
                else:
                    version_control = find_version_select(all_controls)
                versions = version_options(version_control['options'])
                if not versions:
                    raise ValueError('No temporal versions found.')
                manifest['listed_versions'] = versions
                manifest['law_url'] = whole_url
                save_manifest()
                print(f'Found {len(versions)} listed temporal versions.', flush=True)
                for index, version in enumerate(versions, 1):
                    entry = {**version, 'status': 'pending'}
                    manifest['versions'].append(entry)
                    save_manifest()
                    try:
                        await navigate(page, whole_url, args.delay)
                        await select(page, version_control['selector'], version['value'], args.delay)
                        display = page.locator(args.display_selector)
                        options = await display.locator('option').evaluate_all('es => es.map(e => ({label:e.textContent.trim(),value:e.value,disabled:e.disabled}))')
                        if args.annotation_label:
                            selected = [o for o in options if o['label'] == args.annotation_label]
                            if len(selected) != 1:
                                raise ValueError('--annotation-label did not match exactly one display option.')
                            annotation = selected[0]
                        else:
                            annotation = annotated_option(options)
                        await select(page, args.display_selector, annotation['value'], args.delay)
                        if await page.locator(version_control['selector']).input_value() != version['value']:
                            raise ValueError('Selecting annotations reset the temporal version.')
                        raw = await page.content()
                        payload, heading = document_html(raw, page.url, version['date'])
                        filename = f'{index:03d}_{version["date"] or "version"}.html'
                        path = directory / filename
                        temp = path.with_suffix('.html.tmp')
                        temp.write_bytes(payload)
                        temp.replace(path)
                        entry.update(status='downloaded', filename=filename, heading=heading,
                                     url=page.url, annotation_label=annotation['label'],
                                     sha256=hashlib.sha256(payload).hexdigest())
                        print(f'[{index}/{len(versions)}] {version["label"]} -> {filename}', flush=True)
                    except Exception as exc:
                        entry.update(status='failed', error=str(exc))
                        await diagnostics(page, directory / f'failure_{index:03d}')
                        print(f'Failed version {version["label"]}: {exc}', file=sys.stderr)
                    finally:
                        save_manifest()
                manifest['complete'] = all(e['status'] == 'downloaded' for e in manifest['versions'])
                save_manifest()
                if not manifest['complete']:
                    raise RuntimeError(f'Some versions failed; see {manifest_path}')
            except Exception as exc:
                manifest['error'] = str(exc)
                save_manifest()
                try:
                    await diagnostics(page, directory)
                except Exception as diagnostic_error:
                    print(f'Could not capture page diagnostics: {diagnostic_error}', file=sys.stderr)
                raise
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', required=True, type=int)
    parser.add_argument('--number', required=True, type=int)
    parser.add_argument('--output', type=Path, default=Path('html_leggi_regionali'))
    parser.add_argument('--law-url', help='Optional IndiceLex.aspx / XmlLex.aspx URL, bypassing search')
    parser.add_argument('--version-selector', help='CSS selector of temporal-version select')
    parser.add_argument('--display-selector', default='#PageBody_ddlVisualizza')
    parser.add_argument('--annotation-label', help='Exact annotated-text display option label')
    parser.add_argument('--delay', type=float, default=1.0, help='Minimum pause between actions (seconds)')
    parser.add_argument('--timeout', type=float, default=45.0)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--browser-executable', help='Optional installed Chromium executable path')
    parser.add_argument('--backend', choices=('http', 'browser'), default='http',
                        help='HTTP uses the live site postbacks and HTML export; browser is the legacy dropdown adapter')
    args = parser.parse_args()
    if args.year < 1963 or args.number < 1 or args.delay < 0 or args.timeout <= 0:
        parser.error('Use year >= 1963, number >= 1, delay >= 0, timeout > 0.')
    try:
        if args.backend == 'http':
            from lexview_http import scrape_http
            scrape_http(args)
        else:
            asyncio.run(scrape(args))
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f'Scrape incomplete: {exc}\n')


if __name__ == '__main__':
    main()
