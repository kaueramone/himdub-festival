"""Discover public Pixieset images in a normal browser. No credentials or challenge bypass."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API='https://api.github.com/repos/kaueramone/himdub-festival'
WORK=Path('/tmp/himdub-gallery-discovery')
WORK.mkdir(exist_ok=True)


def request_json(url,headers=None,data=None,method=None):
    payload=json.dumps(data).encode() if data is not None else None
    req=Request(url,data=payload,headers={'Content-Type':'application/json',**(headers or {})},method=method)
    with urlopen(req,timeout=30) as response:
        raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ValueError('Response too large')
        return json.loads(raw)


def prepare():
    sources=request_json('https://himdubfestival.com/gallery-import-sources.json')['sources']
    pending=[]
    for source in sources[:10]:
        p=urlsplit(source['url'])
        if (not re.fullmatch(r'[a-f0-9]{64}',source['key']) or p.scheme!='https'
                or not (p.hostname or '').endswith('.pixieset.com') or p.query or p.fragment
                or not re.fullmatch(r'/[\w-]+/',p.path) or p.username or p.password or p.port not in (None,443)):
            raise ValueError('Invalid public collection')
        try:
            request_json('https://raw.githubusercontent.com/kaueramone/himdub-festival/gallery-discovery/'+source['key']+'.json')
        except HTTPError as error:
            if error.code!=404:raise
            pending.append(source)
    (WORK/'pending.json').write_text(json.dumps(pending))
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:
        output.write('changed='+str(bool(pending)).lower()+'\n')
    print('Public galleries awaiting discovery:',len(pending))


def discover():
    from playwright.sync_api import sync_playwright
    sources=json.loads((WORK/'pending.json').read_text())
    with sync_playwright() as browser_runtime:
        browser=browser_runtime.chromium.launch(headless=True)
        for source in sources:
            result={'version':1,'source':source['url']}
            context=browser.new_context(viewport={'width':1600,'height':1000},device_scale_factor=2)
            page=context.new_page()
            errors=[]
            def on_response(response):
                if '/client/loadphotos/' in response.url and response.status>=400:
                    errors.append(response.status)
            page.on('response',on_response)
            try:
                page.goto(source['url'],wait_until='domcontentloaded',timeout=60000)
                page.get_by_role('button',name=re.compile('view gallery',re.I)).click(timeout=15000)
                page.locator('li[data-id] img').first.wait_for(timeout=30000)
                result['title']=page.locator('h1').first.inner_text()[:180]
                business=page.locator('#business-link').first
                result['photographer']=business.inner_text()[:180] if business.count() else ''
                previous,stable=0,0
                all_photos={}
                visited_sets=set()
                for _ in range(400):
                    # Only normal public-page scrolling and visible gallery controls.
                    current=page.locator('li[data-id]').evaluate_all('els => els.map(e => ({id:e.dataset.id,name:e.dataset.filename||e.querySelector("img")?.alt||"",url:e.querySelector("img")?.currentSrc||e.querySelector("img")?.src})).filter(e=>e.url)')
                    all_photos.update({str(p['id']):p for p in current})
                    count=len(all_photos)
                    if count>5000:raise ValueError('Too many photos')
                    stable=stable+1 if count==previous else 0
                    previous=count
                    more=page.locator('#view-more-btn a')
                    signature=str(current[0]['id']) if current else ''
                    if stable>=2 and more.count() and more.is_visible() and signature not in visited_sets:
                        visited_sets.add(signature)
                        more.click()
                        stable=0
                    if stable>=12:break
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    page.wait_for_timeout(750)
                else:raise ValueError('Gallery did not finish loading')
                if errors:raise ValueError('Public gallery returned an error')
                photos=list(all_photos.values())
                # Read the actual public enlargement size; do not guess original/download URLs.
                first=page.locator('li[data-id] img').first
                first.scroll_into_view_if_needed()
                sample_url=first.get_attribute('src')
                if sample_url.startswith('//'):sample_url='https:'+sample_url
                first.click()
                page.wait_for_timeout(1200)
                large=page.locator('img').evaluate_all('els => els.filter(e=>e.getBoundingClientRect().width>0&&e.naturalWidth>=1000).map(e=>({url:e.currentSrc||e.src,width:e.naturalWidth})).sort((a,b)=>b.width-a.width)')
                if large:
                    base=re.sub(r'-[a-z0-9]+\.(?:jpg|jpeg|png|webp)$','',sample_url,flags=re.I)
                    enlarged=next((p['url'] for p in large if p['url'].startswith(base+'-')),None)
                    if enlarged:
                        suffix=enlarged[len(base):]
                        for photo in photos:
                            photo['url']=re.sub(r'-[a-z0-9]+\.(?:jpg|jpeg|png|webp)$',suffix,photo['url'],flags=re.I)
                unique={str(p['id']):p for p in photos}
                result['photos']=list(unique.values())
                if not result['photos']:raise ValueError('No public photos')
                for photo in result['photos']:
                    p=urlsplit(photo['url'])
                    if p.scheme!='https' or p.hostname!='images.pixieset.com' or p.username or p.password:
                        raise ValueError('Unexpected image origin')
                print('Discovered public photographs:',len(result['photos']),flush=True)
            except Exception as error:
                # No CAPTCHA solvers, cookies, PIN guessing or downloads behind access restrictions.
                result['error']=type(error).__name__
                print('Public gallery requires attention:',type(error).__name__,flush=True)
            finally:
                context.close()
            (WORK/(source['key']+'.json')).write_text(json.dumps(result,ensure_ascii=False))
        browser.close()


def publish():
    headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json'}
    try:
        request_json(API+'/git/ref/heads/gallery-discovery',headers=headers)
    except HTTPError as error:
        if error.code!=404:raise
        head=request_json(API+'/git/ref/heads/main',headers=headers)['object']['sha']
        request_json(API+'/git/refs',headers=headers,method='POST',data={'ref':'refs/heads/gallery-discovery','sha':head})
    for path in WORK.glob('*.json'):
        if not re.fullmatch('[a-f0-9]{64}',path.stem):continue
        body={'message':'Discover public gallery images [skip ci]','branch':'gallery-discovery',
              'content':base64.b64encode(path.read_bytes()).decode()}
        try:
            body['sha']=request_json(API+'/contents/'+path.name+'?ref=gallery-discovery',headers=headers)['sha']
        except HTTPError as error:
            if error.code!=404:raise
        request_json(API+'/contents/'+path.name,headers=headers,method='PUT',data=body)
    print('Published public image manifests; no production credentials were used.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('prepare','discover','publish'))
    globals()[parser.parse_args().command]()
