import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import backup_postgres_to_s3


class PostgresBackupTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'BACKUP_DATABASE_URL': 'postgresql://backup-user:private-password@db.example.test:5432/dukas',
            'AWS_ACCESS_KEY_ID': 'test-access-key',
            'AWS_SECRET_ACCESS_KEY': 'test-secret-key',
            'AWS_REGION': 'af-south-1',
            'BACKUP_S3_BUCKET': 'private-dukas-backups',
            'BACKUP_S3_PREFIX': 'dukasmart/production',
        }, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_creates_custom_dump_and_uploads_with_server_side_encryption(self):
        uploaded = {}

        class FakeS3:
            def upload_file(self, file_path, bucket, key, ExtraArgs):
                uploaded.update(
                    file=Path(file_path).read_bytes(),
                    bucket=bucket,
                    key=key,
                    options=ExtraArgs,
                )

        fake_boto3 = types.SimpleNamespace(
            client=lambda service, region_name: FakeS3(),
        )

        def fake_pg_dump(command, **kwargs):
            self.assertEqual(kwargs['env']['PGPASSWORD'], 'private-password')
            self.assertEqual(kwargs['env']['PGSSLMODE'], 'require')
            self.assertNotIn('private-password', ' '.join(command))
            self.assertNotIn('BACKUP_DATABASE_URL', kwargs['env'])
            self.assertNotIn('AWS_SECRET_ACCESS_KEY', kwargs['env'])
            if command[0] == 'pg_dump':
                output_path = Path(command[command.index('--file') + 1])
                output_path.write_bytes(b'isolated-test-dump')
            else:
                self.assertEqual(command[0:2], ['pg_restore', '--list'])

        with patch.dict(sys.modules, {'boto3': fake_boto3}), patch.object(
            backup_postgres_to_s3.subprocess, 'run', side_effect=fake_pg_dump,
        ):
            uri = backup_postgres_to_s3.create_backup()

        self.assertEqual(uploaded['file'], b'isolated-test-dump')
        self.assertEqual(uploaded['bucket'], 'private-dukas-backups')
        self.assertTrue(uploaded['key'].startswith('dukasmart/production/'))
        self.assertEqual(uploaded['options']['ServerSideEncryption'], 'AES256')
        self.assertEqual(
            uri,
            f"s3://{uploaded['bucket']}/{uploaded['key']}",
        )

    def test_rejects_non_postgresql_database_urls(self):
        with patch.dict(os.environ, {'BACKUP_DATABASE_URL': 'sqlite:///test.db'}):
            with self.assertRaisesRegex(RuntimeError, 'requires a PostgreSQL BACKUP_DATABASE_URL'):
                backup_postgres_to_s3.create_backup()

    def test_rejects_missing_required_bucket_settings(self):
        with patch.dict(os.environ, {'BACKUP_S3_BUCKET': ''}):
            with self.assertRaisesRegex(RuntimeError, 'BACKUP_S3_BUCKET'):
                backup_postgres_to_s3.create_backup()


if __name__ == '__main__':
    unittest.main()
