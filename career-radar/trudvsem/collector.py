#!/usr/bin/env python3
"""Career Radar: safe read-only Trudvsem collector with optional Google Sheets sync.

No OpenAI API. No scraping. Public vacancy data only.
By default, fetching does NOT write to Google Sheets.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://opendata.trudvsem.ru/api/v1/vacancies"
SHEET_NAME = "Вакансии по группам"
HEADERS = [
    "Группа", "Название вакансии", "Работодатель", "Площадка", "Зарплата",
    "Удалённо", "Опыт", "Дата проверки", "Ссылка на вакансию", "Статус",
    "Комментарий / ограничения",
]
GROUP_QUERIES = {
    "Копирайтер": ["копирайтер", "автор текстов"],
    "AI-ассистент": ["AI-ассистент", "ассистент по нейросетям"],
    "Специалист по нейросетям": ["специалист по нейросетям"],
    "AI-тренер": ["AI-тренер", "тренер ИИ"],
    "Асессор": ["асессор", "оценка ответов ИИ"],
    "Контент-менеджер": ["контент-менеджер", "специалист по контенту"],
    "Помощник маркетолога": ["помощник маркетолога"],
    "Менеджер маркетплейсов": ["менеджер маркетплейсов"],
    "Помощник менеджера маркетплейсов": ["помощник менеджера маркетплейсов"],
    "Оператор чата": ["оператор чата"],
    "Специалист поддержки": ["специалист поддержки"],
    "Модератор контента": ["модератор контента"],
    "Разметчик данных": ["разметчик данных", "специалист по разметке"],
    "Менеджер-аналитик": ["менеджер-аналитик", "контент-аналитик", "аналитик продаж"],
}
TITLE_PATTERNS = {
    "Копирайтер": r"копирайт|автор текст|контент.?райтер|(?:^|\W)райтер",
    "AI-ассистент": r"ai.?ассистент|ассистент.*(?:ии|нейросет|искусственн|ai)|помощник.*нейросет",
    "Специалист по нейросетям": r"нейросет|специалист.*(?:^|\W)ии(?:\W|$)|ai.?специалист",
    "AI-тренер": r"ai.?тренер|ai.?trainer|тренер.*(?:ии|нейросет)|обучени.*модел",
    "Асессор": r"асессор|оценщик.*(?:ответ|качест)|оценк.*ответ",
    "Контент-менеджер": r"контент.?менедж|специалист по контент|администратор контент",
    "Помощник маркетолога": r"(?:помощник|ассистент|младший).*маркетолог",
    "Менеджер маркетплейсов": r"менеджер.*маркетплейс|специалист.*маркетплейс",
    "Помощник менеджера маркетплейсов": r"(?:помощник|ассистент|младший).*маркетплейс",
    "Оператор чата": r"оператор.*чат|чат.?оператор|текстов.*поддерж|специалист.*чат",
    "Специалист поддержки": r"специалист.*поддерж|оператор.*поддерж|служб.*поддерж",
    "Модератор контента": r"модератор|специалист.*модерац",
    "Разметчик данных": r"разметчик|разметк.*данн|аннотатор",
    "Менеджер-аналитик": r"аналитик|аналитическ",
}


def title_matches_group(title: str, group: str) -> bool:
    pattern = TITLE_PATTERNS.get(group)
    return bool(pattern and re.search(pattern, title, re.IGNORECASE))


REMOTE_POSITIVE = re.compile(
    r"удал[её]нн|дистанционн|работ[аы] (?:из дома|на дому)|home[\s-]?office|\bremote\b",
    re.IGNORECASE,
)
REMOTE_NEGATIVE = re.compile(
    r"не (?:предусмотрен[ао]?|допускается|возможн[ао]?) (?:удал[её]нн|дистанционн)"
    r"|(?:удал[её]нн|дистанционн)[а-я\s]{0,18} не (?:предусмотрен[ао]?|допускается)"
    r"|только в офисе|исключительно офисн|без удал[её]нн",
    re.IGNORECASE,
)


def clean(value: object, max_length: int = 250) -> str:
    """Flatten JSON without hidden formula execution or unbounded cell text."""
    if value is None:
        return ""
    if isinstance(value, dict):
        value = " ".join(clean(v, max_length) for v in value.values())
    if isinstance(value, list):
        value = " ".join(clean(v, max_length) for v in value)
    result = re.sub(r"\s+", " ", str(value)).strip()[:max_length]
    return ("'" + result) if result.startswith(("=", "+", "-", "@")) else result


def remote_status(v: dict) -> str:
    keys = ("workPlaceType", "workplace", "work-places", "schedule",
            "conditions", "duty", "employment", "job-name", "requirement")
    text = " ".join(clean(v.get(k), 1200) for k in keys)
    if REMOTE_NEGATIVE.search(text):
        return "Противоречие / офис"
    if REMOTE_POSITIVE.search(text):
        return "Есть указание"
    return "Не подтверждено"


def experience_text(v: dict) -> str:
    req = v.get("requirement") or {}
    raw = req.get("experience") if isinstance(req, dict) else None
    if raw is None or str(raw).strip() == "":
        return "Не указано"
    raw_s = clean(raw, 80)
    if raw_s in ("0", "0.0", "0 лет", "0 года", "нет"):
        return "0 лет"
    return raw_s


def salary_text(v: dict) -> str:
    low, high = clean(v.get("salary_min"), 30), clean(v.get("salary_max"), 30)
    low = "" if low in ("0", "0.0") else low
    high = "" if high in ("0", "0.0") else high
    salary = clean(v.get("salary"), 60)
    if low and high:
        return (low + " ₽" if low == high else low + "–" + high + " ₽")
    if low:
        return "от " + low + " ₽"
    if high:
        return "до " + high + " ₽"
    return salary or "Не указана"


def safe_vacancy_url(v: dict) -> str:
    url = str(v.get("vac_url") or "").strip()
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return ""
    if not (parsed.hostname == "trudvsem.ru" or parsed.hostname.endswith(".trudvsem.ru")):
        return ""
    return url


def normalize(item: dict, group: str, check_date: str) -> dict | None:
    v = item.get("vacancy", item)
    if not isinstance(v, dict):
        return None
    url = safe_vacancy_url(v)
    name = clean(v.get("job-name"), 180)
    if not url or not name or not title_matches_group(name, group):
        return None
    employer = v.get("company") or {}
    if not isinstance(employer, dict):
        employer = {}
    location = v.get("region") or {}
    region = clean(location.get("name") if isinstance(location, dict) else "", 80)
    status = remote_status(v)
    note = (
        "Данные API; удалёнка требует подтверждения по прямой карточке; "
        "регион найма и оформление не проверены."
    )
    if region:
        note += " Регион: " + region + "."
    return {
        "group": group,
        "name": name,
        "employer": clean(employer.get("name"), 180) or "Не указано",
        "source": "Работа России",
        "salary": salary_text(v),
        "remote": status,
        "experience": experience_text(v),
        "checked": check_date,
        "url": url,
        "status": "На проверку",
        "comment": note,
        "source_id": clean(v.get("id"), 100),
        "modified": clean(v.get("modified-date") or v.get("creation-date"), 80),
    }


def key_for(item: dict) -> str:
    url = item.get("url") or ""
    if url:
        return url.rstrip("/").lower()
    return hashlib.sha256(
        (item.get("name", "") + "|" + item.get("employer", "")).encode("utf-8")
    ).hexdigest()


def unique(items: list[dict]) -> list[dict]:
    seen = set()
    output = []
    for item in items:
        key = key_for(item)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def api_page(query: str, modified_from: str, limit: int, offset: int,
             timeout: int = 35, max_attempts: int = 2) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode({
        "text": query, "experienceFrom": 0, "experienceTo": 0,
        "modifiedFrom": modified_from, "limit": limit, "offset": offset,
    })
    req = urllib.request.Request(
        API + "?" + params,
        headers={"Accept": "application/json", "User-Agent": "CareerRadar/0.1 (data research)"},
    )
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError("HTTP " + str(resp.status))
                payload = json.load(resp)
            if str(payload.get("status", "200")) != "200":
                raise RuntimeError("API status not 200: " + str(payload.get("status")))
            vacancies = payload.get("results", {}).get("vacancies", [])
            if not isinstance(vacancies, list):
                raise ValueError("API results.vacancies is not a list")
            return vacancies, int(payload.get("meta", {}).get("total", len(vacancies)))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            if attempt + 1 >= max_attempts:
                raise RuntimeError(type(exc).__name__ + ": " + str(exc)) from exc
            time.sleep(2 + attempt * 2)
    raise RuntimeError("Unreachable API state")


def fetch(group_queries: dict[str, list[str]], days: int, limit: int,
          pages: int, timeout: int) -> tuple[list[dict], dict]:
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    check_date = dt.datetime.now(dt.timezone.utc).strftime("%d.%m.%Y")
    all_rows, errors = [], []
    stats = []
    for group, queries in group_queries.items():
        for query in queries:
            found_total = None
            count = 0
            for page_no in range(pages):
                offset = page_no * limit
                try:
                    items, total = api_page(query, since, limit, offset, timeout=timeout)
                except Exception as exc:
                    errors.append({"group": group, "query": query, "error": str(exc)[:200]})
                    break
                found_total = total
                count += len(items)
                for raw in items:
                    row = normalize(raw, group, check_date)
                    if row:
                        date_part = row.get("modified", "")[:10]
                        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_part):
                            if date_part < since[:10]:
                                continue
                        all_rows.append(row)
                if len(items) < limit or offset + len(items) >= total:
                    break
            stats.append({"group": group, "query": query, "total": found_total,
                          "received": count})
    rows = unique(all_rows)
    eligible = [x for x in rows if x["remote"] == "Есть указание"]
    summary = {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "modified_from_utc": since,
        "queries": stats, "errors": errors, "received_normalized": len(all_rows),
        "unique_count": len(rows), "remote_candidates": len(eligible),
        "remote_unconfirmed": len(rows) - len(eligible),
        "all_queries_ok": len(errors) == 0,
    }
    return eligible, summary


def sheet_row(v: dict) -> list[str]:
    return [v[x] for x in ("group", "name", "employer", "source", "salary",
                           "remote", "experience", "checked", "url", "status", "comment")]


def send_json(url: str, token: str, data: dict | None = None) -> dict:
    encoded = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=encoded, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"
    }, method="POST" if encoded is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def append_to_google(rows: list[dict]) -> dict:
    """Requires a user-configured Google service account shared on target sheet."""
    service_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID")
    if not service_json or not spreadsheet_id:
        raise RuntimeError(
            "Google credentials missing: GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SPREADSHEET_ID"
        )
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt for Google write mode") from exc
    cred = service_account.Credentials.from_service_account_info(
        json.loads(service_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    cred.refresh(Request())
    encoded_sheet = urllib.parse.quote("'" + SHEET_NAME + "'!A1:K5000", safe="")
    api_url = "https://sheets.googleapis.com/v4/spreadsheets/" + urllib.parse.quote(
        spreadsheet_id, safe=""
    ) + "/values/"
    existing = send_json(api_url + encoded_sheet, cred.token).get("values", [])
    if not existing or existing[0][:len(HEADERS)] != HEADERS:
        raise RuntimeError("Destination Google Sheet headers differ: refusing to write")
    known_urls = {r[8].rstrip("/").lower() for r in existing[1:] if len(r) > 8 and r[8]}
    to_write = [sheet_row(x) for x in rows if key_for(x) not in known_urls]
    if not to_write:
        return {"new_rows": 0, "existing": len(known_urls)}
    append_range = urllib.parse.quote("'" + SHEET_NAME + "'!A:K", safe="")
    query = urllib.parse.urlencode({"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"})
    result = send_json(api_url + append_range + ":append?" + query, cred.token, {
        "majorDimension": "ROWS", "values": to_write
    })
    return {"new_rows": len(to_write), "range": result.get("updates", {}).get("updatedRange")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", action="append", choices=list(GROUP_QUERIES))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--output", default="vacancies.json")
    parser.add_argument("--summary", default="summary.json")
    parser.add_argument("--write-sheet", action="store_true")
    args = parser.parse_args()
    if args.days < 1 or args.days > 365 or args.limit < 1 or args.limit > 100:
        parser.error("days must be 1..365; limit must be 1..100")
    if args.pages < 1 or args.pages > 20 or args.timeout < 5 or args.timeout > 120:
        parser.error("pages must be 1..20; timeout must be 5..120")
    selected = ({g: GROUP_QUERIES[g] for g in args.group}
                if args.group else GROUP_QUERIES)
    rows, summary = fetch(selected, args.days, args.limit, args.pages, args.timeout)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_sheet:
        if not summary["all_queries_ok"]:
            raise RuntimeError("API queries failed. Refusing partial automatic Google write.")
        summary["google_sheet"] = append_to_google(rows)
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "success" if summary["all_queries_ok"] else "partial_failure",
        "queries": len(summary["queries"]), "errors": len(summary["errors"]),
        "unique": summary["unique_count"], "remote_candidates": len(rows),
        "sheet": summary.get("google_sheet", "not_written"),
    }, ensure_ascii=False))
    return 0 if summary["all_queries_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())