# skydivingnews-bot (GitHub Actions)

Бот постит абсурдные «новости» про парашютный мир в Telegram-канал.
Запускается бесплатно по расписанию через GitHub Actions (раз в час).

## Настройка (один раз)

1. Создай репозиторий на github.com и загрузи в него эти файлы:
   - `bot.py`
   - `requirements.txt`
   - `.gitignore`
   - `.github/workflows/post.yml`

2. Добавь секреты: **Settings → Secrets and variables → Actions → New repository secret**:
   - `OPENAI_API_KEY` — ключ OpenAI (sk-...)
   - `BOT_TOKEN` — токен бота от @BotFather
   - `CHANNEL_ID` — `@имя_канала` (бот должен быть админом канала)

3. Проверка: вкладка **Actions → post → Run workflow**. Через минуту смотри канал.

После этого бот постит сам раз в час. История и память сохраняются обратно
в репозиторий (файлы `posted.json`, `recent_*.json`) — поэтому новости не
повторяются, а репозиторий остаётся активным.

## Настройки
- Частота — строка `cron` в `.github/workflows/post.yml` (по умолчанию `0 * * * *` = каждый час, время UTC).
- Модель — `OPENAI_MODEL` в том же файле (`gpt-4o` или `gpt-4o-mini`).
