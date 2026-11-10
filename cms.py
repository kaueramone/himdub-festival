"""Small structured CMS; no executable HTML, arbitrary CSS or remote downloads."""

import io
import os
import re
import secrets
from urllib.parse import urlsplit
import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from extensions import db
from models import PageAppearance, PageBlock, GalleryAlbum, GalleryPhoto, AuditEvent

PAGES = {
    "home": ("Página inicial", "/"),
    "experience": ("The Experience", "/the-experience"),
    "participate": ("Participate", "/participate"),
    "faq": ("Perguntas frequentes", "/infos/faq"),
    "sustainability": ("Sustentabilidade", "/infos/sustainability"),
    "partners": ("Parceiros", "/infos/partners"),
    "contact": ("Contactos", "/infos/contact"),
    "tickets": ("Bilhetes", "/tickets"),
    "gallery": ("Galeria", "/gallery"),
}
from legal_content import LEGAL_PAGES
PAGES.update(LEGAL_PAGES)


def safe_url(value, image=False):
    value = value.strip()
    if not value:
        return ""
    if (
        len(value) > 500
        or any(ord(c) < 32 for c in value)
        or any(c in value for c in "\\\"'<> ")
    ):
        abort(400, description="O endereço indicado não é válido.")
    parsed = urlsplit(value)
    if value.startswith("/") and not value.startswith("//"):
        if not image or value.startswith("/static/"):
            return value
    if (
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
    ):
        # Media is first-party; avoid visitors leaking IPs/referrers to arbitrary hosts.
        allowed = {
            urlsplit(current_app.config.get("CDN_BASE_URL", "")).hostname,
            urlsplit(current_app.config["DOMAIN"]).hostname,
        }
        if not image or parsed.hostname in allowed:
            return value
    abort(
        400,
        description="Usa um endereço HTTPS. Para imagens, carrega o ficheiro ou usa o CDN do festival.",
    )


def upload_image(file):
    if not file or not file.filename:
        return ""
    password = os.environ.get("BUNNY_STORAGE_PASSWORD", "")
    zone = os.environ.get("BUNNY_STORAGE_ZONE", "")
    if (
        not password
        or not re.fullmatch(r"[a-z0-9-]+", zone)
        or not current_app.config.get("CDN_BASE_URL")
    ):
        abort(
            503,
            description="O carregamento de imagens ainda não foi configurado. Contacta o responsável técnico.",
        )
    raw = file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        abort(413, description="Cada imagem pode ter até 10 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as original:
            if (
                original.format not in ("JPEG", "PNG", "WEBP")
                or original.width * original.height > 25_000_000
            ):
                abort(
                    400,
                    description="Usa uma imagem JPEG, PNG ou WebP até 25 megapíxeis.",
                )
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((2400, 2400))
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=85)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        abort(400, description="Não foi possível ler esta imagem.")
    path = "media/" + secrets.token_hex(20) + ".webp"
    try:
        response = requests.put(
            "https://storage.bunnycdn.com/" + zone + "/" + path,
            data=output.getvalue(),
            headers={"AccessKey": password, "Content-Type": "image/webp"},
            timeout=45,
        )
        if response.status_code not in (200, 201):
            raise requests.RequestException("Upload failed")
    except requests.RequestException:
        abort(502, description="Não foi possível carregar a imagem. Tenta novamente.")
    return current_app.config["CDN_BASE_URL"].rstrip("/") + "/" + path


def image_input(field="image_url", file_field="image"):
    return upload_image(request.files.get(file_field)) or safe_url(
        request.form.get(field, ""), image=True
    )


def audit(action, target):
    db.session.add(
        AuditEvent(
            actor=session.get("admin_name", "staff"), action=action, target=str(target)
        )
    )


def page_context():
    if request.blueprint not in ("main", "shop", "gallery_public"):
        return {}
    key = next((k for k, v in PAGES.items() if v[1] == request.path), None)
    if request.path.startswith("/gallery/"):
        key = "gallery"
    if not key:
        return {}
    appearance = db.session.get(PageAppearance, key)
    from native_content import page_items

    return {
        "page_settings": appearance,
        "native_content": page_items(key),
        "page_blocks": PageBlock.query.filter_by(page_key=key, published=True)
        .order_by(PageBlock.sort_order, PageBlock.id)
        .all(),
    }


def init_cms(bp, admin_required):
    from native_content import init_native_content, page_items

    init_native_content(bp, admin_required)

    @bp.route("/content")
    @admin_required
    def content():
        page = request.args.get("page", "experience")
        if page not in PAGES:
            abort(404)
        return render_template(
            "admin/content.html",
            pages=PAGES,
            page_key=page,
            original_items=page_items(page, admin=True),
            content_pages=PAGES,
            blocks=PageBlock.query.filter_by(page_key=page)
            .order_by(PageBlock.sort_order, PageBlock.id)
            .all(),
        )

    @bp.post("/content/duplicate")
    @admin_required
    def duplicate_block():
        original = db.get_or_404(PageBlock, request.form.get("id", type=int))
        fields = (
            "page_key",
            "title",
            "text",
            "image_url",
            "link_url",
            "link_label",
            "title_en",
            "title_fr",
            "text_en",
            "text_fr",
            "sort_order",
        )
        copy = PageBlock(**{field: getattr(original, field) for field in fields})
        copy.title = original.title[:172] + " (cópia)"
        copy.published = False
        copy.sort_order = min(10000, (original.sort_order or 0) + 1)
        db.session.add(copy)
        db.session.flush()
        audit("content_duplicated", copy.id)
        db.session.commit()
        flash("Cópia criada como rascunho.", "success")
        return redirect(url_for("admin.edit_block", id=copy.id))

    @bp.route("/content/edit", methods=["GET", "POST"])
    @admin_required
    def edit_block():
        identifier = request.values.get("id", type=int)
        block = db.session.get(PageBlock, identifier) if identifier else None
        if identifier and not block:
            abort(404)
        page = block.page_key if block else request.values.get("page", "experience")
        if page not in PAGES:
            abort(404)
        if request.method == "POST":
            block = block or PageBlock(page_key=page)
            title = request.form.get("title", "").strip()
            if not title or len(title) > 180:
                abort(400, description="Indica um título até 180 caracteres.")
            block.title = title
            for field, limit in [
                ("text", 10000),
                ("link_label", 80),
            ]:
                setattr(block, field, request.form.get(field, "").strip()[:limit])
            block.image_url = image_input()
            block.link_url = safe_url(request.form.get("link_url", ""))
            block.sort_order = max(
                -10000, min(10000, request.form.get("sort_order", 0, type=int))
            )
            block.published = request.form.get("published") == "on"
            db.session.add(block)
            db.session.flush()
            audit("content_saved", block.id)
            db.session.commit()
            flash(
                "Conteúdo guardado"
                + (" e publicado." if block.published else " como rascunho."),
                "success",
            )
            return redirect(url_for("admin.content", page=page))
        return render_template(
            "admin/edit_block.html", block=block, pages=PAGES, page_key=page
        )

    @bp.route("/appearance", methods=["GET", "POST"])
    @admin_required
    def appearance():
        page = request.values.get("page", "home")
        if page not in PAGES:
            abort(404)
        settings = db.session.get(PageAppearance, page)
        if request.method == "POST":
            mode = request.form.get("mode", "original")
            layout = request.form.get("layout", "cards")
            if mode not in ("original", "image", "color", "gradient") or layout not in (
                "cards",
                "list",
            ):
                abort(400)
            colors = [request.form.get(k, "") for k in ("color_start", "color_end")]
            if not all(re.fullmatch("#[0-9a-fA-F]{6}", c) for c in colors):
                abort(400)
            image = image_input()
            if mode == "image" and not image:
                abort(400, description="Carrega ou seleciona uma imagem de fundo.")
            settings = settings or PageAppearance(key=page)
            settings.mode = mode
            settings.layout = layout
            settings.color_start, settings.color_end = colors
            settings.image_url = image
            db.session.add(settings)
            audit("appearance_saved", page)
            db.session.commit()
            flash("Aspeto da página atualizado.", "success")
            return redirect(url_for("admin.appearance", page=page))
        return render_template(
            "admin/appearance.html", pages=PAGES, page_key=page, settings=settings
        )

    @bp.route("/gallery", methods=["GET", "POST"])
    @admin_required
    def gallery():
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            if not title or len(title) > 180:
                abort(400, description="Indica o título do álbum.")
            album = GalleryAlbum(title=title)
            db.session.add(album)
            db.session.flush()
            audit("album_created", album.id)
            db.session.commit()
            return redirect(url_for("admin.edit_album", album_id=album.id))
        from sqlalchemy.orm import selectinload

        return render_template(
            "admin/gallery.html",
            albums=GalleryAlbum.query.options(selectinload(GalleryAlbum.photos))
            .order_by(GalleryAlbum.sort_order, GalleryAlbum.id.desc())
            .all(),
        )

    @bp.route("/gallery/<int:album_id>", methods=["GET", "POST"])
    @admin_required
    def edit_album(album_id):
        album = db.get_or_404(GalleryAlbum, album_id)
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            if not title or len(title) > 180:
                abort(400, description="Indica o título do álbum.")
            album.title = title
            album.description = request.form.get("description", "")[:5000]
            album.cover_url = image_input("cover_url", "cover")
            album.published = request.form.get("published") == "on"
            album.sort_order = max(
                -10000, min(10000, request.form.get("sort_order", 0, type=int))
            )
            if album.published and (not album.cover_url or not album.photos):
                abort(
                    400,
                    description="Antes de publicar, adiciona uma capa e pelo menos uma fotografia.",
                )
            audit("album_saved", album.id)
            db.session.commit()
            flash("Álbum guardado.", "success")
            return redirect(url_for("admin.edit_album", album_id=album.id))
        return render_template("admin/edit_album.html", album=album)

    @bp.route("/gallery/<int:album_id>/photos", methods=["POST"])
    @admin_required
    def add_photo(album_id):
        album = db.get_or_404(GalleryAlbum, album_id)
        files = [f for f in request.files.getlist("photos") if f.filename]
        if len(files) > 20:
            abort(400, description="Carrega até 20 fotografias de cada vez.")
        urls = [upload_image(f) for f in files]
        if request.form.get("image_url", "").strip():
            urls.append(safe_url(request.form["image_url"], image=True))
        if not urls:
            abort(400, description="Escolhe pelo menos uma fotografia.")
        start = max((p.sort_order for p in album.photos), default=0)
        for i, url in enumerate(urls):
            db.session.add(
                GalleryPhoto(
                    album_id=album.id,
                    image_url=url,
                    caption=request.form.get("caption", "")[:255],
                    sort_order=start + i + 1,
                )
            )
        audit("photos_added", album.id)
        db.session.commit()
        flash("Fotografias adicionadas.", "success")
        return redirect(url_for("admin.edit_album", album_id=album.id))

    @bp.route("/gallery/photos/<int:photo_id>/remove", methods=["POST"])
    @admin_required
    def remove_photo(photo_id):
        photo = db.get_or_404(GalleryPhoto, photo_id)
        album_id = photo.album_id
        album = db.session.get(GalleryAlbum, album_id)
        if len(album.photos) <= 1:
            album.published = False
        db.session.delete(photo)
        audit("photo_removed_from_album", photo_id)
        db.session.commit()
        flash(
            "Fotografia retirada do álbum. O ficheiro original no CDN foi preservado.",
            "success",
        )
        return redirect(url_for("admin.edit_album", album_id=album_id))
