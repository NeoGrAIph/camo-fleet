# Camo-fleet

Минимальный набор сервисов для запуска Camoufox/Firefox сессий с live-просмотром через VNC.
Архитектура построена на sidecar-паттерне: воркер отвечает за API, а браузерный слой вынесен в отдельный
runner-контейнер. Репозиторий содержит четыре приложения и Kubernetes-манифесты для k3s кластера:

- **worker** — API-воркер, который проксирует запросы к локальному Camoufox runner'у и отдаёт `wsEndpoint`.
- **runner** — сервис, запускающий Camoufox и управляющий Playwright server'ом; выпускается в двух вариантах
  образов (headless и с VNC/noVNC).
- **control-plane** — облегчённый оркестратор, проксирующий HTTP-запросы к воркерам и предоставляющий
  единый REST API для UI.
- **ui** — React SPA с панелью: список сессий, запуск новых и ссылки на WebSocket/VNC подключения.

## Возможности

- Direct-сессии (`wsEndpoint`) для Camoufox/Firefox (антидетект).
- TTL и авто-завершение простаивающих сессий.
- Простое round-robin распределение сессий между воркерами/runner'ами.
- Live-экран через VNC/noVNC слой (включается флагом для воркеров с поддержкой VNC).
- REST API без SSE/RBAC/Managed DSL — только базовые CRUD операции над сессиями.

## Структура

```
Camo-fleet/
├── control-plane/         # FastAPI control-plane
├── deploy/k8s/            # k3s-ready manifests
├── docker/                # Dockerfile'ы и entrypoint'ы
├── runner/                # Camoufox runner sidecar
├── ui/                    # Vite + React SPA
└── worker/                # API worker, проксирующий runner
```

## Локальный запуск

### Полностью в Docker (без Python на хосте)

1. Установите [Docker](https://docs.docker.com/get-docker/) и Docker Compose.
2. Запустите локальное окружение с дев-зависимостями:
   ```bash
   docker compose -f docker-compose.dev.yml up --build
   ```
   Будут собраны образы Camoufox runner'ов, воркеров, control-plane и UI. По умолчанию поднимаются два воркера:
   headless и VNC (с собственными runner sidecar'ами). Дополнительных зависимостей на хосте не нужно.
3. После запуска:
   - UI: `http://localhost:5173`
   - Control-plane API: `http://localhost:9000`
   - Headless worker API: `http://localhost:8080`
   - VNC worker API: `http://localhost:8081`
   - noVNC: `http://localhost:69xx` (`ws://localhost:69xx`) — порт выдаётся динамически из диапазона 6900–6999
4. Тесты также можно прогнать внутри контейнеров:
   ```bash
   docker compose -f docker-compose.dev.yml run --rm --entrypoint pytest worker
   docker compose -f docker-compose.dev.yml run --rm --entrypoint pytest control-plane
   ```
   При необходимости дополнительные команды можно выполнять через `docker compose run --rm --entrypoint bash <service>`.

### Нативный запуск (опционально)

1. Установите Python 3.11+, Node.js 20+ и Docker.
2. Соберите runner sidecar (Camoufox):
   ```bash
   pip install -e runner/
   python -m camoufox fetch
   ```
3. Запустите runner:
   ```bash
   python -m camoufox_runner
   ```
   По умолчанию API доступен на `http://127.0.0.1:8070`.
4. Worker:
   ```bash
   cd worker
   python -m camofleet_worker
   ```
   По умолчанию API доступен на `http://127.0.0.1:8080`. Для работы с VNC необходим runner с поддержкой VNC
   (запускаемый из образа `Dockerfile.runner-vnc`).
5. Control-plane:
   ```bash
   cd control-plane
   python -m camofleet_control
   ```
   По умолчанию сервис слушает `http://127.0.0.1:9000`. Список воркеров задаётся переменной `CONTROL_WORKERS`
   (см. `control-plane/camofleet_control/config.py`).
6. UI:
   ```bash
   cd ui
   npm install
   npm run dev
   ```
   Для проксирования API можно переопределить `VITE_API_ORIGIN` (по умолчанию `http://localhost:9000`).

## Docker Desktop (Windows)

Ниже описан полностью контейнеризованный сценарий для Docker Desktop на Windows (WSL2 backend).

1. Установите [Docker Desktop](https://www.docker.com/products/docker-desktop/) и убедитесь, что включён режим Linux Containers.
2. Склонируйте репозиторий и откройте PowerShell/Terminal от имени пользователя:
   ```powershell
   cd path\to\Camo-fleet
   docker compose up --build
   ```
   Первая сборка займёт время (Playwright качает браузеры ~1–2 ГБ).
3. После старта сервисов:
   - UI: `http://localhost:8080`
   - Control-plane API: `http://localhost:9000`
   - noVNC: предпросмотр в UI; фактический порт выбирается автоматически из диапазона `6900-6999`
4. Для остановки окружения выполните:
   ```powershell
   docker compose down
   ```

## Docker-образы

Сборка образов (замените `REGISTRY` на собственный реестр):

```bash
docker build -t REGISTRY/camofleet-runner:latest -f docker/Dockerfile.runner .
docker build -t REGISTRY/camofleet-runner-vnc:latest -f docker/Dockerfile.runner-vnc .
docker build -t REGISTRY/camofleet-worker:latest -f docker/Dockerfile.worker .
docker build -t REGISTRY/camofleet-control:latest -f docker/Dockerfile.control .
docker build -t REGISTRY/camofleet-ui:latest -f docker/Dockerfile.ui .
```

Runner-образы содержат Camoufox + Playwright server: headless (`Dockerfile.runner`) и с VNC (`Dockerfile.runner-vnc`).
Worker-образ запускает только API (`python -m camofleet_worker`) и проксирует запросы в соседний runner.
UI-образ собирается в статический билд и обслуживается nginx с проксированием `/api` на control-plane.

## Kubernetes (k3s)

Манифесты расположены в `deploy/k8s`. Перед применением замените `REGISTRY/...` на ваши образы и
обновите `Ingress` хостнеймы. Затем выполните:

```bash
kubectl apply -k deploy/k8s
```

В результате будут созданы namespace `camofleet`, деплойменты/сервисы для всех компонентов и ingress с TLS.

## Переменные окружения

### Runner

| Переменная | Значение по умолчанию | Описание |
| ---------- | --------------------- | -------- |
| `RUNNER_CORS_ORIGINS` | `['*']` | Список origin'ов (JSON-массив или через запятую) для CORS. Используйте конкретные домены в production; значение `*` автоматически отключает `allow_credentials`. |
| `RUNNER_VNC_WS_BASE` | `None` | Базовый адрес (со схемой и хостом) для генерации WebSocket URL предпросмотра. Порт будет подменён на выделенный для конкретной сессии. |
| `RUNNER_VNC_HTTP_BASE` | `None` | Аналогично `RUNNER_VNC_WS_BASE`, но для noVNC iframe (`/vnc.html`). |
| `RUNNER_VNC_DISPLAY_MIN` / `RUNNER_VNC_DISPLAY_MAX` | `100` / `199` | Диапазон виртуальных `DISPLAY`, выделяемых Xvfb. |
| `RUNNER_VNC_PORT_MIN` / `RUNNER_VNC_PORT_MAX` | `5900` / `5999` | Диапазон TCP-портов для `x11vnc`. |
| `RUNNER_VNC_WS_PORT_MIN` / `RUNNER_VNC_WS_PORT_MAX` | `6900` / `6999` | Диапазон TCP-портов для websockify/noVNC. |
| `RUNNER_VNC_RESOLUTION` | `1920x1080x24` | Разрешение виртуального дисплея. |
| `RUNNER_VNC_WEB_ASSETS_PATH` | `/usr/share/novnc` | Путь к статике noVNC; если отсутствует, websockify раздаёт только WebSocket. |
| `RUNNER_VNC_LEGACY` | `0` | При значении `1` включает прежний режим с одним глобальным VNC-сервером (`vnc-start.sh`). |
| `RUNNER_PREWARM_HEADLESS` | `1` | Количество тёплых резервов без VNC (используется headless=true). |
| `RUNNER_PREWARM_VNC` | `1` | Количество тёплых резервов c VNC (Xvfb+x11vnc+websockify); автоматически отключается, если инструменты VNC недоступны в образе. |
| `RUNNER_PREWARM_CHECK_INTERVAL_SECONDS` | `2.0` | Период проверки/дополнения пула тёплых резервов. |
| `RUNNER_START_URL_WAIT` | `load` | Как долго ждать загрузку `start_url`: `none` (не грузить), `domcontentloaded`, `load`. При значении `none` навигация выполняется клиентом и стартовая вкладка останется пустой (включая VNC). |

Порты и `DISPLAY` выделяются на каждую сессию. Убедитесь, что выбранные диапазоны проброшены наружу (Docker: `6900-6999:6900-6999`, `5900-5999:5900-5999`; Kubernetes — отдельный Ingress/Service или hostNetwork). Для headless‑резервов prewarm используется `headless=true`.

### Worker

| Переменная              | Значение по умолчанию | Описание                                   |
| ----------------------- | --------------------- | ------------------------------------------ |
| `WORKER_CORS_ORIGINS`   | `['*']`                | Список origin'ов для CORS (JSON/CSV). При `*` `allow_credentials` отключается; в production задайте конкретные хосты UI/API. |
| `WORKER_PORT`           | `8080`                | Порт HTTP API.                             |
| `WORKER_SESSION_DEFAULTS__HEADLESS` | `false` | Значение по умолчанию для headless.        |
| `WORKER_RUNNER_BASE_URL`| `http://127.0.0.1:8070` | Адрес sidecar runner'а внутри Pod/Compose. |
| `WORKER_SUPPORTS_VNC`   | `false`               | Помечает воркер как умеющий работать с VNC. |

### Control-plane

| Переменная         | Значение по умолчанию | Описание                                         |
| ------------------ | --------------------- | ------------------------------------------------ |
| `CONTROL_CORS_ORIGINS` | `['*']`              | Origin'ы, которым разрешён доступ к API. При `*` `allow_credentials` отключается; для production перечислите конкретные домены. |
| `CONTROL_WORKERS`  | см. config            | JSON-массив с воркерами: `name`, `url`, `supports_vnc`, `vnc_ws`, `vnc_http`. |
| `CONTROL_PORT`     | `9000`                | Порт HTTP API.                                   |

UI не требует переменных окружения — все настройки кодируются в nginx.

## Тестирование

- `worker`: `pytest` — проверяет менеджер сессий и auto-cleanup.
- `control-plane`: `pytest` — покрытие round-robin логики.

Рекомендуемый (контейнерный) запуск:

```bash
docker compose -f docker-compose.dev.yml run --rm --entrypoint pytest worker
docker compose -f docker-compose.dev.yml run --rm --entrypoint pytest control-plane
```

Нативно:

```bash
cd worker && pip install -e .[dev] && pytest
cd control-plane && pip install -e .[dev] && pytest
```

## API

### Worker

- `GET /health` — состояние сервиса.
- `GET /sessions` — список активных сессий.
- `POST /sessions` — создание новой сессии (поддерживает поля `start_url` и `start_url_wait`; для значений `domcontentloaded` и `load` раннер откроет URL асинхронно, при `none` страница не будет загружена автоматически — например, VNC покажет пустой профиль, пока вы не перейдёте на адрес вручную).
- `GET /sessions/{id}` — детали, включая VNC ссылки и режим ожидания `start_url_wait`.
- `POST /sessions/{id}/touch` — продлить TTL.
- `DELETE /sessions/{id}` — завершение.

### Control-plane

- `GET /workers` — статусы всех воркеров.
- `GET /sessions` — агрегированный список.
- `POST /sessions` — создать сессию на выбранном воркере или через round-robin (прокидывает `start_url_wait` дальше к воркерам и runner'у).
- `GET /sessions/{worker}/{id}` — детали.
- `DELETE /sessions/{worker}/{id}` — завершение.

## Лицензия

MIT.
