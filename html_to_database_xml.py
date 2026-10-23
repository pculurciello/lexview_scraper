#!/usr/bin/env python3
"""HTML → database-aligned FVG ingestion XML 2.0 (no database writes)."""
import argparse
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lxml import etree, html

from html_to_fvg import MONTHS, classes, decode_html, norm
from ingestion_model import ORDER, XSD, validate, xml_from_rows
from legal_citations import destinations

PARSER_VERSION = '2.0.0'
ACT_CODES = {'leggeRegionale':'LR', 'deliberazioneGiunta':'DGR', 'regolamento':'REG',
             'decretoPresidente':'DPREG', 'decretoDirettore':'DD', 'decretoAltro':'DECRETO', 'altro':'OTHER'}
UNIT_CLASSES = {
 'capo':'CHAPTER', 'articolo':'ARTICLE', 'comma':'PARAGRAPH', 'el':'LETTER', 'en':'LIST_POINT',
 'rubrica':'HEADING', 'num_capo':'HEADING', 'rubrica_capo':'HEADING',
 'ndr':'EDITORIAL_NOTE', 'ndr_titolo':'EDITORIAL_NOTE', 'note':'EDITORIAL_HEADING',
 'note_titolo':'EDITORIAL_HEADING', 'decorazione':'NOTE_MARKER',
 'formulainiziale':'PREAMBLE', 'formulafinale':'CLOSING_FORMULA', 'conclusione':'CONCLUSION',
 'premesse':'PREAMBLE', 'dispositivo':'OPERATIVE_SECTION', 'punto':'OPERATIVE_POINT',
 'mod':'AMENDMENT_TEXT', 'virgolette':'QUOTED_TEXT', 'intestazione':'HEADER'}
PROVISION_TOKENS = {'CHAPTER':'CAP', 'ARTICLE':'ART', 'PARAGRAPH':'COM', 'LETTER':'LET',
                    'OPERATIVE_POINT':'PUNTO'}


def uid(key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'fvg-legal-ingest:' + key))


def now():
    return datetime.now(timezone.utc).isoformat()


def document_key(jurisdiction, code, year, number):
    return f'{jurisdiction}:{code}:{year}:{number}'


def parsed_target(dest):
    result = dict(dest)
    act = dest.get('atto', '')
    match = re.fullmatch(r'fvg:lr:(\d{4}):(\d+)', act)
    if match:
        key = document_key('IT-FVG', 'LR', int(match[1]), str(int(match[2])))
        result['target_document_key'] = key
        parts = [key]
        for column, prefix in [('articolo','ART'), ('comma','COM'), ('lettera','LET'), ('punto','PUNTO')]:
            if dest.get(column):
                parts.append(prefix + re.sub(r'\s+', '', dest[column]))
        if len(parts) > 1:
            result['target_provision_key'] = ':'.join(parts)
    elif act:
        result['target_external_identifier'] = act
    return result


def snapshot_scope(temporal, heading):
    dates = list((temporal or {}).get('validity_dates') or [])
    if not dates:
        match = re.search(r'VIGENTE\s+dal\s+(\d{1,2}/\d{1,2}/\d{4})(?:\s+al\s+(\d{1,2}/\d{1,2}/\d{4}))?', heading, re.I)
        if match:
            dates = [datetime.strptime(d, '%d/%m/%Y').date().isoformat() for d in match.groups() if d]
    scope = {'basis': 'DOCUMENT_SNAPSHOT', 'provision_validity_inferred': False,
             'label': (temporal or {}).get('label'), 'known': bool(dates)}
    if dates:
        scope['start'] = date.fromisoformat(dates[0]).isoformat()
    if len(dates) > 1:
        end = date.fromisoformat(dates[1])
        if end < date.fromisoformat(dates[0]):
            raise ValueError('Snapshot interval is reversed')
        scope.update(end_inclusive=end.isoformat(), end_exclusive=(end + timedelta(days=1)).isoformat())
    return scope


class Ingestion:
    def __init__(self, args, temporal=None):
        self.args = args
        self.rows = {table: [] for table in ORDER}
        self.started = now()
        self.run_id = str(uuid.uuid4())
        self.raw = args.input.read_bytes()
        text, self.encoding = decode_html(self.raw, getattr(args, 'encoding', None))
        self.doc = html.fromstring(text, parser=html.HTMLParser(no_network=True))
        self.tree = self.doc.getroottree()
        headers = self.doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," intestazione ")]')
        titles = self.doc.xpath('//head/title')
        heading_node = headers[0] if headers else (titles[0] if titles else self.doc)
        self.heading = norm(' '.join(heading_node.itertext()))
        self.scope = snapshot_scope(temporal, self.heading)
        code = getattr(args, 'document_type', None)
        if not code:
            code = next((v for k, v in ACT_CODES.items() if k == getattr(args, 'tipo', None)), None)
        if not code:
            for pattern, candidate in [('legge regionale','LR'), ('deliberazione','DGR'), ('regolamento','REG'),
                                       ('decreto del presidente','DPREG'), ('decreto del direttore','DD'), ('decreto','DECRETO')]:
                if self.heading.lower().startswith(pattern):
                    code = candidate
                    break
        if not code:
            raise ValueError('Specify --document-type; the act type cannot be inferred reliably')
        issued = getattr(args, 'data', None)
        if not issued:
            m = re.search(r'\b(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})\b', self.heading, re.I)
            if m:
                issued = date(int(m[3]), MONTHS[m[2].lower()], int(m[1])).isoformat()
        if issued:
            issued = date.fromisoformat(issued).isoformat()
        year = getattr(args, 'year', None) or (int(issued[:4]) if issued else None)
        number = getattr(args, 'numero', None)
        if number is None:
            m = re.search(r'\bn\.\s*([\w/.-]+)', self.heading, re.I)
            number = m[1].rstrip('.') if m else None
        if year is None or number is None:
            raise ValueError('Stable act identity requires --year and --numero (or an identifiable heading)')
        number = str(int(number)) if str(number).isdigit() else str(number)
        if issued and int(issued[:4]) != year:
            raise ValueError('Act year conflicts with promulgation date')
        self.key = document_key(getattr(args, 'jurisdiction', 'IT-FVG'), code, year, number)
        self.doc_id = uid(self.key)
        self.source_url = getattr(args, 'source_url', None) or args.input.resolve().as_uri()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.rep_id = uid(self.doc_id + ':HTML:' + self.source_url + ':' + self.digest)
        title = getattr(args, 'titolo', None)
        if not title and headers:
            strong = headers[0].xpath('.//strong | .//b')
            title = norm(' '.join(strong[0].itertext())) if strong else None
        title = title or (norm(' '.join(titles[0].itertext())) if titles else None)
        self.rows['source_document'].append(dict(id=self.doc_id,
            jurisdiction=getattr(args, 'jurisdiction', 'IT-FVG'), document_type=code,
            number=number, year=year, document_date=issued, title=title,
            issuer=getattr(args, 'ente', 'Regione autonoma Friuli Venezia Giulia'),
            official_uri=getattr(args, 'official_uri', None),
            metadata={'canonical_key': self.key, 'organ': getattr(args, 'organo', None)}))
        retrieved = getattr(args, 'retrieved_at', None) or (temporal or {}).get('retrieved_at')
        self.rows['source_representation'].append(dict(id=self.rep_id, source_document_id=self.doc_id,
            source_system=getattr(args, 'source_system', 'LEXVIEW'), format='HTML', source_url=self.source_url,
            retrieved_at=retrieved, content_hash=self.digest, local_object_key=str(args.input.resolve()),
            media_type='text/html', character_encoding=self.encoding, is_official=False, legal_authority=False,
            raw_metadata={'temporal_scope': self.scope, 'raw_artifact_preserved': True,
                          'authority_assessment':'NOT_ESTABLISHED_FROM_HTML',
                          'retrieval_time_known': retrieved is not None}))
        self.provision_keys = {}
        self.ordinals = {}
        if not retrieved:
            self.issue('UNKNOWN_RETRIEVAL_TIME', '', context={'policy':'Do not substitute conversion time for acquisition time'})
        self.issue('PROVISION_VALIDITY_NOT_INFERRED', self.heading,
            context={'snapshot_scope': self.scope, 'policy':'Keep document_unit.provision_version_id NULL until temporal reconciliation'})
        if not issued:
            self.issue('UNKNOWN_ACT_DATE', self.heading)

    def issue(self, kind, text, unit=None, locator=None, context=None):
        self.rows['parser_issue'].append(dict(id=uid(self.run_id + ':issue:' + str(len(self.rows['parser_issue']))),
            ingestion_run_id=self.run_id, document_unit_id=unit, issue_type=kind,
            candidate_text=text, source_locator=locator or {}, parser_context=context or {}, resolution_status='OPEN'))

    def unit_kind(self, node, parent_kind=None):
        if node.tag == 'body':
            return 'DOCUMENT'
        for cls, kind in UNIT_CLASSES.items():
            if cls in classes(node):
                return kind
        if node.tag == 'p':
            return 'RECITAL' if parent_kind in ('PREAMBLE', 'RECITAL') else 'TEXT'
        if node.tag == 'div' and norm(node.text or ''):
            return 'TEXT'
        return {'table':'TABLE', 'tr':'ROW', 'td':'CELL', 'th':'CELL', 'li':'LIST_POINT'}.get(node.tag)

    def own_content(self, node, kind):
        chunks, links = [], []
        def collect(e):
            if e is not node and self.unit_kind(e, kind):
                return
            if e.tag in ('script', 'style') or not isinstance(e.tag, str):
                return
            if e.text:
                chunks.append(e.text)
            for child in e:
                if child.tag == 'br':
                    chunks.append('\n')
                collect(child)
                if child.tail:
                    chunks.append(child.tail)
            if e.tag == 'a' and e.get('href') and norm(' '.join(e.itertext())):
                links.append((norm(' '.join(e.itertext())), e.get('href')))
        collect(node)
        return ''.join(chunks), links

    def visit(self, node, parent=None, provision=None, parent_kind=None, context='BODY'):
        if not isinstance(node.tag, str) or node.tag in ('script', 'style'):
            return
        kind = self.unit_kind(node, parent_kind)
        if classes(node) & {'mod', 'virgolette'}:
            context = 'QUOTED_OR_AMENDMENT_TEXT'
        if kind in ('EDITORIAL_NOTE', 'EDITORIAL_HEADING', 'NOTE_MARKER'):
            context = 'EDITORIAL'
        if kind:
            raw, links = self.own_content(node, kind)
            xpath = self.tree.getpath(node)
            unit_id = uid(self.rep_id + ':' + xpath)
            ordinal = self.ordinals.get(parent, 0) + 1
            self.ordinals[parent] = ordinal
            labels = node.xpath('./strong[1] | ./b[1]')
            printed = norm(' '.join(labels[0].itertext())) if labels else norm(node.text or '')
            patterns = {'ARTICLE':r'Art(?:icolo|\.)?\s*(\d+(?:\s*[a-z]+)?|unico)\s*$',
                        'PARAGRAPH':r'(\d+(?:\s*[a-z]+)?)\s*\.', 'LETTER':r'([a-z]+(?:\s*-?\s*[a-z]+)?)\s*\)',
                        'OPERATIVE_POINT':r'(\d+)\s*[.)]', 'CHAPTER':r'CAPO\s+([IVXLCDM]+)'}
            label = None
            match = re.match(patterns[kind], printed, re.I) if kind in patterns else None
            if kind == 'CHAPTER' and not match:
                match = re.match(patterns[kind], norm(' '.join(node.itertext())), re.I)
            if match:
                label = norm(match[1])
            locator = {'html_xpath': xpath, 'context': context}
            previous = node.getprevious()
            if previous is not None and previous.tag == 'a' and previous.get('name'):
                locator['html_anchor'] = previous.get('name')
            if kind in PROVISION_TOKENS and context == 'BODY':
                if label:
                    base = self.key if kind in ('ARTICLE', 'CHAPTER') or not provision else provision['canonical_key']
                    canonical = base + ':' + PROVISION_TOKENS[kind] + re.sub(r'\s+', '', label)
                    if canonical in self.provision_keys:
                        self.issue('AMBIGUOUS_PROVISION_IDENTITY', printed, unit_id, locator)
                    else:
                        pi = dict(id=uid(canonical), source_document_id=self.doc_id,
                            parent_provision_id=provision['id'] if provision else None,
                            unit_type=kind, unit_label=label, canonical_key=canonical, ordinal=ordinal)
                        self.rows['provision_identity'].append(pi)
                        self.provision_keys[canonical] = pi
                        provision = pi
                        locator['candidate_provision_identity_id'] = pi['id']
                        locator['candidate_provision_key'] = canonical
                else:
                    self.issue('UNNUMBERED_OR_UNRECOGNIZED_PROVISION', printed, unit_id, locator,
                        {'policy':'Physical unit retained; no positional stable legal identity invented'})
            row = dict(id=unit_id, source_representation_id=self.rep_id, parent_unit_id=parent,
                unit_type=kind, unit_label=label, ordinal=ordinal, raw_text=raw,
                normalized_text=norm(raw), source_locator=locator, review_status='UNREVIEWED')
            if kind == 'RECITAL':
                formula = re.match(r'\s*(VIST[OAIE]|CONSIDERAT[OAIE]|RITENUT[OAIE]|PRESO ATTO)', norm(raw), re.I)
                row['recital_type'] = formula[1].upper() if formula else 'UNKNOWN'
                if not formula:
                    self.issue('UNKNOWN_RECITAL_FORMULA', norm(raw), unit_id, locator)
            self.rows['document_unit'].append(row)
            for quoted, href in links:
                # Decorative local note markers are navigation, not legal citations.
                if kind == 'NOTE_MARKER' or (href.startswith('#') and re.search(r'-not\d+', href)):
                    continue
                for target, parsed_status in destinations(quoted, href, self.source_url):
                    parsed = parsed_target(target)
                    parsed.update(original_href=href, context=context,
                                  link_text=quoted,
                                  link_parse_status='PARTIAL' if parsed_status != 'risolta' else 'PARSED')
                    self.rows['citation'].append(dict(id=uid(unit_id + ':citation:' + str(len(self.rows['citation']))),
                        citing_document_unit_id=unit_id, cited_text=norm(raw) if context == 'EDITORIAL' else quoted, parsed_reference=parsed,
                        resolution_status='UNRESOLVED', extraction_method='HTML_LINK'))
            parent, parent_kind = unit_id, kind
        for child in node:
            self.visit(child, parent, provision, parent_kind, context)

    def build(self):
        body = self.doc.xpath('//body')
        body = body[0] if body else self.doc
        self.visit(body)
        self.rows['ingestion_run'].append(dict(id=self.run_id, source_representation_id=self.rep_id,
            parser_name='html_to_database_xml', parser_version=PARSER_VERSION,
            status='COMPLETED_WITH_ISSUES' if self.rows['parser_issue'] else 'COMPLETED',
            started_at=self.started, finished_at=now(),
            statistics={'counts':{**{t:len(r) for t,r in self.rows.items()}, 'ingestion_run':1},
                        'citation_scope':'HTML_LINKS_ONLY', 'provision_versions_materialized':False,
                        'semantic_relations_inferred':False}))
        return xml_from_rows(self.rows)


def convert_database(args, temporal=None):
    builder = Ingestion(args, temporal)
    root = builder.build()
    validate(root, args.xsd)
    if args.output.resolve() in (args.input.resolve(), Path(args.xsd).resolve()):
        raise ValueError('Output must differ from input and schema')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + '.writing')
    temporary.write_bytes(etree.tostring(root, encoding='UTF-8', xml_declaration=True, pretty_print=True))
    temporary.replace(args.output)
    return root


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--xsd', type=Path, default=XSD)
    p.add_argument('--document-type', help='Extensible DB vocabulary: LR, DGR, REG, DPREG, DD, ...')
    p.add_argument('--year', type=int)
    for name in ('numero','data','titolo','organo','source-url','encoding','retrieved-at','official-uri'):
        p.add_argument('--' + name)
    p.add_argument('--jurisdiction', default='IT-FVG')
    p.add_argument('--ente', default='Regione autonoma Friuli Venezia Giulia')
    p.add_argument('--source-system', default='LEXVIEW')
    args = p.parse_args()
    try:
        root = convert_database(args)
        print(f'Validated database ingestion XML: {args.output}')
    except (ValueError, OSError, etree.LxmlError) as exc:
        p.exit(1, f'Conversion failed: {exc}\n')


if __name__ == '__main__':
    main()
