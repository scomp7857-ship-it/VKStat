# VKStat

Поіск по VK-групах та аналітика у стилі TGStat: завантажуєш список груп,
робот обходить їх через VK API, індексує тексти постів у PostgreSQL Full-Text Search
і видає результати з графіками, топом, охопленням і хмарою супутніх слів.

## Швидкий старт

```bash
cp .env.example .env
# відредагуй .env: додай VK_TOKENS=token1,token2  (service token у VK → «Налаштування API»)
make up              # збирає образи, піднімає всі сервіси, запускає міграції
make import FILE=groups.txt   # імпорт груп із файлу (URL / screen / clubNN на кожному рядку)
open http://localhost:8000
```

Все в одному `docker compose`: API + worker + scheduler + PostgreSQL + Redis.
Перший `make up` займає ~1 хв (збірка образу і встановлення залежностей).

## Архітектура

```
                    ┌─────────────┐        ┌─────────────┐
    browser ──────▶│  FastAPI    │──────▶│ PostgreSQL  │  tsvector + GIN
                    │  + HTMX UI  │        │  + pg_trgm  │
                    └─────┬───────┘        └─────────────┘
                          │ enqueue                ▲ upsert + metrics history
                          ▼                        │
                    ┌─────────────┐      VK API    │
                    │   Redis /   │◀────▶│ Worker  │ execute batches, token pool
                    │   RQ queue  │      │ (RQ)    │
                    └─────┬───────┘      └─────────┘
                          ▲
                          │ cron schedules
                    ┌─────┴───────┐
                    │  Scheduler  │  inkremental scrape + metrics refresh
                    └─────────────┘
```

Таблиці:
- `groups` — VK-спільноти з `last_post_id` для інкрементального скрейпу.
- `posts` — пости з `search_vector` (зберігається, GIN індекс) + `gin_trgm_ops` на `text` для підрядкового пошуку.
- `post_metrics_history` — знімки переглядів/лайків у часі; дають графік охоплення для старих постів.

Конфігурація пошукової бази — `vkstat_ru_uk` (unaccent + russian snowball).
Підходить і для українських, і для російських текстів; для повнішої української
морфології встановіть `hunspell_uk` і підкиньте мапінг у словнику.

## Основні команди

| Команда | Що робить |
|---|---|
| `make up` / `make down` | підняти / зупинити все |
| `make logs` | слідкувати за логами |
| `make migrate` | alembic upgrade head |
| `make revision m="add foo"` | нова міграція |
| `make import FILE=groups.txt` | імпорт груп + постановка в чергу первинного скрейпу |
| `make scrape` | поставити scrape-джобу для всіх активних груп |
| `make refresh` | поставити джобу оновлення метрик (fresh + old) |
| `make psql` | psql-шелл у БД |
| `make test` | юніт-тести |

## Режими пошуку

- `автомобіль` — морфологічний пошук (знайде «автомобіля», «автомобілем» тощо).
- `"генеральний директор"` — точна фраза.
- `~ауто~` — підрядкова ILIKE-підказка через pg_trgm (для часткових збігів).

Фільтри: період (`date_from`/`date_to`) і набір груп.

## Аналітика

За запитом видається:
- **Summary** — згадувань, сумарне охоплення, лайки, унікальні групи.
- **Timeseries** — згадування й охоплення по днях за 30 днів.
- **Top posts** — 10 найбільш переглядуваних.
- **Co-occurrence cloud** — найчастіші слова поруч із запитом у свіжих постах.
- **Export CSV** — уся видача для обраних фільтрів.

JSON-ендпоінти:
- `GET /api/search?q=...&order=...` — пошук.
- `GET /api/analytics?q=...&days=30` — summary + timeseries + top + word-cloud.

## VK API

- **Токени** — список у `VK_TOKENS` через кому; кожен токен має власний rate-bucket (3 rps).
- **execute** — скрейпер пакує до 25 `wall.get` в один запит, роблячи повний оборот по 500 групах за кілька хвилин.
- **Ретраї** — коди `6`, `10`, `29` ретраяться з експоненційним бекофом (tenacity).

## Розклад (scheduler)

| Джоба | Розклад | Опис |
|---|---|---|
| `job_scrape_all_incremental` | кожні 4 години | тягне першу сторінку кожної групи батчем |
| `job_refresh_metrics_fresh` | щодня о 03:30 UTC | оновлення метрик постів за останні 30 днів |
| `job_refresh_metrics_old` | щопонеділка 04:45 | оновлення метрик старіших постів (batch=5000) |

Масштабування: `docker compose up -d --scale worker=4`.

## Деплой на Render

Репо вже містить `render.yaml` (Blueprint), який описує 5 ресурсів: Postgres,
Key Value (Redis), web (FastAPI), worker (RQ), scheduler (rq-scheduler).

1. У Render натисни **New → Blueprint**, обери цей репозиторій і гілку.
2. Render прочитає `render.yaml` і запропонує створити всі ресурси.
3. У дашборді відкрий env-group **`vkstat`** і встав `VK_TOKENS=token1,token2`
   (service token VK → «Налаштування API»; кілька через кому для пулу).
4. Deploy.

Міграції запускаються автоматично під час старту web-контейнера
(`alembic upgrade head && uvicorn …`), тому перший запит ніколи не впаде на
непроміграну БД. `unaccent` і `pg_trgm` ставить сама міграція.

**Тарифи**: web + Postgres + Key Value запускаються на `free`. Workers на Render
потребують мінімум `starter` ($7/міс кожен). Якщо хочеш повністю безкоштовний
варіант — прибери `vkstat-worker` і `vkstat-scheduler` з `render.yaml` і
запускай скрейп руками через `render shell` → `python -m app.cli enqueue-scrape`
(або напряму `python -m app.jobs` в одноразовому Job).

Health-check: `GET /healthz`.

## Структура коду

```
app/
  main.py             FastAPI + HTMX endpoints
  config.py           pydantic-settings
  db.py models.py     SQLAlchemy 2.0
  cli.py              python -m app.cli <cmd>
  vk/
    client.py         token pool, rate limit, execute batches
    urls.py           парсинг VK URL/screen-name
  scraper/
    groups.py         resolve + upsert груп
    posts.py          backfill / incremental / metrics refresh
  search/
    query.py          режими запиту (morph / phrase / substring)
    service.py        пошук + аналітика (SQL)
  jobs.py             RQ-джоби (обгортки над scraper)
  queue.py            RQ factory
  workers/
    worker.py         RQ worker
    scheduler.py      rq-scheduler з cron-ами
  templates/ static/  HTMX UI + Chart.js
  migrations/         Alembic
```

## Траєкторія зростання

Коли постів стане >1M і PG FTS почне тиснути:
1. Замінити `search_vector` + пошук у `app/search/service.py` на Manticore/Meilisearch.
2. Основна БД і `post_metrics_history` лишаються без змін.
3. Скрейпер не чіпати.
