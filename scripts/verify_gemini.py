"""Explicit, one-request check on a public article; no model output or secrets in logs."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collectors.archive import read_public_article
from collectors.web import CollectionError, Fetcher
from collectors.writing import WritingError, generate_post


def main():
    if not os.environ.get('MARKETING_GEMINI_KEY', '').strip():
        print('Gemini check: MARKETING_GEMINI_KEY is not configured.')
        return 1
    try:
        article = read_public_article('https://podelu.by/article/bankrotstvo-glazami-byvshego-sobstvennika-chto-vazhno-znat-do-procedury', Fetcher())
        draft = generate_post(article, 'messenger', 'Редакция Marketing Hub')
    except (CollectionError, WritingError) as error:
        print('Gemini check: ' + str(error))
        return 1
    print(f'Gemini live check succeeded: public article characters={len(article["content_text"])}, generated post characters={len(draft["body"])}. Nothing published or automatically approved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
