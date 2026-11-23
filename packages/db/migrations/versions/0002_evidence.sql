CREATE TABLE feature_schemas (
    id text PRIMARY KEY, ordered_manifest jsonb NOT NULL CHECK (jsonb_typeof(ordered_manifest) = 'array'),
    digest sha256_hex NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE model_versions (
    id uuid PRIMARY KEY, version text NOT NULL UNIQUE, schema_id text NOT NULL REFERENCES feature_schemas,
    artifact_path text NOT NULL, artifact_sha256 sha256_hex NOT NULL, metrics jsonb NOT NULL, lineage jsonb NOT NULL,
    review_threshold double precision NOT NULL, block_threshold double precision NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), active boolean NOT NULL DEFAULT false,
    CHECK (0 < review_threshold AND review_threshold < block_threshold AND block_threshold < 1),
    UNIQUE (id,schema_id)
);
CREATE UNIQUE INDEX one_active_model ON model_versions(active) WHERE active;
CREATE TABLE feature_snapshots (
    id uuid PRIMARY KEY, transaction_id uuid NOT NULL UNIQUE REFERENCES transactions,
    schema_id text NOT NULL REFERENCES feature_schemas, history_through_seq bigint NOT NULL CHECK (history_through_seq >= 0),
    values jsonb NOT NULL CHECK (jsonb_typeof(values) = 'array'), created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id,transaction_id,schema_id)
);
CREATE TABLE decisions (
    id uuid PRIMARY KEY, transaction_id uuid NOT NULL UNIQUE REFERENCES transactions,
    model_version_id uuid NOT NULL, snapshot_id uuid NOT NULL, schema_id text NOT NULL,
    score double precision NOT NULL CHECK (score BETWEEN 0 AND 1),
    action text NOT NULL CHECK (action IN ('approve','review','block')),
    decided_at timestamptz NOT NULL DEFAULT now(), latency_ms bigint NOT NULL CHECK (latency_ms >= 0),
    policy_version text NOT NULL,
    FOREIGN KEY (snapshot_id,transaction_id,schema_id) REFERENCES feature_snapshots(id,transaction_id,schema_id),
    FOREIGN KEY (model_version_id,schema_id) REFERENCES model_versions(id,schema_id)
);
CREATE INDEX decisions_action_time ON decisions(action, decided_at DESC, id DESC);
CREATE TABLE explanations (
    decision_id uuid PRIMARY KEY REFERENCES decisions, method text NOT NULL, output_space text NOT NULL,
    bias double precision NOT NULL, raw_margin double precision NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE contributions (
    decision_id uuid NOT NULL REFERENCES explanations, feature_index integer NOT NULL CHECK (feature_index >= 0),
    feature_name text NOT NULL, feature_value double precision NOT NULL, signed_value double precision NOT NULL,
    PRIMARY KEY (decision_id,feature_index)
);
CREATE TABLE users (
    id uuid PRIMARY KEY, username text NOT NULL UNIQUE, password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('viewer','analyst')), disabled boolean NOT NULL DEFAULT false
);
CREATE TABLE sessions (
    token_hash sha256_hex PRIMARY KEY, user_id uuid NOT NULL REFERENCES users, expires_at timestamptz NOT NULL
);
CREATE TABLE cases (
    id uuid PRIMARY KEY, opening_decision_id uuid NOT NULL UNIQUE REFERENCES decisions,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','investigating','resolved')),
    priority text NOT NULL CHECK (priority IN ('normal','high')),
    opened_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1), assignee uuid REFERENCES users,
    resolution text, resolved_by uuid REFERENCES users,
    CHECK (status <> 'resolved' OR (resolution IS NOT NULL AND length(trim(resolution)) > 0 AND resolved_by IS NOT NULL))
);
CREATE TABLE case_transactions (
    case_id uuid NOT NULL REFERENCES cases, transaction_id uuid NOT NULL REFERENCES transactions,
    relation text NOT NULL, added_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (case_id,transaction_id)
);
CREATE INDEX case_transactions_reverse ON case_transactions(transaction_id);
CREATE TABLE case_notes (
    id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES cases, author_id uuid NOT NULL REFERENCES users,
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 2000), created_at timestamptz NOT NULL DEFAULT now(),
    supersedes_note_id uuid, UNIQUE (id,case_id),
    FOREIGN KEY (supersedes_note_id,case_id) REFERENCES case_notes(id,case_id)
);
CREATE TRIGGER immutable_note BEFORE UPDATE OR DELETE ON case_notes FOR EACH ROW EXECUTE FUNCTION reject_history_change();
CREATE TABLE case_events (
    id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES cases, actor_id uuid REFERENCES users,
    kind text NOT NULL, before_after jsonb NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now(), correlation_id uuid NOT NULL
);
CREATE TABLE quarantine (
    id uuid PRIMARY KEY, topic text NOT NULL, partition integer NOT NULL, "offset" bigint NOT NULL,
    reason_code text NOT NULL, transaction_id uuid REFERENCES transactions, resolved_at timestamptz,
    UNIQUE (topic,partition,"offset")
);
CREATE SCHEMA evaluation;
CREATE TABLE evaluation.labels (
    transaction_id uuid PRIMARY KEY, label boolean NOT NULL, scenario text NOT NULL,
    available_at timestamptz NOT NULL, dataset_run uuid NOT NULL
);
CREATE VIEW v_case_evidence_v1 AS
SELECT ct.case_id, ct.relation, t.id AS transaction_id, t.run_id, t.account_id, t.device_id, t.merchant_id,
       t.amount_minor, t.currency, t.event_time, t.received_at, t.accepted_at, d.id AS decision_id,
       d.action, d.score, d.policy_version, s.id AS snapshot_id, s.history_through_seq, s.values AS feature_values,
       m.id AS model_version_id, m.version AS model_version, m.artifact_sha256, m.review_threshold, m.block_threshold,
       f.id AS schema_id, f.ordered_manifest
FROM case_transactions ct JOIN transactions t ON t.id = ct.transaction_id
LEFT JOIN decisions d ON d.transaction_id = t.id
LEFT JOIN feature_snapshots s ON s.id = d.snapshot_id
LEFT JOIN model_versions m ON m.id = d.model_version_id
LEFT JOIN feature_schemas f ON f.id = d.schema_id;
CREATE VIEW v_entity_edges_v1 AS
SELECT e.entity_type, e.entity_id, t.id AS transaction_id, t.accepted_at AS observed_at, t.id AS source_transaction_id
FROM transactions t CROSS JOIN LATERAL (
    VALUES ('account',t.account_id), ('device',t.device_id), ('merchant',t.merchant_id)
) e(entity_type,entity_id) WHERE e.entity_id IS NOT NULL;
CREATE VIEW v_case_notes_v1 AS
SELECT id AS note_id, case_id, author_id, body, created_at, supersedes_note_id FROM case_notes;
CREATE VIEW v_decision_contributions_v1 AS
SELECT c.decision_id, d.transaction_id, c.feature_index, c.feature_name, c.feature_value, c.signed_value,
       e.bias, e.raw_margin, e.method, e.output_space
FROM contributions c JOIN explanations e USING (decision_id) JOIN decisions d ON d.id = c.decision_id;
GRANT SELECT ON v_case_evidence_v1, v_entity_edges_v1, v_case_notes_v1, v_decision_contributions_v1 TO fraud_reader;
GRANT SELECT ON feature_schemas, model_versions, users TO fraud_runtime;
GRANT SELECT, INSERT ON decisions, feature_snapshots, explanations, contributions, case_notes, case_events, case_transactions TO fraud_runtime;
GRANT SELECT, INSERT, UPDATE ON cases, quarantine TO fraud_runtime;
GRANT SELECT, INSERT, DELETE ON sessions TO fraud_runtime;
