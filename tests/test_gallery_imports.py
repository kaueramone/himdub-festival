"""Gallery consolidation, recoverable removal and bounded import regressions."""
import io
import json
import unittest
from unittest.mock import patch, Mock
from PIL import Image
import test_admin_autonomy as autonomy
from extensions import db
from models import GalleryAlbum, GalleryPhoto, GalleryImport, PageBlock
from gallery_imports import migrate_gallery_blocks, drive_manifest, trusted_url, fetch, process_import, ImportProblem


class GalleryTests(unittest.TestCase):
    setUp=autonomy.AdminAutonomyTests.setUp
    tearDown=autonomy.AdminAutonomyTests.tearDown
    login=autonomy.AdminAutonomyTests.login
    post=autonomy.AdminAutonomyTests.post

    def test_migration_is_idempotent_and_old_editor_routes_to_album(self):
        self.login()
        block=PageBlock(page_key='gallery',title='Photographer',text='Credit @artist.name',
            image_url='/static/img/experience-areas.jpg',link_url='https://example.pixieset.com/event/',published=True)
        db.session.add(block);db.session.commit()
        migrate_gallery_blocks();migrate_gallery_blocks()
        self.assertEqual(GalleryAlbum.query.count(),1)
        album=GalleryAlbum.query.one()
        self.assertEqual(album.photographer,'@artist.name')
        self.assertEqual(GalleryImport.query.count(),1)
        self.assertFalse(block.published)
        self.assertTrue(self.client.get('/admin/content?page=gallery').location.endswith('/admin/gallery'))
        self.assertTrue(self.client.get('/admin/content/edit?id='+str(block.id)).location.endswith('/admin/gallery/'+str(album.id)))
        self.assertNotIn(b'managed-card',self.client.get('/gallery').data)

    def test_delete_removes_all_public_routes_and_restore_preserves_photos(self):
        self.login()
        album=GalleryAlbum(title='Memories',published=True,cover_url='/static/img/experience-areas.jpg')
        db.session.add(album);db.session.flush()
        db.session.add(GalleryPhoto(album_id=album.id,image_url=album.cover_url))
        db.session.add(GalleryImport(album_id=album.id,source_url='https://drive.google.com/drive/folders/'+'a'*25))
        db.session.commit()
        path='/gallery/'+str(album.id)
        self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.post('/admin'+path+'/delete').status_code,302)
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertNotIn(b'Memories',self.client.get('/gallery').data)
        self.assertEqual(GalleryPhoto.query.count(),1)
        self.assertEqual(GalleryImport.query.one().status,'cancelled')
        self.post('/admin'+path+'/restore')
        self.assertIsNone(album.deleted_at)
        self.assertFalse(album.published)
        self.assertEqual(GalleryPhoto.query.count(),1)

    def test_staff_cannot_delete_or_import(self):
        self.login(role='checkin')
        album=GalleryAlbum(title='Preserve');db.session.add(album);db.session.commit()
        for action in ('delete','restore','import'):
            response=self.post('/admin/gallery/'+str(album.id)+'/'+action)
            self.assertIn('/checkin/scan',response.location)
        self.assertIsNone(album.deleted_at)
        self.assertEqual(GalleryImport.query.count(),0)

    def test_one_link_creates_draft_and_public_discovery_feed_has_no_editorial_or_tokens(self):
        self.login()
        with patch('gallery_imports.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):
            response=self.post('/admin/gallery',{'source_url':'https://example.pixieset.com/event','photographer':'@artist'})
        self.assertEqual(response.status_code,302)
        album=GalleryAlbum.query.one()
        self.assertFalse(album.published)
        self.assertEqual(album.title,'Álbum importado')
        job=GalleryImport.query.one()
        self.assertEqual(job.source_url,'https://example.pixieset.com/event/')
        db.session.add(GalleryImport(album_id=album.id,source_url='https://example.pixieset.com/download/file/?filekey=private-token'))
        db.session.commit()
        response=self.client.get('/gallery-import-sources.json')
        self.assertEqual(len(response.json['sources']),1)
        self.assertNotIn(b'private-token',response.data)
        self.assertNotIn(b'@artist',response.data)
        self.assertNotIn(b'importado',response.data)
        self.assertNotIn(b'album',self.client.get('/gallery').data.lower())

    def test_drive_lists_more_than_fifty_and_nested_folders_without_duplicates(self):
        folder='a'*25;child='b'*25
        listing=''.join('<a href="https://drive.google.com/file/d/'+str(i).zfill(25)+'/view">photo'+str(i)+'.jpg</a>' for i in range(75))
        listing+='<a href="https://drive.google.com/drive/folders/'+child+'">Child</a>'
        nested='<a href="https://drive.google.com/file/d/'+'z'*25+'/view">last.png</a><a href="https://drive.google.com/drive/folders/'+folder+'">Cycle</a>'
        with patch('gallery_imports.fetch',side_effect=[listing.encode(),nested.encode()]):
            entries=drive_manifest('https://drive.google.com/drive/folders/'+folder)
        self.assertEqual(len(entries),76)
        self.assertEqual(len({e['key'] for e in entries}),76)

    def test_import_rejects_internal_hosts_redirects_and_large_responses(self):
        for url in ('http://drive.google.com/x','https://127.0.0.1/x','https://drive.google.com.evil.invalid/x','https://user:password@drive.google.com/x'):
            with self.assertRaises(ImportProblem):trusted_url(url)
        response=Mock(status_code=302,headers={'Location':'http://169.254.169.254/latest/'})
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch('gallery_imports.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch('gallery_imports.requests.get',return_value=response) as http:
            with self.assertRaises(ImportProblem):fetch('https://drive.google.com/file',100)
            self.assertEqual(http.call_count,1)
        response.status_code=200;response.iter_content.return_value=[b'12345',b'67890']
        with patch('gallery_imports.trusted_url'),patch('gallery_imports.requests.get',return_value=response):
            with self.assertRaises(ImportProblem):fetch('https://drive.google.com/file',8)

    def test_worker_resumes_without_duplicate_images_and_credits_are_visible(self):
        album=GalleryAlbum(title='Event',published=True,photographer='@artist')
        db.session.add(album);db.session.flush()
        entries=[{'key':str(i).zfill(64),'name':'picture.jpg','url':'https://drive.usercontent.google.com/download?id='+str(i)} for i in range(2)]
        job=GalleryImport(album_id=album.id,source_url='https://drive.google.com/drive/folders/'+'a'*25,
            manifest=json.dumps(entries),total=2)
        db.session.add(job);db.session.commit()
        raw=io.BytesIO();Image.new('RGB',(10,10)).save(raw,format='JPEG')
        with patch('gallery_imports.fetch',return_value=raw.getvalue()),patch('cms.upload_image',return_value='/static/img/experience-areas.jpg') as upload:
            process_import(self.app)
            self.assertEqual(upload.call_count,2)
            job.processed=0;job.status='pending';db.session.commit()
            process_import(self.app)
            self.assertEqual(upload.call_count,2)
        self.assertEqual(GalleryPhoto.query.count(),2)
        self.assertEqual(job.status,'complete')
        self.assertIn(b'@artist',self.client.get('/gallery/'+str(album.id)).data)

    def test_zip_import_preserves_completed_photos_and_resumes_after_failure(self):
        import zipfile
        album=GalleryAlbum(title='ZIP',photographer='Photographer')
        db.session.add(album);db.session.flush()
        job=GalleryImport(album_id=album.id,source_url='https://example.pixieset.com/files/album.zip')
        db.session.add(job);db.session.commit()
        photo=io.BytesIO();Image.new('RGB',(10,10)).save(photo,format='JPEG')
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('one.jpg',photo.getvalue())
            z.writestr('two.jpg',photo.getvalue())
            z.writestr('ignore.txt','not an image')
        def download(url,limit,target=None):
            target.write(archive.getvalue());target.seek(0)
        with patch('gallery_imports.fetch',side_effect=download),patch('cms.upload_image',side_effect=['/static/one.webp',RuntimeError('offline')]):
            process_import(self.app)
        self.assertEqual(job.status,'failed')
        self.assertEqual(GalleryPhoto.query.count(),1)
        job.status='pending';db.session.commit()
        with patch('gallery_imports.fetch',side_effect=download),patch('cms.upload_image',return_value='/static/two.webp') as upload:
            process_import(self.app)
            self.assertEqual(upload.call_count,1)
        self.assertEqual(job.status,'complete')
        self.assertEqual(job.processed,2)
        self.assertEqual(GalleryPhoto.query.count(),2)
