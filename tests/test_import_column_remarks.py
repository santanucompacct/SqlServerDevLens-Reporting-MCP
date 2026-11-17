import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('import_remarks', Path('scripts/import_column_remarks.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class API:
    def __init__(self):
        self.calls = []
        self.columns = [{'name': 'Amount', 'sql_type': 'decimal', 'nullable': False, 'remarks': 'Old meaning'}]

    def __call__(self, endpoint, body=None):
        self.calls.append((endpoint, body))
        if endpoint == 'state':
            return {'setup': {'connection': {'server': 'private-sql', 'database': 'analytics'}}}
        if endpoint == 'object':
            return {'revision': 'current-revision', 'columns': self.columns, 'demo': False}
        if endpoint == 'object/save':
            assert body['revision'] == 'current-revision'
            self.columns = body['columns']
            return {'status': 'saved'}
        raise AssertionError('Unexpected endpoint')


def test_dry_run_never_saves_or_creates_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api = API()
    result = module.import_remarks(api, {'amount': 'Taxable amount'}, 'dbo', 'ReportSales')
    assert not result['applied'] and not result['published']
    assert all(endpoint != 'object/save' for endpoint, _ in api.calls)
    assert not list(tmp_path.iterdir())


def test_apply_verifies_values_and_backs_up_previous_remarks(tmp_path):
    api = API()
    backup = tmp_path / 'before.json'
    result = module.import_remarks(api, {'amount': 'Taxable amount'}, 'dbo', 'ReportSales', True, backup)
    assert result['applied'] and not result['published']
    assert 'Old meaning' in backup.read_text()
    assert 'private-sql' not in backup.read_text()
    assert backup.stat().st_mode & 0o077 == 0
    assert sum(endpoint == 'object/save' for endpoint, _ in api.calls) == 1


def test_schema_mismatch_and_blank_or_duplicate_input_do_not_save(tmp_path):
    api = API()
    for source in [{'missing': 'Meaning'}, {'amount': ''},
                   [{'name': 'amount', 'remarks': 'One'}, {'name': 'AMOUNT', 'remarks': 'Two'}]]:
        with pytest.raises(ValueError):
            module.import_remarks(api, source, 'dbo', 'ReportSales', True, tmp_path / 'before.json')
    assert not any(endpoint == 'object/save' for endpoint, _ in api.calls)


@pytest.mark.parametrize('origin', ['https://reports.example.com', 'http://localhost.evil',
                                   'http://user:secret@localhost', 'http://127.0.0.1/admin',
                                   'http://127.0.0.1?token=secret'])
def test_nonprivate_or_ambiguous_origins_are_rejected(origin):
    with pytest.raises(ValueError):
        module.admin_origin(origin)


def test_loopback_origins():
    assert module.admin_origin('http://127.0.0.1:18101/') == 'http://127.0.0.1:18101'
    assert module.admin_origin('https://[::1]:8101') == 'https://[::1]:8101'
