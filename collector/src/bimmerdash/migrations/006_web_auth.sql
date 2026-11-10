-- Web UI accounts and sessions (used by bimmerdash-web).
CREATE TABLE web_users (
    username      text PRIMARY KEY,
    password_hash text        NOT NULL,          -- scrypt$n$r$p$salt$hash
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE web_sessions (
    token_hash text PRIMARY KEY,                 -- sha256 of the cookie value
    username   text        NOT NULL REFERENCES web_users ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    user_agent text
);
CREATE INDEX web_sessions_expires ON web_sessions (expires_at);
