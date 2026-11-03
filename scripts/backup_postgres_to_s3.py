import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def required_setting(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f'Missing required backup setting: {name}')
    return value


def create_backup():
    try:
        database_url = make_url(required_setting('BACKUP_DATABASE_URL'))
    except ArgumentError:
        raise RuntimeError('BACKUP_DATABASE_URL is not a valid PostgreSQL connection URL.') from None
    if database_url.drivername not in ('postgres', 'postgresql', 'postgresql+psycopg2'):
        raise RuntimeError('The scheduled backup job requires a PostgreSQL BACKUP_DATABASE_URL.')
    if not database_url.host or not database_url.database or not database_url.username:
        raise RuntimeError('DATABASE_URL is missing the PostgreSQL host, database, or username.')

    bucket = required_setting('BACKUP_S3_BUCKET')
    region = required_setting('AWS_REGION')
    prefix = os.environ.get('BACKUP_S3_PREFIX', 'dukasmart/production').strip('/')
    if not prefix or '..' in Path(prefix).parts:
        raise RuntimeError('BACKUP_S3_PREFIX must be a non-empty S3 key prefix.')

    connection_env = os.environ.copy()
    for secret_name in (
        'DATABASE_URL',
        'BACKUP_DATABASE_URL',
        'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY',
    ):
        connection_env.pop(secret_name, None)
    connection_env['PGPASSWORD'] = database_url.password or ''
    connection_env['PGSSLMODE'] = str(database_url.query.get('sslmode', 'require'))
    now = datetime.now(timezone.utc)
    key = f'{prefix}/{now:%Y/%m/%d}/dukasmart_{now:%Y%m%dT%H%M%SZ}.dump'

    with tempfile.TemporaryDirectory(prefix='dukasmart-backup-') as temporary_directory:
        dump_path = Path(temporary_directory) / 'database.dump'
        command = [
            'pg_dump',
            '--format=custom',
            '--no-owner',
            '--no-acl',
            '--file',
            str(dump_path),
            '--host',
            database_url.host,
            '--port',
            str(database_url.port or 5432),
            '--username',
            database_url.username,
            '--dbname',
            database_url.database,
        ]
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=connection_env,
        )
        if not dump_path.is_file() or dump_path.stat().st_size == 0:
            raise RuntimeError('pg_dump completed without creating a non-empty backup.')
        subprocess.run(
            ['pg_restore', '--list', str(dump_path)],
            check=True,
            capture_output=True,
            text=True,
            env=connection_env,
        )

        import boto3

        s3 = boto3.client('s3', region_name=region)
        s3.upload_file(
            str(dump_path),
            bucket,
            key,
            ExtraArgs={
                'ServerSideEncryption': 'AES256',
                'ContentType': 'application/octet-stream',
            },
        )

    return f's3://{bucket}/{key}'


def main():
    try:
        backup_uri = create_backup()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'PostgreSQL backup failed: {error}', file=sys.stderr)
        return 1
    print(f'PostgreSQL backup uploaded: {backup_uri}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
