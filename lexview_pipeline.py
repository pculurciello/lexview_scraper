#!/usr/bin/env python3
"""Resumable discovery → all listed temporal HTML exports → validated FVG XML."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from lxml import etree, html

from citation_index import build_index
from html_to_fvg import convert, decode_html
from legal_citations import destinations
from lexview_html_versions import SEARCH, law_identity, normalize
from lexview_http import Session, fields, scrape_http
from html_to_database_xml import convert_database
from ingestion_model import XSD as INGEST_XSD, rows_from_xml

ROOT = Path(__file__).resolve().parent


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def discover_year(session, year):
    session.request(SEARCH)
    data = fields(session.doc)
    for identifier in ('PageBody_cbAbrogate', 'PageBody_cbStorico'):
        for element in session.doc.xpath('//*[@id=$id]', id=identifier):
            data.pop(element.get('name'), None)
    year_field = session.doc.xpath('//*[@id="PageBody_tbAnno"]')[0]
    data[year_field.get('name')] = str(year)
    button = session.doc.xpath('//*[@id="PageBody_btnRicerca"]')[0]
    data[button.get('name')] = button.get('value')
    action = session.doc.xpath('//form[@id="myform"]/@action')[0]
    session.request(urljoin(session.url, action), data)
    counts = session.doc.xpath('//*[@id="PageBody_nDoc"]/text()')
    if not counts or not re.fullmatch(r'\s*\d+\s*', counts[0]):
        raise ValueError('Missing or unrecognized search result count.')
    expected = int(counts[0])
    all_options = session.doc.xpath('//select[@name="selectoroscopo"]/option[normalize-space(.)="tutti"]/@value')
    if all_options:
        session.request(urljoin(session.url, all_options[0]))
    laws = {}
    for link in session.doc.xpath('//*[@id="PageBody_gvListaRegolamenti"]//a[@href]'):
        href = urljoin(session.url, link.get('href'))
        identity = law_identity(href)
        if identity is None:
            continue
        y, number = identity
        if y != year:
            raise ValueError('Search returned a law from another year.')
        laws.setdefault(number, {'year': y, 'number': number, 'url': href})
    if len(laws) != expected:
        raise ValueError(f'Incomplete discovery: server reports {expected} laws, found {len(laws)} unique laws.')
    return [laws[number] for number in sorted(laws)]


def editorial_notes(source, source_url):
    doc = html.fromstring(decode_html(source.read_bytes())[0])
    notes = []
    for node in doc.xpath('//*[@class="ndr" or @class="ndr_titolo"]'):
        refs = []
        for link in node.xpath('.//a[@href]'):
            refs.extend({'destination': dest, 'status': status, 'href': link.get('href')}
                        for dest, status in destinations(normalize(node.text_content()), link.get('href'), source_url))
        notes.append({'category': 'editorial_note', 'text': normalize(node.text_content()),
                      'note_id': f'editorial{len(notes) + 1}',
                      'source_article': next((normalize(' '.join(a.xpath('./strong[1]//text()')))
                          for a in node.iterancestors() if a.get('class') == 'articolo'), None),
                      'references': refs})
    return notes


def run(args):
    args.output = args.output.resolve()
    xml_format = getattr(args, 'xml_format', 'light')
    if args.xsd is None:
        args.xsd = INGEST_XSD if xml_format == 'database' else ROOT / 'schemas/fvg-legal-light-1.0.xsd'
    args.xsd = args.xsd.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    schema = etree.XMLSchema(etree.parse(str(args.xsd)))
    processor = hashlib.sha256(b''.join(p.read_bytes() for p in (
        ROOT / 'html_to_fvg.py', ROOT / 'legal_citations.py', ROOT / 'lexview_pipeline.py',
        ROOT / 'html_to_database_xml.py', ROOT / 'ingestion_model.py', args.xsd))).hexdigest()
    catalog_path, state_path = args.output / 'catalog.json', args.output / 'state.json'
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {'years': {}}
    state = json.loads(state_path.read_text()) if state_path.exists() else {'versions': {}}
    failures = []
    session = Session(args.delay, args.timeout)
    laws = []
    for year in range(args.start_year, args.end_year + 1):
        key = str(year)
        saved = catalog['years'].get(key)
        if not args.offline and (args.refresh_catalog or not saved or saved.get('status') != 'discovered'):
            for attempt in range(args.attempts):
                try:
                    found = discover_year(session, year)
                    saved = catalog['years'][key] = {'status': 'discovered', 'laws': found}
                    break
                except Exception as exc:
                    saved = catalog['years'][key] = {'status': 'failed', 'error': str(exc)}
                    if attempt + 1 < args.attempts:
                        time.sleep(min(2 ** attempt, 30))
            write_json(catalog_path, catalog)
        if not saved or saved.get('status') != 'discovered':
            failures.append({'year': year, 'stage': 'discovery', 'error': (saved or {}).get('error', 'No cached catalogue')})
            continue
        laws.extend(saved['laws'])
        print(f'{year}: {len(saved["laws"])} discovered laws', flush=True)
    selected = laws[:args.limit] if args.limit else laws
    for law in selected:
        directory = f'LR_{law["year"]}_{law["number"]:04d}'
        manifest_path = args.output / 'html' / directory / 'manifest.json'
        if not args.offline:
            download_args = argparse.Namespace(year=law['year'], number=law['number'],
                law_url=law['url'], output=args.output / 'html', delay=args.delay,
                timeout=args.timeout, resume=not getattr(args, 'refresh_html', False))
            for attempt in range(args.attempts):
                try:
                    scrape_http(download_args, session=session)
                    break
                except Exception as exc:
                    if attempt + 1 == args.attempts:
                        failures.append({'law': directory, 'stage': 'download', 'error': str(exc)})
                    else:
                        time.sleep(min(2 ** attempt, 30))
        if not manifest_path.exists():
            failures.append({'law': directory, 'stage': 'download', 'error': 'No HTML manifest'})
            continue
        manifest = json.loads(manifest_path.read_text())
        if not manifest.get('complete'):
            failures.append({'law': directory, 'stage': 'download', 'error': 'Incomplete temporal manifest'})
        else:
            active = {directory + '/' + v['filename'] for v in manifest.get('versions', [])
                      if v.get('status') == 'downloaded'}
            obsolete = [key for key in state['versions'] if key.startswith(directory + '/') and key not in active]
            for key in obsolete:
                del state['versions'][key]
            if obsolete:
                write_json(state_path, state)
        for version in manifest.get('versions', []):
            if version.get('status') != 'downloaded':
                continue
            source = manifest_path.parent / version['filename']
            key = directory + '/' + version['filename']
            record = state['versions'].get(key, {})
            xml_path = args.output / ('xml-db' if xml_format == 'database' else 'xml') / directory / (source.stem + '.xml')
            try:
                source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                if source_hash != version['sha256']:
                    raise ValueError('HTML no longer matches the download manifest hash.')
                temporal_key = json.dumps({k:version.get(k) for k in ('label','validity_dates','retrieved_at')}, sort_keys=True)
                fingerprint = hashlib.sha256((source_hash + processor + temporal_key + xml_format).encode()).hexdigest()
                if (record.get('fingerprint') == fingerprint and record.get('status') == 'converted'
                    and xml_path.exists() and hashlib.sha256(xml_path.read_bytes()).hexdigest() == record.get('xml_sha256')):
                    schema.assertValid(etree.parse(str(xml_path)))
                    continue
                xml_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = xml_path.with_suffix('.xml.tmp')
                version_id = f'fvg:lr:{law["year"]}:{law["number"]}:version:{source_hash[:16]}'
                conversion = argparse.Namespace(input=source, output=temporary, xsd=args.xsd,
                    encoding=None, tipo='leggeRegionale', numero=str(law['number']), data=None,
                    titolo=None, ente='Regione autonoma Friuli Venezia Giulia', organo=None,
                    id=version_id, source_url=version['url'], mode='articolato',
                    premesse_xpath=None, dispositivo_xpath=None)
                if xml_format == 'database':
                    conversion.document_type, conversion.year = 'LR', law['year']
                    converted_root = convert_database(conversion, temporal=version)
                    issue_count = len(rows_from_xml(converted_root)['parser_issue'])
                else:
                    convert(conversion)
                    issue_count = 0
                temporary.replace(xml_path)
                notes_path = xml_path.with_suffix('.editorial.json')
                if xml_format == 'light':
                    write_json(notes_path, {'source_version': version_id,
                        'notes': editorial_notes(source, version['url'])})
                record = {'status': 'converted', 'fingerprint': fingerprint,
                    'xml': str(xml_path), 'xml_sha256': hashlib.sha256(xml_path.read_bytes()).hexdigest(),
                    'source_sha256': source_hash, 'temporal_label': version['label'],
                    'validity_dates': version.get('validity_dates', []), 'xml_format': xml_format,
                    'parser_issue_count': issue_count,
                    'editorial_notes': str(notes_path) if xml_format == 'light' else 'Embedded document_unit rows'}
            except Exception as exc:
                record = {'status': 'failed', 'error': str(exc), 'xml': str(xml_path)}
                failures.append({'version': key, 'stage': 'conversion', 'error': str(exc)})
            finally:
                state['versions'][key] = record
                write_json(state_path, state)
    try:
        build_index(state['versions'].values(), args.output / 'citations.sqlite')
    except Exception as exc:
        failures.append({'stage': 'citation_index', 'error': str(exc)})
    summary = {'start_year': args.start_year, 'end_year': args.end_year,
        'xml_format': xml_format, 'temporal_reconciliation_required': xml_format == 'database',
        'database_rows_loaded': False,
        'parser_issues_in_state': sum(v.get('parser_issue_count', 0) for v in state['versions'].values() if v.get('status') == 'converted'),
        'discovered_laws': len(laws), 'selected_laws': len(selected),
        'scope_complete': not failures and len(selected) == len(laws),
        'limited_run': bool(args.limit), 'failures': failures,
        'converted_versions_in_state': sum(v.get('status') == 'converted' for v in state['versions'].values()),
        'coverage': 'Only temporal entries exposed by Lexview; not unlisted intermediate consolidations.'}
    write_json(args.output / 'report.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-year', type=int, default=1964)
    parser.add_argument('--end-year', type=int, default=2026)
    parser.add_argument('--output', type=Path, default=Path('downloads/corpus'))
    parser.add_argument('--xml-format', choices=('database','light'), default='database')
    parser.add_argument('--xsd', type=Path, default=None, help='Override the schema for the selected XML format')
    parser.add_argument('--limit', type=int, help='Process at most this many discovered laws (sample run)')
    parser.add_argument('--offline', action='store_true', help='Convert cached catalog/HTML without network')
    parser.add_argument('--refresh-catalog', action='store_true')
    parser.add_argument('--refresh-html', action='store_true', help='Refresh exports instead of resuming the saved snapshot')
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--delay', type=float, default=1)
    parser.add_argument('--timeout', type=float, default=45)
    args = parser.parse_args()
    if not 1964 <= args.start_year <= args.end_year <= 2026 or args.attempts < 1 or args.delay < 0 or args.timeout <= 0 or (args.limit is not None and args.limit < 1):
        parser.error('Use years within 1964–2026, positive attempts/timeout/limit, and nonnegative delay.')
    raise SystemExit(run(args))


if __name__ == '__main__':
    main()
