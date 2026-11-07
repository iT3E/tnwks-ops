"""Rehearse an encrypted Grafana PostgreSQL backup on an isolated Docker network.

The private fixture stays encrypted on disk. PostgreSQL, extracted configuration
and Grafana data live in container tmpfs mounts. The internal network blocks real
datasource access and notifications from the restored database. No live API is
called and no Docker logs or private database contents are printed.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from rehearse_archive import docker, eventually
from versions import GRAFANA

POSTGRES = "postgres:18.6@sha256:e3d24f6906d0760bf4da0dae872415391ededefad23fd471c47dc53b0ddf3c42"
BASELINE = "grafana/grafana:12.3.1@sha256:2175aaa91c96733d86d31cf270d5310b278654b03f5718c59de12a865380a31f"


def restore_stream(identity, source, target):
    decrypt = subprocess.Popen(["age", "-d", "-i", str(identity), str(source)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    destination = subprocess.Popen(["docker", "exec", "-i", *target], stdin=decrypt.stdout,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    decrypt.stdout.close()
    _, errors = destination.communicate()
    _, decrypt_errors = decrypt.communicate()
    assert destination.returncode == decrypt.returncode == 0, (
        "Private fixture restore failed; response details suppressed",
        len(errors), len(decrypt_errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-dump", required=True, type=Path)
    parser.add_argument("--provisioning-archive", required=True, type=Path)
    parser.add_argument("--identity", required=True, type=Path)
    args = parser.parse_args()
    for source in [args.database_dump, args.provisioning_archive, args.identity]:
        assert source.is_file()
    suffix = secrets.token_hex(4)
    network, database, grafana = [f"obs-migration-{suffix}-{v}" for v in ["net", "db", "grafana"]]
    password = secrets.token_hex(32)
    db_env = dict(os.environ, POSTGRES_PASSWORD=secrets.token_hex(32))
    url = "http://127.0.0.1:13501"

    def sql(statement):
        result = subprocess.run(["docker", "exec", "-i", database, "psql", "-U", "postgres",
                                 "-d", "grafana", "-At", "-v", "ON_ERROR_STOP=1"],
                                input=statement, capture_output=True, text=True)
        assert result.returncode == 0, "Local database check failed; private details suppressed"
        return result.stdout.strip()

    def database_restore():
        # This database exists only in the disposable container, never Kubernetes.
        subprocess.run(["docker", "exec", "-i", database, "psql", "-U", "postgres",
                        "-v", "ON_ERROR_STOP=1"], input=(
            "DROP DATABASE IF EXISTS grafana WITH (FORCE);\n"
            "CREATE DATABASE grafana OWNER grafana TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C';\n"
        ), text=True, capture_output=True, check=True)
        restore_stream(args.identity, args.database_dump,
                       [database, "pg_restore", "-U", "grafana", "-d", "grafana",
                        "--no-owner", "--no-acl", "--exit-on-error"])

    def api(path):
        # An internal Docker network intentionally blocks published-port ingress.
        result = subprocess.run(["docker", "exec", grafana, "wget", "-qO-",
                                 "http://127.0.0.1:3000" + path], capture_output=True)
        assert result.returncode == 0, "Disposable Grafana API is not ready"
        return json.loads(result.stdout)

    def snapshot():
        dashboards = api("/api/search?type=dash-db&limit=1000")
        sources = api("/api/datasources")
        # Hash private identifiers rather than writing fixture data to output.
        uids = sorted(item["uid"] for item in dashboards)
        return {"dashboards": len(uids), "uids": hashlib.sha256(json.dumps(uids).encode()).hexdigest(),
                "sources": sorted((x["uid"], x["type"]) for x in sources),
                "users": sql('SELECT count(*) FROM public."user";'),
                "user_hashes": sql('SELECT md5(string_agg(password,\',\' ORDER BY id)) FROM public."user";')}

    def launch(image):
        env = dict(os.environ, GF_DATABASE_USER="grafana", GF_DATABASE_PASSWORD=password,
            GF_DATABASE_TYPE="postgres", GF_DATABASE_HOST="db:5432", GF_DATABASE_NAME="grafana",
            GF_DATABASE_SSL_MODE="disable", GF_AUTH_ANONYMOUS_ENABLED="true",
            GF_AUTH_ANONYMOUS_ORG_ROLE="Viewer", GF_AUTH_PROXY_ENABLED="false",
            GF_AUTH_JWT_ENABLED="false", GF_SMTP_ENABLED="false",
            GF_UNIFIED_ALERTING_EXECUTE_ALERTS="false", GF_ALERTING_EXECUTE_ALERTS="false",
            GF_ANALYTICS_REPORTING_ENABLED="false", GF_ANALYTICS_CHECK_FOR_UPDATES="false",
            GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES="false", GF_PLUGINS_PREINSTALL_DISABLED="true",
            GF_PLUGINS_PREINSTALL_AUTO_UPDATE="false", GF_SERVER_HTTP_ADDR="0.0.0.0",
            GF_SERVER_HTTP_PORT="3000", GF_SERVER_ROOT_URL=url + "/",
            GF_RENDERING_SERVER_URL="", GF_RENDERING_CALLBACK_URL="",
            GF_PATHS_DATA="/var/lib/grafana/data", GF_LOG_LEVEL="warn",
            GF_UNIFIED_STORAGE_INDEX_PATH="/var/lib/grafana/search/bleve")
        command = ["run", "-d", "--name", grafana, "--network", network,
                   "--read-only", "--memory", "1g",
                   "--cpus", "1", "--tmpfs", "/etc/grafana:uid=472,gid=0,mode=0755",
                   "--tmpfs", "/var/lib/grafana:uid=472,gid=0", "--tmpfs", "/tmp:uid=472,gid=0"]
        for key in env.keys() - os.environ.keys():
            command += ["-e", key]
        # Explicit keys also work if the caller happened to define one already.
        for key in [k for k in env if k.startswith("GF_") and k in os.environ]:
            command += ["-e", key]
        docker(*command, "--entrypoint", "/bin/sh", image, "-c",
               "while [ ! -f /tmp/fixture-ready ]; do sleep 0.1; done; exec /run.sh", process_env=env)
        restore_stream(args.identity, args.provisioning_archive,
                       [grafana, "tar", "--no-same-owner", "-C", "/", "-xf", "-"])
        docker("exec", grafana, "touch", "/tmp/fixture-ready")
        try:
            eventually(lambda: api("/api/health"), timeout=120)
        except AssertionError:
            # Logs can contain private provisioning data; retain only ciphertext.
            public = subprocess.check_output(["age-keygen", "-y", str(args.identity)],
                                             text=True).splitlines()
            recipients = [arg for key in public if key.startswith("age1") for arg in ["-r", key]]
            logs = subprocess.run(["docker", "logs", grafana], capture_output=True)
            destination = Path(__file__).parent / ".cache/private/migration-failure.log.age"
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with os.fdopen(os.open(destination, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "wb") as output:
                subprocess.run(["age", *recipients], input=logs.stdout + logs.stderr,
                               stdout=output, check=True)
            raise AssertionError("Migration startup failed; encrypted diagnostic log retained") from None
        # File provisioning and unified-storage migration may continue after /health.
        time.sleep(3)
        print("Ready:", "baseline/restore" if image == BASELINE else "upgraded copy", flush=True)

    try:
        docker("network", "create", "--internal", network)
        docker("run", "-d", "--name", database, "--network", network, "--network-alias", "db",
               "--read-only", "--memory", "768m", "--cpus", "1", "--tmpfs",
               "/pgdata:uid=999,gid=999,mode=0700", "--tmpfs", "/var/run/postgresql:uid=999,gid=999",
               "--tmpfs", "/var/lib/postgresql:uid=999,gid=999",
               "-e", "PGDATA=/pgdata", "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_INITDB_ARGS=--locale=C",
               POSTGRES, "-c", "shared_buffers=32MB", "-c", "max_connections=30", process_env=db_env)

        def ready():
            assert subprocess.run(["docker", "exec", database, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                                  capture_output=True).returncode == 0

        eventually(ready)
        result = subprocess.run(["docker", "exec", "-i", database, "psql", "-U", "postgres",
                                 "-v", "ON_ERROR_STOP=1"], input=(
            f"CREATE ROLE grafana LOGIN NOSUPERUSER NOCREATEDB PASSWORD '{password}';\n"),
            text=True, capture_output=True)
        assert result.returncode == 0, "Local database role creation failed"
        database_restore()
        launch(BASELINE)
        before = snapshot()
        assert int(before["users"]) > 0 and before["dashboards"] > 0
        docker("rm", "-f", grafana)
        launch(GRAFANA)
        after = snapshot()
        assert before == after, "Migration changed dashboard UIDs, sources, users or password hashes"
        migrations = sql("SELECT count(*) FROM unifiedstorage_migration_log WHERE error IS NOT NULL AND error <> ''; ")
        assert migrations == "0", "Unified storage migration reported an error"
        # Configuration and search caches are intentionally tmpfs. Rebuild the
        # pod-like container from its fixture while retaining the migrated DB.
        docker("rm", "-f", grafana)
        launch(GRAFANA)
        eventually(lambda: snapshot() == after or (_ for _ in ()).throw(AssertionError()))
        docker("rm", "-f", grafana)
        database_restore()
        launch(BASELINE)
        assert snapshot() == before, "Backup restoration did not reproduce the baseline"
        print(f"PASS: Grafana 12→13 migration, restart and restore to 12; "
              f"{before['dashboards']} dashboards, {len(before['sources'])} datasources, "
              f"{before['users']} users preserved")
    finally:
        for name in [grafana, database]:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


if __name__ == "__main__":
    main()
