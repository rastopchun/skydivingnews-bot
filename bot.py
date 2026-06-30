#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skydive-news-bot — генератор абсурдных «новостей» про парашютный спорт.

Что делает:
  1. Через OpenAI API придумывает одну короткую выдуманную новость.
     Стиль: маленькая нелепая байка из парашютного мира, написанная сухим
     языком настоящей новостной заметки.
  2. Проверяет результат простыми фильтрами качества: есть ли парашютный якорь,
     бытовая нелепая деталь, нет ли generic-штампов и перегруза терминами.
  3. Постит новость в Telegram-канал, где бот добавлен администратором.

Конфиг читается из .env рядом с файлом ИЛИ из переменных окружения.
Обязательно: OPENAI_API_KEY, BOT_TOKEN, CHANNEL_ID.
Опционально: OPENAI_MODEL, FOOTER.

Запуск:
  python bot.py             # с публикацией
  python bot.py --dry-run   # только сгенерировать и вывести в консоль
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import random
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
HISTORY_FILE = BASE_DIR / "posted.json"
HISTORY_MAX = 500
HISTORY_HINT = 60

ANGLES_STATE = BASE_DIR / "recent_angles.json"
ANGLES_COOLDOWN = 18

PERSONS_STATE = BASE_DIR / "recent_persons.json"
PERSONS_COOLDOWN = 15

DETAILS_STATE = BASE_DIR / "recent_details.json"
DETAILS_COOLDOWN = 20

BODY_MAX_CHARS = 320
HEADLINE_MAX_CHARS = 80
MAX_GENERATION_ATTEMPTS = 8


# ----------------------------- ENV -----------------------------
def load_env_file(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            m = re.match(r"([A-Za-z0-9_]+)\s*=\s*(.*)", line)
            if not m:
                continue

            key, val = m.group(1), m.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            env[key] = val

    return env


_envf = load_env_file(ENV_FILE)


def get_env(name: str, default: str = "") -> str:
    return os.environ.get(name, _envf.get(name, default)).strip()


OPENAI_API_KEY = get_env("OPENAI_API_KEY")
BOT_TOKEN = get_env("BOT_TOKEN")
CHANNEL_ID = get_env("CHANNEL_ID")
OPENAI_MODEL = get_env("OPENAI_MODEL", "gpt-4o-mini")
FOOTER = get_env("FOOTER", "🪂 <b>Министерство парашютных дел</b>")
TG_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def log(*args):
    print(*args, file=sys.stderr, flush=True)


# --------------------------- История ---------------------------
def read_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def write_json(path: Path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_history() -> list:
    return read_json_list(HISTORY_FILE)


def save_history(headline: str):
    arr = load_history()
    arr.append({
        "headline": headline,
        "posted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    write_json(HISTORY_FILE, arr[-HISTORY_MAX:])


def recent_headlines() -> list:
    return [h.get("headline", "") for h in load_history()[-HISTORY_HINT:] if h.get("headline")]


# ------------------------- Генерация ---------------------------
# Важная логика: новости должны рождаться из парашютного мира, а не из внешней
# инфоповестки. Поэтому ANGLES — это ситуации на ДЗ, а не «крипта», «финтех» и
# «шоу-бизнес». Внешние темы можно вплетать только как вторичный абсурд.
ANGLES = [
    "бытовая причина временно остановила прыжки на дропзоне",
    "сборы по фрифлаю сорвались из-за внешности или привычки участника",
    "инструктор неправильно истолковал обычный предмет как часть снаряжения",
    "руководитель ДЗ ввёл странный запрет после одного нелепого случая",
    "местные жители помешали работе аэроклуба самым приземлённым способом",
    "модная вещь спортсмена неожиданно повлияла на формацию",
    "одежда участника стала административной проблемой на дропзоне",
    "еда или напиток стали причиной официального решения на ДЗ",
    "парашютный термин был понят чиновником буквально",
    "бренд одежды случайно сделал вывод из существования строп",
    "историческая или религиозная находка абсурдно доказала древность парашюта",
    "тандем, manifest или укладочная стали частью бытового конфликта",
    "обычная табличка, пакет или скотч привели к отмене прыжкового дня",
    "спортсмена забанили за вещь, которая мешала не всем, но очень убедительно",
    "самолёт Ан-2 стал участником мелкого, но официального недоразумения",
    "фрифлайная формация пострадала от слишком понятной бытовой причины",
    "ведомство или известный человек случайно отменили обычную парашютную процедуру",
    "дропзона решила проблему радикально, но с очень серьёзным видом",
]

TONES = [
    "сухая короткая новость районного уровня",
    "невозмутимая ведомственная сводка",
    "деловое сообщение аэроклуба с абсурдной причиной",
    "протокольная заметка с одной короткой странной цитатой",
    "новостная заметка, где полный бред подан как обычный факт",
    "спокойный репортаж с места событий без эмоциональных слов",
]

DROPZONES = [
    "Ватулино", "Крутицы", "Пущино", "Аэроград Коломна", "Танай",
    "Мензелинск", "Большое Грызлово", "Серпухов", "Skydive Dubai",
    "Empuriabrava", "Perris Valley",
]

DETAILS = [
    "бритая голова", "широкие штаны", "шнурки", "стропы", "шелуха от семечек",
    "пакет с халвой", "банка пива", "зеркальная поверхность", "икона",
    "скотч на шлеме", "бахилы", "чайник в укладочной", "носки с пальцами",
    "табличка на manifest", "пластиковая ложка", "пуховик летом", "леопардовая накидка",
    "синий пакет из супермаркета", "пачка влажных салфеток", "неподписанная каска",
    "семечки", "резинка от денег", "чужие тапки", "бутерброд на лавке",
    "неработающий кулер", "зонтик у борта", "расчёска в кармане комбеза",
]

CONSEQUENCES = [
    "сорвались сборы", "ДЗ временно закрыли", "формацию не собрали",
    "отцепки попросили прекратить", "manifest приостановил работу",
    "спортсмена сняли с подъёма", "инструктор потребовал сатисфакции",
    "руководитель ДЗ ввёл запрет", "укладочную перевели на особый режим",
    "борт задержали на двадцать минут", "тандемы перенесли до выяснения обстоятельств",
]

# Реальные публичные лица. Используются только как мягкая абсурдная приправа.
# Нельзя делать правдоподобное обвинение в преступлении или серьёзном проступке.
PERSONS = [
    ("Владимир Путин", "путин"),
    ("Илон Маск", "маск"),
    ("Павел Дуров", "дуров"),
    ("Марк Цукерберг", "цукерберг"),
    ("Моргенштерн", "моргенштерн"),
    ("Баста", "баста"),
    ("Оксимирон", "оксимирон"),
    ("Инстасамка", "инстасамк"),
    ("Настя Ивлеева", "ивлеев"),
    ("Ольга Бузова", "бузов"),
    ("Тимати", "тимати"),
    ("Дмитрий Нагиев", "нагиев"),
    ("Герман Греф", "греф"),
    ("Олег Тиньков", "тинько"),
    ("Криштиану Роналду", "роналд"),
    ("Лионель Месси", "месси"),
    ("Джефф Безос", "безос"),
    ("Билл Гейтс", "гейтс"),
    ("Канье Уэст", "канье"),
]

SYSTEM_PROMPT = """Ты пишешь короткие выдуманные новости для сатирического канала про парашютный мир.

Главное: это НЕ сатирическая новость про политику, бренды, интернет или шоу-бизнес. Это маленькая абсурдная новость из парашютного мира. Внешний мир может попасть в текст только как приправа.

СТИЛЬ:
- Абсурдно, но не бессвязно.
- Один конкретный нелепый случай, который можно пересказать одним предложением.
- Сухой, невозмутимый, почти официальный тон.
- Юмор рождается из серьёзной подачи бытовой глупости.
- Бытовая деталь важнее большого инфоповода.
- Парашютная тема в центре: ДЗ, сборы, инструктор, борт, укладочная, формация, отцепка, стропы, медуза, тандем, заход.

ФОРМУЛА НОВОСТИ:
[бытовая причина] → [парашютное последствие] → [сухой официальный вывод или короткая цитата]

Сначала придумай бытовую нелепость на ДЗ. Потом привяжи её к одному реальному парашютному последствию. Только потом, если нужно, добавь известного человека, бренд или ведомство.

ЭТАЛОННЫЕ ПРИМЕРЫ:

Заголовок: Эпиляция сорвала сборы в Коломне
Текст: Непредсказуемая аэродинамика бритого перформера стала причиной срыва сборов по фрифлаю в Аэрограде Коломна. Человек хоть и был красив, но просвистел мимо формации и отправился за пивом, как мы помним, его продажа лысым людям ограничена.

Заголовок: РПЦ нашла доказательства плагиата
Текст: Митрополит Иларион заявил, что нашёл икону с изображением десантирования святых на остров Пасхи. События датированы 1350 годом, что доказывает лишь одно: парашют придумал не Леонардо да Винчи, а Игнат. Доказательства переданы в Следственный комитет.

Заголовок: Модные бренды отказываются от шнурков
Текст: Акционеры Givenchy и Gucci приняли решение о закрытии заводов по производству шнурков. «Зачем шнурки, если есть стропы?» — заявили они. Завод «Большевичка» готовит ответ.

Заголовок: Шелуха от семечек стала причиной закрытия ДЗ
Текст: В минувшую субботу ДЗ Ватулино приостановила работу из-за огромного количества шелухи от семечек. Инструктор аэроклуба сообщил, что местные коневоды решили собрать урожай подсолнухов и сразу употребить его по назначению.

Заголовок: Широкие штаны неприемлемы
Текст: Несколько девушек забанили в Крутицах за чересчур широкие штаны для прыжков. Руководитель дропзоны расстроен и требует сатисфакции.

Заголовок: Путин отменил отцепки
Текст: Президент провёл совещание и попросил чиновников прекратить отцепки на российских и зарубежных дропзонах: «Что это вообще такое? Пусть прекращают. И принесите халву, я голоден».

ЧТО ДЕЛАЕТ ЭТИ ПРИМЕРЫ ХОРОШИМИ:
- Есть один чёткий абсурдный повод.
- Повод бытовой и приземлённый: волосы, штаны, семечки, шнурки, халва.
- Есть конкретная парашютная сцена или последствие.
- Текст звучит как настоящая сухая новость.
- Прямая речь короткая и нужна только там, где она усиливает шутку.

ЧЕГО НЕ ДЕЛАТЬ:
- Не пиши абстрактную сатиру про общество, интернет, нейросети или рынок.
- Не приклеивай парашюты в конце новости.
- Не используй пафосные штампы: «скандал», «взорвал интернет», «буря обсуждений», «эксперты заявили», «на грани», «резонанс», «шокировал».
- Не нагромождай термины. Максимум 1–2 парашютных термина на новость.
- Не пихай в одну новость больше одного повода.
- Не делай космический сюр. Абсурд должен выглядеть бытовым и почти правдоподобным по форме.
- Не обвиняй реальных людей в настоящих преступлениях, коррупции, насилии или серьёзных проступках.
- Не используй оскорбления по национальности, религии, полу, ориентации, инвалидности или здоровью.

РЕАЛЬНЫЕ ДРОПЗОНЫ: Ватулино, Крутицы, Пущино, Аэроград Коломна, Танай, Мензелинск, Большое Грызлово, Серпухов, Skydive Dubai, Empuriabrava, Perris Valley.
БРЕНДЫ/КОМПАНИИ: ParaAvis, Performance Designs, UPT, Cypres, ФПС России, ДОСААФ, Аэрофлот, S7, Сбер, Тинькофф, Яндекс, Ozon, Wildberries.

ФОРМАТ:
- Заголовок до 80 символов.
- Тело: 1 абзац, 1–3 предложения, до 320 символов.
- Русский язык.
- Без markdown, без пояснений.
- Верни строго JSON-объект вида: {"headline": "...", "body": "..."}
"""

# Слова и обороты, которые почти всегда уводят результат в generic-новость.
BAD_STYLE_PATTERNS = [
    r"\bскандал\w*", r"взорвал\w*", r"бур[яю]\s+обсуждени", r"эксперт[ыа]?\s+заявил",
    r"пользовател[ьи]\s+обсужда", r"на\s+грани", r"\bрезонанс\w*", r"шокировал\w*",
    r"интернет\s+обсужда", r"соцсет[ьи]\s+обсужда", r"громкое\s+заявление",
    r"остались\s+в\s+шоке", r"вызвал[аио]?\s+бур",
]

# Оскорбления по здоровью/инвалидности и общие грубые ярлыки.
BANNED_PATTERNS = [
    r"олигофрен", r"недоразвит", r"даун[аеоы]?\b", r"дебил", r"имбецил",
    r"идиот", r"кретин", r"слабоумн", r"калек", r"уродов?\b", r"даунизм",
]

SKY_ANCHORS = [
    "дз", "дропзон", "аэроград", "ватулино", "крутиц", "пущино", "танай",
    "мензелинск", "грызлов", "серпухов", "прыж", "фрифла", "формац",
    "инструктор", "аэроклуб", "купол", "строп", "отцепк", "медуз", "тандем",
    "борт", "ан-2", "укладоч", "manifest", "перформер", "заход", "подъём",
]

DOMESTIC_ANCHORS = [
    "брит", "лыс", "волос", "эпиляц", "штаны", "семеч", "шелух", "пиво",
    "халв", "шнур", "икон", "зеркал", "скотч", "пакет", "бахил", "чайник",
    "чай", "кофе", "носок", "носки", "тапоч", "ложк", "салфет", "бутерброд",
    "кулер", "зонтик", "расчёск", "накидк", "табличк", "пуховик", "касск", "шлем",
]

SKY_TERMS = [
    "отцепк", "медуз", "строп", "купол", "тандем", "вингсьют", "фрифла",
    "формац", "заход", "борт", "ан-2", "укладоч", "manifest", "подъём",
]


def has_any_pattern(text: str, patterns: list[str]) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in patterns)


def has_any_fragment(text: str, fragments: list[str]) -> bool:
    low = text.lower()
    return any(f in low for f in fragments)


def count_fragments(text: str, fragments: list[str]) -> int:
    low = text.lower()
    return sum(1 for f in fragments if f in low)


def load_recent_persons() -> list:
    return read_json_list(PERSONS_STATE)


def detect_persons(text: str) -> list:
    low = text.lower()
    found = []
    for full, key in PERSONS:
        if re.search(r"\b" + re.escape(key), low) and full not in found:
            found.append(full)
    return found


def save_recent_persons(persons: list):
    if not persons:
        return
    recent = load_recent_persons()
    recent.extend(persons)
    write_json(PERSONS_STATE, recent[-PERSONS_COOLDOWN:])


def pick_non_recent(items: list[str], state_path: Path, cooldown: int) -> str:
    recent = read_json_list(state_path)
    pool = [item for item in items if item not in recent] or list(items)
    chosen = random.choice(pool)
    recent.append(chosen)
    write_json(state_path, recent[-cooldown:])
    return chosen


def pick_angle() -> str:
    return pick_non_recent(ANGLES, ANGLES_STATE, ANGLES_COOLDOWN)


def pick_detail() -> str:
    return pick_non_recent(DETAILS, DETAILS_STATE, DETAILS_COOLDOWN)


def trim_body(body: str, max_chars: int = BODY_MAX_CHARS) -> str:
    body = body.split("\n\n")[0]
    body = re.sub(r"\s*\n\s*", " ", body).strip()
    body = re.sub(r"\s{2,}", " ", body)

    if len(body) <= max_chars:
        return body

    cut = body[:max_chars]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"), cut.rfind("»"))
    if end >= 80:
        return cut[:end + 1].strip()
    return cut.rstrip(" ,;:—-") + "…"


def quality_reject(headline: str, body: str) -> str | None:
    text = f"{headline} {body}".lower()

    if len(headline) > HEADLINE_MAX_CHARS:
        return "заголовок слишком длинный"

    if len(body) > BODY_MAX_CHARS:
        return "тело слишком длинное"

    if has_any_pattern(text, BANNED_PATTERNS):
        return "запрещённая лексика"

    if has_any_pattern(text, BAD_STYLE_PATTERNS):
        return "новостные штампы вместо сухого абсурда"

    if not has_any_fragment(text, SKY_ANCHORS):
        return "нет настоящего парашютного якоря"

    if not has_any_fragment(text, DOMESTIC_ANCHORS):
        return "нет бытовой нелепой детали"

    if count_fragments(text, SKY_TERMS) > 4:
        return "перегруз парашютными терминами"

    if text.count(",") > 6:
        return "перегруженное предложение"

    persons = detect_persons(text)
    if len(persons) > 1:
        return "больше одного известного человека"

    recent_persons = set(load_recent_persons())
    if set(persons) & recent_persons:
        return "персона недавно уже была"

    return None


def parse_news(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # На всякий случай вынимаем первый JSON-объект, если модель добавила мусор.
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise
        data = json.loads(m.group(0))

    if not isinstance(data, dict):
        raise RuntimeError("ответ модели не JSON-объект")

    headline = str(data.get("headline", "")).strip()
    body = str(data.get("body", "")).strip()

    if not headline or not body:
        raise RuntimeError(f"пустой ответ модели: {raw[:200]}")

    return {"headline": headline, "body": body}


def generate_news(client: OpenAI) -> dict:
    angle = pick_angle()
    detail = pick_detail()
    place = random.choice(DROPZONES)
    consequence = random.choice(CONSEQUENCES)
    tone = random.choice(TONES)

    avoid = recent_headlines()
    avoid_block = ""
    if avoid:
        joined = "\n".join(f"- {h}" for h in avoid)
        avoid_block = "\n\nНЕ повторяй темы и формулировки этих недавних новостей:\n" + joined

    recent_persons = load_recent_persons()
    persons_block = ""
    if recent_persons:
        persons_block = (
            "\n\nНе упоминай этих людей — они недавно уже были: "
            + ", ".join(dict.fromkeys(recent_persons))
        )

    user_prompt = f"""Вводные для одной новости:
- Ситуация: {angle}
- Дропзона или место: {place}
- Бытовая деталь: {detail}
- Последствие: {consequence}
- Тон: {tone}

Используй вводные свободно, но сохрани механику: бытовая нелепость → парашютное последствие → сухой вывод или короткая цитата.
Не делай обзор темы. Не делай новость про интернет. Нужен один конкретный случай.{avoid_block}{persons_block}
"""

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.82,
        top_p=0.95,
        presence_penalty=0.25,
        frequency_penalty=0.2,
        max_tokens=420,
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content.strip()
    news = parse_news(raw)
    news["body"] = trim_body(news["body"])

    reason = quality_reject(news["headline"], news["body"])
    if reason:
        raise RuntimeError(f"Плохой стиль: {reason}")

    persons = detect_persons(news["headline"] + " " + news["body"])
    save_recent_persons(persons)

    news["angle"] = angle
    news["detail"] = detail
    news["place"] = place
    return news


# -------------------------- Telegram ---------------------------
def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(news: dict) -> str:
    headline = html_escape(news["headline"])
    body = html_escape(news["body"])
    parts = [f"<b>{headline}</b>", "", body]
    if FOOTER:
        parts += ["", FOOTER]
    return "\n".join(parts)


def tg_send_message(text: str) -> dict:
    payload = json.dumps({
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        TG_API_BASE + "/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "skydive-news-bot/2.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}: {body}"}


# ---------------------------- Main -----------------------------
def main():
    dry_run = "--dry-run" in sys.argv

    required = [("OPENAI_API_KEY", OPENAI_API_KEY)]
    if not dry_run:
        required += [("BOT_TOKEN", BOT_TOKEN), ("CHANNEL_ID", CHANNEL_ID)]

    missing = [n for n, v in required if not v]
    if missing:
        log("❌ Не заданы переменные:", ", ".join(missing))
        log("   Заполните .env или переменные окружения.")
        sys.exit(2)

    log("🚀 Старт. Модель:", OPENAI_MODEL, "| dry-run:", dry_run)
    client = OpenAI(api_key=OPENAI_API_KEY)

    news = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        try:
            log(f"🧠 Генерация новости (попытка {attempt}/{MAX_GENERATION_ATTEMPTS})...")
            news = generate_news(client)
            log("📰 Заголовок:", news["headline"])
            log("🧩 Вводные:", news.get("place"), "|", news.get("detail"), "|", news.get("angle"))
            break
        except Exception as e:
            log(f"⚠️ Ошибка генерации: {e!r}")
            time.sleep(min(2 * attempt, 10))

    if not news:
        log("❌ Не удалось сгенерировать новость нужного качества")
        sys.exit(1)

    text = format_message(news)

    if dry_run:
        log("🧪 DRY-RUN — постинг отключён. Сгенерированное сообщение:")
        print("\n" + "=" * 48)
        print(text)
        print("=" * 48)
        sys.exit(0)

    log("📤 Публикую в канал...")
    res = tg_send_message(text)
    if res.get("ok"):
        save_history(news["headline"])
        log("✅ Опубликовано.")
        sys.exit(0)

    log("❌ Telegram отказал:", res.get("description") or res)
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log("Fatal:", repr(e))
        sys.exit(1)
