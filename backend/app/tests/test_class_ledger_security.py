from types import SimpleNamespace
import pytest
from app.db.init_db import _secure_class_upload_ledger


@pytest.mark.parametrize('already_enabled', [True, False])
def test_class_security_does_not_relock_an_already_secured_table(already_enabled):
    statements = []
    class Connection:
        dialect = SimpleNamespace(name='postgresql')
        def execute(self, statement, params=None):
            sql = str(statement)
            statements.append(sql)
            return SimpleNamespace(scalar_one=lambda: already_enabled, scalar=lambda: False)
    _secure_class_upload_ledger(Connection())
    assert ('ALTER TABLE matalk_class_uploads ENABLE ROW LEVEL SECURITY' in statements) is (not already_enabled)
    assert 'REVOKE ALL ON matalk_class_uploads FROM PUBLIC' in statements
