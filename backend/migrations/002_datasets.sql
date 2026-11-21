-- Synthetic portfolios built by the generator; the CSV itself lives in DATASETS_DIR.
CREATE TABLE IF NOT EXISTS datasets (
    id          uuid PRIMARY KEY,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz,
    status      text NOT NULL CHECK (status IN ('running', 'done', 'failed')),
    rows        integer NOT NULL,
    written     integer NOT NULL DEFAULT 0,
    size_bytes  bigint,
    params      jsonb NOT NULL,
    path        text NOT NULL,
    error       text
);

CREATE INDEX IF NOT EXISTS datasets_created_idx ON datasets (created_at DESC);
