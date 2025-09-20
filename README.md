# Camo-fleet

**Camo-fleet** — это набор сервисов и образов для управления «флотом» браузерных сессий на базе **Camoufox**: создание/завершение сессий, live-обновления статуса, артефакты (HAR/видео/скрины), опциональный live-экран, API для интеграций и UI для операторов.

---

## Содержание

* [Архитектура](#архитектура)
* [Возможности](#возможности)
* [Быстрый старт](#быстрый-старт)
* [Репозитории и структура](#репозитории-и-структура)
* [Образы Docker](#образы-docker)
* [Конфигурация (ENV)](#конфигурация-env)
* [API](#api)
* [Мониторинг и логи](#мониторинг-и-логи)
* [Безопасность](#безопасность)
* [Разработка](#разработка)
* [Тестирование](#тестирование)
* [Деплой в Kubernetes](#деплой-в-kubernetes)
* [Дорожная карта](#дорожная-карта)
* [FAQ](#faq)
* [Лицензия](#лицензия)

---

## Архитектура

* **Workers (Python / FastAPI)** — запускают Camoufox/Playwright, создают сессии, пишут артефакты, отдают `wsEndpoint` (Direct) или исполняют команды (Managed), следят за TTL.
* **Control-plane (Node.js / TypeScript)** — реестр сессий, агрегация статуса, SSE `/events`, проксирование команд к воркерам, RBAC, presigned-URL для артефактов.
* **UI (SPA)** — панель оператора: список сессий (live), детали, Manual Run, артефакты, Kill, (опц.) VNC-просмотр.

> Принята модель **Вариант A**: воркеры на **Python**, control-plane и UI-backend на **TypeScript**.

---

## Возможности

* Создание сессий: **Direct** (получить `wsEndpoint`) и **Managed** (последовательности действий).
* Отпечатки (**auto / hybrid / manual**) через Camoufox/BrowserForge; согласование geoip/locale/tz/WebRTC.
* Артефакты: **HAR**, **видео**, **скриншоты** (S3/MinIO).
* Live-обновления статуса через **SSE** (/events); сводка через **/status**.
* (Опционально) **VNC** / WebSocket-туннель для live-экрана (noVNC).
* TTL/auto-kill, логи страницы и компактный network-лог.
* RBAC (viewer/operator/admin), audit событий (control-plane).

---

## Быстрый старт

### Локальная проверка образов

```bash
# базовый образ без VNC
docker build -t camo-fleet/runner-core -f docker/Dockerfile.runner-core .

# VNC-вариант (live-экран)
docker build -t camo-fleet/runner-vnc -f docker/Dockerfile.runner-vnc .

# smoke: версия Camoufox
docker run --rm camo-fleet/runner-core python -m camoufox version

# smoke: скриншот/HAR/видео
mkdir -p out out/video
docker run --rm -v "$PWD/out:/out" camo-fleet/runner-core bash -lc 'python - <<PY
from camoufox.sync_api import Camoufox
with Camoufox(headless=True) as b:
    ctx = b.new_context(record_har_path="/out/session.har.zip", record_har_mode="full", record_video_dir="/out/video")
    p = ctx.new_page(); p.goto("https://example.com"); p.screenshot(path="/out/smoke.png", full_page=True); ctx.close()
PY'
```

### Быстрый запуск сервисов (Dev)

```bash
# Control-plane (Node)
cd control-plane && cp .env.example .env && npm i && npm run dev

# Worker (Python)
cd worker && cp .env.example .env && pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

---

## Репозитории и структура

```
camo-fleet/
├─ README.md
├─ docker/
│  ├─ Dockerfile.runner-core
│  └─ Dockerfile.runner-vnc
├─ worker/                # Python/FastAPI (Camoufox/Playwright)
│  ├─ app/
│  ├─ requirements.txt
│  └─ openapi.worker.yaml
├─ control-plane/         # Node/TS (SSE, registry, RBAC, proxy)
│  ├─ src/
│  ├─ package.json
│  └─ openapi.control.yaml
├─ ui/                    # SPA (панель оператора)
│  ├─ src/
│  └─ package.json
└─ deploy/
   ├─ helm/
   │  ├─ camo-fleet-control/
   │  └─ camo-fleet-worker/
   └─ kustomize/
```

---

## Образы Docker

* `camo-fleet/runner-core` — Camoufox + Playwright + ffmpeg + шрифты/локали + либы для API/артефактов/метрик.
* `camo-fleet/runner-vnc` — всё из core + Xvfb + x11vnc + websockify + novnc.

> В образах используется пользователь **`pwuser`**. Для bind-mount используйте `chown` на хосте (`uid/gid 1001`) или `fsGroup` в k8s.

---

## Конфигурация (ENV)

Общее:

* `TZ` — часовой пояс (по умолчанию `Europe/Helsinki`).
* `LANG`, `LC_ALL` — локали (по умолчанию `en_US.UTF-8`).

Worker:

* `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_SECURE=true|false`
* `RUNNER_DATA=/data` — базовая директория артефактов/профилей.
* `IDLE_TTL_SEC` — время авто-завершения неактивной сессии.
* `ALLOW_DIRECT=true|false` — разрешить Direct-режим.
* `JWT_AUDIENCE`, `JWT_ISSUER`, `JWT_JWKS_URL` — если включён JWT-доступ.

Control-plane:

* `PORT` — порт API и SSE.
* `REGISTRY_URL` или параметры подключения к Redis/Postgres.
* `UI_ALLOWED_ORIGINS` — CORS.
* `OIDC_*` или `JWT_*` — аутентификация.
* `ARTIFACT_BASE_URL` — базовый URL для presigned-ссылок.

---

## API

* **Worker API** — `worker/openapi.worker.yaml`
  Основные ручки: `/health`, `/metrics`, `/sessions (POST/GET/DELETE)`, `/sessions/{id}`, `/sessions/{id}/actions`, `/artifacts`, `/logs`.
* **Control-plane API** — `control-plane/openapi.control.yaml`
  Основные ручки: `/status`, `/events` (SSE), `/sessions (GET/POST)`, `/sessions/{id} (GET/DELETE)`, `/artifacts`, `/logs`, `/vnc/{id}`.

> В UI используются `/status` и `/events` control-plane; UI **не** обращается к воркерам напрямую.

---

## Мониторинг и логи

* **/metrics** (Worker и Control-plane) — Prometheus text format.
* Логи: структурированные JSON-строки (python-json-logger / loguru на воркере; pino/winston — control-plane).
* Бизнес-метрики: число активных сессий, средняя длительность, p50/p95 навигации, объём артефактов, ошибки.

---

## Безопасность

* **JWT/OIDC** для API (bearer).
* **RBAC**: viewer/operator/admin (enforce на control-plane, дубляж на worker).
* Короткоживущие токены для `wsEndpoint`/`vnc`, presigned-URL для артефактов.
* Маскирование секретов в логах; запрет сырых cookies в ответах.

---

## Разработка

```bash
# Worker
cd worker
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
ruff check . ; pytest

# Control-plane
cd control-plane
npm i
npm run dev
npm test
```

Рекомендуемые инструменты: **pre-commit** (ruff/isort/black), **eslint + prettier**, **commitlint**.

---

## Тестирование

* **Unit**: модели и утилиты (валидация fingerprint/консистентность).
* **Integration**: жизненный цикл сессии, артефакты, TTL.
* **E2E**: мок-воркер ↔ control-plane ↔ UI, SSE-поток, RBAC.
* **Smoke**: запуск Camoufox, `goto`, `screenshot`, запись HAR/видео.

---

## Деплой в Kubernetes

Базовые фрагменты (Helm/values):

```yaml
# worker
securityContext:
  runAsNonRoot: true
  runAsUser: 1001
  runAsGroup: 1001
  fsGroup: 1001
  fsGroupChangePolicy: OnRootMismatch

# PVC и права через initContainer
initContainers:
- name: fix-perms
  image: busybox:1.36
  command: ["sh","-c","chown -R 1001:1001 /data && chmod -R u+rwX,g+rwX /data"]
  securityContext: { runAsUser: 0 }
  volumeMounts: [{ name: data, mountPath: /data }]
```

Ingress, HPA, ресурсы и лимиты — см. `deploy/helm/`.

---

## Дорожная карта

* **MVP**: Worker Direct + артефакты; Control-plane `/status`,`/events`; UI: список/детали/Manual Run.
* **Next**: Managed-DSL (минимум), RBAC/OIDC, presigned-URL, Helm-чарты, нагрузочные прогоны.
* **Later**: VNC-туннель по токенам, расширенная телеметрия, Chromium-адаптер, маскирование PII в артефактах, квоты и лимиты.

---

## FAQ

**Почему Firefox/Сamoufox, а не Chromium?**
Для задач с UA-Client Hints/CH планируем отдельный Chromium-адаптер. Основной поток идёт через Camoufox (уклон на анти-фп).

**Нужен ли root в контейнере?**
Нет. Образы настроены под `pwuser`. Для томов используйте `fsGroup`/`chown`, а не `chmod 777`.

**Можно ли всё запустить одной командой?**
Да, через docker-compose/Helm. В репозитории будут примеры.

---

## Лицензия

© Camo-fleet • Лицензия будет указана в корне проекта (`LICENSE`).
