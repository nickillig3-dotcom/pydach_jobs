from io import BytesIO
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from segno.helpers import make_epc_qr

def euro(amount: float):
    return Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def make_epc_qr_png(iban: str, name: str, amount_eur: float, reference: str, bic: Optional[str] = None, scale: int = 6) -> bytes:
    qr = make_epc_qr(name=name[:70], iban=iban.replace(" ", ""), amount=euro(amount_eur), text=reference, bic=bic or None)
    buf = BytesIO()
    qr.save(buf, kind='png', scale=scale)
    return buf.getvalue()
