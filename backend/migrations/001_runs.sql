CREATE TABLE IF NOT EXISTS runs (
    id          uuid PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz,
    status      text NOT NULL CHECK (status IN ('running', 'done', 'failed')),
    stage       text,
    progress    real NOT NULL DEFAULT 0,
    request     jsonb NOT NULL,
    result      jsonb,
    error       text
);

CREATE TABLE IF NOT EXISTS run_periods (
    run_id          uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    period          date NOT NULL,
    curve_date      date,
    deals           integer NOT NULL,
    npv_base        double precision NOT NULL,
    ec              double precision NOT NULL,
    worst_scenario  text NOT NULL,
    dnpv            jsonb NOT NULL,
    PRIMARY KEY (run_id, period)
);

CREATE TABLE IF NOT EXISTS explanations (
    id            uuid PRIMARY KEY,
    run_id        uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    question      text,
    provider      text NOT NULL,
    model         text,
    answer        jsonb NOT NULL,
    verification  jsonb NOT NULL,
    facts         jsonb NOT NULL DEFAULT '[]',
    note          text
);

CREATE INDEX IF NOT EXISTS runs_created_at ON runs (created_at DESC);
CREATE INDEX IF NOT EXISTS explanations_run ON explanations (run_id, created_at);
