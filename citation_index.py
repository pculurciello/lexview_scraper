"""Cross-document citation index. Incoming references remain outside the light XSD."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from lxml import etree
from ingestion_model import NS as INGEST_NS, validate as validate_ingestion

NS = {'f': 'urn:fvg:legal:light:1.0'}


def build_index(records, database):
    database = Path(database)
    temporary = database.with_suffix('.sqlite.tmp')
    if temporary.exists():
        temporary.unlink()
    with sqlite3.connect(temporary) as db:
        db.execute('''CREATE TABLE citations (
          source_version TEXT, source_law TEXT, source_xml TEXT, citation_id TEXT,
          source_anchor TEXT, target_law TEXT, article TEXT, comma TEXT, letter TEXT,
          uri TEXT, quote TEXT, status TEXT, context TEXT, PRIMARY KEY(source_xml,citation_id))''')
        for record in records:
            if record.get('status') != 'converted':
                continue
            path = Path(record['xml'])
            if record.get('xml_sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != record['xml_sha256']:
                raise ValueError(f'XML changed since validated conversion: {path}')
            root = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
            if etree.QName(root).namespace == INGEST_NS:
                data = validate_ingestion(root)
                source = data['source_document'][0]
                source_law = source['metadata']['canonical_key']
                for citation in data['citation']:
                    target = citation['parsed_reference']
                    db.execute('INSERT INTO citations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                        data['source_representation'][0]['id'], source_law, str(path), citation['id'],
                        citation.get('citing_document_unit_id') or citation.get('citing_provision_version_id'),
                        target.get('target_document_key') or target.get('target_external_identifier'),
                        target.get('articolo'), target.get('comma'), target.get('lettera'), target.get('uri'),
                        citation['cited_text'], citation['resolution_status'], target.get('context')))
                continue
            year = root.findtext('f:metadati/f:data', namespaces=NS)[:4]
            number = root.findtext('f:metadati/f:numero', namespaces=NS)
            source_law = f'fvg:lr:{year}:{int(number)}'
            for citation in root.findall('f:citazioni/f:citazione', NS):
                dest = citation.find('f:destinazione', NS)
                attrs = dest.attrib if dest is not None else {}
                db.execute('INSERT INTO citations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    root.get('id'), source_law, str(path), citation.get('id'), citation.get('sorgente'),
                    attrs.get('atto'), attrs.get('articolo'), attrs.get('comma'), attrs.get('lettera'),
                    attrs.get('uri'), citation.findtext('f:testoCitato', namespaces=NS), citation.get('stato'), 'BODY'))
        db.execute('CREATE INDEX target_lookup ON citations(target_law)')
    temporary.replace(database)


def incoming(database, target, context=None):
    parts = target.split(':')
    alternate = f'IT-FVG:LR:{parts[2]}:{parts[3]}' if len(parts) == 4 and parts[:2] == ['fvg','lr'] else target
    with sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            'SELECT * FROM citations WHERE target_law IN (?,?) AND (? IS NULL OR context=?) ORDER BY source_version,citation_id',
            (target, alternate, context, context))]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--target', required=True, help='Canonical law ID, e.g. fvg:lr:2015:26')
    parser.add_argument('--context', choices=('BODY','EDITORIAL','QUOTED_OR_AMENDMENT_TEXT'))
    args = parser.parse_args()
    print(json.dumps(incoming(args.database, args.target, args.context), ensure_ascii=False, indent=2))
