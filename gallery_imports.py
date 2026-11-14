"""Resumable public Drive imports and authorised ZIP downloads, one image at a time."""
import hashlib
from html.parser import HTMLParser
import io
import json
import os
import re
import socket
import ipaddress
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urljoin, parse_qs

import requests
from flask import abort
from PIL import Image
from sqlalchemy import or_
from werkzeug.datastructures import FileStorage
from extensions import db
from models import GalleryAlbum, GalleryPhoto, GalleryImport, PageBlock

MAX_PHOTOS = 5000
MAX_IMAGE = 30 * 1024 * 1024
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024


class ImportProblem(ValueError):
    pass


def trusted_url(url):
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    allowed = host in ('drive.google.com', 'drive.usercontent.google.com', 'images.pixieset.com') or host.endswith('.pixieset.com') or host.endswith('.googleusercontent.com') or host.endswith('.cloudfront.net')
    if (p.scheme != 'https' or not allowed or p.username or p.password or p.port not in (None,443)
            or len(url) > 4000 or any(ord(c) < 33 for c in url)):
        raise ImportProblem('Usa uma pasta pública do Google Drive ou uma ligação de descarga autorizada da Pixieset.')
    # Only vendor-owned HTTPS hosts, never arbitrary/internal endpoints. Redirects are rechecked.
    for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(result[4][0]).is_global:
            raise ImportProblem('O endereço não corresponde a um serviço público permitido.')
    if host.endswith('.pixieset.com') and re.fullmatch(r'/[\w-]+',p.path) and not p.query and not p.fragment:
        return url+'/'
    return url


def fetch(url, limit, *, target=None):
    for _ in range(5):
        trusted_url(url)
        with requests.get(url, stream=True, timeout=(8,60), allow_redirects=False,
                          headers={'User-Agent':'HimDubGalleryImporter/1.0'}) as response:
            if response.status_code in (301,302,303,307,308):
                url = urljoin(url,response.headers.get('Location',''))
                continue
            if response.status_code != 200:
                raise ImportProblem('A origem recusou a descarga. Verifica a partilha pública ou fornece a ligação oficial do ZIP.')
            output = target if target is not None else io.BytesIO()
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > limit:
                    raise ImportProblem('O ficheiro excede o limite de importação. Divide o álbum em partes.')
                output.write(chunk)
            if target is not None:
                output.seek(0)
                return
            return output.getvalue()
    raise ImportProblem('A origem redireccionou demasiadas vezes.')


class DriveListing(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.files = []
        self.folders = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            href = attrs.get('href','')
            match = re.match(r'https://drive\.google\.com/file/d/([\w-]{20,})/view',href)
            if match:
                self.current = {'id':match[1],'name':''}
            match = re.match(r'https://drive\.google\.com/drive/(?:u/\d+/)?folders/([\w-]{20,})',href)
            if match:
                self.folders.append(match[1])
    def handle_data(self,data):
        if self.current is not None:
            self.current['name'] += data
    def handle_endtag(self,tag):
        if tag == 'a' and self.current:
            self.files.append(self.current)
            self.current = None


def drive_manifest(url):
    p = urlsplit(url)
    match = re.fullmatch(r'/drive/(?:u/\d+/)?folders/([\w-]{20,})/?',p.path)
    if p.hostname != 'drive.google.com' or not match:
        raise ImportProblem('Usa a ligação da pasta pública do Google Drive.')
    pending, visited, entries = [match[1]], set(), {}
    while pending:
        identifier = pending.pop(0)
        if identifier in visited:
            continue
        visited.add(identifier)
        if len(visited) > 100:
            raise ImportProblem('Demasiadas subpastas. Importa cada álbum separadamente.')
        listing = DriveListing()
        listing.feed(fetch('https://drive.google.com/embeddedfolderview?id='+identifier,8*1024*1024).decode('utf-8'))
        pending.extend(listing.folders)
        for file in listing.files:
            if re.search(r'\.(?:jpe?g|png|webp)$',file['name'],re.I):
                entries[file['id']] = {'name':file['name'].strip(),
                    'url':'https://drive.usercontent.google.com/download?id='+file['id']+'&export=download',
                    'key':hashlib.sha256(('drive:'+file['id']).encode()).hexdigest()}
        if len(entries) > MAX_PHOTOS:
            raise ImportProblem('Demasiadas fotografias. Importa o álbum por partes.')
    if not entries:
        raise ImportProblem('Não foram encontradas fotografias públicas. Confirma a partilha da pasta.')
    return sorted(entries.values(),key=lambda item:item['name'])


def migrate_gallery_blocks():
    """Idempotent migration: preserve the original block as an unpublished archive."""
    for block in PageBlock.query.filter_by(page_key='gallery').all():
        album = GalleryAlbum.query.filter_by(legacy_block_id=block.id).first()
        if album:
            continue
        handle = re.search(r'@([\w.]+)',block.text or '')
        photographer = ('@'+handle[1]) if handle else block.title
        album = GalleryAlbum(title=block.title,description=block.text,
            cover_url=block.image_url,source_url=block.link_url,photographer=photographer,
            photographer_url=('https://www.instagram.com/'+handle[1]+'/') if handle else '',
            sort_order=block.sort_order,published=block.published,legacy_block_id=block.id)
        db.session.add(album)
        db.session.flush()
        if block.link_url:
            db.session.add(GalleryImport(album_id=album.id,source_url=block.link_url))
        block.published=False
    db.session.commit()


def store_photo(album_id, raw, key, name):
    if GalleryPhoto.query.filter_by(album_id=album_id,source_key=key).first():
        return
    album = db.session.get(GalleryAlbum,album_id)
    if not album or album.deleted_at:
        raise ImportProblem('O álbum foi eliminado. A importação foi interrompida.')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            credit = str(image.getexif().get(315) or image.getexif().get(33432) or '')[:180].strip()
            if image.width*image.height > 50_000_000:
                raise ImportProblem('Fotografia demasiado grande. Reduz para até 50 megapíxeis.')
            image.load()
            # Re-encode upstream originals first, without retaining EXIF/GPS metadata.
            from PIL import ImageOps
            output=io.BytesIO()
            converted=ImageOps.exif_transpose(image).convert('RGB')
            converted.thumbnail((2400,2400))
            converted.save(output,format='WEBP',quality=85)
    except (OSError,Image.DecompressionBombError):
        raise ImportProblem('Uma fotografia não pôde ser lida. A importação pode ser retomada.') from None
    from cms import upload_image
    url = upload_image(FileStorage(stream=io.BytesIO(output.getvalue()),filename='import.webp'))
    # Lock after network I/O: deletion must win over a late import completion.
    album = GalleryAlbum.query.filter_by(id=album_id).populate_existing().with_for_update().one()
    if album.deleted_at:
        raise ImportProblem('O álbum foi eliminado. A importação foi interrompida.')
    db.session.add(GalleryPhoto(album_id=album_id,image_url=url,source_key=key,
        photographer=album.photographer or credit,sort_order=GalleryPhoto.query.filter_by(album_id=album_id).count()+1))
    if not album.cover_url:
        album.cover_url=url
    if not album.photographer and credit:
        album.photographer=credit
    db.session.commit()


def process_import(app):
    with app.app_context():
        now=datetime.utcnow()
        job=GalleryImport.query.filter(GalleryImport.status.in_(('pending','running','discovering')),
            or_(GalleryImport.lease_until.is_(None),GalleryImport.lease_until<now))\
            .order_by(GalleryImport.id).first()
        if not job:
            return False
        identifier=job.id
        claimed=GalleryImport.query.filter_by(id=identifier).filter(
            or_(GalleryImport.lease_until.is_(None),GalleryImport.lease_until<now)).update(
                {'lease_until':now+timedelta(minutes=3),'status':'running'},synchronize_session=False)
        db.session.commit()
        if not claimed:
            return True
        job=db.session.get(GalleryImport,identifier)
        try:
            if job.album.deleted_at:
                job.status='cancelled'
            elif (urlsplit(job.source_url).hostname == 'drive.google.com'
                    or (urlsplit(job.source_url).hostname or '').endswith('.pixieset.com')
                    and re.fullmatch(r'/[\w-]+/',urlsplit(job.source_url).path)):
                if not job.manifest:
                    if urlsplit(job.source_url).hostname == 'drive.google.com':
                        entries=drive_manifest(job.source_url)
                    else:
                        entries=discovered_pixieset(job)
                        if entries is None:
                            job.status='discovering'
                            job.lease_until=datetime.utcnow()+timedelta(seconds=60)
                            job.last_error='A recolher as fotografias públicas. Este passo pode demorar alguns minutos.'
                            db.session.commit()
                            return True
                    job.manifest=json.dumps(entries)
                    job.total=len(json.loads(job.manifest))
                    db.session.commit()
                entries=json.loads(job.manifest)
                # Bounded batch; commit after each photo and resume on deployment/restart.
                for item in entries[job.processed:job.processed+5]:
                    if not GalleryPhoto.query.filter_by(album_id=job.album_id,source_key=item['key']).first():
                        store_photo(job.album_id,fetch(item['url'],MAX_IMAGE),item['key'],item['name'])
                    job.processed+=1
                    job.lease_until=datetime.utcnow()+timedelta(minutes=3)
                    db.session.commit()
                job.status='complete' if job.processed==job.total else 'pending'
            elif ('.zip' in urlsplit(job.source_url).path.lower()
                    or urlsplit(job.source_url).path == '/download/filestart/'):
                # Disk-backed temporary ZIP, removed automatically; never extract paths.
                with tempfile.TemporaryFile() as archive:
                    fetch(job.source_url,MAX_ARCHIVE,target=archive)
                    with zipfile.ZipFile(archive) as z:
                        entries=[i for i in z.infolist() if not i.is_dir() and not i.filename.startswith('__MACOSX/')
                                 and re.search(r'\.(?:jpe?g|png|webp)$',i.filename,re.I)]
                        if not entries or len(entries)>MAX_PHOTOS:
                            raise ImportProblem('O ZIP deve conter entre 1 e 5000 fotografias.')
                        job.total=len(entries)
                        for index,item in enumerate(entries):
                            if item.file_size>MAX_IMAGE or item.flag_bits & 1:
                                raise ImportProblem('O ZIP contém uma imagem demasiado grande ou protegida.')
                            key=hashlib.sha256((str(item.CRC)+':'+str(item.file_size)+':'+item.filename).encode()).hexdigest()
                            if not GalleryPhoto.query.filter_by(album_id=job.album_id,source_key=key).first():
                                store_photo(job.album_id,z.read(item),key,item.filename)
                            job.processed=index+1
                            job.lease_until=datetime.utcnow()+timedelta(minutes=3)
                            db.session.commit()
                job.status='complete'
            else:
                raise ImportProblem('Esta galeria exige a descarga oficial. Obtém na Pixieset a ligação HTTPS do ficheiro ZIP e importa-a aqui. Não são contornados PIN, login ou bloqueios da origem.')
            job.last_error=''
        except Exception as error:
            db.session.rollback()
            job=db.session.get(GalleryImport,identifier)
            job.status='failed'
            job.last_error=str(error)[:500] if isinstance(error,ImportProblem) else 'A importação foi interrompida. Verifica a origem e tenta retomar; as fotografias já importadas são preservadas.'
            app.logger.warning('Gallery import %s interrupted (%s)',identifier,type(error).__name__)
        job.lease_until=None
        db.session.commit()
        return True


def start_gallery_worker(app):
    if app.testing or not app.config.get('PRODUCTION') or os.environ.get('GALLERY_WORKER_ENABLED','true')!='true':
        return
    if app.extensions.get('gallery_worker_pid')==os.getpid():
        return
    app.extensions['gallery_worker_pid']=os.getpid()
    def consume():
        stop=threading.Event()
        while not stop.wait(3):
            try:
                process_import(app)
            except Exception as error:
                app.logger.warning('Gallery import worker unavailable (%s)',type(error).__name__)
    threading.Thread(target=consume,name='gallery-import',daemon=True).start()


def discovery_key(job):
    return hashlib.sha256((str(job.id)+':'+str(job.created_at)+':'+job.source_url).encode()).hexdigest()


def discovered_pixieset(job):
    """Consume only validated public image manifests produced by our own workflow."""
    url='https://raw.githubusercontent.com/kaueramone/himdub-festival/gallery-discovery/'+discovery_key(job)+'.json'
    with requests.get(url,timeout=(5,20),stream=True,allow_redirects=False) as response:
        if response.status_code==404:
            return None
        if response.status_code!=200:
            raise ImportProblem('A recolha está temporariamente indisponível. Tenta retomar mais tarde.')
        raw=bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw)>4*1024*1024:
                raise ImportProblem('Lista de fotografias demasiado grande.')
    document=json.loads(raw)
    if document.get('source')!=job.source_url or document.get('version')!=1:
        raise ImportProblem('A lista recebida não corresponde a este álbum.')
    if document.get('error'):
        raise ImportProblem('Não foi possível recolher automaticamente esta galeria pública. Confirma que não exige login ou PIN e tenta novamente.')
    photos=document.get('photos')
    if not isinstance(photos,list) or not 0<len(photos)<=MAX_PHOTOS:
        raise ImportProblem('A galeria não apresentou fotografias públicas importáveis.')
    entries=[]
    for photo in photos:
        p=urlsplit(photo['url'])
        if p.scheme!='https' or p.hostname!='images.pixieset.com' or p.username or p.password or p.port not in (None,443):
            raise ImportProblem('A galeria apresentou uma origem de imagem não permitida.')
        entries.append({'url':photo['url'],'name':str(photo.get('name',''))[:255],
            'key':hashlib.sha256(('pixieset:'+str(photo['id'])).encode()).hexdigest()})
    if len({e['key'] for e in entries})!=len(entries):
        raise ImportProblem('A lista da galeria contém fotografias repetidas.')
    if not job.album.photographer and document.get('photographer'):
        job.album.photographer=str(document['photographer'])[:180]
    if job.album.title=='Álbum importado' and document.get('title'):
        job.album.title=str(document['title'])[:180]
    return entries


def init_gallery_discovery(app):
    @app.get('/gallery-import-sources.json')
    def gallery_import_sources():
        # Public collection links only, no ZIP capabilities, PINs, buyer data or editorial text.
        sources=[]
        for job in GalleryImport.query.join(GalleryAlbum).filter(
                GalleryAlbum.deleted_at.is_(None),GalleryImport.manifest=='',
                GalleryImport.status.in_(('pending','running','discovering'))).order_by(GalleryImport.id).limit(100):
            p=urlsplit(job.source_url)
            if (p.scheme=='https' and (p.hostname or '').endswith('.pixieset.com')
                    and re.fullmatch(r'/[\w-]+/',p.path) and not p.query and not p.fragment):
                sources.append({'key':discovery_key(job),'url':job.source_url})
        return {'sources':sources[:10]},200,{'X-Robots-Tag':'noindex','Cache-Control':'no-store'}
