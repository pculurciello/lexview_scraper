"""Conservative link-based legal destinations; no remote or temporal inference."""
import re
from urllib.parse import parse_qs, urljoin, urlsplit

SUFFIX = r'(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies)'
NUMBER = rf'\d+(?:\s*{SUFFIX})?'


def article_numbers(quote):
    match = re.search(rf'\barticoli\s+({NUMBER}(?:(?:\s*,\s*|\s+e\s+){NUMBER})+)\b', quote, re.I)
    if not match:
        return None
    return [re.sub(r'\s+', '', n) for n in re.findall(NUMBER, match[1], re.I)]


def destinations(quote, href, source_url=None):
    """Return one XSD-compatible destination per explicit, enumerated article."""
    url = urlsplit(href)
    query = {k.lower(): v for k, v in parse_qs(url.query).items()}
    dest = {}
    if url.scheme in ('http', 'https') or source_url or href.startswith('#'):
        dest['uri'] = urljoin(source_url or '', href)
    local_host = urlsplit(source_url).hostname if source_url else None
    regional = (url.path.rsplit('/', 1)[-1].lower() in ('xmllex.aspx', 'indicelex.aspx')
                and url.hostname in (None, local_host, 'lexview-int.regione.fvg.it'))
    year = query.get('anno', [''])[0]
    number = query.get('legge', [''])[0]
    fragment = url.fragment
    if regional and re.fullmatch(r'\d{4}', year) and number.isdigit():
        dest['atto'] = f'fvg:lr:{year}:{int(number)}'
    elif query.get('doc', [''])[0].startswith('urn:nir:'):
        urn, _, target = query['doc'][0].partition('~')
        dest['atto'] = urn
        fragment = target or fragment
    elif href.startswith('#') and re.match(r'art\d', fragment):
        # Intra-document links have no foreign law identifier.
        fragment = url.fragment
    match = re.fullmatch(r'art([\w]+)(?:-com([\w]+))?(?:-let_?([\w]+))?', fragment)
    if match:
        for key, value in zip(('articolo', 'comma', 'lettera'), match.groups()):
            if value:
                dest[key] = value
    status = 'risolta' if 'atto' in dest else ('parziale' if dest else 'nonRisolta')
    articles = article_numbers(quote)
    if articles and 'atto' in dest:
        # A link may name articles 1 and 2 while its fragment points only at 1.
        # Do not attach one article's subordinate comma to all enumerated articles.
        if 'comma' in dest or 'lettera' in dest:
            return [(dest, 'parziale')]
        results = []
        for article in articles:
            target = {**dest, 'articolo': article}
            if dest.get('articolo') and dest['articolo'] != article:
                # The original href navigates to the first named article, not this one.
                target.pop('uri', None)
            results.append((target, status))
        return results
    if re.search(r'\barticoli\b|\bcommi\b', quote, re.I):
        status = 'parziale'
    return [(dest, status)]
