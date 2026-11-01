"""PDF에서 삽입: 끼운 문서의 레이어(OCG)가 결과 문서의 레이어 목록에 남는다."""
import fitz

from app.engine import pages as P
from app.engine.document import Document


def _layered(path, names=(("레이어A", True), ("레이어B", False))):
    s = fitz.open()
    p = s.new_page()
    for i, (n, on) in enumerate(names):
        p.insert_text((50, 50 + 30 * i), n, oc=s.add_ocg(n, on=on))
    s.save(path)
    s.close()


def _target(tmp_path):
    f = fitz.open()
    f.new_page()
    out = str(tmp_path / "t.pdf")
    f.save(out)
    f.close()
    return Document(out)


def test_insert_keeps_layers_and_state(tmp_path):
    src = str(tmp_path / "layers.pdf")
    _layered(src)
    d = _target(tmp_path)
    assert P.insert_pdfs(d, 1, [(src, None, None)]) == 1
    got = {v["name"]: v["on"] for v in d.fitz.get_ocgs().values()}
    assert got == {"레이어A": True, "레이어B": False}
    out = str(tmp_path / "o.pdf")
    d.fitz.save(out)
    r = fitz.open(out)
    assert {v["name"] for v in r.get_ocgs().values()} == {"레이어A", "레이어B"}


def test_insert_adds_to_existing_layers(tmp_path):
    a, b = str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")
    _layered(a, (("가", True),))
    _layered(b, (("나", True),))
    d = _target(tmp_path)
    P.insert_pdfs(d, 1, [(a, None, None), (b, None, None)])
    assert {v["name"] for v in d.fitz.get_ocgs().values()} == {"가", "나"}
