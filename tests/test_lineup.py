"""Visibility, scheduling, editorial permissions and protected previews."""
from datetime import datetime,timedelta
import unittest
import test_admin_autonomy as autonomy
from models import LineupStage,LineupArtist,LineupAnnouncement
from extensions import db
from lineup import lineup_data

class LineupTests(unittest.TestCase):
    setUp=autonomy.AdminAutonomyTests.setUp
    tearDown=autonomy.AdminAutonomyTests.tearDown
    login=autonomy.AdminAutonomyTests.login
    post=autonomy.AdminAutonomyTests.post

    def artist(self,**values):
        stage=LineupStage.query.first()
        artist=LineupArtist(name='Unannounced Synthetic Artist',stage_id=stage.id,image_url='/static/img/experience-areas.jpg',**values)
        db.session.add(artist);db.session.commit();return artist

    def test_future_and_draft_content_never_leaks_in_public_pages_or_translation_feed(self):
        phase=LineupAnnouncement(title='Secret Synthetic Phase',description='Secret phase details',published=True,publish_at=datetime.utcnow()+timedelta(days=1))
        db.session.add(phase);db.session.commit()
        artist=self.artist(published=True,announcement_id=phase.id,biography='Private artist biography')
        for path in ['/line-up','/line-up/dub-temple','/i18n/sources.json']:
            data=self.client.get(path).data
            self.assertNotIn(b'Secret Synthetic Phase',data);self.assertNotIn(b'Private artist biography',data)
        self.assertEqual(lineup_data()['lineup_artists'],[])
        phase.publish_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
        self.assertIn(artist,lineup_data()['lineup_artists'])
        self.assertIn(b'Unannounced Synthetic Artist',self.client.get('/line-up').data)
        artist.stage.published=False;db.session.commit()
        self.assertEqual(lineup_data()['lineup_artists'],[])

    def test_editor_creates_and_duplicates_as_draft_then_removes_and_restores(self):
        self.login();stage=LineupStage.query.first()
        data={'name':'Novo Artista','stage_id':stage.id,'biography':'<script>unsafe</script>','image_url':'/static/img/experience-areas.jpg','published':'on'}
        self.assertEqual(self.post('/admin/lineup/artist',data).status_code,302)
        artist=LineupArtist.query.one()
        self.assertIn(b'&lt;script&gt;unsafe&lt;/script&gt;',self.client.get('/line-up').data)
        self.assertEqual(self.post(f'/admin/lineup/artist/{artist.id}/duplicate').status_code,302)
        copy=LineupArtist.query.filter(LineupArtist.id!=artist.id).one();self.assertFalse(copy.published)
        self.post(f'/admin/lineup/artist/{artist.id}/delete');self.assertIsNotNone(artist.deleted_at)
        self.post(f'/admin/lineup/artist/{artist.id}/restore');self.assertFalse(artist.published);self.assertIsNone(artist.deleted_at)
        data['music_url']='javascript:alert(1)';self.assertEqual(self.post('/admin/lineup/artist',data).status_code,400)

    def test_staff_and_guests_cannot_edit_or_preview_and_csrf_is_required(self):
        self.assertIn(self.client.get('/admin/lineup/preview').status_code,(302,403))
        self.login(role='checkin')
        for path in ['/admin/lineup','/admin/lineup/preview','/admin/lineup/artist','/admin/lineup/stage','/admin/lineup/announcement']:
            self.assertEqual(self.client.get(path).status_code,302)
            self.assertIn('/checkin/scan',self.client.get(path).location)
        self.assertEqual(self.post('/admin/lineup/artist',{'name':'Rejected'}).status_code,302)
        self.assertEqual(LineupArtist.query.count(),0)

    def test_preview_is_private_and_scheduling_uses_portugal_time(self):
        self.login();self.artist(published=False)
        preview=self.client.get('/admin/lineup/preview')
        self.assertEqual(preview.status_code,200);self.assertIn('no-store',preview.headers['Cache-Control'])
        self.assertIn('noindex',preview.headers['X-Robots-Tag']);self.assertIn(b'Unannounced Synthetic Artist',preview.data)
        self.assertEqual(self.post('/admin/lineup/announcement',{'title':'First wave','published':'on','publish_at':'2027-07-01T12:00'}).status_code,302)
        self.assertEqual(LineupAnnouncement.query.one().publish_at,datetime(2027,7,1,11))
        self.assertEqual(self.post('/admin/lineup/announcement',{'title':'Bad clock','publish_at':'2027-03-28T01:30'}).status_code,400)
        for path in ['/admin/lineup','/admin/lineup/artist','/admin/lineup/stage','/admin/lineup/announcement']:
            self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.client.post('/admin/lineup/stage',data={'name':'No csrf'}).status_code,400)
