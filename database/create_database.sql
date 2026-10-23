-- Iter Bando Contributivo FVG
-- PostgreSQL 15+ bootstrap script. Run with psql:
--   psql -U postgres -f create_database.sql

SELECT 'CREATE DATABASE fvg_laws ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'fvg_laws')\gexec

GRANT ALL PRIVILEGES ON DATABASE "fvg_laws" to postgres;

\connect fvg_laws

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS fvg_laws;
SET search_path TO fvg_laws, public;
ALTER DATABASE fvg_laws SET search_path TO fvg_laws, public;

-- =============================================================================
-- RELATIONSHIP MAP
-- =============================================================================
-- Cardinality notation: A (1) ---- (N) B means one A relates to many B rows.
--
-- SOURCE AND PROVISION LAYER
-- source_document       (1) ---- (N) source_representation
-- source_document       (1) ---- (N) provision_identity
-- source_representation (1) ---- (N) document_unit
-- provision_identity    (1) ---- (N) provision_version
-- provision_version     (1) ---- (N) document_unit [optional on document_unit]
--
-- Both provision_identity and document_unit are recursive trees:
-- provision_identity    (1 parent) ---- (N children) provision_identity
-- document_unit         (1 parent) ---- (N children) document_unit
--
-- INGESTION AND CITATIONS
-- source_representation (1) ---- (N) ingestion_run ---- (N) parser_issue
-- citation has exactly one citing origin: provision_version OR document_unit.
-- A resolved citation optionally targets source_document and provision_identity.
--
-- NORMATIVE GRAPH
-- provision_identity    (1) ---- (0..1) knowledge_entity
-- knowledge_entity      (1) ---- (N) legal_assertion as subject/object
-- knowledge_entity      (1) ---- (N) legal_relation as subject/object
-- provision_version/document_unit provide evidence for assertions/relations.
--
-- ACT GRAMMAR AND RUNTIME
-- act_template (N) ---- (M) requirement_definition via template_requirement
-- act_template          (1) ---- (N) drafting_case
-- drafting_case         (1) ---- (N) case_requirement ---- (N) proof
-- drafting_case         (1) ---- (N) administrative_fact
-- proof                 (1) ---- (N) proof_evidence
-- Each proof_evidence row points to exactly one admissible evidence object.
-- drafting_case         (1) ---- (N) act_draft ---- (N) draft_clause
-- draft_clause (N) ---- (M) proof via draft_clause_evidence
-- =============================================================================

-- Keep controlled terms extensible without PostgreSQL enum migrations.
-- This table is independent: vocabulary/code pairs support application-level
-- validation without coupling every controlled value to a PostgreSQL enum.
CREATE TABLE vocabulary_term (
    vocabulary          varchar(60)  NOT NULL,
    code                varchar(80)  NOT NULL,
    label_it            text         NOT NULL,
    description         text,
    is_active           boolean      NOT NULL DEFAULT true,
    sort_order          integer      NOT NULL DEFAULT 0,
    PRIMARY KEY (vocabulary, code)
);

-- One legal/administrative act, independently of its downloaded formats.
-- Parent of source_representation and provision_identity (both 1:N).
CREATE TABLE source_document (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    jurisdiction        varchar(50)  NOT NULL DEFAULT 'IT-FVG',
    document_type       varchar(80)  NOT NULL,
    number              varchar(80),
    year                integer,
    document_date       date,
    title               text,
    issuer              varchar(255),
    publication_type    varchar(80),
    publication_number  varchar(80),
    publication_date    date,
    official_uri        text,
    metadata            jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now(),
    CHECK (year IS NULL OR year BETWEEN 1800 AND 2200),
    UNIQUE NULLS NOT DISTINCT (jurisdiction, document_type, year, number)
);

-- A concrete HTML/XML/PDF/TXT representation acquired from a source.
-- N:1 source_document: several representations can describe the same act.
-- Parent of document_unit and ingestion_run (both 1:N).
CREATE TABLE source_representation (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_document_id  uuid         NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
    source_system       varchar(80)  NOT NULL,
    format              varchar(30)  NOT NULL,
    source_url          text         NOT NULL,
    retrieved_at        timestamptz  NOT NULL DEFAULT now(),
    content_hash        varchar(128),
    local_object_key    text,
    media_type          varchar(120),
    character_encoding  varchar(40),
    is_official         boolean      NOT NULL DEFAULT false,
    legal_authority     boolean      NOT NULL DEFAULT false,
    raw_metadata        jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (source_document_id, source_url, content_hash)
);

-- Stable, recursive identity of a provision at any granularity.
-- N:1 source_document: every provision belongs to exactly one legal act.
-- Self 1:N through parent_provision_id, e.g. ARTICLE -> PARAGRAPH -> LETTER.
-- Parent of provision_version (1:N): identity remains stable as text changes.
CREATE TABLE provision_identity (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_document_id  uuid         NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
    parent_provision_id uuid         REFERENCES provision_identity(id) ON DELETE CASCADE,
    unit_type           varchar(50)  NOT NULL,
    unit_label          varchar(100) NOT NULL,
    canonical_key       varchar(700) NOT NULL UNIQUE,
    ordinal             integer,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    CHECK (parent_provision_id IS NULL OR parent_provision_id <> id),
    UNIQUE NULLS NOT DISTINCT (source_document_id, parent_provision_id, unit_type, unit_label)
);

-- Temporally valid text of a stable provision identity.
-- N:1 provision_identity; validity ranges for one identity cannot overlap.
-- introduced_by_* records the amending act/provision: these are provenance
-- links, not ownership links. One version may have many document_unit proofs.
CREATE TABLE provision_version (
    id                          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    provision_identity_id       uuid        NOT NULL REFERENCES provision_identity(id) ON DELETE CASCADE,
    valid_period                daterange   NOT NULL,
    text                        text        NOT NULL,
    normalized_text             text,
    version_number              integer,
    status                      varchar(30) NOT NULL DEFAULT 'IN_FORCE',
    introduced_by_document_id   uuid        REFERENCES source_document(id),
    introduced_by_provision_id  uuid        REFERENCES provision_identity(id),
    metadata                    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT isempty(valid_period)),
    CHECK (lower_inc(valid_period) AND NOT upper_inc(valid_period)),
    CHECK (version_number IS NULL OR version_number > 0),
    UNIQUE (provision_identity_id, version_number),
    EXCLUDE USING gist (provision_identity_id WITH =, valid_period WITH &&)
);

-- Parsed physical/logical units. The nullable version FK implements the
-- MVP's 1:N ProvisionVersion -> DocumentUnit evidence model.
-- N:1 source_representation: every unit is traceable to one acquired file.
-- Self 1:N through parent_unit_id, e.g. ARTICLE -> PARAGRAPH -> LETTER.
-- provision_version_id is nullable because headings, recitals and operative
-- clauses are document units but do not necessarily represent provisions.
CREATE TABLE document_unit (
    id                          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_representation_id    uuid         NOT NULL REFERENCES source_representation(id) ON DELETE CASCADE,
    provision_version_id        uuid         REFERENCES provision_version(id) ON DELETE SET NULL,
    parent_unit_id              uuid         REFERENCES document_unit(id) ON DELETE CASCADE,
    unit_type                   varchar(50)  NOT NULL,
    unit_label                  varchar(100),
    ordinal                     integer,
    recital_type                varchar(50),
    raw_text                    text,
    normalized_text             text,
    source_locator              jsonb        NOT NULL DEFAULT '{}'::jsonb,
    parser_confidence           numeric(5,4),
    review_status               varchar(30)  NOT NULL DEFAULT 'UNREVIEWED',
    created_at                  timestamptz  NOT NULL DEFAULT now(),
    CHECK (parent_unit_id IS NULL OR parent_unit_id <> id),
    CHECK (parser_confidence IS NULL OR parser_confidence BETWEEN 0 AND 1),
    UNIQUE NULLS NOT DISTINCT (source_representation_id, parent_unit_id, unit_type, unit_label, ordinal)
);

-- Auditable ingestion and parsing executions, including UNKNOWN cases.
-- N:1 source_representation: a representation can be parsed repeatedly by
-- different parser versions without losing processing history.
CREATE TABLE ingestion_run (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    source_representation_id uuid    NOT NULL REFERENCES source_representation(id) ON DELETE CASCADE,
    parser_name         varchar(100) NOT NULL,
    parser_version      varchar(50)  NOT NULL,
    status              varchar(30)  NOT NULL DEFAULT 'RUNNING',
    started_at          timestamptz  NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    statistics          jsonb        NOT NULL DEFAULT '{}'::jsonb,
    error_detail        text,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

-- N:1 ingestion_run. document_unit_id is optional because an issue can arise
-- before the parser has been able to create a document unit.
CREATE TABLE parser_issue (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_run_id    uuid         NOT NULL REFERENCES ingestion_run(id) ON DELETE CASCADE,
    document_unit_id    uuid         REFERENCES document_unit(id) ON DELETE SET NULL,
    issue_type          varchar(60)  NOT NULL,
    candidate_text      text         NOT NULL,
    source_locator      jsonb        NOT NULL DEFAULT '{}'::jsonb,
    parser_context      jsonb        NOT NULL DEFAULT '{}'::jsonb,
    resolution_status   varchar(30)  NOT NULL DEFAULT 'OPEN',
    resolution          jsonb,
    resolved_by         varchar(100),
    resolved_at         timestamptz,
    created_at          timestamptz  NOT NULL DEFAULT now()
);

-- Explicit references found in source provisions/units and their resolution.
-- The citing side is exclusive: exactly one of citing_provision_version_id and
-- citing_document_unit_id must be populated. target_document_id represents an
-- act-level target; target_provision_id resolves the exact stable provision.
CREATE TABLE citation (
    id                          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    citing_provision_version_id uuid         REFERENCES provision_version(id) ON DELETE CASCADE,
    citing_document_unit_id     uuid         REFERENCES document_unit(id) ON DELETE CASCADE,
    cited_text                  text         NOT NULL,
    parsed_reference            jsonb        NOT NULL DEFAULT '{}'::jsonb,
    target_document_id          uuid         REFERENCES source_document(id),
    target_provision_id         uuid         REFERENCES provision_identity(id),
    resolution_status           varchar(30)  NOT NULL DEFAULT 'UNRESOLVED',
    extraction_method           varchar(40)  NOT NULL,
    confidence                  numeric(5,4),
    verified_by                 varchar(100),
    verified_at                 timestamptz,
    created_at                  timestamptz  NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(citing_provision_version_id, citing_document_unit_id) = 1),
    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

-- General entities make the normative graph usable beyond provisions alone.
-- provision_identity_id optionally anchors an entity to a legal provision;
-- other entities model measures, beneficiaries, objectives, offices, etc.
CREATE TABLE knowledge_entity (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type         varchar(60)  NOT NULL,
    canonical_key       varchar(700) NOT NULL UNIQUE,
    preferred_label     text         NOT NULL,
    description         text,
    provision_identity_id uuid       REFERENCES provision_identity(id),
    attributes          jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz  NOT NULL DEFAULT now()
);

-- An assertion is an evidence-backed statement extracted from one provision
-- version. Subject/object connect it to graph entities; at least one is needed.
-- evidence_document_unit_id identifies the concrete acquired source passage.
CREATE TABLE legal_assertion (
    id                          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    assertion_type              varchar(80)  NOT NULL,
    statement                   text         NOT NULL,
    subject_entity_id           uuid         REFERENCES knowledge_entity(id),
    object_entity_id            uuid         REFERENCES knowledge_entity(id),
    source_provision_version_id uuid         NOT NULL REFERENCES provision_version(id),
    evidence_document_unit_id   uuid         REFERENCES document_unit(id),
    extraction_method           varchar(40)  NOT NULL,
    verification_status         varchar(30)  NOT NULL DEFAULT 'UNVERIFIED',
    confidence                  numeric(5,4),
    model_info                  jsonb        NOT NULL DEFAULT '{}'::jsonb,
    verified_by                 varchar(100),
    verified_at                 timestamptz,
    created_at                  timestamptz  NOT NULL DEFAULT now(),
    CHECK (subject_entity_id IS NOT NULL OR object_entity_id IS NOT NULL),
    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

-- Directed graph edge: subject_entity --predicate--> object_entity.
-- Every edge is backed by a legal assertion, a provision version, or both.
CREATE TABLE legal_relation (
    id                          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_entity_id           uuid         NOT NULL REFERENCES knowledge_entity(id),
    predicate                   varchar(80)  NOT NULL,
    object_entity_id            uuid         NOT NULL REFERENCES knowledge_entity(id),
    source_assertion_id         uuid         REFERENCES legal_assertion(id),
    source_provision_version_id uuid         REFERENCES provision_version(id),
    verification_status         varchar(30)  NOT NULL DEFAULT 'UNVERIFIED',
    valid_period                daterange,
    created_at                  timestamptz  NOT NULL DEFAULT now(),
    CHECK (subject_entity_id <> object_entity_id),
    CHECK (num_nonnulls(source_assertion_id, source_provision_version_id) >= 1)
);

-- Administrative-act grammar: templates and their required/conditional facts.
-- Parent of drafting_case and one side of the template/requirement N:M link.
CREATE TABLE act_template (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    code                varchar(100) NOT NULL UNIQUE,
    name                text         NOT NULL,
    description         text,
    version             integer      NOT NULL DEFAULT 1,
    is_active           boolean      NOT NULL DEFAULT true,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    CHECK (version > 0)
);

-- Reusable requirement catalogue. Requirements are attached to templates via
-- template_requirement and instantiated at runtime in case_requirement.
CREATE TABLE requirement_definition (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    code                varchar(100) NOT NULL UNIQUE,
    family              varchar(50)  NOT NULL,
    name                text         NOT NULL,
    description         text,
    expected_evidence   jsonb        NOT NULL DEFAULT '{}'::jsonb
);

-- Bridge implementing act_template (N) <-> (M) requirement_definition.
-- Relationship attributes define necessity, order, applicability and formula.
CREATE TABLE template_requirement (
    act_template_id     uuid         NOT NULL REFERENCES act_template(id) ON DELETE CASCADE,
    requirement_id      uuid         NOT NULL REFERENCES requirement_definition(id),
    necessity           varchar(20)  NOT NULL,
    applicability_rule  jsonb        NOT NULL DEFAULT '{}'::jsonb,
    ordinal             integer      NOT NULL,
    preferred_formula   varchar(40),
    PRIMARY KEY (act_template_id, requirement_id),
    UNIQUE (act_template_id, ordinal),
    CHECK (necessity IN ('REQUIRED', 'CONDITIONAL', 'OPTIONAL'))
);

-- A concrete request for a new contribution call/decree.
-- N:1 act_template. Parent of case_requirement, administrative_fact and
-- act_draft, keeping all runtime work scoped to the originating request.
CREATE TABLE drafting_case (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    act_template_id     uuid         NOT NULL REFERENCES act_template(id),
    title               text         NOT NULL,
    user_input          text         NOT NULL,
    jurisdiction        varchar(50)  NOT NULL DEFAULT 'IT-FVG',
    reference_date      date         NOT NULL,
    qualification       jsonb        NOT NULL DEFAULT '{}'::jsonb,
    status              varchar(30)  NOT NULL DEFAULT 'DRAFT',
    created_by          varchar(100),
    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now()
);

-- Facts used for motivation, kept separate from legal assertions.
-- drafting_case_id is nullable so reusable facts can exist outside one case.
-- The three source FKs provide progressively precise provenance: whole act,
-- temporally valid provision, and exact physical passage.
CREATE TABLE administrative_fact (
    id                          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    drafting_case_id            uuid         REFERENCES drafting_case(id) ON DELETE CASCADE,
    fact_type                   varchar(80)  NOT NULL,
    statement                   text         NOT NULL,
    origin_type                 varchar(30)  NOT NULL,
    source_document_id          uuid         REFERENCES source_document(id),
    source_provision_version_id uuid         REFERENCES provision_version(id),
    source_document_unit_id     uuid         REFERENCES document_unit(id),
    declared_by                 varchar(100),
    verification_status         varchar(30)  NOT NULL DEFAULT 'UNVERIFIED',
    valid_period                daterange,
    created_at                  timestamptz  NOT NULL DEFAULT now(),
    CHECK (origin_type IN ('SOURCE_DERIVED', 'PROCEDURALLY_DERIVED', 'USER_PROVIDED')),
    CHECK (
        (origin_type = 'USER_PROVIDED' AND declared_by IS NOT NULL)
        OR origin_type <> 'USER_PROVIDED'
    )
);

-- Runtime instantiation of a requirement for a case. The unique constraint
-- allows at most one current resolution state for each case/requirement pair.
CREATE TABLE case_requirement (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    drafting_case_id    uuid         NOT NULL REFERENCES drafting_case(id) ON DELETE CASCADE,
    requirement_id      uuid         NOT NULL REFERENCES requirement_definition(id),
    status              varchar(30)  NOT NULL DEFAULT 'UNRESOLVED',
    resolution_note     text,
    resolved_at         timestamptz,
    UNIQUE (drafting_case_id, requirement_id),
    CHECK (status IN ('VERIFIED', 'INFERRED_TO_VERIFY', 'UNRESOLVED', 'NOT_APPLICABLE'))
);

-- N:1 case_requirement: revised or alternative proofs can support the same
-- requirement without overwriting earlier reasoning.
CREATE TABLE proof (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    case_requirement_id uuid         NOT NULL REFERENCES case_requirement(id) ON DELETE CASCADE,
    conclusion          text         NOT NULL,
    reasoning           jsonb        NOT NULL DEFAULT '[]'::jsonb,
    status              varchar(30)  NOT NULL DEFAULT 'UNVERIFIED',
    created_at          timestamptz  NOT NULL DEFAULT now(),
    verified_by         varchar(100),
    verified_at         timestamptz
);

-- Ordered proof evidence. This is a polymorphic relationship: exactly one of
-- the five evidence FKs must be populated on each row (enforced by CHECK).
CREATE TABLE proof_evidence (
    proof_id                    uuid         NOT NULL REFERENCES proof(id) ON DELETE CASCADE,
    ordinal                     integer      NOT NULL,
    evidence_role               varchar(50)  NOT NULL,
    provision_version_id        uuid         REFERENCES provision_version(id),
    document_unit_id            uuid         REFERENCES document_unit(id),
    legal_assertion_id          uuid         REFERENCES legal_assertion(id),
    legal_relation_id           uuid         REFERENCES legal_relation(id),
    administrative_fact_id      uuid         REFERENCES administrative_fact(id),
    PRIMARY KEY (proof_id, ordinal),
    CHECK (num_nonnulls(
        provision_version_id, document_unit_id, legal_assertion_id,
        legal_relation_id, administrative_fact_id
    ) = 1)
);

-- Versioned generated output; each clause remains traceable to evidence.
-- N:1 drafting_case: a case retains every generated/reviewed draft version.
CREATE TABLE act_draft (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    drafting_case_id    uuid         NOT NULL REFERENCES drafting_case(id) ON DELETE CASCADE,
    version             integer      NOT NULL,
    status              varchar(30)  NOT NULL DEFAULT 'GENERATED',
    model_info          jsonb        NOT NULL DEFAULT '{}'::jsonb,
    validation_result   jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    approved_by         varchar(100),
    approved_at         timestamptz,
    UNIQUE (drafting_case_id, version),
    CHECK (version > 0)
);

-- N:1 act_draft. ordinal preserves the planned order of VISTO, CONSIDERATO,
-- RITENUTO and other clauses in the generated document.
CREATE TABLE draft_clause (
    id                  uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    act_draft_id        uuid         NOT NULL REFERENCES act_draft(id) ON DELETE CASCADE,
    ordinal             integer      NOT NULL,
    section_type        varchar(40)  NOT NULL,
    formula             varchar(40),
    semantic_role       varchar(100) NOT NULL,
    clause_text         text         NOT NULL,
    validation_status   varchar(30)  NOT NULL DEFAULT 'PENDING',
    UNIQUE (act_draft_id, ordinal)
);

-- Bridge implementing draft_clause (N) <-> (M) proof. It makes each generated
-- clause traceable and permits one proof to support several clauses.
CREATE TABLE draft_clause_evidence (
    draft_clause_id     uuid         NOT NULL REFERENCES draft_clause(id) ON DELETE CASCADE,
    proof_id            uuid         NOT NULL REFERENCES proof(id),
    PRIMARY KEY (draft_clause_id, proof_id)
);

-- Persist the most important relationship documentation in PostgreSQL itself,
-- so it is visible through psql \d+, pgAdmin, DBeaver and schema introspection.
COMMENT ON TABLE source_representation IS
    'N:1 source_document; concrete acquired formats of one legal act.';
COMMENT ON TABLE provision_identity IS
    'N:1 source_document; recursive stable hierarchy of provisions.';
COMMENT ON COLUMN provision_identity.parent_provision_id IS
    'Optional self-reference forming ARTICLE/PARAGRAPH/LETTER/etc. trees.';
COMMENT ON TABLE provision_version IS
    'N:1 provision_identity; non-overlapping temporal versions of its text.';
COMMENT ON COLUMN provision_version.introduced_by_document_id IS
    'Optional provenance link to the act that introduced this version.';
COMMENT ON COLUMN provision_version.introduced_by_provision_id IS
    'Optional provenance link to the exact amending provision.';
COMMENT ON TABLE document_unit IS
    'N:1 source_representation; parsed source tree and optional evidence for a provision_version.';
COMMENT ON COLUMN document_unit.parent_unit_id IS
    'Optional self-reference forming the physical/logical source-document tree.';
COMMENT ON COLUMN document_unit.provision_version_id IS
    'Nullable N:1 evidence link; non-provision units intentionally leave it NULL.';
COMMENT ON TABLE citation IS
    'Reference from exactly one source origin to an optionally resolved document/provision target.';
COMMENT ON TABLE legal_assertion IS
    'Evidence-backed semantic statement linking graph entities to a source provision/version.';
COMMENT ON TABLE legal_relation IS
    'Directed subject-predicate-object edge in the normative/administrative graph.';
COMMENT ON TABLE template_requirement IS
    'N:M bridge between act templates and reusable requirement definitions.';
COMMENT ON TABLE case_requirement IS
    'Runtime state of one reusable requirement within one drafting case.';
COMMENT ON TABLE proof_evidence IS
    'Ordered polymorphic evidence for a proof; exactly one evidence FK per row.';
COMMENT ON TABLE draft_clause_evidence IS
    'N:M bridge connecting generated clauses to the proofs that justify them.';

-- Frequently used traversal and retrieval indexes.
CREATE INDEX idx_source_document_date ON source_document (document_date);
CREATE INDEX idx_source_document_title_trgm ON source_document USING gin (title gin_trgm_ops);
CREATE INDEX idx_source_representation_document ON source_representation (source_document_id);
CREATE INDEX idx_document_unit_parent ON document_unit (parent_unit_id);
CREATE INDEX idx_document_unit_version ON document_unit (provision_version_id);
CREATE INDEX idx_document_unit_text_trgm ON document_unit USING gin (normalized_text gin_trgm_ops);
CREATE INDEX idx_provision_identity_parent ON provision_identity (parent_provision_id);
CREATE INDEX idx_provision_identity_document ON provision_identity (source_document_id);
CREATE INDEX idx_provision_version_period ON provision_version USING gist (valid_period);
CREATE INDEX idx_citation_target ON citation (target_provision_id) WHERE target_provision_id IS NOT NULL;
CREATE INDEX idx_knowledge_entity_type ON knowledge_entity (entity_type);
CREATE INDEX idx_legal_relation_subject ON legal_relation (subject_entity_id, predicate);
CREATE INDEX idx_legal_relation_object ON legal_relation (object_entity_id, predicate);
CREATE INDEX idx_administrative_fact_case ON administrative_fact (drafting_case_id, fact_type);
CREATE INDEX idx_case_requirement_status ON case_requirement (drafting_case_id, status);

-- Prevent cross-document parents in both recursive trees.
CREATE FUNCTION check_provision_parent_document() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE parent_document_id uuid;
BEGIN
    IF NEW.parent_provision_id IS NOT NULL THEN
        SELECT source_document_id INTO parent_document_id
        FROM provision_identity WHERE id = NEW.parent_provision_id;
        IF parent_document_id IS DISTINCT FROM NEW.source_document_id THEN
            RAISE EXCEPTION 'Provision parent must belong to the same source document';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_provision_parent_document
BEFORE INSERT OR UPDATE OF parent_provision_id, source_document_id ON provision_identity
FOR EACH ROW EXECUTE FUNCTION check_provision_parent_document();

CREATE FUNCTION check_document_unit_parent_representation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE parent_representation_id uuid;
BEGIN
    IF NEW.parent_unit_id IS NOT NULL THEN
        SELECT source_representation_id INTO parent_representation_id
        FROM document_unit WHERE id = NEW.parent_unit_id;
        IF parent_representation_id IS DISTINCT FROM NEW.source_representation_id THEN
            RAISE EXCEPTION 'Document-unit parent must belong to the same representation';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_document_unit_parent_representation
BEFORE INSERT OR UPDATE OF parent_unit_id, source_representation_id ON document_unit
FOR EACH ROW EXECUTE FUNCTION check_document_unit_parent_representation();

-- Seed the act template and requirements explicitly described in the PDF.
INSERT INTO act_template (code, name, description)
VALUES ('GRANT_CALL_APPROVAL_DECREE', 'Decreto di approvazione di un bando contributivo',
        'Template per il preambolo e la motivazione di un decreto dirigenziale FVG.');

INSERT INTO requirement_definition (code, family, name) VALUES
('LEGAL_BASIS',                  'LEGAL',      'Base giuridica della misura'),
('IMPLEMENTING_RULES',           'LEGAL',      'Disciplina attuativa'),
('ADMINISTRATIVE_PROCEDURE',     'LEGAL',      'Disciplina del procedimento'),
('FINANCIAL_COVERAGE',           'LEGAL',      'Copertura finanziaria'),
('ORGANIZATIONAL_COMPETENCE',    'LEGAL',      'Competenza organizzativa'),
('SIGNATORY_AUTHORITY',          'LEGAL',      'Potere di firma'),
('STATE_AID',                    'LEGAL',      'Disciplina degli aiuti di Stato'),
('EU_PROGRAMMING',               'LEGAL',      'Programmazione europea'),
('ADMINISTRATIVE_NEED',          'MOTIVATION', 'Esigenza amministrativa'),
('POLICY_OBJECTIVE',             'MOTIVATION', 'Obiettivo di politica pubblica'),
('BENEFICIARY_RATIONALE',        'MOTIVATION', 'Motivazione dei beneficiari'),
('INTERVENTION_RATIONALE',       'MOTIVATION', 'Motivazione degli interventi'),
('PROCEDURAL_CHOICE',            'MOTIVATION', 'Motivazione della procedura'),
('RESOURCE_ALLOCATION_RATIONALE','MOTIVATION', 'Motivazione dell’allocazione delle risorse'),
('CALL_APPROVAL_RATIONALE',      'MOTIVATION', 'Motivazione dell’approvazione del bando');

INSERT INTO template_requirement
    (act_template_id, requirement_id, necessity, ordinal, preferred_formula)
SELECT at.id, rd.id,
       CASE WHEN rd.code IN ('STATE_AID', 'EU_PROGRAMMING', 'BENEFICIARY_RATIONALE',
                             'INTERVENTION_RATIONALE', 'PROCEDURAL_CHOICE',
                             'RESOURCE_ALLOCATION_RATIONALE')
            THEN 'CONDITIONAL' ELSE 'REQUIRED' END,
       row_number() OVER (ORDER BY CASE rd.family WHEN 'LEGAL' THEN 1 ELSE 2 END, rd.code),
       CASE
         WHEN rd.family = 'LEGAL' THEN 'VISTO'
         WHEN rd.code = 'CALL_APPROVAL_RATIONALE' THEN 'RITENUTO'
         ELSE 'CONSIDERATO'
       END
FROM act_template at
CROSS JOIN requirement_definition rd
WHERE at.code = 'GRANT_CALL_APPROVAL_DECREE';

COMMIT;

-- Example temporal lookup:
-- SELECT pv.*
-- FROM fvg_laws.provision_version pv
-- JOIN fvg_laws.provision_identity pi ON pi.id = pv.provision_identity_id
-- WHERE pi.canonical_key = 'IT-FVG:LR:2000:7:ART30'
--   AND pv.valid_period @> DATE '2026-05-20';
