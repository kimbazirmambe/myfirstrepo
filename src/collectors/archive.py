"""Progressive archive discovery via public sitemaps, never internal search APIs."""
import re
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import snowballstemmer

from .web import CollectionError, metadata

_STEM = snowballstemmer.stemmer('russian')
_TRANSLIT = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                     ['a','b','v','g','d','e','e','zh','z','i','j','k','l','m','n','o','p','r','s','t','u','f','h','c','ch','sh','sch','','y','','e','yu','ya']))


def terms(text):
    return {_STEM.stemWord(word) for word in re.findall(r'[а-яa-z0-9]+', text.casefold().replace('ё','е'))} - {''}


def matches_query(text, query):
    words = terms(text)
    groups = [terms(part) for part in query.split(',') if terms(part)]
    return bool(groups) and any(group.issubset(words) for group in groups)


def same_site(url, base):
    try:
        return urlsplit(url).hostname.removeprefix('www.') == urlsplit(base).hostname.removeprefix('www.')
    except (AttributeError, ValueError):
        return False


def discover_archive(source_url, query, fetcher):
    # Fetcher reads robots.txt first and applies its rules to every redirect/URL.
    _, final = fetcher.get(source_url)
    if not same_site(final, source_url):
        raise CollectionError('Источник перенаправляет на другой сайт; поиск остановлен.')
    parts = urlsplit(final)
    sitemap = f'{parts.scheme}://{parts.netloc}/sitemap.xml'
    pending = [sitemap]
    visited = set()
    urls = []
    limited = False
    while pending and len(visited) < 5:
        address = pending.pop(0)
        if address in visited or not same_site(address, final):
            continue
        visited.add(address)
        text, actual = fetcher.get(address)
        if not same_site(actual, final):
            raise CollectionError('Sitemap перенаправляет на другой сайт; поиск остановлен.')
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise CollectionError('Неподдерживаемые XML-сущности в sitemap.')
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise CollectionError('Sitemap не является корректным XML; для этого сайта нужен отдельный обработчик.') from None
        local = root.tag.rsplit('}', 1)[-1]
        locations = [node.text.strip() for node in root.iter() if node.tag.rsplit('}',1)[-1] == 'loc' and node.text]
        if local == 'sitemapindex':
            pending.extend(location for location in locations if same_site(location, final))
        elif local == 'urlset':
            for location in locations:
                if not same_site(location, final):
                    continue
                parsed = urlsplit(location)
                if parsed.scheme not in {'https', 'http'} or parsed.username is not None or parsed.password is not None:
                    limited = True
                    continue
                path = parsed.path
                if parts.hostname.removeprefix('www.') == 'podelu.by' and not path.startswith('/article/'):
                    continue
                if path in {'', '/'} or re.search(r'/(?:admin|api|login|register|search|tag|category|rubric)(?:/|$)', path):
                    continue
                if location not in urls:
                    urls.append(location)
        else:
            raise CollectionError('Неизвестный формат sitemap.')
    if pending:
        limited = True
    transliterated = [''.join(_TRANSLIT.get(char, char) for char in word) for word in terms(query)]
    urls.sort(key=lambda url: (not any(word and word in url.casefold() for word in transliterated), url))
    bounded = []
    size = 0
    for url in urls:
        size += len(url.encode()) + 4
        if len(url) > 2048:
            limited = True
            continue
        if len(bounded) >= 1000 or size > 600000:
            limited = True
            break
        bounded.append(url)
    if not bounded:
        raise CollectionError('В публичном sitemap не найдены страницы статей. Поиск сайта и API не используются в обход robots.txt.')
    return bounded, limited


def read_public_article(url, fetcher):
    html, actual = fetcher.get(url)
    if not same_site(actual, url):
        raise CollectionError('Статья перенаправляет на другой сайт; чтение остановлено.')
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script[type="application/ld+json"]'):
        # Explicit structured paywall declarations are not bypassed.
        if re.search(r'"isAccessibleForFree"\s*:\s*(?:false|"false")', node.get_text(), re.I):
            raise CollectionError('Полный текст ограничен подпиской; обход не выполняется.')
    if soup.select_one('[class*="paywall"], [id*="paywall"]'):
        raise CollectionError('Обнаружено ограничение доступа к тексту.')
    container = soup.select_one('[itemprop="articleBody"], article .prose, article, main .prose')
    if urlsplit(actual).hostname.removeprefix('www.') == 'podelu.by':
        column = soup.select_one('main div.min-w-0')
        if column:
            blocks = column.find_all('div', recursive=False)
            if blocks:
                container = max(blocks, key=lambda block: sum(len(p.get_text()) for p in block.find_all('p')))
    if container is None:
        raise CollectionError('Не удалось выделить основной текст статьи без меню и рекомендаций.')
    for node in container.select('script, style, nav, aside, footer, form, button, .summary-block'):
        node.decompose()
    paragraphs = []
    for node in container.find_all(['h2','h3','p','li']):
        if node.name == 'li' and node.find('p'):
            continue
        text = ' '.join(node.get_text(' ',strip=True).split())
        if text:
            paragraphs.append(text)
    content = '\n\n'.join(paragraphs)
    if len(content) < 200:
        raise CollectionError('Доступного текста недостаточно для подготовки материала.')
    row = metadata(html, actual, '')
    if not row['title']:
        raise CollectionError('У статьи не найден заголовок.')
    row.update(content_text=content[:30000], content_complete=len(content) <= 30000)
    return row
