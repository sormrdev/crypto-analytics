from crypto_analytics.config import postgres_env


def test_postgres_env_splits_the_url_for_dbt():
    env = postgres_env("postgresql+psycopg://ana:s3cret@db.example.com:6543/crypto?sslmode=require")
    assert env == {"PGHOST": "db.example.com", "PGPORT": "6543", "PGUSER": "ana", "PGPASSWORD": "s3cret",
                   "PGDATABASE": "crypto", "PGSSLMODE": "require"}


def test_postgres_env_defaults_port_and_omits_sslmode():
    env = postgres_env("postgresql+psycopg://postgres:pw@localhost/kraken_spot_analysis")
    assert env["PGPORT"] == "5432" and "PGSSLMODE" not in env
