"""Комментарии с сайта (ветка inbox) -> записи для db `comments` страницы задач. Двухфазно.

Разбор комментариев (10:00, 13:00, 18:00):
  1) git fetch origin inbox
  2) python3 -I scripts/inbox_to_db.py prepare <репо> <файл-учёта> <batch.json>
     -> batch.json (записи для ArtifactData batch) и batch.json.manifest.json.
     Файл учёта НЕ меняется: подготовка не считается получением.
  3) ArtifactData batch из batch.json в `comments` страницы 3FMn9JyWFXrL6ZzgAx5JAY.
  4) Только если batch применён целиком: прочитать из db `comments` id записанных документов
     (query по doc_id из манифеста), сохранить списком в applied.json и выполнить
     python3 -I scripts/inbox_to_db.py ack <репо> <файл-учёта> <batch.json> <applied.json>
     ack сверяет хэш batch.json с манифестом, хэши файлов в origin/inbox и наличие всех doc_id;
     при любом расхождении ничего не отмечает и выходит с кодом 1.
  Сбой на шаге 3 или частичный batch: просто повторить с шага 2. doc_id стабильны
  (имя файла + хэш содержимого + номер), повторная запись перезаписывает те же документы.
Файл учёта: /mnt/project-files/daily-control/svodka-site/inbox-imported.txt, строки «имя хэш»
(старые строки без хэша считаются подтверждёнными). Ветку inbox не трогаем.
Ошибка git (нет origin/inbox, сбой ls-tree/show) останавливает импорт с кодом 2.
status "done" у комментария ставить только после того, как действие внесено в задачу db:
по eventId таких комментариев пульт снимает отметку «ждёт синхронизации» (appliedEvents в сводке).
"""
import hashlib, json, subprocess, sys
from datetime import datetime, timedelta, timezone

ACTIONS = {"eve": "на вечер", "prio": "в приоритет", "wait": "ждём клиента", "done": "сделано", "later": "отложить до завтра", "reopen": "вернуть в работу"}
MSK = timezone(timedelta(hours=3))


class GitError(Exception):
    pass


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode:
        raise GitError(f"git {' '.join(args)}: {r.stderr.strip() or r.returncode}")
    return r.stdout


def inbox_files(repo):
    """{имя: хэш blob} из origin/inbox; ошибка git - исключение, а не пустой inbox."""
    git(repo, "rev-parse", "--verify", "--quiet", "origin/inbox^{commit}")
    out = {}
    for line in git(repo, "ls-tree", "origin/inbox", "inbox/").splitlines():
        meta, name = line.split("\t", 1)
        if name.endswith(".json"):
            out[name] = meta.split()[2]
    return out


def read_seen(path):
    seen = {}
    try:
        for line in open(path, encoding="utf-8"):
            p = line.split()
            if p:
                seen[p[0]] = p[1] if len(p) > 1 else "*"
    except FileNotFoundError:
        pass
    return seen


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def prepare(repo, seen_path, out):
    seen, files = read_seen(seen_path), inbox_files(repo)
    writes, manifest = [], []
    for name in sorted(files):
        blob = files[name]
        if seen.get(name) in ("*", blob):
            continue
        f = json.loads(git(repo, "cat-file", "blob", blob))
        ids = []
        for i, x in enumerate(f.get("items", [])):
            at = datetime.fromisoformat((x.get("at") or f["sentAt"]).replace("Z", "+00:00")).astimezone(MSK)
            act = ACTIONS.get(x.get("action") or "", "")
            text = (("[" + act + "] ") if act else "") + (x.get("text") or "").strip()
            doc_id = "site-" + name[6:-5] + "-" + blob[:8] + "-" + str(i)
            ids.append(doc_id)
            writes.append({"op": "set", "collection": "comments", "doc_id": doc_id,
                           "data": {"text": text, "taskId": x.get("taskId"), "taskTitle": x.get("taskTitle") or x.get("client"),
                                    "client": x.get("client"), "action": x.get("action") or "",
                                    "at": at.isoformat(), "atStr": at.strftime("%d.%m, %H:%M"),
                                    "status": "new", "source": "сайт svodka-rei", "inboxFile": name, "inboxBlob": blob,
                                    "eventId": x.get("eid") or ""}})
        manifest.append({"name": name, "blob": blob, "docIds": ids})
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(writes, fh, ensure_ascii=False, indent=1)
    with open(out + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"batchSha256": sha256(out), "files": manifest}, fh, ensure_ascii=False, indent=1)
    print(f"prepare: files {len(manifest)}, comments {len(writes)} -> {out} (учёт не изменён, нужен ack после batch)")


def ack(repo, seen_path, out, applied_path):
    m = json.load(open(out + ".manifest.json", encoding="utf-8"))
    errors = []
    if sha256(out) != m["batchSha256"]:
        errors.append("batch.json изменён после prepare")
    batch = json.load(open(out, encoding="utf-8"))
    in_batch = {(w["data"]["inboxFile"], w["data"]["inboxBlob"], w["doc_id"]) for w in batch}
    in_manifest = {(f["name"], f["blob"], d) for f in m["files"] for d in f["docIds"]}
    if in_batch != in_manifest:
        errors.append("манифест не совпадает с batch.json")
    files = inbox_files(repo)
    applied = set(json.load(open(applied_path, encoding="utf-8")))
    for f in m["files"]:
        if f["name"] not in files:
            errors.append(f"{f['name']}: нет в origin/inbox (чужой файл)")
        elif files[f["name"]] != f["blob"]:
            errors.append(f"{f['name']}: содержимое изменилось после prepare")
        missing = [d for d in f["docIds"] if d not in applied]
        if missing:
            errors.append(f"{f['name']}: в db нет {len(missing)} из {len(f['docIds'])} записей ({missing[0]}…)")
    if errors:
        print("ack отклонён, учёт не изменён:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 1
    seen = read_seen(seen_path)
    with open(seen_path, "a", encoding="utf-8") as fh:
        fh.writelines(f"{f['name']} {f['blob']}\n" for f in m["files"] if seen.get(f["name"]) != f["blob"])
    print(f"ack: подтверждено файлов {len(m['files'])}")
    return 0


def main():
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    try:
        if cmd == "prepare" and len(args) == 3:
            prepare(*args)
            return 0
        if cmd == "ack" and len(args) == 4:
            return ack(*args)
    except GitError as e:
        print(f"Импорт остановлен, ошибка git: {e}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
