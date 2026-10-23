# Citations to and from a law: actual temporal-version analysis

**Legacy light 1.0 analysis.** For the revised database-aligned XML, see
[DATABASE_ALIGNMENT.md](DATABASE_ALIGNMENT.md). In 2.0 editorial evidence is embedded
as document units, citing origins are UUID references, and parsed links remain
`UNRESOLVED` until their target foreign keys are actually resolved.

Source inspected: the annotated HTML export of **law 7/2024, version effective
11 June–7 August 2026**, downloaded during live verification.
The [complete converted XML](examples/lr-2024-7_2026-06-11.xml) validates against
the supplied XSD and contains 695 citation records. Its 132 editorial notes are
preserved in the [separate evidence file](examples/lr-2024-7_2026-06-11.editorial.json).

## Outgoing reference in the legislative text

Article 1, comma 4 contains this actual link:

```html
<a href="xmllex.aspx?anno=2015&amp;legge=26#art4-com3">articolo 4, comma 3, della legge regionale 26/2015</a>
```

The schema already provides everything needed for this direction:

```xml
<citazione xmlns="urn:fvg:legal:light:1.0"
           id="cit_example" sorgente="art1_com4" stato="risolta">
  <destinazione atto="fvg:lr:2015:26" articolo="4" comma="3"
                uri="https://lexview-int.regione.fvg.it/FontiNormative/xml/xmllex.aspx?anno=2015&amp;legge=26#art4-com3"/>
  <testoCitato>articolo 4, comma 3, della legge regionale 26/2015</testoCitato>
</citazione>
```

This is a fragment: `art1_com4` must exist in the enclosing document as an XML ID.
`cit_example` is an illustrative ID; generated documents use sequential citation IDs.
The same XML contains the unchanged quoted wording in `comma/testo`.
The link establishes an act/provision identity; it does not establish the target
law's applicable temporal version, legal effect, or remote-resource availability.
`risolta` here means the identity was parsed, not that a legal interpretation was made.

## Multiple destinations and national laws

The same HTML includes a link whose text says **articoli 1 e 2 della legge regionale
10 novembre 2015, n. 26**, but whose fragment points only to `#art1`. The converter
now produces two citation records, with `articolo="1"` and `articolo="2"`, using the
explicit enumeration in the quote. It does not silently lose article 2. An enumeration
does not reuse `#art1` as article 2's URI: that mismatching navigation URI is omitted
for the second destination, while its act and article identity remain explicit.
An enumeration
with ambiguous subordinate comma/letter targets, or an unsupported range, remains
`parziale`; no unspecified targets are invented.

National references use links such as:

```text
LeggiEsterne.aspx?doc=urn:nir:stato:decreto.legislativo:2011-06-23%3b118~art42
```

They map to `atto="urn:nir:stato:decreto.legislativo:2011-06-23;118"` and
`articolo="42"`, retaining the original navigation URI. Broad EU portal links without
an identifiable act remain `parziale`. Unlinked references remain ordinary text;
this implementation does not pretend to detect every plain-text citation.

Offsets `inizio` and `fine` are optional in the XSD and are omitted. Quoted strings
are retained, and each `sorgente` is checked against actual IDs in the output document.

## Incoming references: reverse the corpus index

If law A cites law B, the citation belongs in **A's XML**, where its source paragraph
exists. To ask which laws cite B, query the index by `target_law = B`:

```sh
python citation_index.py --database downloads/corpus/citations.sqlite --target fvg:lr:2015:26
```

Rows contain the citing law, its exact exported version, local source anchor, target
provision, quoted wording and URI. The index includes references from every successfully
converted temporal version, so group by `source_law` if counting distinct citing laws.
It does not infer which target temporal version applies to a citation.

Do not put A's paragraph ID into a citation inside B's XML: `sorgente` is `xs:IDREF`
and must identify an element in the **same** document. The supplied XSD has no element
for a foreign source act/version, so a first-class incoming-citations section would
require a schema extension. This pipeline keeps the XSD unchanged and uses SQLite
for the reverse view. Incoming coverage grows as the corpus is collected; absence
from a partial index does not establish that a law has never been cited.

## Editorial notes: separate evidence

This version's annotations include:

```text
Parole aggiunte al comma 92 da art. 2, comma 8, L. R. 8/2024
```

Here the local HTML note links to law 8/2024. Its prose describes an amendment **by**
that law to the inspected law. This is editorial evidence, not an operative outgoing
reference in the legislative paragraph. It is also not proof of all incoming citations.

The light XSD has neither an editorial-note element nor an amendment-relation type.
The converter therefore excludes note markers from the operative text and saves
editorial note wording, source article, links and parsed destinations in a separate
`*.editorial.json` file keyed by source version. It does not reverse every note link
into an asserted legal relation. The original annotated HTML is retained as evidence.
