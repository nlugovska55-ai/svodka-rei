"""Обезличенная копия data/svodka.json для тестов: клиенты, тексты и шаги заменены, суммы, сроки и статусы сохранены.
Запуск: python3 -I tests/make_fixture.py data/svodka.json tests/fixture-svodka.json"""
import json, sys

src, out = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding="utf-8"))
names = {}
def anon(c):
    if c in ("", "Ольга", "Лена", "Бухгалтерия", "Claude"):
        return c
    return names.setdefault(c, "Клиент %d" % (len(names) + 1))
for i, t in enumerate(d["tasks"]):
    t["client"] = anon(t["client"])
    t["title"] = "%s: дело %d по счёту" % (t["client"] or "Внутреннее", i + 1)
    t["detail"] = ""
    t["next"] = ("Ольга: шаг по делу %d" % (i + 1)) if (t.get("next") or "").startswith("Ольг") else ("Шаг по делу %d" % (i + 1))
    t["lastMove"] = ""
    t["link"] = ""
    t["owner"] = t["owner"] if t["owner"] in ("Ольга", "Лена", "Бухгалтерия", "Браузер") else "Ветка"
ids = {t["id"] for t in d["tasks"]}
for j, s in enumerate(d["summary"]):
    s["text"] = ("Тема %d: проверенное дело %d от Ольги" % (j + 1, j + 1)) if s["section"] == "Нужно от вас" else ("Проект %d: статус" % (j + 1))
    s["client"] = ""
d["lena_clients"] = []
if d.get("prognoz"):
    d["prognoz"]["source"] = "обезличено"
json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("tasks", len(d["tasks"]), "summary", len(d["summary"]), "->", out)
