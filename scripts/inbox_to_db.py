"""Комментарии с сайта (ветка inbox) -> записи для db `comments` страницы задач. Двухфазно.

Разбор комментариев (10:00, 13:00, 18:00):
  1) git fetch origin inbox
  2) python3 -I scripts/inbox_to_db.py prepare <репо> <файл-учёта> <batch.json> [<папка tasks выгрузки db>]
     -> batch.json (записи для ArtifactData batch) и batch.json.manifest.json.
     Файл учёта НЕ меняется: подготовка не считается получением.
     С папкой tasks отметка к задаче, которой нет в db, не применяется (комментарий status new для разбора).
  3) ArtifactData batch из batch.json страницы 3FMn9JyWFXrL6ZzgAx5JAY, строго по порядку:
     сначала update в `tasks` (последнее событие по задаче), потом set в `comments`.
     Комментарий со status done и eventId в db значит: его действие уже внесено в задачу.
  4) Только если batch применён целиком: прочитать из db (ArtifactData get по doc_id из манифеста)
     записанные комментарии и задачи из taskDocs, сохранить в applied.json как
     {"comments": [id комментариев, найденных в db], "tasks": {id задачи: документ задачи из db как есть}}
     и выполнить python3 -I scripts/inbox_to_db.py ack <репо> <файл-учёта> <batch.json> <applied.json>
     ack сверяет хэш batch.json с манифестом, хэши файлов в origin/inbox, наличие всех комментариев
     и для каждой задачи из taskDocs совпадение КАЖДОГО поля patch с прочитанным из db.
     Порядок batch не считается доказательством. Старый формат applied.json (список id) годится,
     только если в партии нет изменений задач. При любом расхождении ничего не отмечает, код 1.
  Сбой на шаге 3 или частичный batch: просто повторить с шага 2. doc_id стабильны
  (имя файла + хэш содержимого + номер), повторная запись перезаписывает те же документы.
Файл учёта: /mnt/project-files/daily-control/svodka-site/inbox-imported.txt, строки «имя хэш»
(старые строки без хэша считаются подтверждёнными). Ветку inbox не трогаем.
Ошибка git (нет origin/inbox, сбой ls-tree/show) останавливает импорт с кодом 2.
Что делает каждое действие с задачей (только последнее событие по задаче, latest=true):
  done    -> status done (Ольга подтвердила «Я выполнила»)
  claude  -> status open, waiting Claude, delegated <eid>: дело НЕ закрывается, его ведёт Claude
  reopen  -> status open, waiting пусто;  wait -> status wait;  prio -> prio;  eve -> evening
  later   -> только на сайте до конца дня, задачу не меняет
Раннее событие, отменённое позже, помечено «не применять» и задачу не меняет.
Сообщение без действия (старые комментарии с action "" и «Сообщение Claude») задачу не меняет,
остаётся status new: его читает разбор (10, 13, 18 ч) и сам ставит done после ответа.
По eventId комментариев со status done пульт снимает отметку «ждёт синхронизации» (appliedEvents в сводке).
"""
import hashlib, json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

ACTIONS = {"eve": "на вечер", "prio": "в приоритет", "wait": "ждём клиента", "done": "Ольга выполнила сама", "later": "отложить до завтра", "reopen": "вернуть в работу", "claude": "поручено Claude"}
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


STATUS_ACTIONS = {"done", "later", "wait", "eve", "prio", "reopen", "claude"}
PATCH = {"done": {"status": "done"}, "claude": {"status": "open", "waiting": "Claude"}, "reopen": {"status": "open", "waiting": ""},
         "wait": {"status": "wait"}, "prio": {"prio": True}, "eve": {"evening": True}}


def task_ids(tasks_dir):
    """id задач из выгрузки db (имя файла без .json и поле id); без папки — None, проверки нет."""
    if not tasks_dir:
        return None
    ids = set()
    for n in os.listdir(tasks_dir):
        if n.endswith(".json"):
            ids.add(n[:-5])
            try:
                ids.add(str(json.load(open(os.path.join(tasks_dir, n), encoding="utf-8")).get("id") or n[:-5]))
            except (ValueError, AttributeError):
                pass
    return ids


def latest_events(repo, files):
    """Последнее событие-статус по каждой задаче во всём inbox (не только в новых файлах):
    применять к задаче надо только его, раннее «сделано», отменённое «вернуть», не закрывает дело."""
    last = {}
    for name in sorted(files):
        f = json.loads(git(repo, "cat-file", "blob", files[name]))
        for i, x in enumerate(f.get("items", [])):
            if x.get("taskId") and x.get("action") in STATUS_ACTIONS:
                key = (x.get("at") or f["sentAt"], name, i)
                if x["taskId"] not in last or key > last[x["taskId"]][0]:
                    last[x["taskId"]] = (key, x)
    return last


def prepare(repo, seen_path, out, tasks_dir=None):
    seen, files = read_seen(seen_path), inbox_files(repo)
    last, known = latest_events(repo, files), task_ids(tasks_dir)
    task_writes, writes, manifest = [], [], []
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
            latest = True
            if x.get("taskId") in last and x.get("action") in STATUS_ACTIONS:
                (lat_at, lat_name, lat_i), lx = last[x["taskId"]]
                latest = (lat_name, lat_i) == (name, i)
                if not latest:
                    later = datetime.fromisoformat(lat_at.replace("Z", "+00:00")).astimezone(MSK)
                    text = f"[отменено позже: {ACTIONS.get(lx.get('action'), lx.get('action'))} {later.strftime('%d.%m %H:%M')}, не применять] " + text
            doc_id = "site-" + name[6:-5] + "-" + blob[:8] + "-" + str(i)
            ids.append(doc_id)
            action, tid, applied, status = x.get("action") or "", x.get("taskId"), "", "new"
            if action in STATUS_ACTIONS and (not latest or action == "later"):
                status = "done"  # отменено позже или «отложить» (только сайт): в задачу вносить нечего
            elif action in PATCH and tid and (known is None or tid in known):
                patch = dict(PATCH[action], lastMove=at.strftime("%d.%m %H:%M") + " сайт: " + ACTIONS[action])
                if action == "claude":
                    patch["delegated"] = x.get("eid") or doc_id
                task_writes.append({"op": "update", "collection": "tasks", "doc_id": tid, "data": patch})
                applied, status = tid, "done"
            elif action in PATCH and tid:
                text = "[задачи нет в db, не применено] " + text
            writes.append({"op": "set", "collection": "comments", "doc_id": doc_id,
                           "data": {"text": text, "taskId": x.get("taskId"), "taskTitle": x.get("taskTitle") or x.get("client"),
                                    "client": x.get("client"), "action": x.get("action") or "",
                                    "at": at.isoformat(), "atStr": at.strftime("%d.%m, %H:%M"),
                                    "status": status, "appliedTo": applied, "source": "сайт svodka-rei", "inboxFile": name, "inboxBlob": blob,
                                    "eventId": x.get("eid") or "", "latest": latest,
                                    "supersededBy": "" if latest else (last[x["taskId"]][1].get("eid") or "")}})
        manifest.append({"name": name, "blob": blob, "docIds": ids})
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(task_writes + writes, fh, ensure_ascii=False, indent=1)
    with open(out + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"batchSha256": sha256(out), "files": manifest, "taskDocs": [w["doc_id"] for w in task_writes],
                   "taskPatches": {w["doc_id"]: w["data"] for w in task_writes}}, fh, ensure_ascii=False, indent=1)
    print(f"prepare: files {len(manifest)}, tasks {len(task_writes)}, comments {len(writes)} -> {out} (учёт не изменён, нужен ack после batch)")


def ack(repo, seen_path, out, applied_path):
    m = json.load(open(out + ".manifest.json", encoding="utf-8"))
    errors = []
    if sha256(out) != m["batchSha256"]:
        errors.append("batch.json изменён после prepare")
    batch = json.load(open(out, encoding="utf-8"))
    in_batch = {(w["data"]["inboxFile"], w["data"]["inboxBlob"], w["doc_id"]) for w in batch if w["collection"] == "comments"}
    if [w["doc_id"] for w in batch if w["collection"] == "tasks"] != m.get("taskDocs", []):
        errors.append("задачи в batch.json не совпадают с манифестом")
    in_manifest = {(f["name"], f["blob"], d) for f in m["files"] for d in f["docIds"]}
    if in_batch != in_manifest:
        errors.append("манифест не совпадает с batch.json")
    if {w["doc_id"]: w["data"] for w in batch if w["collection"] == "tasks"} != m.get("taskPatches", {}):
        errors.append("изменения задач в batch.json не совпадают с манифестом")
    files = inbox_files(repo)
    ap = json.load(open(applied_path, encoding="utf-8"))
    applied = set(ap if isinstance(ap, list) else ap.get("comments", []))
    readback = {} if isinstance(ap, list) else ap.get("tasks") or {}
    # Задачи подтверждаются только чтением из db: каждое поле patch должно совпасть с документом задачи
    for tid, patch in m.get("taskPatches", {}).items():
        doc = readback.get(tid)
        if not isinstance(doc, dict):
            errors.append(f"задача {tid}: нет чтения из db (нужен applied.json с tasks)")
            continue
        bad = [k for k, v in patch.items() if doc.get(k) != v]
        if bad:
            errors.append(f"задача {tid}: в db не применено {', '.join(f'{k}={doc.get(k)!r}, ждали {patch[k]!r}' for k in bad)}")
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
        if cmd == "prepare" and len(args) in (3, 4):
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
