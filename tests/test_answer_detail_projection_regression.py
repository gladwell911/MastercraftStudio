import pytest

import main


@pytest.mark.parametrize("source, expected", [
    ('<ol start="bad"><li>one</li></ol>', '1. one'),
    ('<ol start><li>one</li></ol>', '1. one'),
    ('<ol start="4"><li>one</li><li>two</li></ol>', '4. one\n5. two'),
    ('[documentation](https://example.com/docs)', 'documentation (https://example.com/docs)'),
    ('[https://example.com/docs](https://example.com/docs)', 'https://example.com/docs'),
    ('first<br><br>second', 'first\n\nsecond'),
    ('first\n\nsecond', 'first\n\nsecond'),
    ('<ul><li>outer<ul><li>inner</li></ul></li><li>next</li></ul>', '- outer\n  - inner\n- next'),
    ('- outer\n    - inner\n- next', '- outer\n  - inner\n- next'),
    ('2. outer\n    - inner\n3. next', '2. outer\n  - inner\n3. next'),
    ('<ol start="2"><li>outer<ul><li>inner<ol start="7"><li>deep</li></ol></li></ul></li><li>next</li></ol>', '2. outer\n  - inner\n    7. deep\n3. next'),
    ('<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>', 'a\tb\nc\td'),
    ('```\na*b # code\n\n\nlast\n```', 'a*b # code\n\n\nlast'),
])
def test_detail_projection_preserves_content(source, expected):
    assert main.answer_detail_plain_text(source) == expected


def test_boundary_does_not_read_accumulated_output(monkeypatch):
    parser = main._ParagraphStripper()
    monkeypatch.setattr(parser, 'text', lambda: pytest.fail('boundary joined accumulated output'))
    parser.feed('<p>paragraph</p>' * 8000)
    assert len(parser.parts) == 16000
