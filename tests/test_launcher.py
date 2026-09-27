import pytest

from scripts import start_backend as launcher


def test_env_values_are_literal_and_errors_do_not_echo_keys(tmp_path):
    path = tmp_path / '.env'
    path.write_text('FTTH_API_KEY="$(do-not-execute);$HOME#literal"\nFTTH_DATA_DIR=C:/a path\n', encoding='utf-8-sig')
    assert launcher.read_env(path) == {
        'FTTH_API_KEY': '$(do-not-execute);$HOME#literal', 'FTTH_DATA_DIR': 'C:/a path'}
    for content in ('FTTH_API_KEY=secret\nFTTH_API_KEY=another-secret', 'PATH=secret', 'FTTH_API_KEY="secret'):
        path.write_text(content, encoding='utf-8')
        with pytest.raises(ValueError) as error:
            launcher.read_env(path)
        assert 'secret' not in str(error.value)


def test_launcher_loads_key_without_prompt_and_stop_uses_same_data(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / '.env'
    path.write_text('FTTH_API_KEY=local-test-key\nFTTH_PROVIDER=qwen\nFTTH_DATA_DIR=runtime\n', encoding='utf-8')
    monkeypatch.setenv('FTTH_API_KEY', 'stale-terminal-key')
    calls = []
    monkeypatch.setattr('backend.cli.main', lambda args: calls.append(args) or 0)
    assert launcher.main(['--check-config']) == 0
    assert not calls
    assert launcher.os.environ['FTTH_API_KEY'] == 'local-test-key'
    assert launcher.main([]) == 0
    assert calls[-1] == ['serve']
    path.write_text('FTTH_API_KEY=\nFTTH_PROVIDER=qwen\nFTTH_DATA_DIR=runtime\n', encoding='utf-8')
    assert launcher.main([]) == 2
    assert launcher.main(['--stop']) == 0
    assert calls[-1] == ['stop']
    output = capsys.readouterr().out
    assert 'local-test-key' not in output and 'stale-terminal-key' not in output
