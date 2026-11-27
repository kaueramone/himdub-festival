"""Verified account preferences for recipient-specific transactional messages."""
from sqlalchemy import func
from models import CustomerAccount

LANGUAGES = {'pt':'Português', 'en':'English', 'es':'Español', 'fr':'Français'}

def normalize_language(value, fallback='en'):
    return value if value in LANGUAGES else (fallback if fallback in LANGUAGES else 'en')

def preferred_language(email, fallback='en'):
    account = CustomerAccount.query.filter(
        func.lower(CustomerAccount.email) == (email or '').strip().lower()).first()
    return normalize_language(account.preferred_language if account else None, fallback)

class LocalizedOrder:
    def __init__(self, order, language):
        self._order, self.lang_code = order, language

    def __getattr__(self, name):
        return getattr(self._order, name)

NOTICE_COPY = {
 'pt': {
  'access':('O teu acesso privado — HIM Dub Festival','Entra na tua área de cliente para consultar encomendas, bilhetes e pedidos.','Entrar na minha conta','Link privado, válido por 20 minutos e uma única utilização. Se não pediste acesso, ignora este email.'),
  'transfer':('Confirmar troca de titular — HIM Dub Festival','Foi pedida uma troca de titular do teu bilhete. Confirma os dados do novo titular e paga a taxa de serviço para concluir.','Continuar troca de titular','Entra com a tua conta. Se não fizeste este pedido, ignora o email. O bilhete mantém-se válido até o pagamento ser confirmado.'),
  'completed':('Transferência de titular concluída — HIM Dub Festival','A troca foi concluída. O QR antigo foi invalidado. O novo titular recebe o bilhete no email que indicaste.','','')},
 'en': {
  'access':('Your private access — HIM Dub Festival','Sign in to your customer account to view orders, tickets and requests.','Open my account','Private link, valid for 20 minutes and one use only. If you did not request access, ignore this email.'),
  'transfer':('Confirm ticket holder change — HIM Dub Festival','A holder change was requested for your ticket. Confirm the new holder’s details and pay the service fee to complete it.','Continue holder change','Sign in with your account. If you did not request this, ignore the email. Your ticket stays valid until payment is confirmed.'),
  'completed':('Ticket holder change completed — HIM Dub Festival','The change is complete. The old QR code is no longer valid. The new holder receives the ticket at the email address you provided.','','')},
 'es': {
  'access':('Tu acceso privado — HIM Dub Festival','Entra en tu área de cliente para consultar pedidos, entradas y solicitudes.','Entrar en mi cuenta','Enlace privado, válido durante 20 minutos y para un solo uso. Si no solicitaste el acceso, ignora este email.'),
  'transfer':('Confirmar cambio de titular — HIM Dub Festival','Se ha solicitado un cambio de titular de tu entrada. Confirma los datos del nuevo titular y paga la tarifa de gestión para completarlo.','Continuar cambio de titular','Entra con tu cuenta. Si no hiciste esta solicitud, ignora el email. Tu entrada sigue siendo válida hasta que se confirme el pago.'),
  'completed':('Cambio de titular completado — HIM Dub Festival','El cambio está completado. El QR anterior ya no es válido. El nuevo titular recibe la entrada en el email que indicaste.','','')},
 'fr': {
  'access':('Ton accès privé — HIM Dub Festival','Connecte-toi à ton espace client pour consulter commandes, billets et demandes.','Ouvrir mon compte','Lien privé, valable 20 minutes et une seule fois. Si tu n’as pas demandé cet accès, ignore cet email.'),
  'transfer':('Confirmer le changement de titulaire — HIM Dub Festival','Un changement de titulaire a été demandé pour ton billet. Confirme les coordonnées du nouveau titulaire et paie les frais pour le finaliser.','Continuer le changement','Connecte-toi avec ton compte. Si tu n’as pas fait cette demande, ignore cet email. Ton billet reste valable jusqu’à la confirmation du paiement.'),
  'completed':('Changement de titulaire terminé — HIM Dub Festival','Le changement est terminé. L’ancien QR n’est plus valable. Le nouveau titulaire reçoit le billet à l’adresse email indiquée.','','')},
}

for _language, _copy in {
 'pt':('Confirma a tua subscrição — HIM Dub Festival','Confirma que queres receber novidades, programação e informações sobre os bilhetes.','Confirmar subscrição','Link válido por 48 horas. Podes cancelar a subscrição a qualquer momento. Se não fizeste este pedido, ignora o email.'),
 'en':('Confirm your subscription — HIM Dub Festival','Confirm that you want to receive festival news, line-up and ticket information.','Confirm subscription','Link valid for 48 hours. You can unsubscribe at any time. If you did not request this, ignore the email.'),
 'es':('Confirma tu suscripción — HIM Dub Festival','Confirma que quieres recibir novedades, programación e información sobre las entradas.','Confirmar suscripción','Enlace válido durante 48 horas. Puedes darte de baja en cualquier momento. Si no lo solicitaste, ignora el email.'),
 'fr':('Confirme ton inscription — HIM Dub Festival','Confirme que tu souhaites recevoir les actualités, la programmation et les informations sur les billets.','Confirmer mon inscription','Lien valable 48 heures. Tu peux te désinscrire à tout moment. Si tu n’as pas fait cette demande, ignore cet email.'),
}.items():
    NOTICE_COPY[_language]['newsletter'] = _copy

def notice_payload(kind, email, sender, link='', fallback='en'):
    from flask import render_template
    lang = preferred_language(email, fallback)
    subject, body, button, hint = NOTICE_COPY[lang][kind]
    return {'from':sender, 'to':[email], 'subject':subject,
            'text':'\n'.join(part for part in (body, link, hint) if part),
            'html':render_template('emails/customer_notice.html', language_code=lang,
                heading=subject.split(' — ')[0], body=body, button=button, hint=hint, link=link)}
