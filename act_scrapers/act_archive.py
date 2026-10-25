"""Shared HTTP collection and independent PDF identity checks for FVG archives."""
import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit, unquote
from urllib.request import HTTPCookieProcessor, Request, build_opener
from lxml import html

START = {'DPREG': 'https://decreti.regione.fvg.it/elencoDecreti.asp',
         'DGR': 'https://www.regione.fvg.it/asp/delibere/layout2008_2.asp'}
MONTHS = dict(zip('gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split(), range(1, 13)))
LABEL_ID = re.compile(r'(?:DPReg|Delibera(?:zione)?)(?:\s+n[.°º]?)?\s*0*(\d+)\s*[-/]\s*(\d{4})', re.I)
DATE = re.compile(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b')


def norm(text):
    return ' '.join(text.split())


def words(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    return set(re.findall(r'[a-z0-9]{3,}', text))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(path)


def search_url(kind, year, number=None):
    params = {'pag': '1', 'cerca': 'true', 'anno': str(year)}
    if number is not None:
        params['numero' if kind == 'DPREG' else 'num'] = str(number)
    return START[kind] + '?' + urlencode(params)


def link_url(anchor, base):
    value = anchor.get('href', '')
    if value.lower().startswith('javascript:') or not value or value == '#':
        code = value + ' ' + anchor.get('onclick', '')
        candidates = re.findall(r'''['"]([^'"\r\n]+)['"]''', code)
        value = next((v for v in candidates if re.search(r'\.(?:asp|pdf|zip|docx?|xlsx?)(?:\?|$)|^https?://', v, re.I)), '')
    if not value or value.startswith(('#', 'mailto:')):
        return None
    value = urljoin(base, value)
    return value if urlsplit(value).scheme in ('https', 'http') else None


def parse_listing(content, base, kind, requested_year):
    """Group downloads by explicit link identities or a local dated act heading."""
    doc = html.fromstring(content, base_url=base)
    downloads, pages, issues = [], [], []
    for anchor in doc.xpath('//a[@href or @onclick]'):
        label = norm(' '.join(anchor.itertext()) + ' ' + anchor.get('title', ''))
        url = link_url(anchor, base)
        if not url:
            continue
        if re.search(r'\btesto\s+integrale\b|\ballegat[oi]\s*\d*', label, re.I):
            identity = LABEL_ID.search(label)
            # Do not use a year or act reference from arbitrary page-wide text.
            context = None
            if not identity:
                for ancestor in anchor.iterancestors():
                    if ancestor.tag in ('body', 'html'):
                        break
                    text = norm(' '.join(ancestor.itertext()))
                    headings = list(re.finditer(r'\bn[.°º]\s*0*(\d+)\s+del\s+(\d{1,2}[./]\d{1,2}[./]\d{4})', text, re.I))
                    if len(headings) == 1:
                        context = headings[0]
                        break
                    if len(headings) > 1:
                        break
            if identity:
                number, year = int(identity[1]), int(identity[2])
            elif context:
                number, year = int(context[1]), int(context[2][-4:])
            else:
                issues.append({'type': 'UNASSIGNED_DOWNLOAD', 'label': label, 'url': url})
                continue
            annex = re.search(r'allegat[oi]\s*(?:n[.°º]?\s*)?(\d+)', label, re.I)
            role = 'full_text' if re.search(r'testo\s+integrale', label, re.I) else 'annex'
            item = dict(kind=kind, number=number, year=year, label=label, url=url,
                        role=role, annex_number=int(annex[1]) if annex else None,
                        listing_url=base, expected_date=None, expected_title=None)
            # Metadata candidates are evidence, not identity overrides.
            for ancestor in anchor.iterancestors():
                if ancestor.tag in ('body', 'html'):
                    break
                text = norm(' '.join(ancestor.itertext()))
                heads = list(re.finditer(r'\bn[.°º]\s*0*(\d+)\s+del\s+(\d{1,2}[./]\d{1,2}[./]\d{4})', text, re.I))
                if len(heads) == 1 and int(heads[0][1]) == number:
                    match = DATE.search(heads[0][2])
                    if match:
                        item['expected_date'] = f'{int(match[3]):04}-{int(match[2]):02}-{int(match[1]):02}'
                    # The complete local card is retained, rather than guessing which
                    # department/title paragraph the publisher uses in this layout.
                    item['listing_context'] = text[:12000]
                    title_nodes = ancestor.xpath('.//*[@data-title] | .//*[contains(concat(" ",normalize-space(@class)," ")," oggetto ")]')
                    if not title_nodes:
                        title_nodes = [node for node in ancestor.xpath('.//p[not(.//a)]') if len(norm(' '.join(node.itertext()))) > 60]
                        title_nodes.sort(key=lambda node: len(norm(' '.join(node.itertext()))), reverse=True)
                    if title_nodes:
                        item['expected_title'] = norm(' '.join(title_nodes[0].itertext()))
                    break
                if len(heads) > 1:
                    break
            if year == requested_year:
                downloads.append(item)
        elif urlsplit(url).hostname == urlsplit(base).hostname:
            query = dict(parse_qsl(urlsplit(url).query))
            if 'pag' in query and (label.isdigit() or re.search(r'successiv|avanti|next|[»›]', label, re.I)):
                # Preserve requested year/number when pagination links omit filters.
                previous = dict(parse_qsl(urlsplit(base).query))
                previous.update(query)
                parts = urlsplit(url)
                pages.append(urlunsplit(parts._replace(query=urlencode(previous))))
    unique = { (d['number'], d['year'], d['role'], d['url']): d for d in downloads }
    return list(unique.values()), list(dict.fromkeys(pages)), issues


def pdf_text(path, ocr=False, language='ita'):
    result = subprocess.run(['pdftotext', '-layout', str(path), '-'], capture_output=True, check=True)
    text = result.stdout.decode('utf-8', errors='replace')
    pages = text.split('\f')
    if pages and not pages[-1].strip():
        pages.pop()
    cover = pages[0] if pages else ''
    method = 'PDF_TEXT_LAYER'
    if ocr:
        with tempfile.TemporaryDirectory(prefix='fvg-check-') as directory:
            prefix = str(Path(directory)/'cover')
            subprocess.run(['pdftoppm','-f','1','-singlefile','-scale-to','2200','-png',str(path),prefix], capture_output=True, check=True)
            cover = subprocess.run(['tesseract',prefix+'.png','stdout','-l',language],capture_output=True,check=True,text=True).stdout
        method = 'TESSERACT_OCR'
    return cover, pages, method


def extract_identity(cover, kind, annex=False):
    """Only anchored cover statements count; body citations never supply identity."""
    if annex:
        match = re.search(r'^\s*ALLEGATO\s+ALL[AO]\s+(?:DELIBERA|DPREG|DECRETO)\s+N[.°º]?\s*0*(\d+)\s+DE[LL’\' ]*\s*(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s+(\d{4})', cover, re.I | re.M)
    elif kind == 'DPREG':
        number = re.search(r'^\s*Decreto\s+n[°º.]*\s*0*(\d+)\s*/\s*Pres\.', cover, re.I | re.M)
        issued = re.search(r'Trieste,\s*(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s+(\d{4})', cover, re.I)
        match = None
        if number and issued:
            return {'number':int(number[1]), 'year':int(issued[3]), 'date':f'{int(issued[3]):04}-{MONTHS[issued[2].lower()]:02}-{int(issued[1]):02}'}
    else:
        number = re.search(r'^\s*Delibera\s+n[°º.]*\s*0*(\d+)\b', cover, re.I | re.M)
        issued = re.search(r'seduta\s+del\s+(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s+(\d{4})', cover, re.I)
        match = None
        if number and issued:
            return {'number':int(number[1]), 'year':int(issued[3]), 'date':f'{int(issued[3]):04}-{MONTHS[issued[2].lower()]:02}-{int(issued[1]):02}'}
    if match:
        return {'number':int(match[1]), 'year':int(match[4]), 'date':f'{int(match[4]):04}-{MONTHS[match[3].lower()]:02}-{int(match[2]):02}'}
    return None


def verify(item, path, ocr=False, language='ita', server_filename=None):
    evidence, mismatch, unknown = {}, [], []
    try:
        cover, pages, method = pdf_text(path, ocr, language)
        evidence.update(extraction=method, page_count=len(pages), cover_text=cover,
                        identity=extract_identity(cover,item['kind'],item['role']=='annex'))
        identity = evidence['identity']
        if item['role'] == 'full_text' and not identity:
            other = extract_identity(cover, 'DGR' if item['kind'] == 'DPREG' else 'DPREG')
            if other:
                mismatch.append('ACT_TYPE_MISMATCH')
        if identity:
            for field in ('number','year'):
                if identity[field] != item[field]:
                    mismatch.append('CONTENT_' + field.upper() + '_MISMATCH')
            if item.get('expected_date') and identity['date'] != item['expected_date']:
                mismatch.append('CONTENT_DATE_MISMATCH')
        else:
            unknown.append('CONTENT_IDENTITY_NOT_FOUND')
        if item['role'] == 'full_text':
            body = '\n'.join(pages[1:])
            heading = 'Decreta' if item['kind']=='DPREG' else 'Delibera'
            if not re.search(r'^\s*'+heading+r'\s*:?\s*$', body,re.I|re.M):
                unknown.append('OPERATIVE_HEADING_NOT_FOUND')
            if not re.search(r'^\s*(?:Vist[oaie]|Premess[oaie]|Attes[oaie]|Considerat[oaie]|Ritenut[oaie])\b',body,re.I|re.M):
                unknown.append('PREAMBLE_NOT_FOUND')
            subject = re.search(r'oggetto\s*:\s*(.*?)(?:Firmato da\s*:|\n[^\n]+\s+(?:Vice\s*Presidente|Presidente|Assessore)\s+(?:presente|assente))',cover,re.I|re.S)
            evidence['content_title'] = norm(subject[1]) if subject else None
            if item.get('expected_title') and subject:
                a,b=words(item['expected_title']),words(subject[1]);score=len(a&b)/max(1,len(a|b))
                evidence['title_similarity']=round(score,4)
                if score < .65:
                    unknown.append('TITLE_REQUIRES_REVIEW')
            else:
                unknown.append('INDEPENDENT_TITLE_CHECK_UNAVAILABLE')
        else:
            annex = re.search(r'^\s*ALLEGATO\s*(?:N[.°º]?\s*)?(\d+)\s*$',cover,re.I|re.M)
            if item.get('annex_number') and annex and int(annex[1]) != item['annex_number']:
                mismatch.append('ANNEX_NUMBER_MISMATCH')
            if not annex:
                unknown.append('ANNEX_NUMBER_NOT_FOUND')
            if not identity:
                unknown.append('ANNEX_RELATIONSHIP_NOT_PROVEN_BY_CONTENT')
        if any(not page.strip() for page in pages):
            unknown.append('EMPTY_OR_SCANNED_BODY_PAGE')
    except (OSError,subprocess.CalledProcessError,ValueError) as exc:
        unknown.append('PDF_EXTRACTION_FAILED')
        evidence['extraction_error']=str(exc)
    for name in [path.name,server_filename or '',unquote(urlsplit(item.get('url','')).path).split('/')[-1]]:
        identity = re.search(r'(?:DPREG|DGR)[-_ ]+((?:18|19|20|21|22)\d{2})[-_ ]+(\d+)', name,re.I)
        original = LABEL_ID.search(unquote(name))
        pair = (int(identity[2]),int(identity[1])) if identity else ((int(original[1]),int(original[2])) if original else None)
        if pair and pair != (item['number'],item['year']):
            mismatch.append('FILENAME_IDENTITY_MISMATCH')
    return dict(status='MISMATCH' if mismatch else ('REVIEW_REQUIRED' if unknown else 'MATCH'),
                mismatches=sorted(set(mismatch)), review_reasons=sorted(set(unknown)), evidence=evidence)


class Client:
    def __init__(self,delay=1.0,retries=3):
        self.opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.delay,self.retries=delay,retries
    def get(self,url):
        for attempt in range(self.retries+1):
            time.sleep(self.delay if attempt==0 else max(self.delay,2**attempt))
            try:
                response=self.opener.open(Request(url,headers={'User-Agent':'FVG-legal-archive/1.0 (document research)','Accept':'*/*'}),timeout=60)
                with response:
                    data=response.read(100*1024*1024+1)
                    if len(data)>100*1024*1024:
                        raise ValueError('Response exceeds 100 MiB limit')
                    return data,response.geturl(),dict(response.headers)
            except HTTPError as exc:
                if exc.code not in (429,500,502,503,504) or attempt==self.retries:
                    raise
            except URLError:
                if attempt==self.retries:
                    raise


def extension(data,url):
    if data.lstrip().startswith(b'%PDF-'): return '.pdf'
    # Preserve non-PDF attachments too, including signed containers and archives.
    suffix=Path(unquote(urlsplit(url).path)).suffix.lower()
    if suffix in ('.zip','.p7m','.doc','.docx','.xls','.xlsx','.odt','.ods','.csv','.txt'):
        return suffix
    return '.bin'


def collect(kind,args):
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    client=Client(args.delay,args.retries)
    queue=deque([args.start_url or search_url(kind,args.year,args.number)])
    seen,items,issues=set(),{},[]
    page_hashes=set()
    manifest=dict(schema_version=1,kind=kind,requested_year=args.year,requested_number=args.number,
                  started_at=datetime.now(timezone.utc).isoformat(),status='INCOMPLETE',files=[],issues=issues)
    manifest_path=output/'manifest.json'
    try:
        while queue and len(seen)<args.max_pages:
            url=queue.popleft()
            if url in seen:continue
            seen.add(url)
            data,final,headers=client.get(url)
            snapshot=output/'pages'/(hashlib.sha256(url.encode()).hexdigest()+'.html')
            snapshot.parent.mkdir(parents=True,exist_ok=True);snapshot.write_bytes(data)
            fingerprint=hashlib.sha256(data).hexdigest()
            if fingerprint in page_hashes:
                issues.append({'type':'REPEATED_LISTING_CONTENT','url':url})
                continue
            page_hashes.add(fingerprint)
            found,pages,problems=parse_listing(data,final,kind,args.year)
            issues.extend(problems)
            for item in found:
                if args.number is None or item['number']==args.number:
                    items[(item['number'],item['role'],item['url'])]=item
            queue.extend(p for p in pages if p not in seen)
        if any(p not in seen for p in queue):issues.append({'type':'PAGE_LIMIT_REACHED'})
        if not items:issues.append({'type':'NO_DOWNLOAD_LINKS_DISCOVERED','action':'Inspect saved HTML; layout may have changed or use JavaScript.'})
        for item in items.values():
            record=dict(item)
            try:
                data,final,headers=client.get(item['url'])
                headers={k.lower():v for k,v in headers.items()}
                if data.lstrip()[:100].lower().startswith((b'<!doctype html',b'<html')) or 'text/html' in headers.get('content-type','').lower():
                    raise ValueError('Expected file but received HTML; detail/download endpoint requires an adapter update')
                suffix=extension(data,final)
                if item['role']=='full_text' and suffix!='.pdf':raise ValueError('Full text is not a PDF')
                digest=hashlib.sha256(data).hexdigest()
                role='testo-integrale' if item['role']=='full_text' else 'allegato-'+str(item['annex_number'] or 'non-numerato')
                filename=f'{kind}-{item["year"]}-{item["number"]}-{role}-{digest[:12]}{suffix}'
                path=output/'files'/filename;path.parent.mkdir(parents=True,exist_ok=True)
                temp=path.with_suffix(path.suffix+'.part');temp.write_bytes(data);temp.replace(path)
                disposition=headers.get('content-disposition','')
                match=re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)',disposition,re.I)
                server_name=unquote(match[1]) if match else None
                check=verify(item,path,args.ocr_cover,args.ocr_language,server_name) if suffix=='.pdf' else {'status':'REVIEW_REQUIRED','review_reasons':['NON_PDF_CONTENT_NOT_ANALYSED']}
                record.update(path=str(path.relative_to(output)),final_url=final,server_filename=server_name,
                              sha256=digest,bytes=len(data),media_type=headers.get('content-type'),retrieved_at=datetime.now(timezone.utc).isoformat(),verification=check)
                if check['status']=='MISMATCH':
                    quarantine=output/'quarantine'/filename;quarantine.parent.mkdir(parents=True,exist_ok=True)
                    path.replace(quarantine);record['path']=str(quarantine.relative_to(output))
            except Exception as exc:
                record.update(error=str(exc),verification={'status':'DOWNLOAD_FAILED'})
            manifest['files'].append(record)
            atomic_json(manifest_path,manifest)
        hashes={}
        for record in manifest['files']:
            if record.get('sha256'):
                origin=(record['number'],record['role'],record.get('annex_number'))
                previous=hashes.setdefault(record['sha256'],origin)
                if previous!=origin:
                    issues.append({'type':'DUPLICATE_BYTES_ACROSS_DOCUMENTS','first':previous,'second':origin,'sha256':record['sha256']})
        acts={i['number'] for i in items.values()}
        for number in sorted(acts):
            full=[r for r in manifest['files'] if r['number']==number and r['role']=='full_text']
            if len(full)!=1:issues.append({'type':'FULL_TEXT_COUNT_NOT_ONE','number':number,'count':len(full)})
        bad=any(r['verification']['status']!='MATCH' for r in manifest['files'])
        manifest['status']='REVIEW_REQUIRED' if issues or bad else 'DOWNLOADED_AND_CHECKED'
    except Exception as exc:
        issues.append({'type':'COLLECTION_FAILED','error':str(exc)})
        manifest['status']='FAILED'
    manifest['listing_pages_visited']=len(seen)
    manifest['finished_at']=datetime.now(timezone.utc).isoformat()
    # No claim of archive-wide completeness: only discovered links were collected.
    manifest['completeness_basis']='DISCOVERED_LINKS_ONLY'
    atomic_json(manifest_path,manifest)
    return manifest


def main(kind):
    parser=argparse.ArgumentParser(description=f'Download FVG {kind} full texts and all listed annexes, with content identity checks')
    parser.add_argument('--year',type=int,required=True)
    parser.add_argument('--number',type=int)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--start-url',help='Explicit filtered results URL, if the publisher changes query parameters')
    parser.add_argument('--max-pages',type=int,default=10000)
    parser.add_argument('--delay',type=float,default=1)
    parser.add_argument('--retries',type=int,default=3)
    parser.add_argument('--ocr-cover',action='store_true')
    parser.add_argument('--ocr-language',default='ita')
    args=parser.parse_args()
    if args.max_pages<1 or args.delay<0 or args.retries<0:parser.error('Invalid limits')
    manifest=collect(kind,args)
    print(f'{manifest["status"]}: {len(manifest["files"])} files; {args.output / "manifest.json"}')
    return 0 if manifest['status']=='DOWNLOADED_AND_CHECKED' else 2
