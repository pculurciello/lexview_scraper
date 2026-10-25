import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from act_archive import parse_listing, verify, search_url, collect

COVER='Decreto n° 013 / Pres.\nTrieste, 2 febbraio 2026\noggetto:\nExample subject for a regional regulation\nFirmato da:\nNAME'
BODY='Visto il testo;\nDecreta\n1. Dispone.'

class ArchiveTests(unittest.TestCase):
    def item(self,**kw):
        return dict(dict(kind='DPREG',number=13,year=2026,role='full_text',annex_number=None,expected_date='2026-02-02',expected_title='Example subject for a regional regulation'),**kw)
    def test_discovery_and_pagination_filters(self):
        data=b'''<html><article><h3>n. 13 del 02.02.2026</h3><p class="oggetto">Example subject</p><a href="main.asp?id=1">Testo integrale del DPReg 013-2026</a><a href="a.zip">Allegato1 al DPReg 013-2026</a><a href="b.pdf">Allegato2 al DPReg 013-2026</a></article><a href="?pag=2">2</a></html>'''
        files,pages,issues=parse_listing(data,search_url('DPREG',2026,13),'DPREG',2026)
        self.assertEqual(len(files),3)
        self.assertEqual(files[0]['expected_date'],'2026-02-02')
        self.assertEqual(files[0]['expected_title'],'Example subject')
        self.assertIn('anno=2026',pages[0]);self.assertIn('numero=13',pages[0])
        self.assertFalse(issues)
    def test_missing_identity_is_not_guessed(self):
        files,_,issues=parse_listing(b'<a href="a.pdf">Testo integrale</a>',search_url('DGR',2024),'DGR',2024)
        self.assertFalse(files);self.assertEqual(issues[0]['type'],'UNASSIGNED_DOWNLOAD')
    def test_javascript_and_non_pdf_attachment(self):
        data=b'''<a href="javascript:window.open('document.asp?id=2')">Allegato3 alla Delibera 1506-2024</a>'''
        files,_,_=parse_listing(data,search_url('DGR',2024),'DGR',2024)
        self.assertTrue(files[0]['url'].endswith('document.asp?id=2'))
        self.assertEqual(files[0]['annex_number'],3)
    def check(self,item,cover=COVER,body=BODY,path='DPREG-2026-13-testo.pdf',**kwargs):
        with patch('act_archive.pdf_text',return_value=(cover,[cover,body],'PDF_TEXT_LAYER')):
            return verify(item,Path(path),**kwargs)
    def test_identity_title_preamble_and_filename_match(self):
        self.assertEqual(self.check(self.item())['status'],'MATCH')
        self.assertEqual(self.check(self.item(),path='TESTO INTEGRALE DEL DPREG 013-2026.pdf')['status'],'MATCH')
    def test_swapped_pdf_quarantinable(self):
        result=self.check(self.item(),cover=COVER.replace('013','014'))
        self.assertEqual(result['status'],'MISMATCH')
        self.assertIn('CONTENT_NUMBER_MISMATCH',result['mismatches'])
    def test_server_filename_conflict(self):
        result=self.check(self.item(),server_filename='TESTO INTEGRALE DEL DPREG 266-2013.pdf')
        self.assertEqual(result['status'],'MISMATCH')
    def test_citations_never_supply_identity(self):
        result=self.check(self.item(),cover='Si richiama il decreto n. 13 del 2026')
        self.assertEqual(result['status'],'REVIEW_REQUIRED')
    def test_dispositivo_only_is_not_full_text(self):
        self.assertIn('PREAMBLE_NOT_FOUND',self.check(self.item(),body='Decreta\n1. Dispone')['review_reasons'])
    def test_annex_parent_identity_conflict(self):
        cover='ALLEGATO ALLA DELIBERA N. 1506 DELL’11 OTTOBRE 2024\nALLEGATO 2'
        item=dict(kind='DGR',number=1507,year=2024,role='annex',annex_number=2)
        self.assertIn('CONTENT_NUMBER_MISMATCH',self.check(item,cover=cover,path='annex.pdf')['mismatches'])
    def test_unstated_annex_relationship_needs_review(self):
        item=dict(kind='DGR',number=1506,year=2024,role='annex',annex_number=1)
        result=self.check(item,cover='Linee guida\nArticolo 1',path='annex.pdf')
        self.assertEqual(result['status'],'REVIEW_REQUIRED')
    def test_html_error_not_saved_as_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            args=argparse.Namespace(output=directory,delay=0,retries=0,start_url=None,year=2026,number=13,max_pages=10,ocr_cover=False,ocr_language='ita')
            listing=b'<a href="a.pdf">Testo integrale del DPReg 013-2026</a>'
            with patch('act_archive.Client.get',side_effect=[(listing,search_url('DPREG',2026,13),{}),(b'<html>Error</html>','https://example.org/a.pdf',{'Content-Type':'text/html'})]):
                result=collect('DPREG',args)
            self.assertEqual(result['files'][0]['verification']['status'],'DOWNLOAD_FAILED')
            self.assertFalse(list(Path(directory).rglob('*.pdf')))
    def test_explicit_conflict_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            args=argparse.Namespace(output=directory,delay=0,retries=0,start_url=None,year=2026,number=13,max_pages=10,ocr_cover=False,ocr_language='ita')
            listing=b'<a href="a.pdf">Testo integrale del DPReg 013-2026</a>'
            with patch('act_archive.Client.get',side_effect=[(listing,search_url('DPREG',2026,13),{}),(b'%PDF-fixture','https://example.org/a.pdf',{})]),patch('act_archive.pdf_text',return_value=(COVER.replace('013','014'),[COVER,BODY],'PDF_TEXT_LAYER')):
                result=collect('DPREG',args)
            self.assertTrue(result['files'][0]['path'].startswith('quarantine/'))
            self.assertTrue((Path(directory)/result['files'][0]['path']).exists())


class AuditTests(unittest.TestCase):
    def test_modified_bytes_are_reported_before_text_extraction(self):
        import json
        from audit_downloads import audit
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'file.pdf').write_bytes(b'changed')
            record=dict(number=13,year=2026,role='full_text',path='file.pdf',sha256='0'*64)
            (root/'manifest.json').write_text(json.dumps({'files':[record]}))
            report=audit(root/'manifest.json')
            self.assertEqual(report['files'][0]['verification']['mismatches'],['STORED_BYTES_CHANGED'])
    def test_manifest_cannot_audit_outside_its_directory(self):
        import json
        from audit_downloads import audit
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            record=dict(number=13,year=2026,role='full_text',path='../outside.pdf',sha256='0'*64)
            (root/'manifest.json').write_text(json.dumps({'files':[record]}))
            report=audit(root/'manifest.json')
            self.assertEqual(report['files'][0]['verification']['status'],'AUDIT_FAILED')

if __name__=='__main__':unittest.main()
