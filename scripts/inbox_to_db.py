"""Комментарии с сайта (ветка inbox) -> записи для db `comments` страницы задач.

Запуск на разборе комментариев (10:00, 13:00, 18:00):
  git fetch origin inbox
  python3 -I scripts/inbox_to_db.py <репо> <файл-учёта> <out.json>
Затем out.json передать в ArtifactData batch (url страницы задач 3FMn9JyWFXrL6ZzgAx5JAY)
и разобрать комментарии как обычно. Учёт уже перенесённых файлов - <файл-учёта>
(/mnt/project-files/daily-control/svodka-site/inbox-imported.txt), ветку inbox не трогаем.
"""
import json, subprocess, sys
from datetime import datetime, timedelta, timezone

ACTIONS = {"eve": "на вечер", "prio": "в приоритет", "wait": "ждём клиента", "expert": "ждём эксперта", "done": "сделано"}
MSK = timezone(timedelta(hours=3))


def main():
    repo, seen_path, out = sys.argv[1:4]
    try:
        seen = set(open(seen_path, encoding="utf-8").read().split())
    except FileNotFoundError:
        seen = set()
    names = subprocess.run(["git", "-C", repo, "ls-tree", "--name-only", "origin/inbox", "inbox/"],
                           capture_output=True, text=True).stdout.split()
    writes, new = [], []
    for name in sorted(n for n in names if n.endswith(".json") and n not in seen):
        f = json.loads(subprocess.run(["git", "-C", repo, "show", "origin/inbox:" + name],
                                      capture_output=True, text=True, check=True).stdout)
        for i, x in enumerate(f.get("items", [])):
            at = datetime.fromisoformat((x.get("at") or f["sentAt"]).replace("Z", "+00:00")).astimezone(MSK)
            act = ACTIONS.get(x.get("action") or "", "")
            text = (("[" + act + "] ") if act else "") + (x.get("text") or "").strip()
            writes.append({"op": "set", "collection": "comments",
                           "doc_id": "site-" + name[6:-5] + "-" + str(i),
                           "data": {"text": text, "taskId": x.get("taskId"), "taskTitle": x.get("taskTitle") or x.get("client"),
                                    "client": x.get("client"), "action": x.get("action") or "",
                                    "at": at.isoformat(), "atStr": at.strftime("%d.%m, %H:%M"),
                                    "status": "new", "source": "сайт svodka-rei"}})
        new.append(name)
    json.dump(writes, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(seen_path, "a", encoding="utf-8") as fh:
        fh.writelines(n + "\n" for n in new)
    print(f"files {len(new)}, comments {len(writes)} -> {out}")


if __name__ == "__main__":
    main()
