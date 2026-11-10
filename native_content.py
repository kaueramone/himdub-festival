"""Edit original page items without rendering a second copy below the page."""

import json
import re
import secrets
from flask import abort, flash, redirect, render_template, request, url_for
from auto_translation import translate_public
from content_catalog import DEFAULT_CONTENT
from legal_content import LEGAL_DEFAULT_CONTENT
from extensions import db
from models import NativeContent

GROUPS = {"promoters": "Promotores", "partners": "Parceiros", "support": "Apoio"}


def page_items(page, *, admin=False):
    defaults = LEGAL_DEFAULT_CONTENT.get(page, DEFAULT_CONTENT.get(page, []))
    overrides = {
        row.key: json.loads(row.payload)
        for row in NativeContent.query.filter_by(page_key=page)
    }
    items = []
    for i, original in enumerate(defaults):
        item = {
            "title": "",
            "text": "",
            "image_url": "",
            "link_url": "",
            "link_label": "",
            "published": True,
            "sort_order": i * 10,
            **original,
        }
        item.update(overrides.pop(original["key"], {}))
        items.append(item)
    items.extend(dict(data, key=key) for key, data in overrides.items())
    if not admin:
        from i18n import current_lang

        language = current_lang()
        items = [item for item in items if item.get("published")]
        for item in items:
            if language != "pt":
                for field in ("title", "text", "link_label"):
                    item[field] = translate_public(item.get(field, ''), language)
            if item.get("link_url", "").startswith("/") and language != "pt":
                item["link_url"] = "/" + language + item["link_url"]
    return sorted(items, key=lambda item: (item["sort_order"], item["key"]))


def find_item(key):
    if not key or ":" not in key:
        abort(404)
    page = key.split(":", 1)[0]
    item = next(
        (item for item in page_items(page, admin=True) if item["key"] == key), None
    )
    if item is None:
        abort(404)
    return page, item


def save_item(page, item):
    row = db.session.get(NativeContent, item["key"]) or NativeContent(
        key=item["key"], page_key=page
    )
    row.payload = json.dumps(item, ensure_ascii=False)
    db.session.add(row)


def init_native_content(bp, admin_required):
    from cms import PAGES, audit, image_input, safe_url

    @bp.route("/content/original/edit", methods=["GET", "POST"])
    @admin_required
    def edit_native_content():
        page, item = find_item(request.values.get("key", ""))
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            if not title or len(title) > 180:
                abort(400, description="Indica um título até 180 caracteres.")
            item["title"] = title
            for field, limit in (
                ("text", 10000),
                ("link_label", 80),
            ):
                item[field] = request.form.get(field, "").strip()[:limit]
            item["sort_order"] = max(
                -10000, min(10000, request.form.get("sort_order", 0, type=int))
            )
            item["published"] = request.form.get("published") == "on"
            if item["kind"] in ("card", "partner", "way"):
                item["image_url"] = image_input()
                item["link_url"] = safe_url(request.form.get("link_url", ""))
            if item["kind"] == "way":
                item["status"] = request.form.get("status", "closed")
                if item["status"] not in ("open", "closed"):
                    abort(400)
            if item["kind"] == "eco":
                item["color"] = request.form.get("color", "")
                if not re.fullmatch(r"#[0-9a-fA-F]{6}", item["color"]):
                    abort(400)
            if item["kind"] == "partner":
                item["group"] = request.form.get("group", "")
                if item["group"] not in GROUPS:
                    abort(400)
                if not item["image_url"] and item["published"]:
                    abort(
                        400,
                        description="Adiciona o logótipo antes de publicar o parceiro.",
                    )
            if item["kind"] == "contact":
                item["email"] = request.form.get("email", "").strip().lower()
                if (
                    not re.fullmatch(
                        r"[a-z0-9.!#$%&*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}",
                        item["email"],
                    )
                    or len(item["email"]) > 180
                ):
                    abort(400, description="Indica um endereço de email válido.")
            save_item(page, item)
            audit("original_content_saved", item["key"])
            db.session.commit()
            flash(
                (
                    "Conteúdo guardado na página."
                    if item["published"]
                    else "Conteúdo guardado como rascunho e retirado da página pública."
                ),
                "success",
            )
            return redirect(url_for("admin.content", page=page))
        return render_template(
            "admin/edit_native_content.html",
            item=item,
            page_key=page,
            pages=PAGES,
            groups=GROUPS,
        )

    @bp.post("/content/original/duplicate")
    @admin_required
    def duplicate_native_content():
        page, original = find_item(request.form.get("key", ""))
        copy = dict(
            original,
            key=page + ":copy-" + secrets.token_hex(12),
            published=False,
            sort_order=min(10000, original["sort_order"] + 1),
        )
        for field in ("title", "title_en", "title_fr"):
            copy[field] = (copy.get(field) or original["title"])[:172] + " (cópia)"
        save_item(page, copy)
        audit("original_content_duplicated", copy["key"])
        db.session.commit()
        flash(
            "Cópia criada como rascunho. Edita e publica quando estiver pronta.",
            "success",
        )
        return redirect(url_for("admin.edit_native_content", key=copy["key"]))
