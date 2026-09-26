
import io
import pytest
from dotenv.parser import parse_stream
@pytest.mark.parametrize('newline', ['\r\n', '\n', '\r'])
@pytest.mark.parametrize('count', [1, 2, 3])
def test_recovery(newline, count):
    invalid = 'BAD value'
    data = (invalid + newline) * count + 'GOOD=ok' + newline
    bindings = list(parse_stream(io.StringIO(data)))
    assert len(bindings) == count + 1
    for i, binding in enumerate(bindings[:-1]):
        assert binding.error
        assert binding.original.string == invalid + newline
        assert binding.original.line == i + 1
    valid = bindings[-1]
    assert not valid.error and valid.key == 'GOOD' and valid.value == 'ok'
    assert valid.original.string == 'GOOD=ok' + newline
    assert valid.original.line == count + 1
