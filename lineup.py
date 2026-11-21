"""Editorial lineup with UTC scheduling and authenticated draft previews."""
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo
from flask import abort, flash, redirect, render_template, request, url_for, make_response
from extensions import db
from models import LineupStage, LineupAnnouncement, LineupArtist

DEFAULT_STAGES = [('dub-temple','Dub Temple'),('river-station','River Station'),
    ('roots-garden','Roots Garden'),('lectures','Lectures'),('kids-area','Kids Area'),
    ('workshops','Workshops'),('healing-area','Healing Area')]


def ordered(model):
    return model.query.order_by(model.sort_order,model.id).all()


def lineup_data(preview=False, stage_slug=None, now=None):
    now = now or datetime.utcnow()
    stages = ordered(LineupStage)
    phases = ordered(LineupAnnouncement)
    if not preview:
        stages = [s for s in stages if s.published]
        phases = [p for p in phases if p.published and (not p.publish_at or p.publish_at <= now)]
    stage_ids, phase_ids = {s.id for s in stages}, {p.id for p in phases}
    artists = [a for a in ordered(LineupArtist) if not a.deleted_at and a.stage_id in stage_ids
        and (preview or (a.published and (not a.announcement_id or a.announcement_id in phase_ids)))]
    if stage_slug:
        artists = [a for a in artists if a.stage.slug == stage_slug]
    return dict(lineup_stages=stages,lineup_phases=phases,lineup_artists=artists,lineup_preview=preview)


def text_field(name, limit, required=False):
    value = request.form.get(name,'').strip()
    if len(value)>limit or (required and not value):
        abort(400,description='Preenche os campos obrigatórios e respeita os limites indicados.')
    return value


def order_field():
    value = request.form.get('sort_order',0,type=int)
    if value is None or not -10000 <= value <= 10000:
        abort(400,description='Ordem inválida.')
    return value


def local_date(value):
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is not None:
            raise ValueError()
        zone = ZoneInfo('Europe/Lisbon')
        aware = date.replace(tzinfo=zone)
        # Reject nonexistent and ambiguous clock-change times rather than guessing.
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != date or aware.utcoffset()!=date.replace(tzinfo=zone,fold=1).utcoffset():
            raise ValueError()
        return aware.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        abort(400,description='Data inválida ou ambígua na mudança de hora em Portugal.')


def init_lineup(bp, admin_required):
    from cms import audit, image_input, safe_url

    @bp.get('/lineup')
    @admin_required
    def lineup_admin():
        return render_template('admin/lineup.html',stages=ordered(LineupStage),
            phases=ordered(LineupAnnouncement),artists=ordered(LineupArtist))

    @bp.route('/lineup/stage',methods=['GET','POST'])
    @admin_required
    def lineup_stage():
        identifier=request.values.get('id',type=int)
        stage=db.get_or_404(LineupStage,identifier) if identifier else LineupStage()
        if request.method=='POST':
            stage.name=text_field('name',120,True)
            slug=text_field('slug',80,True)
            if not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*',slug):
                abort(400,description='Usa letras minúsculas, números e hífen no endereço do palco.')
            if stage.id and slug != stage.slug:
                abort(400,description='O identificador de um palco existente não pode ser alterado.')
            other=LineupStage.query.filter_by(slug=slug).first()
            if other and other.id != stage.id:
                abort(400,description='Já existe um palco com esse endereço.')
            stage.slug,stage.description=slug,text_field('description',10000)
            stage.sort_order,stage.published=order_field(),request.form.get('published')=='on'
            db.session.add(stage); db.session.flush(); audit('lineup_stage_saved',stage.id); db.session.commit()
            flash('Palco guardado. Ocultar um palco oculta também os seus artistas.','success')
            return redirect(url_for('admin.lineup_admin'))
        return render_template('admin/lineup_stage.html',stage=stage)

    @bp.route('/lineup/announcement',methods=['GET','POST'])
    @admin_required
    def lineup_announcement():
        identifier=request.values.get('id',type=int)
        phase=db.get_or_404(LineupAnnouncement,identifier) if identifier else LineupAnnouncement()
        if request.method=='POST':
            phase.title=text_field('title',180,True)
            phase.description=text_field('description',10000)
            phase.poster_url=image_input('poster_url')
            if request.form.get('remove_poster')=='on': phase.poster_url=''
            phase.publish_at=local_date(request.form.get('publish_at',''))
            phase.sort_order,phase.published=order_field(),request.form.get('published')=='on'
            db.session.add(phase); db.session.flush(); audit('lineup_announcement_saved',phase.id); db.session.commit()
            flash('Fase guardada. A publicação respeita a data e hora indicadas.','success')
            return redirect(url_for('admin.lineup_admin'))
        return render_template('admin/lineup_announcement.html',phase=phase)

    @bp.route('/lineup/artist',methods=['GET','POST'])
    @admin_required
    def lineup_artist():
        identifier=request.values.get('id',type=int)
        artist=db.get_or_404(LineupArtist,identifier) if identifier else LineupArtist()
        if artist.deleted_at: abort(404)
        if request.method=='POST':
            artist.name=text_field('name',180,True)
            artist.country=text_field('country',100)
            artist.biography=text_field('biography',10000)
            artist.music_url=safe_url(request.form.get('music_url',''))
            artist.image_url=image_input()
            stage=db.get_or_404(LineupStage,request.form.get('stage_id',type=int))
            phase_id=request.form.get('announcement_id',type=int)
            if phase_id: db.get_or_404(LineupAnnouncement,phase_id)
            artist.stage_id,artist.announcement_id=stage.id,phase_id
            artist.sort_order,artist.published=order_field(),request.form.get('published')=='on'
            if artist.published and not artist.image_url:
                abort(400,description='Adiciona a fotografia antes de publicar o artista.')
            db.session.add(artist); db.session.flush(); audit('lineup_artist_saved',artist.id); db.session.commit()
            flash('Artista guardado. A fase e o palco também precisam de estar publicados para aparecer no website.','success')
            return redirect(url_for('admin.lineup_admin'))
        return render_template('admin/lineup_artist.html',artist=artist,stages=ordered(LineupStage),phases=ordered(LineupAnnouncement))

    @bp.post('/lineup/artist/<int:id>/<action>')
    @admin_required
    def lineup_artist_action(id,action):
        artist=db.get_or_404(LineupArtist,id)
        if action=='duplicate' and not artist.deleted_at:
            artist=LineupArtist(name=artist.name[:172]+' (cópia)',country=artist.country,
                biography=artist.biography,image_url=artist.image_url,music_url=artist.music_url,
                stage_id=artist.stage_id,announcement_id=artist.announcement_id,sort_order=artist.sort_order,published=False)
            db.session.add(artist)
        elif action=='delete' and not artist.deleted_at:
            artist.deleted_at,artist.published=datetime.utcnow(),False
        elif action=='restore' and artist.deleted_at:
            artist.deleted_at,artist.published=None,False
        else: abort(400)
        db.session.flush(); audit('lineup_artist_'+action,artist.id); db.session.commit()
        flash('Alteração guardada. Cópias e recuperações ficam em rascunho.','success')
        return redirect(url_for('admin.lineup_admin'))

    @bp.get('/lineup/preview')
    @admin_required
    def lineup_preview():
        response=make_response(render_template('lineup_preview.html',**lineup_data(preview=True)))
        response.headers['Cache-Control']='no-store'
        response.headers['X-Robots-Tag']='noindex, nofollow'
        return response
