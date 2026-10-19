# Test Fixtures

This directory holds sanitized offline HTML fixtures and simulated captive portal server responses for offline testing with `pytest` and `respx`.

## Security Note

**Never commit real credentials, campus session tokens, or personal identifiers in test fixtures.** Always scrub real HTML dumps to replace actual IDs/passwords with mock placeholders before adding them here.
