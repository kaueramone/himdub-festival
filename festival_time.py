"""Conversão de horas para exibição (UTC -> hora de Portugal).

Tudo o que gravamos na base de dados (`Order.created_at`,
`Ticket.checked_in_at`, etc.) fica em UTC -- é o que `datetime.utcnow()`
devolve, e é a prática certa para guardar datas (não depende de onde o
servidor está alojado nem de horário de verão/inverno).

O problema é mostrar essas datas a uma pessoa: o admin e o check-in são
usados em Portugal, por isso qualquer hora apresentada no site (lista de
encomendas, exportação Excel, check-in de bilhetes) tem de ser convertida
para a hora de Portugal continental antes de aparecer no ecrã -- senão
fica sempre com 1h de diferença da hora real (Portugal está em UTC+1 no
horário de verão e UTC+0 no horário de inverno; a conversão trata disto
sozinha, não é preciso decidir à mão qual dos dois é).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FESTIVAL_TIMEZONE = ZoneInfo("Europe/Lisbon")
_UTC = ZoneInfo("UTC")


def to_local_time(dt):
    """Converte um datetime "ingénuo" (sem timezone, como os que vêm da
    base de dados) assumindo que está em UTC, para a hora de Portugal.
    Devolve None se `dt` for None, para se poder continuar a escrever
    `{% if ticket.checked_in_at %}` nos templates sem rebentar."""
    if dt is None:
        return None
    return dt.replace(tzinfo=_UTC).astimezone(FESTIVAL_TIMEZONE)


def local_day_bounds_utc(day):
    """Caminho inverso do `to_local_time`: dado um dia (objeto `date`, na
    hora de Portugal -- ex: o que vem de um `<input type="date">` no
    admin), devolve (inicio, fim) em UTC "ingénuo", prontos a comparar
    com `Order.created_at`. `fim` é exclusivo (00h desse dia + 1) para o
    intervalo cobrir o dia completo mesmo com a mudança de hora de
    verão/inverno a meio -- usar `inicio <= created_at < fim`."""
    start_local = datetime.combine(day, datetime.min.time(), tzinfo=FESTIVAL_TIMEZONE)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(_UTC).replace(tzinfo=None),
        end_local.astimezone(_UTC).replace(tzinfo=None),
    )
