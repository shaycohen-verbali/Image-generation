from contextlib import contextmanager
from datetime import datetime, timedelta

from app import worker
from app.models import CloudUploadBatch


def test_upload_claim_excludes_live_workers_and_recovers_abandoned_batches(db_session, monkeypatch):
    @contextmanager
    def session():
        yield db_session
    monkeypatch.setattr(worker, 'SessionLocal', session)
    db_session.add_all([
        CloudUploadBatch(id='first', bucket='matalkimages', status='queued'),
        CloudUploadBatch(id='second', bucket='matalkimages', status='queued'),
    ])
    db_session.commit()
    assert worker._claim_next_cloud_upload_batch() == 'first'
    assert worker._claim_next_cloud_upload_batch() == 'second'
    # A second worker must not claim either freshly running batch.
    assert worker._claim_next_cloud_upload_batch() is None
    first = db_session.get(CloudUploadBatch, 'first')
    first.updated_at = datetime.utcnow() - timedelta(minutes=11)
    db_session.commit()
    assert worker._claim_next_cloud_upload_batch() == 'first'
    assert worker._claim_next_cloud_upload_batch() is None
