"""Build translated editorial assets using a temporary local LibreTranslate.

Only public website strings are input. No paid API, persistent service, Actions
artifact or model cache. Run inside a standard runner for this public repo.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from translation_codec import TARGETS, glossary_version, segments, validate_translation

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.translation-work'
PUBLIC_SOURCES = 'https://himdubfestival.com/i18n/sources.json'
OUTPUT = 'https://raw.githubusercontent.com/kaueramone/himdub-festival/site-translations/translations.json'
API = 'https://api.github.com/repos/kaueramone/himdub-festival'
IMAGE = 'libretranslate/libretranslate@sha256:98d9ec356102a93ffb4e88046688a1a7ab2f3bf8e54b63588332c04f9686a668'


def request_json(url, *, method='GET', data=None, headers=None, max_bytes=4000000):
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    headers = {'Content-Type': 'application/json', 'User-Agent': 'himdub-editorial-build', **(headers or {})}
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=120) as response:
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError('Response exceeds editorial asset limit')
        return json.loads(raw)


def fingerprint(sources):
    return hashlib.sha256(json.dumps([glossary_version(), sources], ensure_ascii=False).encode()).hexdigest()


def prepare():
    try:
        document = request_json(PUBLIC_SOURCES)
    except HTTPError as error:
        if error.code != 404:
            raise
        # Initial website deployment can precede the first published endpoint.
        # The next scheduled build will retry; never block that deployment.
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
                stream.write('changed=false\n')
        print('Public source endpoint not deployed yet; next schedule will retry.')
        return
    if document.get('version') != 1 or document.get('glossary') != glossary_version():
        raise ValueError('Public website and build glossary differ')
    sources = document['sources']
    if (not isinstance(sources, list) or len(sources) > 5000 or
            any(not isinstance(text, str) or len(text) > 20000 for text in sources)):
        raise ValueError('Unexpected editorial source list')
    try:
        previous = request_json(OUTPUT)
    except HTTPError as error:
        if error.code != 404:
            raise
        previous = {}
    changed = previous.get('fingerprint') != fingerprint(sources)
    WORK.mkdir(exist_ok=True)
    (WORK / 'sources.json').write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    (WORK / 'previous.json').write_text(json.dumps(previous, ensure_ascii=False), encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write('changed=' + str(changed).lower() + '\n')
    print(f'Public editorial sources: {len(sources)}; translation build needed: {changed}')


def translate_source(source, language):
    parts = segments(source)
    chunks = [value.strip() for protected, value in parts
              if not protected and re.search(r'[^\W\d_]', value, re.UNICODE)]
    if not chunks:
        return source
    detected = request_json('http://127.0.0.1:5000/detect', method='POST', data={'q': source})
    source_language = detected[0]['language'] if detected else 'pt'
    if source_language not in ('pt', *TARGETS):
        source_language = 'pt'
    if source_language == language:
        return source
    response = request_json('http://127.0.0.1:5000/translate', method='POST', data={
        'q': chunks, 'source': source_language, 'target': language, 'format': 'text'})
    translated = response['translatedText']
    if not isinstance(translated, list) or len(translated) != len(chunks):
        raise ValueError('Translation engine returned an invalid batch')
    result, index = [], 0
    for protected, value in parts:
        if protected or not re.search(r'[^\W\d_]', value, re.UNICODE):
            result.append(value)
        else:
            leading = value[:len(value) - len(value.lstrip())]
            trailing = value[len(value.rstrip()):]
            result.append(leading + translated[index].strip() + trailing)
            index += 1
    return validate_translation(source, ''.join(result))


def build():
    import subprocess
    sources = json.loads((WORK / 'sources.json').read_text(encoding='utf-8'))['sources']
    previous = json.loads((WORK / 'previous.json').read_text(encoding='utf-8'))
    reusable = {entry['source']: entry['translations'] for entry in previous.get('entries', [])
                if previous.get('glossary') == glossary_version()}
    subprocess.run(['docker', 'run', '-d', '--name', 'himdub-translation-build',
                    '--publish', '127.0.0.1:5000:5000', '--memory', '6g', '--cpus', '2',
                    '--env', 'LT_LOAD_ONLY=pt,en,fr,es', '--env', 'LT_THREADS=1',
                    '--env', 'LT_DISABLE_WEB_UI=true', '--env', 'LT_DISABLE_FILES_TRANSLATION=true',
                    IMAGE], check=True, stdout=subprocess.DEVNULL)
    ready = False
    try:
        for _ in range(180):
            try:
                languages = request_json('http://127.0.0.1:5000/languages')
                available = {item['code'] for item in languages}
                if {'pt', *TARGETS} <= available:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(5)
        if not ready:
            raise RuntimeError('Translation engine did not become ready with all four languages')
        entries = []
        for index, source in enumerate(sources):
            translations = {}
            for language in TARGETS:
                text = reusable.get(source, {}).get(language)
                translations[language] = (validate_translation(source, text) if text
                                          else translate_source(source, language))
            entries.append({'source': source, 'translations': translations})
            if index % 25 == 0:
                print(f'Translated {index + 1}/{len(sources)} public strings', flush=True)
        output = {'version': 1, 'glossary': glossary_version(),
                  'fingerprint': fingerprint(sources), 'entries': entries}
        (WORK / 'translations.json').write_text(json.dumps(output, ensure_ascii=False), encoding='utf-8')
    finally:
        subprocess.run(['docker', 'rm', '-f', 'himdub-translation-build'], check=False,
                       stdout=subprocess.DEVNULL)


def publish():
    # Ephemeral GITHUB_TOKEN, only in this job. Never receives production keys.
    headers = {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
               'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    try:
        request_json(API + '/git/ref/heads/site-translations', headers=headers)
    except HTTPError as error:
        if error.code != 404:
            raise
        head = request_json(API + '/git/ref/heads/main', headers=headers)['object']['sha']
        request_json(API + '/git/refs', method='POST', headers=headers,
                     data={'ref': 'refs/heads/site-translations', 'sha': head})
    data = {'message': 'Build public website translations [skip ci]', 'branch': 'site-translations',
            'content': base64.b64encode((WORK / 'translations.json').read_bytes()).decode()}
    try:
        data['sha'] = request_json(API + '/contents/translations.json?ref=site-translations',
                                   headers=headers)['sha']
    except HTTPError as error:
        if error.code != 404:
            raise
    request_json(API + '/contents/translations.json', method='PUT', headers=headers, data=data)
    print('Published public language assets; no website deployment was triggered.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'build', 'publish'))
    globals()[parser.parse_args().command]()
