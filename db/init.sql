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

-- ---------------------------------------------------------------------------
-- Table: clinics
-- One representative clinic per department (used for "nearest clinic" lookup)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS clinics (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    address         TEXT         NOT NULL,
    department_code VARCHAR(50)  NOT NULL REFERENCES departments(code) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_clinics_dept ON clinics (department_code);

-- ---------------------------------------------------------------------------
-- Table: doctors
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS doctors (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    specialty       VARCHAR(255) NOT NULL,
    department_code VARCHAR(50)  NOT NULL REFERENCES departments(code) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_doctors_dept ON doctors (department_code);

-- ---------------------------------------------------------------------------
-- Table: appointments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS appointments (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id       VARCHAR(255) NOT NULL,
    doctor_id        UUID         NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    department_code  VARCHAR(50)  NOT NULL,
    appointment_time TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Seed: clinics (one per department)
-- ---------------------------------------------------------------------------

INSERT INTO clinics (name, address, department_code) VALUES
    -- Times City (Hai Bà Trưng)
    ('Vinmec Times City – Nội Tim Mạch',    '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'TIM_MACH'),
    ('Vinmec Times City – Ngoại Tiêu hoá',  '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'NGOAI_TH'),
    ('Vinmec Times City – Nội Thần Kinh',   '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'THAN_KINH'),
    ('Vinmec Times City – Sản Phụ Khoa',    '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'SAN_PHU'),
    ('Vinmec Times City – Nhi Khoa',        '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'NHI'),
    ('Vinmec Times City – Da liễu',         '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'DA_LIEU'),
    ('Vinmec Times City – Nhãn Khoa',       '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'MAT'),
    ('Vinmec Times City – Tai Mũi Họng',    '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'TAI_MUI_HONG'),
    ('Vinmec Times City – Cơ Xương Khớp',   '458 Minh Khai, Hai Bà Trưng, Hà Nội',  'CO_XUONG_KHOP'),
    ('Vinmec Times City – Ngoại Chỉnh hình','458 Minh Khai, Hai Bà Trưng, Hà Nội',  'NGOAI_CHINH_HINH'),
    -- Royal City (Thanh Xuân)
    ('Vinmec Royal City – Nội Tim Mạch',    '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'TIM_MACH'),
    ('Vinmec Royal City – Ngoại Tiêu hoá',  '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'NGOAI_TH'),
    ('Vinmec Royal City – Nội Thần Kinh',   '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'THAN_KINH'),
    ('Vinmec Royal City – Sản Phụ Khoa',    '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'SAN_PHU'),
    ('Vinmec Royal City – Nhi Khoa',        '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'NHI'),
    ('Vinmec Royal City – Da liễu',         '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'DA_LIEU'),
    ('Vinmec Royal City – Nhãn Khoa',       '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'MAT'),
    ('Vinmec Royal City – Tai Mũi Họng',    '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'TAI_MUI_HONG'),
    ('Vinmec Royal City – Cơ Xương Khớp',   '72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'CO_XUONG_KHOP'),
    ('Vinmec Royal City – Ngoại Chỉnh hình','72A Nguyễn Trãi, Thanh Xuân, Hà Nội',  'NGOAI_CHINH_HINH'),
    -- Ocean Park (Đông Anh)
    ('Vinmec Ocean Park – Nội Tim Mạch',    '2 Hải Bối, Đông Anh, Hà Nội',          'TIM_MACH'),
    ('Vinmec Ocean Park – Ngoại Tiêu hoá',  '2 Hải Bối, Đông Anh, Hà Nội',          'NGOAI_TH'),
    ('Vinmec Ocean Park – Nội Thần Kinh',   '2 Hải Bối, Đông Anh, Hà Nội',          'THAN_KINH'),
    ('Vinmec Ocean Park – Sản Phụ Khoa',    '2 Hải Bối, Đông Anh, Hà Nội',          'SAN_PHU'),
    ('Vinmec Ocean Park – Nhi Khoa',        '2 Hải Bối, Đông Anh, Hà Nội',          'NHI'),
    ('Vinmec Ocean Park – Da liễu',         '2 Hải Bối, Đông Anh, Hà Nội',          'DA_LIEU'),
    ('Vinmec Ocean Park – Nhãn Khoa',       '2 Hải Bối, Đông Anh, Hà Nội',          'MAT'),
    ('Vinmec Ocean Park – Tai Mũi Họng',    '2 Hải Bối, Đông Anh, Hà Nội',          'TAI_MUI_HONG'),
    ('Vinmec Ocean Park – Cơ Xương Khớp',   '2 Hải Bối, Đông Anh, Hà Nội',          'CO_XUONG_KHOP'),
    ('Vinmec Ocean Park – Ngoại Chỉnh hình','2 Hải Bối, Đông Anh, Hà Nội',          'NGOAI_CHINH_HINH')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- Seed: doctors (~2 per department)
-- ---------------------------------------------------------------------------

INSERT INTO doctors (name, specialty, department_code) VALUES
    ('BS. Nguyễn Văn An',      'Tim mạch can thiệp',          'TIM_MACH'),
    ('BS. Trần Thị Bình',      'Rối loạn nhịp tim',           'TIM_MACH'),
    ('BS. Lê Hoàng Cường',     'Phẫu thuật tiêu hoá',         'NGOAI_TH'),
    ('BS. Phạm Thị Dung',      'Nội soi tiêu hoá',            'NGOAI_TH'),
    ('BS. Vũ Minh Đức',        'Thần kinh học lâm sàng',      'THAN_KINH'),
    ('BS. Hoàng Thị Lan',      'Đau đầu và đột quỵ',          'THAN_KINH'),
    ('BS. Ngô Thị Mai',        'Sản khoa',                    'SAN_PHU'),
    ('BS. Đinh Văn Nam',       'Phụ khoa – Nội tiết sinh sản','SAN_PHU'),
    ('BS. Trịnh Thu Hà',       'Nhi tổng quát',               'NHI'),
    ('BS. Bùi Quang Huy',      'Nhi sơ sinh',                 'NHI'),
    ('BS. Đặng Thị Kim',       'Da liễu thẩm mỹ',             'DA_LIEU'),
    ('BS. Lý Văn Long',        'Dị ứng – miễn dịch da',       'DA_LIEU'),
    ('BS. Phan Thị Minh',      'Nhãn khoa tổng quát',         'MAT'),
    ('BS. Cao Văn Nghĩa',      'Phẫu thuật mắt',              'MAT'),
    ('BS. Dương Thị Oanh',     'Tai mũi họng tổng quát',      'TAI_MUI_HONG'),
    ('BS. Lương Quốc Phong',   'Phẫu thuật nội soi TMH',      'TAI_MUI_HONG'),
    ('BS. Mai Thị Quỳnh',      'Cơ xương khớp nội khoa',      'CO_XUONG_KHOP'),
    ('BS. Hồ Văn Sơn',         'Thấp khớp học',               'CO_XUONG_KHOP'),
    ('BS. Tô Thị Thanh',       'Chỉnh hình chấn thương',      'NGOAI_CHINH_HINH'),
    ('BS. Nguyễn Đức Uy',      'Phẫu thuật khớp và cột sống', 'NGOAI_CHINH_HINH')
ON CONFLICT DO NOTHING;
