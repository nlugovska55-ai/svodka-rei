"""Проверка двухфазного импорта inbox -> db без сети. Запуск: python3 -I tests/inbox_import.test.py
db страницы задач подменена словарём: batch применяется к нему целиком, частично или не применяется."""
import json, os, subprocess, sys, tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "inbox_to_db.py")
n = fail = 0


def ok(name, cond, fact):
    global n, fail
    n += 1
    fail += not cond
    print(("PASS " if cond else "FAIL ") + name + " — " + fact)


def sh(cwd, *a):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


def run(*a):
    return subprocess.run([sys.executable, "-I", SCRIPT, *a], capture_output=True, text=True)


def put(repo, name, items):
    os.makedirs(os.path.join(repo, "inbox"), exist_ok=True)
    json.dump({"sentAt": "2026-10-28T10:00:00Z", "items": items}, open(os.path.join(repo, "inbox", name), "w"))
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-qm", name)
    sh(repo, "git", "update-ref", "refs/remotes/origin/inbox", "HEAD")


TASKS = {}


def apply(db, batch, limit=None):
    """Как ArtifactData batch по порядку: update в tasks, set в comments."""
    for w in json.load(open(batch))[:limit]:
        if w["collection"] == "tasks":
            TASKS.setdefault(w["doc_id"], {}).update(w["data"])
        else:
            db[w["doc_id"]] = w["data"]


def comments(batch):
    return [w for w in json.load(open(batch)) if w["collection"] == "comments"]


def ack(repo, seen, batch, db, tasks=None):
    """applied.json как его собирает разбор: id комментариев из db и чтение задач из db (TASKS)."""
    ap = batch + ".applied.json"
    m = json.load(open(batch + ".manifest.json"))
    src = TASKS if tasks is None else tasks
    json.dump({"comments": sorted(db), "tasks": {t: src[t] for t in m.get("taskDocs", []) if t in src}}, open(ap, "w"))
    return run("ack", repo, seen, batch, ap)


def seen_lines(seen):
    return open(seen).read().split("\n") if os.path.exists(seen) else []


t = tempfile.mkdtemp()
repo, seen, batch = os.path.join(t, "r"), os.path.join(t, "seen.txt"), os.path.join(t, "batch.json")
os.makedirs(repo)
sh(repo, "git", "init", "-q", "-b", "inbox")
sh(repo, "git", "config", "user.email", "t@t")
sh(repo, "git", "config", "user.name", "t")
put(repo, "fixture.json", [{"taskId": "t1", "action": "done", "at": "2026-10-28T10:00:00Z"},
                           {"taskId": "t2", "text": "позвонить", "at": "2026-10-28T10:01:00Z"}])
db = {}

r = run("prepare", repo, seen, batch)
ids1 = [w["doc_id"] for w in comments(batch)]
ok("Сценарий Codex: prepare не отмечает получение", r.returncode == 0 and len(ids1) == 2 and not seen_lines(seen),
   f"записей {len(ids1)}, строк учёта {len([x for x in seen_lines(seen) if x])}")
r = run("prepare", repo, seen, batch)
ids2 = [w["doc_id"] for w in comments(batch)]
ok("Падение до batch: повтор отдаёт те же стабильные doc_id", ids1 == ids2, f"{ids2}")

apply(db, batch, limit=2)
r = ack(repo, seen, batch, db)
ok("Частичный batch: ack отклонён, учёт не изменён", r.returncode == 1 and not any(seen_lines(seen)), r.stderr.strip().splitlines()[-1])
r = run("prepare", repo, seen, batch)
apply(db, batch)
ok("Повтор после частичного batch: те же документы, без дублей", len(db) == 2 and sorted(db) == sorted(ids1), f"в db {len(db)} записей")

json.dump(json.load(open(batch))[:1], open(batch, "w"))
r = ack(repo, seen, batch, db)
ok("ack с подменённым batch.json отклонён", r.returncode == 1 and "изменён" in r.stderr, r.stderr.strip().splitlines()[-1])

run("prepare", repo, seen, batch)
put(repo, "fixture.json", [{"taskId": "t1", "action": "wait", "at": "2026-10-28T11:00:00Z"}])
r = ack(repo, seen, batch, db)
ok("ack изменённого после prepare файла отклонён", r.returncode == 1 and "изменилось" in r.stderr, r.stderr.strip().splitlines()[-1])

sh(repo, "git", "checkout", "-q", "HEAD~1", "--", "inbox/fixture.json")
sh(repo, "git", "commit", "-qm", "back")
sh(repo, "git", "update-ref", "refs/remotes/origin/inbox", "HEAD")
run("prepare", repo, seen, batch)
m = json.load(open(batch + ".manifest.json"))
m["files"][0]["name"] = "inbox/chuzhoy.json"
json.dump(m, open(batch + ".manifest.json", "w"))
r = ack(repo, seen, batch, db)
ok("ack чужого файла отклонён", r.returncode == 1 and "чужой" in r.stderr, r.stderr.strip().splitlines()[-1])

run("prepare", repo, seen, batch)
apply(db, batch)
r = ack(repo, seen, batch, db)
ok("Полный batch: ack проходит, файл учтён с хэшем", r.returncode == 0 and any(x.startswith("inbox/fixture.json ") for x in seen_lines(seen)), r.stdout.strip())
r = run("prepare", repo, seen, batch)
ok("После ack повтор не дублирует", json.load(open(batch)) == [] and len(db) == 2, r.stdout.strip())

put(repo, "second.json", [{"taskId": "t3", "action": "eve", "at": "2026-10-28T12:00:00Z"}])
run("prepare", repo, seen, batch)
ok("Новый файл после ack берётся, старый нет", [w["data"]["inboxFile"] for w in comments(batch)] == ["inbox/second.json"], "в партии только second.json")

put(repo, "third.json", [{"taskId": "t3", "action": "reopen", "at": "2026-10-28T12:03:00Z", "eid": "ereopen1"}])
open(seen, "w").close()
run("prepare", repo, seen, batch)
b = {(w["data"]["inboxFile"], w["data"]["taskId"]): w["data"] for w in comments(batch)}
tw = [w for w in json.load(open(batch)) if w["collection"] == "tasks"]
done_t3, reopen_t3 = b[("inbox/second.json", "t3")], b[("inbox/third.json", "t3")]
ok("Ошибочное действие и «вернуть»: применяется последнее событие", not done_t3["latest"] and done_t3["supersededBy"] == "ereopen1" and "не применять" in done_t3["text"] and reopen_t3["latest"],
   f"раннее: latest={done_t3['latest']}, текст «{done_t3['text'][:50]}»; позднее: latest={reopen_t3['latest']}")
ok("Отменённое «сделано» задачу не закрывает: в tasks только «вернуть»", [w["data"]["status"] for w in tw if w["doc_id"] == "t3"] == ["open"] and done_t3["status"] == "done",
   f"изменения t3: {[w['data'] for w in tw if w['doc_id'] == 't3']}")

# Codex 19:44: поручение Claude — отдельный тип по всей цепочке; дело остаётся open и переходит Claude
open(seen, "w").close()
put(repo, "fourth.json", [{"taskId": "t4", "action": "claude", "text": "сверить акт", "at": "2026-10-28T13:00:00Z", "eid": "edlg0001"},
                          {"taskId": "t5", "action": "", "text": "старое сообщение", "at": "2026-10-28T13:01:00Z", "eid": "emsg0001"},
                          {"taskId": "t6", "action": "done", "at": "2026-10-28T13:02:00Z", "eid": "edone001"},
                          {"taskId": "net-v-db", "action": "done", "at": "2026-10-28T13:03:00Z", "eid": "edone002"}])
tdir = os.path.join(t, "tasks")
os.makedirs(tdir)
for tid in ("t1", "t2", "t3", "t4", "t5", "t6"):
    json.dump({"id": tid, "status": "open"}, open(os.path.join(tdir, tid + ".json"), "w"))
r = run("prepare", repo, seen, batch, tdir)
TASKS.clear()
db = {}
apply(db, batch)
c4 = {w["data"]["taskId"]: w["data"] for w in comments(batch) if w["data"]["inboxFile"] == "inbox/fourth.json"}
ok("Поручить Claude: задача остаётся open, waiting Claude, не done", TASKS.get("t4", {}).get("status") == "open" and TASKS["t4"].get("waiting") == "Claude" and TASKS["t4"].get("delegated") == "edlg0001",
   f"t4 после применения: {TASKS.get('t4')}")
ok("Поручение подтверждено: комментарий done с eventId (пульт видит appliedEvents)", c4["t4"]["status"] == "done" and c4["t4"]["eventId"] == "edlg0001" and c4["t4"]["appliedTo"] == "t4", f"комментарий: status {c4['t4']['status']}, applied {c4['t4']['appliedTo']}")
ok("Старый комментарий с action пусто: задачу не меняет, ждёт разбора как текст", "t5" not in TASKS and c4["t5"]["status"] == "new" and c4["t5"]["text"] == "старое сообщение", f"t5 в tasks: {'t5' in TASKS}, status {c4['t5']['status']}")
ok("«Я выполнила» закрывает задачу после применения", TASKS.get("t6", {}).get("status") == "done", f"t6: {TASKS.get('t6')}")
ok("Отметка к задаче, которой нет в db: не применяется, остаётся разбору", "net-v-db" not in TASKS and c4["net-v-db"]["status"] == "new" and "не применено" in c4["net-v-db"]["text"], c4["net-v-db"]["text"][:60])
ok("В batch задачи идут раньше комментариев", [w["collection"] for w in json.load(open(batch))] == sorted([w["collection"] for w in json.load(open(batch))], key=lambda c: c != "tasks"), "порядок tasks → comments")
seen_before = open(seen).read()

# Codex 20:13: ack подтверждает задачи только чтением из db, не порядком batch и не id комментариев
cdb = {w["doc_id"]: w["data"] for w in comments(batch)}
r = ack(repo, seen, batch, cdb, tasks={"t4": {"status": "open", "waiting": "я"}, "t3": {"status": "open"}, "t6": {"status": "open"}})
ok("Применены только comments, задача waiting=я: ack отклонён, учёт прежний", r.returncode == 1 and "t4" in r.stderr and "waiting" in r.stderr and open(seen).read() == seen_before, r.stderr.strip().splitlines()[1][:110])
bad = {k: dict(v) for k, v in TASKS.items()}
bad["t6"]["status"] = "open"
r = ack(repo, seen, batch, cdb, tasks=bad)
ok("Неверный status в db («Я выполнила» не закрыла): ack отклонён", r.returncode == 1 and "t6" in r.stderr and open(seen).read() == seen_before, r.stderr.strip().splitlines()[-1][:110])
part = {k: v for k, v in TASKS.items() if k != "t3"}
r = ack(repo, seen, batch, cdb, tasks=part)
ok("Частичное применение задач (нет чтения t3): ack отклонён", r.returncode == 1 and "t3" in r.stderr and open(seen).read() == seen_before, r.stderr.strip().splitlines()[-1][:110])
json.dump(sorted(cdb), open(batch + ".applied.json", "w"))
r = run("ack", repo, seen, batch, batch + ".applied.json")
ok("Старый формат applied (только id комментариев) при изменениях задач: отклонён", r.returncode == 1 and "нет чтения из db" in r.stderr and open(seen).read() == seen_before, r.stderr.strip().splitlines()[-1][:110])
r = ack(repo, seen, batch, db)
ok("Полное подтверждённое чтение задач из db: ack проходит", r.returncode == 0, r.stdout.strip() or r.stderr.strip())

# Старые текстовые комментарии: изменений задач нет, старый формат applied (список) по-прежнему годится
put(repo, "fifth.json", [{"taskId": "t5", "text": "просто текст", "at": "2026-10-28T14:00:00Z"}])
run("prepare", repo, seen, batch, tdir)
apply(db, batch)
json.dump(sorted(db), open(batch + ".applied.json", "w"))
r = run("ack", repo, seen, batch, batch + ".applied.json")
ok("Только текстовые комментарии: ack со списком id проходит", r.returncode == 0 and json.load(open(batch + ".manifest.json"))["taskDocs"] == [], r.stdout.strip() or r.stderr.strip())

sh(repo, "git", "update-ref", "-d", "refs/remotes/origin/inbox")
r = run("prepare", repo, seen, batch)
ok("Нет origin/inbox: импорт остановлен (код 2), не пустой inbox", r.returncode == 2 and "ошибка git" in r.stderr, r.stderr.strip()[:90])
r = run("prepare", os.path.join(t, "net-repo"), seen, batch)
ok("Сбой git (нет репо): код 2", r.returncode == 2, r.stderr.strip()[:90])

print(f"\nИтог импорта: {n - fail} из {n}")
sys.exit(1 if fail else 0)
