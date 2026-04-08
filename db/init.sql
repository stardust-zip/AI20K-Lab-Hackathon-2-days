-- =============================================================================
-- Vinmec AI Triage – Database Initialisation Script
-- Apply to Supabase via the SQL editor or psql.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

-- pgvector: enables VECTOR column type and cosine-similarity operator (<=>)
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- ENUM types
-- ---------------------------------------------------------------------------

CREATE TYPE triage_resolution AS ENUM (
    'AI_AUTO',
    'NURSE_APPROVED',
    'NURSE_CORRECTED',
    'DOCTOR_CORRECTED'
);

CREATE TYPE queue_status AS ENUM (
    'PENDING',
    'RESOLVED',
    'TIMEOUT'
);

-- ---------------------------------------------------------------------------
-- Table: departments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS departments (
    id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50)  UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL
);

-- ---------------------------------------------------------------------------
-- Table: triage_logs  (Semantic Memory / Flywheel)
--
-- Every triage attempt is logged here.
-- symptom_embedding is used for future semantic retrieval and model improvement.
-- final_dept / resolution_type are back-filled by the nurse resolution flow.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS triage_logs (
    id                  UUID      PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_symptoms        TEXT      NOT NULL,
    symptom_embedding   VECTOR(1536),                   -- OpenAI text-embedding-3-small
    ai_suggested_dept   VARCHAR(255),
    confidence          FLOAT,
    final_dept          VARCHAR(255),                   -- filled in after nurse review
    resolution_type     triage_resolution,              -- filled in after nurse review
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for fast ANN search on symptom embeddings (cosine distance)
CREATE INDEX IF NOT EXISTS idx_triage_logs_embedding
    ON triage_logs
    USING ivfflat (symptom_embedding vector_cosine_ops)
    WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- Table: human_triage_queue
--
-- Low-confidence cases (confidence < 85) are inserted here.
-- Nurses see these on their dashboard and approve or correct the routing.
-- SLA: items older than 3 minutes without resolution are marked TIMEOUT.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS human_triage_queue (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id       VARCHAR(255) NOT NULL,
    clinical_summary TEXT         NOT NULL,
    suggested_dept   VARCHAR(255),
    status           queue_status DEFAULT 'PENDING',
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for fast lookup of PENDING items (nurse dashboard polling)
CREATE INDEX IF NOT EXISTS idx_human_triage_queue_status
    ON human_triage_queue (status, created_at ASC);

-- ---------------------------------------------------------------------------
-- Table: red_flags
--
-- Stores Vietnamese emergency keyword embeddings for semantic red-flag detection.
--
-- HOW TO POPULATE:
--   Embeddings cannot be pre-inserted in plain SQL because they must be
--   generated at runtime via the OpenAI Embeddings API.
--
--   After running this migration, call the seed endpoint once:
--
--       POST /api/v1/admin/seed-red-flags
--
--   That endpoint will:
--     1. Iterate over all 15 Vietnamese emergency keywords defined in config.py.
--     2. Call OpenAI text-embedding-3-small to generate a 1536-dim vector for each.
--     3. Upsert each row via ON CONFLICT (keyword) DO UPDATE – so it is safe
--        to call multiple times (e.g. after switching embedding models).
--
-- Emergency keywords that will be seeded:
--   đau thắt ngực, nhồi máu cơ tim, đột quỵ, liệt nửa người, khó thở nặng,
--   xuất huyết não, co giật, mất ý thức, ngừng tim, suy hô hấp,
--   vỡ động mạch, chấn thương đầu nặng, sốc phản vệ, băng huyết sau sinh, hôn mê
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS red_flags (
    id        UUID  PRIMARY KEY DEFAULT gen_random_uuid(),
    keyword   TEXT  UNIQUE NOT NULL,               -- Vietnamese emergency term
    embedding VECTOR(1536)                          -- populated by /admin/seed-red-flags
);

-- Index for fast ANN cosine similarity search against symptom embeddings
CREATE INDEX IF NOT EXISTS idx_red_flags_embedding
    ON red_flags
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);   -- small table, 10 lists is sufficient

-- ---------------------------------------------------------------------------
-- Seed: departments
-- ---------------------------------------------------------------------------

INSERT INTO departments (code, name) VALUES
    ('TIM_MACH',          'Nội Tim Mạch'),
    ('NGOAI_TH',          'Ngoại Tiêu hoá'),
    ('THAN_KINH',         'Nội Thần Kinh'),
    ('SAN_PHU',           'Sản Phụ Khoa'),
    ('NHI',               'Nhi Khoa'),
    ('DA_LIEU',           'Da liễu'),
    ('MAT',               'Nhãn Khoa'),
    ('TAI_MUI_HONG',      'Tai Mũi Họng'),
    ('CO_XUONG_KHOP',     'Cơ Xương Khớp'),
    ('NGOAI_CHINH_HINH',  'Ngoại Chỉnh hình')
ON CONFLICT (code) DO NOTHING;
