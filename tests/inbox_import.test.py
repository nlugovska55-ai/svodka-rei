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


def apply(db, batch, limit=None):
    for w in json.load(open(batch))[:limit]:
        db[w["doc_id"]] = w["data"]


def ack(repo, seen, batch, db):
    ap = batch + ".applied.json"
    json.dump(sorted(db), open(ap, "w"))
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
ids1 = [w["doc_id"] for w in json.load(open(batch))]
ok("Сценарий Codex: prepare не отмечает получение", r.returncode == 0 and len(ids1) == 2 and not seen_lines(seen),
   f"записей {len(ids1)}, строк учёта {len([x for x in seen_lines(seen) if x])}")
r = run("prepare", repo, seen, batch)
ids2 = [w["doc_id"] for w in json.load(open(batch))]
ok("Падение до batch: повтор отдаёт те же стабильные doc_id", ids1 == ids2, f"{ids2}")

apply(db, batch, limit=1)
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
ok("Новый файл после ack берётся, старый нет", [w["data"]["inboxFile"] for w in json.load(open(batch))] == ["inbox/second.json"], "в партии только second.json")

sh(repo, "git", "update-ref", "-d", "refs/remotes/origin/inbox")
r = run("prepare", repo, seen, batch)
ok("Нет origin/inbox: импорт остановлен (код 2), не пустой inbox", r.returncode == 2 and "ошибка git" in r.stderr, r.stderr.strip()[:90])
r = run("prepare", os.path.join(t, "net-repo"), seen, batch)
ok("Сбой git (нет репо): код 2", r.returncode == 2, r.stderr.strip()[:90])

print(f"\nИтог импорта: {n - fail} из {n}")
sys.exit(1 if fail else 0)
