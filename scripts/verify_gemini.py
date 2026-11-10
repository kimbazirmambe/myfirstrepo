"""Explicit, one-request check on a public article; no model output or secrets in logs."""
import os
import json
import re
from urllib.request import Request, build_opener
from urllib.error import HTTPError
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collectors.archive import read_public_article
from collectors.web import CollectionError, Fetcher, NoRedirect
from collectors.writing import WritingError, generate_post, gemini_error


def main():
    if not os.environ.get('MARKETING_GEMINI_KEY', '').strip():
        print('Gemini check: MARKETING_GEMINI_KEY is not configured.')
        return 1
    try:
        # A metadata GET has no generation cost. Print validated model IDs only.
        req = Request('https://generativelanguage.googleapis.com/v1beta/models',
                      headers={'x-goog-api-key': os.environ['MARKETING_GEMINI_KEY'].strip()})
        try:
            with build_opener(NoRedirect).open(req, timeout=30) as response:
                models = json.loads(response.read(256000))
        except HTTPError as error:
            raise WritingError(gemini_error(error)) from None
        names = [item.get('name', '') for item in models.get('models', [])
                 if 'generateContent' in item.get('supportedGenerationMethods', [])]
        names = [name for name in names if re.fullmatch(r'models/gemini-[a-z0-9.-]{1,80}', name)]
        print('Available Gemini model IDs: ' + ', '.join(names), flush=True)
        if '--models-only' in sys.argv:
            return 0
        article = read_public_article('https://podelu.by/article/bankrotstvo-glazami-byvshego-sobstvennika-chto-vazhno-znat-do-procedury', Fetcher())
        draft = generate_post(article, 'messenger', 'Редакция Marketing Hub')
    except (CollectionError, WritingError) as error:
        print('Gemini check: ' + str(error))
        return 1
    except (OSError, ValueError, TypeError, AttributeError):
        print('Gemini check: provider metadata unavailable; no credentials logged.')
        return 1
    print(f'Gemini live check succeeded: public article characters={len(article["content_text"])}, generated post characters={len(draft["body"])}. Nothing published or automatically approved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
