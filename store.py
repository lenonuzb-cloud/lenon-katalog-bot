"""Katalog ma'lumotlari: saqlash, yuklash va Telegram'ga zaxira nusxa."""
import asyncio
import copy
import io
import json
import logging
import re
import time
import uuid

import config

log = logging.getLogger("store")

STATE_FILE = config.DATA_DIR / "state.json"
INITIAL_FILE = config.BASE_DIR / "katalog_boshlangich.json"
if config.LENONUZ:
    INITIAL_FILE = config.BASE_DIR / "katalog_lenonuz.json"

SECTIONS = [
    {"key": "lenon", "title": "LENON", "kind": "design"},
    {"key": "boden", "title": "BODEN", "kind": "design"},
    {"key": "nabor44", "title": "LENON nabor 44", "kind": "nabor"},
    {"key": "nabor33", "title": "LENON nabor 33", "kind": "nabor"},
    {"key": "nabor39", "title": "LENON nabor 39", "kind": "nabor"},
    {"key": "rasmlar", "title": "Rasmlar", "kind": "photos"},
]
if config.LENONUZ:
    try:
        SECTIONS = json.loads((config.BASE_DIR / "katalog_lenonuz.json").read_text("utf-8"))["sections"]
    except Exception:  # noqa
        SECTIONS = [{"key": "nb", "title": "Naborlar", "kind": "design"}]
SECTION_BY_KEY = {s["key"]: s for s in SECTIONS}


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def empty_state() -> dict:
    return {
        "version": 1,
        "saved_at": 0,
        "settings": {
            "owner_id": None,
            "admins": [],
            "group_id": None,
            "group_username": "",
            "topics": {},
            "agents": dict(config.DEFAULT_AGENTS),
        },
        "sections": copy.deepcopy(SECTIONS),
        "nabor_templates": {},
        "designs": {},
        "index_msgs": {},
        "events": [],
        "users": {},
    }


class Store:
    def __init__(self):
        self.s: dict = empty_state()
        self._dirty = False
        self._backup_task: asyncio.Task | None = None
        self.backup_hook = None  # async funksiya (bytes) -> None
        self.last_backup_ok: float | None = None
        self.last_backup_error: str = ""

    # ---------- yuklash / saqlash ----------
    def load_local(self) -> bool:
        if STATE_FILE.exists():
            try:
                self.s = json.loads(STATE_FILE.read_text("utf-8"))
                self._upgrade()
                return True
            except Exception as e:  # noqa
                log.error("state.json o'qilmadi: %s", e)
        return False

    def load_bytes(self, data: bytes) -> bool:
        try:
            st = json.loads(data.decode("utf-8"))
            if "designs" not in st:
                return False
            self.s = st
            self._upgrade()
            self.save(backup=False)
            return True
        except Exception as e:  # noqa
            log.error("zaxira o'qilmadi: %s", e)
            return False

    def _upgrade(self):
        base = empty_state()
        for k, v in base.items():
            self.s.setdefault(k, v)
        for k, v in base["settings"].items():
            self.s["settings"].setdefault(k, v)
        for name, user in config.DEFAULT_AGENTS.items():
            self.s["settings"]["agents"].setdefault(name, user)

    def dump_bytes(self) -> bytes:
        return json.dumps(self.s, ensure_ascii=False, indent=1).encode("utf-8")

    def save(self, backup: bool = True):
        self.s["saved_at"] = now()
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_bytes(self.dump_bytes())
        tmp.replace(STATE_FILE)
        if backup:
            self.schedule_backup()

    def schedule_backup(self, delay: float = 15):
        if self.backup_hook is None:
            return
        if self._backup_task and not self._backup_task.done():
            return
        self._backup_task = asyncio.get_event_loop().create_task(self._backup_later(delay))

    async def _backup_later(self, delay):
        await asyncio.sleep(delay)
        await self.backup_now()

    async def backup_now(self):
        if not self.backup_hook:
            return
        try:
            await self.backup_hook(self.dump_bytes())
            self.last_backup_ok = now()
            self.last_backup_error = ""
        except Exception as e:  # noqa
            self.last_backup_error = str(e)
            log.error("zaxira yuborilmadi: %s", e)

    # ---------- boshlang'ich katalog ----------
    def import_initial(self):
        """Guruhdan olingan jadvallarni (katalog_boshlangich.json) katalogga kiritadi."""
        if not INITIAL_FILE.exists():
            return
        data = json.loads(INITIAL_FILE.read_text("utf-8"))
        if config.LENONUZ:
            return self._import_lenonuz(data)
        order = 0
        for sec_key, lst in (("lenon", data.get("lenon", [])), ("boden", data.get("boden", []))):
            for d in lst:
                order += 1
                title = d["title"] if sec_key == "lenon" else d["title"]
                did = self.add_design(sec_key, title, agents=d.get("agents") or [], note=d.get("note", ""),
                                      log_event=False, mark_new=False)
                des = self.s["designs"][did]
                des["order"] = order
                for c in d["categories"]:
                    cat = {"name": c["name"], "items": []}
                    for it in c["items"]:
                        cat["items"].append(self._mk_item(it["name"], it.get("code", ""), it["price"], new=False))
                    des["categories"].append(cat)
        for nb in data.get("nabor", []):
            key = "nabor" + nb["id"]
            self.s["nabor_templates"][key] = {"pieces": nb["pieces"], "price": nb["price"], "items": nb["items"]}
            for st in nb["sets"]:
                did = self.add_design(key, st["code"], log_event=False, mark_new=False)
                self.s["designs"][did]["in_stock"] = st["in_stock"]
        self.save(backup=False)

    def _import_lenonuz(self, data):
        """LENON_UZ katalogi (eski botdan ko'chirilgan): har bir post — alohida «dizayn»,
        jadval qatorlarida nomi, kodi, soni, narxi, jami."""
        order = 0
        for x in data.get("designs", []):
            order += 1
            did = self.add_design(x["section"], x["title"], note=x.get("note", ""), photos=x.get("photos") or [],
                                  log_event=False, mark_new=False)
            des = self.s["designs"][did]
            des["order"] = order
            des["agents"] = ["Abduvosit", "Azamat", "Ibrohim"]
            for c in x["categories"]:
                cat = {"name": c["name"], "items": []}
                for it in c["items"]:
                    x_ = self._mk_item(it["name"], it.get("code", ""), it.get("price", ""), new=False,
                                       qty=it.get("qty", ""), total=it.get("total", ""))
                    if it.get("in_stock") is False:
                        x_["in_stock"] = False
                    cat["items"].append(x_)
                des["categories"].append(cat)
        st = self.settings
        if config.DEFAULT_GROUP_ID:
            st["group_id"] = config.DEFAULT_GROUP_ID
        st["topics"] = {s["key"]: -1 for s in self.s["sections"]}
        st["topics"]["umumiy"] = -1
        st["rot"] = {"queue": [d["id"] for d in sorted(self.designs.values(), key=lambda d: d.get("order", 0))],
                     "last_slot": "", "posted": 0}
        self.save(backup=False)

    # ---------- yordamchi ----------
    def _mk_item(self, name, code, price, new=True, qty="", total="") -> dict:
        t = now()
        return {
            "id": new_id(),
            "name": name.strip(),
            "code": (code or "").strip().upper(),
            "qty": (qty or "").strip(),
            "total": (total or "").strip(),
            "price": (price or "").strip(),
            "in_stock": True,
            "new_until": t + config.NEW_DAYS * 86400 if new else 0,
            "keldi_until": 0,
            "updated": t,
        }

    @property
    def settings(self) -> dict:
        return self.s["settings"]

    @property
    def designs(self) -> dict:
        return self.s["designs"]

    def section(self, key):
        for s in self.s["sections"]:
            if s["key"] == key:
                return s
        return None

    def designs_in(self, section_key) -> list:
        lst = [d for d in self.designs.values() if d["section"] == section_key and not d.get("deleted")]
        return sorted(lst, key=lambda d: (d.get("order", 0), d.get("created", 0)))

    def find_design(self, text: str, section_key: str | None = None):
        """'14-NL', 'LENON 14-NL', 'boden 003', 'Y878-UW4' kabi nomdan dizaynni topadi."""
        t = re.sub(r"\s+", " ", text.strip().upper())
        for d in self.designs.values():
            if d.get("deleted") or (section_key and d["section"] != section_key):
                continue
            title = re.sub(r"\s+", " ", d["title"].upper())
            short = title.split(" ")[-1]
            if t in (title, short, d["id"].upper()):
                return d
        return None

    def add_design(self, section_key, title, agents=None, note="", photos=None,
                   log_event=True, mark_new=True) -> str:
        did = new_id()
        sec = self.section(section_key)
        if sec and sec["kind"] == "design" and section_key == "lenon":
            t = title.upper().strip()
            if not t.startswith("LENON"):
                title = "LENON " + title.strip()
        if sec and sec["kind"] == "design" and section_key == "boden":
            t = title.upper().strip()
            if not t.startswith("BODEN"):
                title = "BODEN " + title.strip()
        t = now()
        self.designs[did] = {
            "id": did,
            "section": section_key,
            "title": re.sub(r"\s+", " ", title.strip()),
            "photos": photos or [],
            "agents": agents if agents is not None else self.default_agents(section_key),
            "note": note,
            "categories": [],
            "in_stock": True,
            "new_until": t + config.NEW_DAYS * 86400 if mark_new else 0,
            "keldi_until": 0,
            "msg": None,
            "created": t,
            "order": int(t),
            "deleted": False,
        }
        if log_event:
            self.event("new", f"Yangi: {self.designs[did]['title']}", did)
        return did

    def default_agents(self, section_key):
        if config.LENONUZ:
            return ["Abduvosit", "Azamat", "Ibrohim"]
        if section_key in ("lenon", "boden"):
            return ["Ibrohim", "Azamat", "Abduvosit"]
        return []

    def find_item(self, design: dict, item_id: str):
        for c in design["categories"]:
            for it in c["items"]:
                if it["id"] == item_id:
                    return c, it
        return None, None

    def add_item(self, design: dict, category: str, name, code, price, log_event=True) -> dict:
        cat = None
        for c in design["categories"]:
            if c["name"].lower() == category.strip().lower():
                cat = c
                break
        if cat is None:
            cat = {"name": category.strip() or "Aralash", "items": []}
            design["categories"].append(cat)
        it = self._mk_item(name, code, price, new=True)
        cat["items"].append(it)
        if log_event:
            self.event("new", f"Yangi mahsulot: {it['name']} ({design['title']})", design["id"])
        return it

    def set_stock(self, design: dict, item: dict | None, in_stock: bool) -> bool:
        """Qolmadi/Keldi belgisini qo'yadi. O'zgarish bo'lsa True."""
        target = item if item is not None else design
        if bool(target.get("in_stock", True)) == in_stock:
            return False
        target["in_stock"] = in_stock
        target["keldi_until"] = now() + config.KELDI_DAYS * 86400 if in_stock else 0
        target["updated"] = now()
        name = item["name"] if item else design["title"]
        where = f" ({design['title']})" if item else ""
        if in_stock:
            self.event("keldi", f"{name}{where}", design["id"])
        else:
            self.event("qolmadi", f"{name}{where}", design["id"])
        return True

    def event(self, kind, text, design_id=None):
        self.s["events"].append({"ts": now(), "kind": kind, "text": text, "design": design_id})
        cutoff = now() - 90 * 86400
        if len(self.s["events"]) > 3000:
            self.s["events"] = [e for e in self.s["events"] if e["ts"] > cutoff][-3000:]

    def is_admin(self, user_id) -> bool:
        st = self.settings
        return user_id is not None and (user_id == st.get("owner_id") or user_id in st.get("admins", []))

    def is_owner(self, user_id) -> bool:
        return user_id is not None and user_id == self.settings.get("owner_id")

    def expire_marks(self) -> set:
        """Muddati o'tgan NEW/KELDI belgilarini olib tashlaydi. Ta'sirlangan dizaynlarni qaytaradi."""
        t = now()
        changed = set()
        for d in self.designs.values():
            for obj in [d] + [it for c in d["categories"] for it in c["items"]]:
                for k in ("new_until", "keldi_until"):
                    if obj.get(k) and obj[k] < t:
                        obj[k] = 0
                        changed.add(d["id"])
        if changed:
            self.save()
        return changed

    def public_catalog(self) -> dict:
        """Mini ilova uchun katalog (maxfiy sozlamalarsiz)."""
        t = now()
        secs = []
        for s in self.s["sections"]:
            ds = []
            for d in self.designs_in(s["key"]):
                ds.append({
                    "id": d["id"],
                    "title": d["title"],
                    "photos": d["photos"],
                    "note": d.get("note", ""),
                    "agents": [{"name": a, "username": self.settings["agents"].get(a, "")} for a in d.get("agents", [])],
                    "in_stock": d.get("in_stock", True),
                    "is_new": d.get("new_until", 0) > t,
                    "is_keldi": d.get("keldi_until", 0) > t,
                    "link": self.post_link(d),
                    "categories": [
                        {"name": c["name"], "items": [
                            {"id": it["id"], "name": it["name"], "code": it["code"], "price": it["price"], "size": it.get("size", ""),
                             "qty": it.get("qty", ""), "total": it.get("total", ""),
                             "photo": it.get("photo", ""),
                             "in_stock": it.get("in_stock", True),
                             "is_new": it.get("new_until", 0) > t,
                             "is_keldi": it.get("keldi_until", 0) > t}
                            for it in c["items"]]}
                        for c in d["categories"]
                    ],
                })
            item = {"key": s["key"], "title": s["title"], "kind": s["kind"], "designs": ds, "whole": bool(s.get("whole"))}
            if s["kind"] == "nabor":
                item["template"] = self.s["nabor_templates"].get(s["key"])
            secs.append(item)
        import registry
        return {"sections": secs, "registry": registry.all_registries(), "bot": None, "updated": self.s.get("saved_at", 0),
                "layout": "table" if config.LENONUZ else "cards", "rotation": bool(config.ROTATION)}

    def known_photo(self, fid: str) -> bool:
        for d in self.designs.values():
            if fid in d.get("photos", []):
                return True
            for c in d["categories"]:
                for it in c["items"]:
                    if it.get("photo") == fid:
                        return True
        return False

    def post_link(self, d) -> str:
        m = d.get("msg")
        st = self.settings
        if not m or not st.get("group_id"):
            return ""
        thread = st["topics"].get(d["section"])
        if thread == -1:
            thread = None
        if st.get("group_username"):
            base = f"https://t.me/{st['group_username']}"
        else:
            base = "https://t.me/c/" + str(st["group_id"]).replace("-100", "", 1)
        if thread:
            return f"{base}/{thread}/{m['message_id']}"
        return f"{base}/{m['message_id']}"


store = Store()
