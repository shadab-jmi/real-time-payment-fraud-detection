CREATE DOMAIN sha256_hex AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE DOMAIN country_code AS text CHECK (
    VALUE = ANY(string_to_array('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW', ' '))
);
CREATE TABLE runs (
    id uuid PRIMARY KEY,
    seed bigint NOT NULL,
    generator_version text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    clock_mode text NOT NULL CHECK (clock_mode IN ('wall','logical'))
);
CREATE TABLE accounts (
    id text PRIMARY KEY CHECK (id ~ '^acct_[A-Za-z0-9_-]{1,59}$'),
    run_id uuid NOT NULL REFERENCES runs,
    next_seq bigint NOT NULL DEFAULT 1,
    last_scored_seq bigint NOT NULL DEFAULT 0,
    last_accepted_at timestamptz,
    CHECK (last_scored_seq >= 0 AND next_seq > last_scored_seq),
    UNIQUE (run_id, id)
);
CREATE TABLE transactions (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs,
    account_id text NOT NULL,
    account_seq bigint NOT NULL CHECK (account_seq >= 1),
    merchant_id text NOT NULL CHECK (merchant_id ~ '^mer_[A-Za-z0-9_-]{1,60}$'),
    device_id text CHECK (device_id ~ '^dev_[A-Za-z0-9_-]{1,60}$'),
    channel text NOT NULL CHECK (channel IN ('app','web','pos')),
    amount_minor bigint NOT NULL CHECK (amount_minor BETWEEN 1 AND 100000000),
    currency text NOT NULL CHECK (currency = 'INR'),
    account_country country_code NOT NULL,
    merchant_country country_code NOT NULL,
    event_time timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    accepted_at timestamptz NOT NULL,
    request_hash sha256_hex NOT NULL,
    FOREIGN KEY (run_id, account_id) REFERENCES accounts(run_id,id),
    CONSTRAINT uq_account_sequence UNIQUE (account_id, account_seq)
);
CREATE INDEX transactions_run_time ON transactions(run_id, received_at DESC, id DESC);
CREATE INDEX transactions_history ON transactions(account_id, accepted_at, account_seq);
CREATE INDEX transactions_device ON transactions(device_id, event_time);
CREATE TABLE events (
    event_id uuid PRIMARY KEY,
    transaction_id uuid REFERENCES transactions,
    account_id text REFERENCES accounts,
    account_seq bigint CHECK (account_seq >= 1),
    type text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    correlation_id uuid NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_transaction_time ON events(transaction_id, occurred_at, event_id);
CREATE TABLE outbox (
    event_id uuid PRIMARY KEY REFERENCES events,
    topic text NOT NULL,
    key text NOT NULL,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    last_error text
);
CREATE INDEX outbox_pending ON outbox(next_attempt_at) WHERE published_at IS NULL;
CREATE FUNCTION reject_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical row is immutable' USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER immutable_receipt BEFORE UPDATE OR DELETE ON transactions
FOR EACH ROW EXECUTE FUNCTION reject_history_change();
GRANT SELECT, INSERT ON runs, accounts, transactions, events, outbox TO fraud_runtime;
GRANT UPDATE ON accounts, outbox TO fraud_runtime;
