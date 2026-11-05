"""인증서 기반 디지털 서명(pyHanko): 디지털 ID 만들기·읽기, 서명, 검증, 서명 지우기.

화면이 없는 순수 함수만 둔다. 화면은 `app/ui/dlg_dsign.py`.
디지털 ID 는 pfx/p12 파일 하나(비밀번호는 저장하지 않는다). 설정 목록에는 경로·이름과
비밀번호 없이 보여 줄 공개 인증서(DER base64)를 같이 적어 둔다.
"""
from __future__ import annotations

import base64
import datetime as _dt
import io
import os
import re
import logging
import tempfile
from dataclasses import dataclass, field as _field

import fitz

from ..appinfo import data_dir

MIN_PW = 6
REASONS = ("서명 이유", "나는 이 파일의 소유자입니다.", "이 문서를 검토했습니다.", "문서의 내용이 사실입니다.",
           "이 이용 약관을 읽었으며 이에 동의합니다.")
STYLE_ITEMS = (("name", "이름"), ("location", "지역"), ("dn", "고유 이름"), ("logo", "로고"), ("date", "날짜"),
               ("time", "시간"), ("reason", "원인"), ("label", "라벨"))
DEFAULT_STYLE = {"items": ["name", "date", "reason", "location", "dn"], "image": "", "dir": "auto"}

MSG_BAD_PW = "비밀번호가 일치하지 않습니다."
MSG_EXPIRED = "선택한 인증서는 만료되었습니다!"
MSG_NEED_PW = "이 디지털 ID를 쓰기 위해서는 암호가 필요합니다."
MSG_INVALID = "유효하지 않은 인증서로 인해 서명에 실패했습니다."
MSG_ALL_OK = "서명되었으며 모든 서명이 유효합니다."
MSG_SOME_BAD = "하나 이상의 서명이 잘못되었습니다."
MSG_EMAIL = "The email address must be of the form xxx@xx.com"
MSG_PW_SHORT = f"비밀번호는 {MIN_PW}자 이상이어야 합니다."
COV_OK = "이 서명이 적용된 후 문서가 수정되지 않았습니다."
COV_LATER = "이 서명이 있는 문서의 리비전은 바뀌지 않았습니다. 그러나 그 이후에 문서에 변경이 있었습니다."
COV_BAD = "서명이 적용된 후에 문서가 변경되었거나 손상이 있었습니다."
ID_OK = "서명자의 ID가 유효합니다."
ID_UNKNOWN = "서명자의 ID를 알 수 없습니다. 신뢰할 수 있는 인증서 목록에 없습니다."
ID_EXPIRED = "서명자의 인증서가 만료되었습니다."
TIME_NOTE = "서명 시간은 서명자 컴퓨터의 시간을 기준으로 합니다."
SIG_FIELD_LOCK_ALL = True

# 자가 서명 인증서를 검증할 때 pyHanko 가 남기는 긴 오류 기록은 숨긴다(결과는 상태로 돌려준다)
logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)

_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DsignError(Exception):
    """화면에 그대로 보일 문구를 담는다."""


def security_dir() -> str:
    return os.path.join(data_dir(), "Security")


# ── 인증서 · 디지털 ID ──────────────────────────────────────────────
def _x509():
    from cryptography import x509
    return x509


def _cn(name) -> str:
    from cryptography.x509.oid import NameOID
    a = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return a[0].value if a else name.rfc4514_string()


def _email(cert) -> str:
    from cryptography.x509.oid import NameOID
    a = cert.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS)
    return a[0].value if a else ""


def _dn(name) -> str:
    """사람이 읽는 고유 이름(CN=…, O=…, E=…)."""
    from cryptography.x509.oid import NameOID
    short = {NameOID.COMMON_NAME: "CN", NameOID.ORGANIZATIONAL_UNIT_NAME: "OU", NameOID.ORGANIZATION_NAME: "O",
             NameOID.EMAIL_ADDRESS: "E", NameOID.COUNTRY_NAME: "C", NameOID.LOCALITY_NAME: "L",
             NameOID.STATE_OR_PROVINCE_NAME: "S"}
    return ", ".join(f"{short.get(a.oid, a.oid.dotted_string)}={a.value}" for a in name)


def _font() -> dict:
    """서명 모양 글꼴: 맑은 고딕이 있으면 그것, 없으면 내장 CJK."""
    p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "malgun.ttf")
    return {"fontname": "malgun", "fontfile": p} if os.path.exists(p) else {"fontname": "korea"}


def _usages(cert) -> list[str]:
    from cryptography import x509
    try:
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return ["지정되지 않음"]
    out = []
    for attr, label in (("digital_signature", "전자 서명"), ("content_commitment", "부인 금지"),
                        ("key_encipherment", "키 암호화"), ("data_encipherment", "데이터 암호화"),
                        ("key_agreement", "주요 합의"), ("key_cert_sign", "인증 서명"), ("crl_sign", "CRL 서명")):
        if getattr(ku, attr):
            out.append(label)
    if ku.key_agreement:
        if ku.encipher_only:
            out.append("단 암호화")
        if ku.decipher_only:
            out.append("단 비암호화")
    return out or ["지정되지 않음"]


def _utc(t: _dt.datetime) -> _dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def cert_info(cert) -> dict:
    """인증서 한 장의 보일 정보. cert 는 cryptography 인증서나 DER 바이트."""
    if isinstance(cert, (bytes, bytearray)):
        cert = _x509().load_der_x509_certificate(bytes(cert))
    nb, na = _utc(cert.not_valid_before_utc), _utc(cert.not_valid_after_utc)
    return {"name": _cn(cert.subject), "issuer": _cn(cert.issuer), "email": _email(cert),
            "dn": _dn(cert.subject), "not_before": nb, "not_after": na,
            "expired": na < _dt.datetime.now(_dt.timezone.utc), "usages": _usages(cert),
            "self_signed": cert.subject == cert.issuer, "serial": format(cert.serial_number, "x")}


def cert_der(cert) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding
    return cert.public_bytes(Encoding.DER)


def cert_b64(cert) -> str:
    return base64.b64encode(cert_der(cert)).decode("ascii")


def b64_cert(s: str):
    return _x509().load_der_x509_certificate(base64.b64decode(s))


def check_identity(name: str, email: str, password: str, confirm: str | None = None):
    """새 ID 칸 검사. 틀리면 DsignError(화면 문구)."""
    if not name.strip():
        raise DsignError("이름을 입력하세요.")
    if not _EMAIL_RX.match(email.strip()):
        raise DsignError(MSG_EMAIL)
    if len(password) < MIN_PW:
        raise DsignError(MSG_PW_SHORT)
    if confirm is not None and confirm != password:
        raise DsignError("비밀번호와 비밀번호 확인이 다릅니다.")


def default_id_path(name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name.strip()) or "ID"
    return os.path.join(security_dir(), safe + ".pfx")


def create_id(name: str, email: str, password: str, path: str | None = None, org: str = "", unit: str = "",
              country: str = "", years: int = 5, not_before: _dt.datetime | None = None) -> dict:
    """자가 서명 디지털 ID(RSA 2048·SHA-256)를 pfx 로 만든다. 설정 목록에 넣을 항목을 돌려준다."""
    check_identity(name, email, password)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, name.strip())]
    if unit.strip():
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, unit.strip()))
    if org.strip():
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org.strip()))
    attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email.strip()))
    if len(country.strip()) == 2:
        attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country.strip().upper()))
    subj = x509.Name(attrs)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nb = not_before or (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5))
    cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(subj).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(nb)
            .not_valid_after(nb + _dt.timedelta(days=365 * years))
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.RFC822Name(email.strip())]), critical=False)
            .sign(key, hashes.SHA256()))
    path = path or default_id_path(name)
    if not path.lower().endswith((".pfx", ".p12")):
        raise DsignError("파일 이름은 .pfx 또는 .p12 로 끝나야 합니다.")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = pkcs12.serialize_key_and_certificates(name.strip().encode("utf-8"), key, cert, None,
                                                 serialization.BestAvailableEncryption(password.encode("utf-8")))
    with open(path, "wb") as fp:
        fp.write(data)
    return entry_for(path, cert)


def entry_for(path: str, cert) -> dict:
    """설정 `digital_ids` 한 줄. 공개 인증서를 같이 적어 비밀번호 없이 목록·정보·내보내기를 한다."""
    return {"path": os.path.abspath(path), "name": _cn(cert.subject), "cert": cert_b64(cert)}


def load_pfx(path: str, password: str):
    """(개인 키, 인증서, 나머지 인증서들). 비밀번호가 틀리면 DsignError(MSG_BAD_PW)."""
    from cryptography.hazmat.primitives.serialization import pkcs12
    if not password:
        raise DsignError(MSG_NEED_PW)
    try:
        with open(path, "rb") as fp:
            data = fp.read()
    except OSError as e:
        raise DsignError(f"디지털 ID 파일을 열 수 없습니다.\n{e}")
    try:
        key, cert, extra = pkcs12.load_key_and_certificates(data, password.encode("utf-8"))
    except ValueError:
        raise DsignError(MSG_BAD_PW)
    if key is None or cert is None:
        raise DsignError(MSG_INVALID)
    return key, cert, list(extra or [])


def import_pfx(path: str, password: str) -> dict:
    if not path.lower().endswith((".pfx", ".p12")):
        raise DsignError("디지털 ID 파일(*.pfx, *.p12)을 고르세요.")
    _key, cert, _ = load_pfx(path, password)
    return entry_for(path, cert)


def entry_cert(entry: dict):
    """설정 항목의 공개 인증서(없으면 None)."""
    try:
        return b64_cert(entry["cert"]) if entry.get("cert") else None
    except Exception:
        return None


def add_entry(ids: list, entry: dict) -> bool:
    """같은 인증서(또는 같은 파일)가 있으면 바꾸고 True, 없으면 붙이고 False."""
    for k, e in enumerate(ids):
        if (e.get("cert") and e.get("cert") == entry.get("cert")) or os.path.normcase(e.get("path", "")) == os.path.normcase(entry["path"]):
            ids[k] = entry
            return True
    ids.append(entry)
    return False


def delete_entry(ids: list, entry: dict, remove_file: bool = True) -> None:
    """목록에서 빼고, 우리 Security 폴더에 만든 파일이면 파일도 지운다."""
    ids[:] = [e for e in ids if e is not entry and e.get("path") != entry.get("path")]
    p = entry.get("path", "")
    if remove_file and p and os.path.normcase(os.path.dirname(os.path.abspath(p))) == os.path.normcase(security_dir()):
        try:
            os.remove(p)
        except OSError:
            pass


def export_cert(entry_or_cert, path: str) -> None:
    """공개 인증서만 .cer(DER) 로 내보낸다. .pem 이면 PEM."""
    from cryptography.hazmat.primitives.serialization import Encoding
    cert = entry_cert(entry_or_cert) if isinstance(entry_or_cert, dict) else entry_or_cert
    if cert is None:
        raise DsignError("인증서를 읽을 수 없습니다.")
    enc = Encoding.PEM if path.lower().endswith(".pem") else Encoding.DER
    with open(path, "wb") as fp:
        fp.write(cert.public_bytes(enc))


def read_cert_file(path: str):
    """신뢰할 인증서 추가용: .cer/.crt/.der/.pem 이나 pfx 의 공개 인증서는 못 읽으니 cer 만."""
    x509 = _x509()
    with open(path, "rb") as fp:
        data = fp.read()
    try:
        return x509.load_der_x509_certificate(data)
    except ValueError:
        try:
            return x509.load_pem_x509_certificate(data)
        except ValueError:
            raise DsignError("인증서 파일을 읽을 수 없습니다.")


# ── 서명 칸 찾기 ───────────────────────────────────────────────────
def sig_widgets(f: fitz.Document) -> list[dict]:
    """문서의 서명 칸: page, xref, name, rect, signed."""
    out = []
    for i in range(f.page_count):
        for w in f[i].widgets():
            if w.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE:
                out.append({"page": i, "xref": w.xref, "name": w.field_name or "", "rect": fitz.Rect(w.rect),
                            "signed": _is_signed(f, w.xref)})
    return out


def _parent_or_self(f: fitz.Document, xref: int) -> int:
    """위젯이 이름 없는 kid 면 필드(부모)를, 아니면 자기를."""
    t, v = f.xref_get_key(xref, "T")
    if t == "null":
        pt, pv = f.xref_get_key(xref, "Parent")
        if pt == "xref":
            return int(pv.split()[0])
    return xref


def _is_signed(f: fitz.Document, xref: int) -> bool:
    t, _ = f.xref_get_key(_parent_or_self(f, xref), "V")
    return t not in ("null", "")


def empty_sig_fields(f: fitz.Document) -> list[dict]:
    return [s for s in sig_widgets(f) if not s["signed"]]


def has_signatures(f: fitz.Document) -> bool:
    return any(s["signed"] for s in sig_widgets(f))


# ── 서명 모양 ─────────────────────────────────────────────────────
def appearance_lines(style: dict, info: dict, reason: str, location: str, when: _dt.datetime) -> list[str]:
    items = (style or DEFAULT_STYLE).get("items", DEFAULT_STYLE["items"])
    out = []
    if "label" in items or "name" in items:
        out.append(("전자 서명: " if "label" in items else "") + info["name"])
    if "dn" in items:
        out.append("DN: " + info["dn"])
    if "reason" in items and reason:
        out.append("이유: " + reason)
    if "location" in items and location:
        out.append("위치: " + location)
    if "date" in items or "time" in items:
        fmt = ("%Y.%m.%d " if "date" in items else "") + ("%H:%M:%S %z" if "time" in items else "")
        out.append("날짜: " + when.astimezone().strftime(fmt.strip()))
    return out


def appearance_pdf(width: float, height: float, lines: list[str], image: str = "", direction: str = "auto",
                   name_shape: str = "") -> bytes:
    """서명 칸 크기의 한 쪽 PDF(그림·이름 글자 왼쪽, 글 줄 오른쪽). 한글은 내장 CJK 글꼴."""
    d = fitz.open()
    pg = d.new_page(width=max(width, 10), height=max(height, 10))
    r = pg.rect
    left = fitz.Rect(r)
    has_shape = bool(image and os.path.exists(image)) or bool(name_shape)
    if has_shape:
        half = r.width * 0.4
        rtl = direction == "rtl"
        shape_r = fitz.Rect(r.x1 - half, r.y0, r.x1, r.y1) if rtl else fitz.Rect(r.x0, r.y0, r.x0 + half, r.y1)
        left = fitz.Rect(r.x0, r.y0, r.x1 - half, r.y1) if rtl else fitz.Rect(r.x0 + half, r.y0, r.x1, r.y1)
        if image and os.path.exists(image):
            try:
                pg.insert_image(shape_r + (2, 2, -2, -2), filename=image, keep_proportion=True)
            except Exception:
                pass
        elif name_shape:
            fs = min(shape_r.height * 0.45, shape_r.width / max(len(name_shape), 1) * 1.1)
            pg.insert_textbox(shape_r + (2, (shape_r.height - fs * 1.3) / 2, -2, 0), name_shape, fontsize=max(fs, 4),
                              **_font(), align=fitz.TEXT_ALIGN_CENTER)
    if lines:
        n = len(lines)
        fs = max(min((left.height - 4) / (n * 1.25), 10.0), 3.0)
        longest = max(len(s) for s in lines)
        fs = max(min(fs, (left.width - 4) / max(longest * 0.6, 1)), 3.0)
        y = left.y0 + 2 + fs
        for s in lines:
            pg.insert_text((left.x0 + 2, y), s, fontsize=fs, **_font())
            y += fs * 1.25
    data = d.tobytes()
    d.close()
    return data


# ── 서명 ─────────────────────────────────────────────────────────
@dataclass
class SignRequest:
    pfx: str
    password: str
    field: str | None = None                      # 이미 있는 빈 서명 칸 이름
    new_page: int | None = None                   # 칸이 없으면 새로 만들 쪽·자리(fitz 좌표)
    new_rect: fitz.Rect | None = None
    reason: str = ""
    location: str = ""
    lock: bool = False
    style: dict = _field(default_factory=lambda: dict(DEFAULT_STYLE))
    doc_password: str | None = None


def make_signer(pfx: str, password: str, check_expiry: bool = True):
    from asn1crypto import keys as a_keys, x509 as a_x509
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
    from pyhanko.sign import signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore
    key, cert, extra = load_pfx(pfx, password)
    info = cert_info(cert)
    if check_expiry and (info["expired"] or info["not_before"] > _dt.datetime.now(_dt.timezone.utc)):
        raise DsignError(MSG_EXPIRED)
    a_cert = a_x509.Certificate.load(cert_der(cert))
    a_key = a_keys.PrivateKeyInfo.load(key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption()))
    reg = SimpleCertificateStore.from_certs([a_x509.Certificate.load(cert_der(c)) for c in extra])
    return signers.SimpleSigner(signing_cert=a_cert, signing_key=a_key, cert_registry=reg), cert, info


def _pdf_box(f: fitz.Document, page: int, rect: fitz.Rect) -> tuple[float, float, float, float]:
    """fitz(왼쪽 위 원점) 사각형 → PDF 사용자 좌표."""
    m = ~f[page].transformation_matrix
    r = fitz.Rect(rect) * m
    r.normalize()
    return (r.x0, r.y0, r.x1, r.y1)


def _field_rect(src: bytes, name: str) -> tuple[int, fitz.Rect] | None:
    f = fitz.open("pdf", src)
    try:
        for s in sig_widgets(f):
            if s["name"] == name:
                return s["page"], s["rect"]
    finally:
        f.close()
    return None


def _lock_fields(w, sig_name: str) -> None:
    """서명 후 잠금: 양식 칸 전부를 읽기 전용으로, 서명 칸에 /Lock(All) 을 단다(서명과 같은 판 안에서)."""
    from pyhanko.pdf_utils import generic
    form = w.root.get("/AcroForm")
    if form is None:
        return
    form = form.get_object()

    def walk(arr):
        for ref in arr:
            fld = ref.get_object()
            ff = int(fld.get("/Ff", 0))
            if fld.get("/FT") != "/Sig":
                fld["/Ff"] = generic.NumberObject(ff | 1)
            elif str(fld.get("/T", "")) == sig_name:
                fld["/Lock"] = generic.DictionaryObject({generic.NameObject("/Type"): generic.NameObject("/SigFieldLock"),
                                                         generic.NameObject("/Action"): generic.NameObject("/All")})
            if isinstance(ref, generic.IndirectObject):
                w.update_container(fld)
            kids = fld.get("/Kids")
            if kids is not None and any("/T" in k.get_object() for k in kids):
                walk(kids)
    walk(form.get("/Fields", []))


def sign_bytes(src: bytes, req: SignRequest, when: _dt.datetime | None = None) -> bytes:
    """서명한 PDF 바이트(원본 뒤에 덧붙인 판). 원래 서명은 그대로 남는다."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    from pyhanko import stamp
    signer, cert, info = make_signer(req.pfx, req.password)
    when = when or _dt.datetime.now(_dt.timezone.utc)
    w = IncrementalPdfFileWriter(io.BytesIO(src), strict=False)
    if w.prev.encrypted:
        w.encrypt(req.doc_password or "")
    f = fitz.open("pdf", src)
    if req.doc_password:
        f.authenticate(req.doc_password)
    try:
        if req.field:
            hit = next((s for s in sig_widgets(f) if s["name"] == req.field), None)
            if hit is None:
                raise DsignError("서명 칸을 찾을 수 없습니다.")
            if hit["signed"]:
                raise DsignError("이미 서명된 칸입니다.")
            name, rect = req.field, hit["rect"]
        else:
            used = {s["name"] for s in sig_widgets(f)}
            n = 1
            while f"Signature{n}" in used:
                n += 1
            name, rect = f"Signature{n}", fitz.Rect(req.new_rect)
            fields.append_signature_field(w, fields.SigFieldSpec(sig_field_name=name, on_page=req.new_page,
                                                                 box=_pdf_box(f, req.new_page, rect)))
    finally:
        f.close()
    if req.lock:
        _lock_fields(w, name)
    st = req.style or DEFAULT_STYLE
    lines = appearance_lines(st, info, req.reason, req.location, when)
    shape = info["name"] if (not st.get("image") and st.get("shape") == "name") else ""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(appearance_pdf(rect.width, rect.height, lines, st.get("image", ""), st.get("dir", "auto"), shape))
        tmp.close()
        style = stamp.StaticStampStyle.from_pdf_file(tmp.name, border_width=0)
        meta = signers.PdfSignatureMetadata(field_name=name, reason=req.reason or None, location=req.location or None,
                                            name=info["name"], md_algorithm="sha256")
        out = io.BytesIO()
        pdf_signer = signers.PdfSigner(meta, signer=signer, stamp_style=style)
        try:
            pdf_signer.sign_pdf(w, output=out)
        except Exception as e:   # 키·인증서가 서명에 못 쓰이는 경우
            raise DsignError(f"{MSG_INVALID}\n{e}")
        return out.getvalue()
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def signed_name(path: str | None, fallback: str = "문서") -> str:
    """「이름_서명.pdf」."""
    base = os.path.splitext(os.path.basename(path))[0] if path else fallback
    return base + "_서명.pdf"


# ── 검증 ─────────────────────────────────────────────────────────
@dataclass
class SigState:
    field: str
    page: int
    rect: fitz.Rect
    signer: str
    reason: str
    location: str
    when: _dt.datetime | None
    intact: bool          # 서명한 바이트가 그대로
    crypto_ok: bool       # 서명 값이 맞음
    coverage: str         # COV_OK / COV_LATER / COV_BAD
    trusted: bool         # 신뢰 목록(또는 그 발급자)에 있음
    expired: bool
    cert: object = None   # cryptography 인증서
    error: str = ""

    @property
    def valid(self) -> bool:
        """문서 쪽으로 유효(서명 값이 맞고 서명한 판 뒤에 허용되지 않은 변경이 없음)."""
        return self.intact and self.crypto_ok and self.coverage != COV_BAD

    @property
    def status_text(self) -> str:
        if self.valid and self.trusted and not self.expired:
            return "서명이 유효합니다."
        if not self.valid:
            return "서명이 유효하지 않습니다."
        return "서명의 유효성을 알 수 없습니다."

    @property
    def identity_text(self) -> str:
        if self.expired:
            return ID_EXPIRED
        return ID_OK if self.trusted else ID_UNKNOWN


def _trust_ctx(trusted: list[str]):
    from asn1crypto import x509 as a_x509
    from pyhanko_certvalidator import ValidationContext
    roots = []
    for s in trusted or []:
        try:
            roots.append(a_x509.Certificate.load(base64.b64decode(s)))
        except Exception:
            pass
    return ValidationContext(trust_roots=roots, allow_fetching=False, revocation_mode="soft-fail")


def validate(src: bytes, trusted: list[str] | None = None, doc_password: str | None = None) -> list[SigState]:
    """문서의 모든 서명을 검사한다(서명 안 된 칸은 뺌)."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.sign.diff_analysis import ModificationLevel
    from pyhanko.sign.validation.status import SignatureCoverageLevel
    r = PdfFileReader(io.BytesIO(src), strict=False)
    if r.encrypted:
        r.decrypt(doc_password or "")
    places = {}
    f = fitz.open("pdf", src)
    if doc_password:
        f.authenticate(doc_password)
    try:
        for s in sig_widgets(f):
            places.setdefault(s["name"], (s["page"], s["rect"]))
    finally:
        f.close()
    out = []
    x509 = _x509()
    for es in r.embedded_signatures:
        name = es.field_name or ""
        page, rect = places.get(name, (0, fitz.Rect()))
        sd = es.sig_object
        reason, loc = str(sd.get("/Reason", "") or ""), str(sd.get("/Location", "") or "")
        try:
            ccert = x509.load_der_x509_certificate(es.signer_cert.dump())
            info = cert_info(ccert)
        except Exception:
            ccert, info = None, {"name": "", "expired": False}
        try:
            st = validate_pdf_signature(es, signer_validation_context=_trust_ctx(trusted or []))
            cov = st.coverage
            mod = st.modification_level
            if not st.intact:
                cv = COV_BAD
            elif cov == SignatureCoverageLevel.ENTIRE_FILE:
                cv = COV_OK
            elif cov == SignatureCoverageLevel.ENTIRE_REVISION and mod is not None and mod != ModificationLevel.OTHER:
                cv = COV_LATER
            else:
                cv = COV_BAD
            when = es.self_reported_timestamp
            out.append(SigState(name, page, rect, info["name"], reason, loc, when, bool(st.intact), bool(st.valid),
                                cv, bool(st.trusted), bool(info["expired"]), ccert))
        except Exception as e:
            out.append(SigState(name, page, rect, info["name"], reason, loc, None, False, False, COV_BAD, False,
                                bool(info["expired"]), ccert, error=str(e)))
    return out


def summary(states: list[SigState]) -> str:
    return MSG_ALL_OK if states and all(s.valid for s in states) else MSG_SOME_BAD


# ── 서명 지우기 ────────────────────────────────────────────────────
def signer_certs(src: bytes, doc_password: str | None = None) -> dict[str, str]:
    """서명 칸 이름 → 서명자 공개 인증서(base64)."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    r = PdfFileReader(io.BytesIO(src), strict=False)
    if r.encrypted:
        r.decrypt(doc_password or "")
    out = {}
    for es in r.embedded_signatures:
        try:
            out[es.field_name or ""] = base64.b64encode(es.signer_cert.dump()).decode("ascii")
        except Exception:
            pass
    return out


def clear(f: fitz.Document, my_certs: list[str] | None = None, only: str | None = None,
          require_local: bool = True) -> tuple[list[int], int]:
    """서명 값을 지우고 서명 칸은 빈 칸으로 둔다. (바뀐 쪽, 이 PC 에 인증서가 없어 못 지운 수).
    my_certs: 이 PC 디지털 ID 의 공개 인증서(base64). require_local=False 면 이 PC 것이 아니어도 지운다."""
    mine = set(my_certs or [])
    who = signer_certs(f.tobytes()) if require_local and has_signatures(f) else {}
    pages, failed = set(), 0
    for s in sig_widgets(f):
        if not s["signed"] or (only is not None and s["name"] != only):
            continue
        fx = _parent_or_self(f, s["xref"])
        if require_local and who.get(s["name"]) not in mine:
            failed += 1
            continue
        f.xref_set_key(fx, "V", "null")
        f.xref_set_key(fx, "Lock", "null")
        f.xref_set_key(s["xref"], "AP", "null")
        pages.add(s["page"])
    if pages and not has_signatures(f):
        try:
            f.xref_set_key(f.pdf_catalog(), "Perms", "null")
        except Exception:
            pass
    return sorted(pages), failed
