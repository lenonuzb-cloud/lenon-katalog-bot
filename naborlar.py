"""LENON_UZ: «Naborlar» bo'limini nabor turlariga bo'lish va naborni butunligicha «qolmadi» qilish.

Nabor ichidagi buyumlar alohida sotilmaydi, shuning uchun bunday bo'limlarda («whole») qolmadi/keldi
faqat butun naborga qo'yiladi: jadval qatorlarida alohida belgi yo'q."""
import re

from store import store

GROUPS = [  # (kalit, bo'lim nomi, nom boshlanishi)
    ("nb46", "Nabor 46", "LENON nabor 46"),
    ("nb39", "Nabor 39", "LENON nabor 39"),
    ("nb33", "Nabor 33", "LENON nabor 33"),
    ("nb44", "KLASS nabor 44", "KLASS nabor 44"),
]


def is_whole(d) -> bool:
    s = store.section(d.get("section")) or {}
    return bool(s.get("whole"))


def tidy_note(note: str) -> str:
    """«jami; 46шт narxi; 115$ kodi; P823» -> «Jami: 46шт\\nNarxi: 115$\\nKodi: P823»."""
    pairs = re.findall(r"(jami|narxi|kodi)\s*[:;]\s*([^\s;]+(?:\s*\+\s*[^\s;]+)?)", note or "", re.I)
    if not pairs:
        return note or ""
    return "\n".join(f"{k.capitalize()}: {v}" for k, v in pairs)


def note_line(note: str) -> str:
    return " · ".join(x.strip() for x in (note or "").splitlines() if x.strip())


def migrate():
    """Bir martalik: Naborlar -> 4 ta bo'lim. Natija: (bo'limlar, ko'chirilgan, qolmadi bo'lgan naborlar)."""
    st = store.settings
    if st.get("nb_split_v1"):
        return None
    secs = store.s["sections"]
    nb_i = next((i for i, s in enumerate(secs) if s["key"] == "nb"), None)
    if nb_i is None:
        st["nb_split_v1"] = True
        return None
    new = [{"key": k, "title": t, "kind": "design", "whole": True} for k, t, _ in GROUPS]
    moved, out = 0, []
    for d in store.designs.values():
        if d.get("section") != "nb":
            continue
        key = next((k for k, _, pre in GROUPS if d["title"].lower().startswith(pre.lower())), None)
        if not key:
            continue
        d["section"] = key
        d["note"] = tidy_note(d.get("note", ""))
        items = [it for c in d["categories"] for it in c["items"]]
        if items and all(not it.get("in_stock", True) for it in items):
            d["in_stock"] = False
            out.append(d["title"])
        for it in items:  # buyumlarga alohida belgi qo'yilmaydi
            it["in_stock"] = True
        moved += 1
    left = [d for d in store.designs.values() if d.get("section") == "nb" and not d.get("deleted")]
    keep_nb = [secs[nb_i]] if left else []
    store.s["sections"] = secs[:nb_i] + new + keep_nb + secs[nb_i + 1:]
    for k, _, _ in GROUPS:
        st.setdefault("topics", {})[k] = -1
    st["nb_split_v1"] = True
    store.save()
    return [t for _, t, _ in GROUPS], moved, out
