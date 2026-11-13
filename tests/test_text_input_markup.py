from pathlib import Path


CHAT_HTML = Path(__file__).parent.parent / "app" / "website_files" / "templates" / "chat.html"


def test_text_input_markup_has_form_controls():
    chat_html = CHAT_HTML.read_text()

    assert 'id="text-form"' in chat_html
    assert 'id="text-input"' in chat_html
    assert 'id="text-send"' in chat_html
