
import io
import pytest
from dotenv import dotenv_values
@pytest.mark.parametrize('line', ['KEY= # note', 'KEY=  # note', 'KEY=\t# note', 'export KEY = # note'])
def test_empty_comment(line):
    assert dotenv_values(stream=io.StringIO(line + '\nNEXT=yes\n')) == {'KEY': '', 'NEXT': 'yes'}
@pytest.mark.parametrize('line,value', [('KEY=#literal', '#literal'), ('KEY=a#b', 'a#b'), ('KEY=abc # note', 'abc'), ('KEY=" # literal"', ' # literal'), ("KEY=' # literal'", ' # literal'), ('KEY=', '')])
def test_preserved(line, value):
    assert dotenv_values(stream=io.StringIO(line))['KEY'] == value
