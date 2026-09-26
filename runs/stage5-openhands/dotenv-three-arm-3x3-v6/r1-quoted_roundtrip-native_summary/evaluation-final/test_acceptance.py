
import pytest
from dotenv import set_key, dotenv_values
@pytest.mark.parametrize('value', [r'one\\two', r'\\server\share', "x\\\\'y", 'plain', "it's fine"])
@pytest.mark.parametrize('mode', ['always', 'auto'])
@pytest.mark.parametrize('export', [False, True])
def test_roundtrip(tmp_path, value, mode, export):
    p = tmp_path / 'sample.env'
    p.write_text('OTHER=preserved\n', encoding='utf-8')
    result = set_key(p, 'VALUE', value, quote_mode=mode, export=export)
    assert result == (True, 'VALUE', value)
    values = dotenv_values(p, interpolate=False)
    assert values['VALUE'] == value
    assert values['OTHER'] == 'preserved'
