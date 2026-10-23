import argparse
import copy
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from lxml import etree

from html_to_database_xml import Ingestion, convert_database
from ingestion_model import NS, ORDER, TABLES, XSD, fields, generate_xsd, rows_from_xml, validate, xml_from_rows
from database_xml_to_json import export
from citation_index import build_index, incoming

ROOT = Path(__file__).resolve().parents[1]


class DatabaseXmlTests(unittest.TestCase):
    def args(self, source, output, **overrides):
        d = dict(input=source, output=output, xsd=XSD, document_type='LR',
                 source_url='https://lexview-int.regione.fvg.it/FontiNormative/xml/scarico.aspx?ANN=2001&LEX=3',
                 year=2001, numero='3', data=None, titolo=None, jurisdiction='IT-FVG',
                 ente='Regione autonoma Friuli Venezia Giulia', source_system='LEXVIEW',
                 retrieved_at='2026-10-21T16:00:00+00:00')
        d.update(overrides)
        return argparse.Namespace(**d)

    def rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(ROOT / 'examples/scarico.htm', Path(tmp) / 'doc.xml')
            return rows_from_xml(convert_database(args))

    def test_supplied_database_column_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            regenerated = Path(tmp) / 'contract.xsd'
            generate_xsd(regenerated)
            self.assertEqual(regenerated.read_bytes(), XSD.read_bytes())
        sql = (ROOT / 'database/create_database.sql').read_text()
        for table in ORDER:
            definition = re.search(r'CREATE TABLE ' + table + r'\s*\((.*?)\n\);', sql, re.S)[1]
            columns = set(re.findall(r'^    (\w+)\s+(?:uuid|varchar|integer|date\b|text\b|jsonb|timestamptz|boolean|numeric|daterange)', definition, re.M))
            self.assertFalse({name for name, _, _ in fields(table)} - columns, table)

    def test_stable_identity_physical_notes_and_unresolved_targets(self):
        rows = self.rows()
        self.assertEqual(rows['source_document'][0]['document_type'], 'LR')
        self.assertEqual(len(rows['provision_identity']), 158)
        notes = [r for r in rows['document_unit'] if r['unit_type'] == 'EDITORIAL_NOTE']
        self.assertEqual(len(notes), 70)
        self.assertFalse(rows['provision_version'])
        self.assertFalse(rows['legal_relation'])
        self.assertTrue(all('provision_version_id' not in r for r in rows['document_unit']))
        self.assertTrue(all(r['resolution_status'] == 'UNRESOLVED' for r in rows['citation']))
        self.assertTrue(all('target_document_id' not in r for r in rows['citation']))
        self.assertTrue(any(r['parsed_reference'].get('target_document_key') == 'IT-FVG:LR:2002:12' for r in rows['citation']))
        self.assertTrue(any(r['parsed_reference']['context'] == 'EDITORIAL' and 'Parole' in r['cited_text'] for r in rows['citation']))
        keys = {r['canonical_key'] for r in rows['provision_identity']}
        self.assertIn('IT-FVG:LR:2001:3:ART4:COM2', keys)
        self.assertNotIn('IT-FVG:LR:2001:3:CAPI:ART4:COM2', keys)

    def test_snapshot_is_not_provision_validity_and_identity_survives_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(ROOT / 'examples/scarico.htm', Path(tmp) / 'a.xml')
            a = rows_from_xml(convert_database(args, {'validity_dates':['2026-06-11','2026-08-07']}))
            scope = a['source_representation'][0]['raw_metadata']['temporal_scope']
            self.assertEqual(scope['end_exclusive'], '2026-08-08')
            changed = Path(tmp) / 'changed.htm'
            changed.write_bytes(args.input.read_bytes().replace(b'La presente legge', b'Questa legge', 1))
            args.input = changed
            b = rows_from_xml(convert_database(args))
            self.assertEqual(a['source_document'][0]['id'], b['source_document'][0]['id'])
            self.assertEqual([r['id'] for r in a['provision_identity']], [r['id'] for r in b['provision_identity']])
            self.assertNotEqual(a['source_representation'][0]['id'], b['source_representation'][0]['id'])

    def test_invalid_origins_parent_cycles_and_overlapping_versions_rejected(self):
        original = self.rows()
        rows = copy.deepcopy(original)
        rows['citation'][0]['citing_provision_version_id'] = rows['source_document'][0]['id']
        with self.assertRaises((ValueError, etree.DocumentInvalid)):
            validate(xml_from_rows(rows))
        rows = copy.deepcopy(original)
        rows['document_unit'][0]['parent_unit_id'] = rows['document_unit'][0]['id']
        with self.assertRaises(ValueError):
            validate(xml_from_rows(rows))
        rows = copy.deepcopy(original)
        identity = rows['provision_identity'][0]['id']
        import uuid
        for start, end in [('2020-01-01','2022-01-01'), ('2021-01-01','2023-01-01')]:
            rows['provision_version'].append(dict(id=str(uuid.uuid4()), provision_identity_id=identity,
                valid_period=f'[{start},{end})', text='Reviewed text', status='IN_FORCE', metadata={}))
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            validate(xml_from_rows(rows))
        # Graph endpoints may exist in the database outside this one-act XML.
        rows['provision_version'] = rows['provision_version'][:1]
        relation = dict(id=str(uuid.uuid4()), subject_entity_id=str(uuid.uuid4()),
            object_entity_id=str(uuid.uuid4()), predicate='AMENDS',
            source_provision_version_id=rows['provision_version'][0]['id'], verification_status='UNVERIFIED')
        rows['legal_relation'] = [relation]
        validate(xml_from_rows(rows))
        del relation['source_provision_version_id']
        with self.assertRaisesRegex(ValueError, 'evidence'):
            validate(xml_from_rows(rows))

    def test_all_act_types_recitals_unknowns_and_export_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'act.htm'
            source.write_text('''<html><body><div class="intestazione"><strong>Esempio fittizio</strong></div>
            <div class="premesse"><p>VISTO <a href="xmllex.aspx?anno=2015&amp;legge=26#art4-com3">articolo 4 della legge regionale 26/2015</a>.</p>
            <p>Formula non classificata.</p></div><div class="dispositivo"><p class="punto"><strong>1.</strong>Di approvare.</p></div>
            <div>Testo aggiuntivo conservato.</div></body></html>''')
            doc_ids = set()
            for code in ('DGR','DD','DPREG','REG'):
                args = self.args(source, Path(tmp) / (code + '.xml'), document_type=code, year=2025, numero='100', data='2025-02-10')
                rows = rows_from_xml(convert_database(args))
                doc_ids.add(rows['source_document'][0]['id'])
                self.assertTrue(any(r.get('recital_type') == 'VISTO' for r in rows['document_unit']))
                self.assertTrue(any(r['issue_type'] == 'UNKNOWN_RECITAL_FORMULA' for r in rows['parser_issue']))
                self.assertTrue(any('Testo aggiuntivo' in r['raw_text'] for r in rows['document_unit']))
            self.assertEqual(len(doc_ids), 4)
            payload = export(args.output, Path(tmp) / 'rows.json')
            self.assertIsInstance(payload['tables']['citation'][0]['parsed_reference'], dict)
            database = Path(tmp) / 'citations.sqlite'
            build_index([{'status':'converted', 'xml':str(args.output),
                          'xml_sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}], database)
            refs = incoming(database, 'fvg:lr:2015:26')
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0]['status'], 'UNRESOLVED')


if __name__ == '__main__':
    unittest.main()
