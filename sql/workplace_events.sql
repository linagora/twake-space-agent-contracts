-- The table this service reads. Storage writes it from twake.workplace.events.v1,
-- and its migration must create exactly this.
CREATE TABLE IF NOT EXISTS workplace_events (
    id          text        PRIMARY KEY,
    type        text        NOT NULL,
    org         text,
    actor       text,
    targets     text[]      NOT NULL,
    subject     text,
    time        timestamptz NOT NULL,
    data        jsonb       NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS workplace_events_targets_idx ON workplace_events USING gin (targets);
CREATE INDEX IF NOT EXISTS workplace_events_time_idx ON workplace_events (time DESC);

-- The contracts find a user's events by the email of their targets, the subject of their token
CREATE INDEX IF NOT EXISTS workplace_events_target_emails_idx
    ON workplace_events USING gin ((data -> 'targets') jsonb_path_ops);
