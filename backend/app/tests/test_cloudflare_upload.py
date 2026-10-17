from __future__ import annotations

import json
import logging
from csv import DictReader
from io import StringIO

from sqlalchemy import select

from app.api.word_sources import cloudflare_upload_report
from app.core.config import get_settings
from app.models import CloudUpload, CloudUploadBatch
from app.services.cloudflare_upload import CloudflareUploadService
from app.services.image_filenames import final_image_filename, final_image_filename_for_field, versioned_upload_filename


def test_final_image_filenames_use_canonical_tokens_and_single_upload_version() -> None:
    assert final_image_filename(
        "Happy Hands",
        "Noun",
        background="white_bg",
        gender="female",
        age="teenager",
        skin_color="brown",
        sense_id="sense-42",
    ) == "happy_hands__noun__wbg__f__teen__br__sense-42.jpg"
    assert final_image_filename_for_field(
        "Happy Hands",
        "Noun",
        "toddler_male_black_regular_path",
        "sense-42",
    ) == "happy_hands__noun__reg__m__todd__bl__sense-42.jpg"
    assert versioned_upload_filename("/tmp/old_name__v9.jpeg") == "old_name__v1.jpg"


def test_cloudflare_uploads_images_in_bounded_worker_pool(db_session, tmp_path, monkeypatch, caplog) -> None:
    # Capture the R2 response log emitted by each successful PutObject call.
    caplog.set_level(logging.INFO)
    settings = get_settings()
    settings.cloudflare_r2_endpoint = "https://r2.example.test"
    settings.cloudflare_r2_access_key_id = "test-access"
    settings.cloudflare_r2_secret_access_key = "test-secret"
    settings.cloudflare_r2_upload_workers = 2

    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"first image")
    second_path.write_bytes(b"second image")
    rows = [
        {
            "id": "row-1",
            "word": "first",
            "part_of_speech": "noun",
            "sense_id": "sense-first",
            "kid_male_white_regular_path": first_path.as_posix(),
        },
        {
            "id": "row-2",
            "word": "second",
            "part_of_speech": "noun",
            "sense_id": "sense-second",
            "kid_male_white_regular_path": second_path.as_posix(),
        },
    ]
    batch = CloudUploadBatch(
        id="r2_test_parallel",
        bucket="matalkimages",
        source_rows_json=json.dumps(rows),
        row_count=2,
        status="queued",
    )
    db_session.add(batch)
    db_session.commit()

    class FakeClient:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def put_object(self, **kwargs) -> dict[str, object]:
            self.keys.append(kwargs["Key"])
            return {
                "ETag": '"test-etag"',
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-123",
                },
            }

    fake_client = FakeClient()
    monkeypatch.setattr(CloudflareUploadService, "_client", staticmethod(lambda bucket="": fake_client))
    monkeypatch.setattr(
        CloudflareUploadService,
        "_compress",
        staticmethod(lambda payload, quality: b"compressed image"),
    )

    result = CloudflareUploadService(db_session).upload_rows(
        rows,
        bucket="matalkimages",
        batch_id="r2_test_parallel",
    )

    assert result["status"] == "completed"
    assert result["uploaded"] == 2
    assert result["failed"] == 0
    assert "Cloudflare R2 put_object succeeded" in caplog.text
    assert all(
        record.cloudflare_response["ResponseMetadata"]["RequestId"] == "request-123"
        for record in caplog.records
        if record.name == "app.services.cloudflare_upload"
    )
    assert sorted(fake_client.keys) == [
        "first__noun__reg__m__kid__w__sense-first__v1.jpg",
        "second__noun__reg__m__kid__w__sense-second__v1.jpg",
    ]
    ledgers = db_session.scalars(
        select(CloudUpload).where(CloudUpload.batch_id == "r2_test_parallel")
    ).all()
    assert len(ledgers) == 2
    assert {ledger.status for ledger in ledgers} == {"uploaded"}
    assert {ledger.destination_url for ledger in ledgers} == {
        "https://r2.example.test/matalkimages/first__noun__reg__m__kid__w__sense-first__v1.jpg",
        "https://r2.example.test/matalkimages/second__noun__reg__m__kid__w__sense-second__v1.jpg",
    }

    report = cloudflare_upload_report("r2_test_parallel", db_session)
    report_rows = list(DictReader(StringIO(report.body.decode("utf-8"))))
    assert {row["destination_url"] for row in report_rows} == {
        "https://r2.example.test/matalkimages/first__noun__reg__m__kid__w__sense-first__v1.jpg",
        "https://r2.example.test/matalkimages/second__noun__reg__m__kid__w__sense-second__v1.jpg",
    }


def test_class_uploads_are_isolated_and_reports_use_selected_bucket(db_session, tmp_path, monkeypatch):
    from app.models import MatalkClassUpload
    from app.services.cloudflare_upload import configured_buckets
    from app.services.matalk_export import _cloudflare_urls

    settings = get_settings()
    for name, value in {
        'cloudflare_r2_class_bucket': 'matalk-class-images',
        'cloudflare_r2_class_endpoint': 'https://class.example.test',
        'cloudflare_r2_class_access_key_id': 'class-key',
        'cloudflare_r2_class_secret_access_key': 'class-secret',
        'cloudflare_r2_public_base_url': 'https://aac.example.test',
    }.items():
        monkeypatch.setattr(settings, name, value)
    assert 'matalk-class-images' in configured_buckets()
    source = tmp_path / 'apple.jpg'
    source.write_bytes(b'image')
    row = {'id': 'apple', 'word': 'apple', 'sense_id': 'sense-apple', 'part_of_speech': 'noun',
           'category': 'Food', 'sense_oxford': 'a fruit', 'sense_wordnet': 'fallback',
           'kid_male_white_white_bg_path': str(source)}
    calls = []
    class Client:
        def put_object(self, **kwargs):
            calls.append(kwargs)
            return {}
    chosen = []
    def client(bucket=''):
        chosen.append(bucket)
        return Client()
    monkeypatch.setattr(CloudflareUploadService, '_client', staticmethod(client))
    monkeypatch.setattr(CloudflareUploadService, '_compress', staticmethod(lambda data, quality: data))
    for bucket, batch_id in [('matalkimages', 'aac'), ('matalk-class-images', 'class')]:
        db_session.add(CloudUploadBatch(id=batch_id, bucket=bucket))
        db_session.commit()
        result = CloudflareUploadService(db_session).upload_rows([row], bucket=bucket, batch_id=batch_id)
        assert result['uploaded'] == 1  # AAC's existing image must not skip Class.
    record = db_session.scalars(select(MatalkClassUpload)).one()
    assert (record.category, record.meaning) == ('Food', 'a fruit')
    assert record.destination_url.startswith('https://class.example.test/matalk-class-images/')
    assert len(db_session.scalars(select(CloudUpload)).all()) == 1
    assert {call['Bucket'] for call in calls} == {'matalkimages', 'matalk-class-images'}
    assert 'matalk-class-images' in chosen
    assert all('class.example' not in url for url in _cloudflare_urls(db_session, {str(source)}).values())
    class_urls = _cloudflare_urls(db_session, {str(source)}, 'matalk-class-images')
    assert len(class_urls) == 1
    assert all('class.example.test/matalk-class-images/' in url for url in class_urls.values())
    assert not _cloudflare_urls(db_session, {str(source)}, 'other-bucket')
    report = list(DictReader(StringIO(cloudflare_upload_report('class', db_session).body.decode())))
    assert len(report) == 1 and report[0]['bucket'] == 'matalk-class-images'
    repeated = CloudflareUploadService(db_session).upload_rows([row], bucket='matalk-class-images', batch_id='class')
    assert repeated['skipped'] == 1 and repeated['uploaded'] == 0


def test_class_client_uses_only_class_credentials(monkeypatch):
    import boto3
    from app.services.cloudflare_upload import configured_buckets
    settings = get_settings()
    monkeypatch.setattr(settings, 'cloudflare_r2_class_endpoint', 'https://class.example.test')
    monkeypatch.setattr(settings, 'cloudflare_r2_class_access_key_id', 'class-key')
    monkeypatch.setattr(settings, 'cloudflare_r2_class_secret_access_key', 'class-secret')
    captured = {}
    def fake_client(*args, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(boto3, 'client', fake_client)
    CloudflareUploadService._client('matalk-class-images')
    assert captured['endpoint_url'] == 'https://class.example.test'
    assert captured['aws_access_key_id'] == 'class-key'
    assert captured['aws_secret_access_key'] == 'class-secret'
    monkeypatch.setattr(settings, 'cloudflare_r2_class_secret_access_key', '')
    assert 'matalk-class-images' not in configured_buckets()
    import pytest
    with pytest.raises(RuntimeError):
        CloudflareUploadService._client('matalk-class-images')


def test_reused_object_refreshes_source_links_and_checks_content(db_session, tmp_path, monkeypatch):
    from app.services.matalk_export import build_matalk_tables

    calls = []
    class Client:
        def put_object(self, **kwargs):
            calls.append(kwargs)
            return {}
    monkeypatch.setattr(CloudflareUploadService, '_client', staticmethod(lambda bucket='': Client()))
    monkeypatch.setattr(CloudflareUploadService, '_compress', staticmethod(lambda data, quality: data))
    old = tmp_path / 'old.jpg'
    new = tmp_path / 'new.jpg'
    old.write_bytes(b'same image')
    new.write_bytes(b'same image')
    row = {'id': 'apple', 'word': 'apple', 'sense_id': 'sense-apple', 'part_of_speech': 'noun',
           'kid_male_white_white_bg_path': str(old)}
    service = CloudflareUploadService(db_session)
    assert service.upload_rows([row], bucket='matalkimages')['uploaded'] == 1
    db_session.commit()
    row['kid_male_white_white_bg_path'] = str(new)
    assert service.upload_rows([row], bucket='matalkimages')['skipped'] == 1
    assert len(calls) == 1
    ledger = db_session.scalars(select(CloudUpload)).one()
    assert ledger.source_path == str(new)
    db_session.commit()
    assert len(build_matalk_tables([row], db=db_session).image_rows) == 1
    db_session.commit()
    new.write_bytes(b'changed image')
    assert service.upload_rows([row], bucket='matalkimages')['uploaded'] == 1
    assert len(calls) == 2
    db_session.commit()
    assert service.upload_rows([row], bucket='matalkimages', quality=50)['uploaded'] == 1
    assert len(calls) == 3


def test_upload_reads_remote_bytes_without_populating_disk_cache(db_session, monkeypatch):
    import app.services.cloudflare_upload as upload
    import app.services.storage as storage
    calls = []
    class Client:
        def put_object(self, **kwargs):
            calls.append(kwargs)
            return {}
    monkeypatch.setattr(CloudflareUploadService, '_client', staticmethod(lambda bucket='': Client()))
    monkeypatch.setattr(CloudflareUploadService, '_compress', staticmethod(lambda data, quality: data))
    downloaded = []
    def download(uri):
        downloaded.append(uri)
        return b'remote image bytes'
    monkeypatch.setattr(storage, '_download_from_supabase', download)
    def no_disk_cache(*args, **kwargs):
        raise AssertionError('Upload sources must not enter the persistent disk cache')
    monkeypatch.setattr(storage, 'runtime_cache_root', no_disk_cache)
    rows = [{'id': str(i), 'word': f'word-{i}', 'sense_id': str(i),
             'kid_male_white_regular_path': f'supabase://images/{i}.jpg'} for i in range(25)]
    result = upload.CloudflareUploadService(db_session).upload_rows(rows, bucket='matalkimages')
    assert result['uploaded'] == len(rows) == len(downloaded) == len(calls)


def test_resumed_batch_preserves_counts_and_reads_only_unfinished_images(db_session, monkeypatch):
    import app.services.cloudflare_upload as upload
    sources = []
    class Client:
        def put_object(self, **kwargs):
            return {}
    monkeypatch.setattr(CloudflareUploadService, '_client', staticmethod(lambda bucket='': Client()))
    monkeypatch.setattr(CloudflareUploadService, '_compress', staticmethod(lambda data, quality: data))
    def read(source):
        sources.append(source)
        return b'image'
    monkeypatch.setattr(upload, 'read_binary', read)
    first = {'id': 'first', 'word': 'first', 'sense_id': 'first', 'kid_male_white_regular_path': 'supabase://images/first.jpg'}
    second = {'id': 'second', 'word': 'second', 'sense_id': 'second', 'kid_male_white_regular_path': 'supabase://images/second.jpg'}
    batch = CloudUploadBatch(id='resumed', bucket='matalkimages', status='running')
    db_session.add(batch)
    db_session.commit()
    service = CloudflareUploadService(db_session)
    service.upload_rows([first], bucket='matalkimages', batch_id=batch.id)
    batch.status = 'running'
    batch.total = 2
    db_session.commit()
    sources.clear()
    result = service.upload_rows([first, second], bucket='matalkimages', batch_id=batch.id)
    assert sources == ['supabase://images/second.jpg']
    assert result['total'] == result['uploaded'] == 2
    assert result['skipped'] == result['failed'] == 0
    assert result['status'] == 'completed'


def test_existing_ledger_is_not_rewritten_before_image_io(db_session, tmp_path, monkeypatch):
    import app.services.cloudflare_upload as upload
    class Client:
        def put_object(self, **kwargs):
            return {}
    monkeypatch.setattr(CloudflareUploadService, '_client', staticmethod(lambda bucket='': Client()))
    monkeypatch.setattr(CloudflareUploadService, '_compress', staticmethod(lambda data, quality: data))
    old = tmp_path / 'old.jpg'
    old.write_bytes(b'image')
    row = {'id': 'apple', 'word': 'apple', 'sense_id': 'apple', 'kid_male_white_regular_path': str(old)}
    service = CloudflareUploadService(db_session)
    service.upload_rows([row], bucket='matalkimages')
    db_session.commit()
    ledger = db_session.scalars(select(CloudUpload)).one()
    row['kid_male_white_regular_path'] = 'supabase://images/new.jpg'
    def read(source):
        assert ledger.status == 'uploaded'
        assert ledger.source_path == str(old)
        return b'new image'
    monkeypatch.setattr(upload, 'read_binary', read)
    result = service.upload_rows([row], bucket='matalkimages')
    assert result['uploaded'] == 1
    assert ledger.source_path == 'supabase://images/new.jpg'
