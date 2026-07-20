import xml.sax.saxutils


def escape_ssml(text: str) -> str:
    """Escape special characters for SSML."""
    return xml.sax.saxutils.escape(text)
