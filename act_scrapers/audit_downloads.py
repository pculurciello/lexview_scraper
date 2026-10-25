#!/usr/bin/env python3
"""Independently recheck bytes and PDF identities from an existing download manifest."""
import argparse
import hashlib
import json
from pathlib import Path
from act_archive import atomic_json, verify

def audit(manifest_path,ocr=False,language='ita'):
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    root=manifest_path.parent.resolve()
    report=[]
    for record in manifest['files']:
        result=dict(number=record['number'],year=record['year'],role=record['role'],path=record.get('path'))
        try:
            path=(root/record['path']).resolve()
            if not path.is_relative_to(root):raise ValueError('Manifest path is outside download directory')
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            result['hash_matches']=digest==record['sha256']
            if not result['hash_matches']:
                result['verification']={'status':'MISMATCH','mismatches':['STORED_BYTES_CHANGED']}
            elif path.suffix=='.pdf':
                result['verification']=verify(record,path,ocr,language,record.get('server_filename'))
            else:
                result['verification']={'status':'REVIEW_REQUIRED','review_reasons':['NON_PDF_CONTENT_NOT_ANALYSED']}
        except (OSError,KeyError,ValueError) as exc:
            result['verification']={'status':'AUDIT_FAILED','error':str(exc)}
        report.append(result)
    return {'files':report,'status':'MATCH' if report and all(r['verification']['status']=='MATCH' for r in report) else 'REVIEW_REQUIRED'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--ocr-cover',action='store_true')
    parser.add_argument('--ocr-language',default='ita')
    args=parser.parse_args()
    if args.output.resolve()==args.manifest.resolve():parser.error('Audit output must differ from input manifest')
    report=audit(args.manifest,args.ocr_cover,args.ocr_language)
    atomic_json(args.output,report)
    print(report['status'])
    return 0 if report['status']=='MATCH' else 2
if __name__=='__main__':raise SystemExit(main())
