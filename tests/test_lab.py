"""Checks for the lab profile (kept out of the public repo)."""
import sys
sys.path.insert(0, "tests")
from test_offline import J, BI


def test_lab_profile():
    from radar.match import Group
    g = Group()
    L = next((p for p in g.extra if p.name == "lab"), None)
    if L is None:          # the lab profile is kept out of the public repo for now
        raise SystemExit("no lab profile")
    T = " Praca w laboratorium, analizy HPLC, GMP. Zapraszamy absolwentów. " * 3
    a = L.evaluate(J("Laborant / Laborantka", "Częstochowa, śląskie", T))
    assert a and a["role"] == "Lab analyst / technician", a
    assert L.evaluate(J("Analityk Kontroli Jakości", "Częstochowa", T))
    assert L.evaluate(J("Laborant", "Kłobuck", T))
    assert L.evaluate(J("Laborant", "Kraków", T)) is None           # outside the area
    assert L.evaluate(J("Laborant", "Koszalin", T)) is None
    assert L.evaluate(J("Laborant", "Częstochowa", T + " Praca w systemie trzyzmianowym.")) is None
    assert L.evaluate(J("Laborant", "Częstochowa", T + " Praca zmianowa, także w weekendy.")) is None
    assert L.evaluate(J("Kontroler Jakości", "Częstochowa", T)) is None
    assert L.evaluate(J("Laborant Kontroli Międzyoperacyjnej", "Częstochowa", T)) is None
    assert L.evaluate(J("Specjalista ds. Jakości", "Częstochowa", T)) is None
    assert L.evaluate(J("Kierownik Laboratorium", "Częstochowa", T)) is None
    assert L.evaluate(J("Power BI Developer", "Częstochowa", BI)) is None
    assert L.parttime(J("Laborant (1/2 etatu)", "Myszków", T))
    assert g.main.evaluate(J("Laborant", "Remote, Poland", T)) is None
    from radar.run import both
    lists = {m["list"] for m in both(g, J("Laborant (zastępstwo)", "Radomsko", T))}
    assert lists == {"lab", "lab:parttime"}, lists
    assert "laborant" in g.search_words and "power bi" in g.search_words
    assert g.wants("Chemik") and g.wants("Head of Analytics") and not g.wants("Barista")



if __name__ == "__main__":
    test_lab_profile()
    print("lab checks passed")
