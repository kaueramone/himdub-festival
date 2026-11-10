"""Audited, administrator-only checkout provider selection."""
from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError
from auth import totp_counter
from extensions import db
from models import AdminUser, AuditEvent, Order, PaymentCheckout, PaymentGatewayConfiguration
from teya_gateway import provider, configured, ready


def init_payment_admin(bp, admin_required):
    @bp.route('/payments',methods=['GET','POST'])
    @admin_required
    def payments():
        if request.method=='POST':
            user=AdminUser.query.filter_by(id=session.get('user_id'),active=True,role='admin').populate_existing().with_for_update().first()
            if not user:
                abort(403)
            row=PaymentGatewayConfiguration.query.filter_by(id=1).populate_existing().with_for_update().first()
            try:
                revision=int(request.form.get('revision',''))
            except ValueError:
                abort(400)
            if revision!=(row.revision if row else 0):
                abort(409,description='A configuração foi alterada por outra pessoa. Actualiza a página.')
            selected=request.form.get('provider','')
            if selected not in ('stripe','teya') or request.form.get('confirm')!='yes':
                abort(400,description='Escolhe o fornecedor e confirma a alteração.')
            if selected=='teya' and not ready():
                abort(409,description='A Teya ainda não concluiu a configuração e validação técnica.')
            if selected=='stripe' and current_app.config.get('PRODUCTION') and not current_app.config.get('STRIPE_SECRET_KEY'):
                abort(409,description='Stripe não está configurado.')
            if user.totp_secret:
                counter=totp_counter(user.totp_secret,request.form.get('totp','').strip())
                if counter is None or counter<=user.totp_last_counter:
                    abort(400,description='Confirma com um código novo do autenticador.')
                user.totp_last_counter=counter
            elif current_app.config.get('PRODUCTION'):
                abort(403)
            old=row.provider if row else 'stripe'
            if not row:
                row=PaymentGatewayConfiguration(id=1,provider=selected,revision=1)
                db.session.add(row)
            else:
                row.provider=selected
                row.revision+=1
            db.session.add(AuditEvent(actor=user.email,action='checkout_provider_changed',target=f'{old}->{selected}'))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                abort(409,description='A configuração mudou. Actualiza a página.')
            flash('Fornecedor actualizado para novas compras. Os pagamentos já iniciados mantêm o fornecedor original.','success')
            return redirect(url_for('admin.payments'))
        row=db.session.get(PaymentGatewayConfiguration,1)
        pending=PaymentCheckout.query.filter(PaymentCheckout.status.in_(('preparing','active','processing'))).count()
        issues=PaymentCheckout.query.filter(PaymentCheckout.last_error!='').count()
        return render_template('admin/payments.html',selected_provider=provider(),revision=row.revision if row else 0,
                               teya_configured=configured(),teya_ready=ready(),pending=pending,issues=issues,
                               pagination=PaymentCheckout.query.filter_by(status='paid').order_by(PaymentCheckout.created_at.desc()).paginate(page=request.args.get('page',1,type=int),per_page=20,error_out=False))

    @bp.post('/payments/refunds/<int:order_id>')
    @admin_required
    def record_teya_refund(order_id):
        user=AdminUser.query.filter_by(id=session.get('user_id'),active=True,role='admin').populate_existing().with_for_update().first()
        if not user:
            abort(403)
        if request.form.get('confirm')!='yes':
            abort(400,description='Confirma primeiro o reembolso integral no painel Teya.')
        reference=request.form.get('refund_reference','').strip()
        if not reference or len(reference)>100:
            abort(400,description='Indica a referência do reembolso concluído na Teya.')
        if user.totp_secret:
            counter=totp_counter(user.totp_secret,request.form.get('totp','').strip())
            if counter is None or counter<=user.totp_last_counter:
                abort(400,description='Confirma com um código novo do autenticador.')
            user.totp_last_counter=counter
        elif current_app.config.get('PRODUCTION'):
            abort(403)
        order=Order.query.filter_by(id=order_id).populate_existing().with_for_update().first_or_404()
        payment=PaymentCheckout.query.filter_by(order_id=order_id,provider='teya').populate_existing().with_for_update().first_or_404()
        if order.status!='paid' or payment.status!='paid':
            abort(409,description='Esta encomenda não tem um pagamento Teya por reconciliar.')
        order.status='refunded'
        payment.status='refunded'
        db.session.add(AuditEvent(actor=user.email,action='teya_refund_recorded',target=f'{order_id}:{reference}'))
        db.session.commit()
        flash('Reembolso registado no website. Os bilhetes desta encomenda deixam de permitir entrada. Não foi efectuada nenhuma operação financeira por este formulário.','success')
        return redirect(url_for('admin.payments'))
