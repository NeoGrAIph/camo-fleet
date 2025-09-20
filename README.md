# Camo-fleet

Минимальный набор сервисов для запуска headful Playwright сессий с live-просмотром через VNC.
Репозиторий содержит три приложения и Kubernetes-манифесты для k3s кластера:

- **worker** — FastAPI сервис, поднимающий браузерные сессии и отдающий `wsEndpoint`. Внутри контейнера
  включены Xvfb + x11vnc + websockify, так что live-экран доступен по WebSocket (noVNC).
- **control-plane** — облегчённый оркестратор, проксирующий HTTP-запросы к воркерам и предоставляющий
  единый REST API для UI.
- **ui** — React SPA с базовой панелью: список сессий, запуск новых и ссылки на WebSocket/VNC подключения.

## Возможности

- Direct-сессии (`wsEndpoint`) для Chromium/Firefox/WebKit.
- TTL и авто-завершение простаивающих сессий.
- Простое round-robin распределение сессий между воркерами.
- Live-экран через встроенный VNC/WebSocket слой (без отключения).
- REST API без SSE/RBAC/Managed DSL — только базовые CRUD операции над сессиями.

## Структура

```
Camo-fleet/
├── control-plane/         # FastAPI control-plane
├── deploy/k8s/            # k3s-ready manifests
├── docker/                # Dockerfile'ы и entrypoint'ы
├── ui/                    # Vite + React SPA
└── worker/                # FastAPI worker
```

## Локальный запуск

### Полностью в Docker (без Python на хосте)

1. Установите [Docker](https://docs.docker.com/get-docker/) и Docker Compose.
2. Запустите локальное окружение с дев-зависимостями:
   ```bash
   docker compose -f docker-compose.dev.yml up --build
   ```
   Команда соберёт образы с `pip install -e .[dev]`, скачает Playwright браузеры и поднимет три контейнера
   (worker, control-plane и Vite dev-сервер). Перезапуск контейнеров не требует установки Python или Node.js на хосте.
3. После запуска:
   - UI: `http://localhost:5173`
   - Control-plane API: `http://localhost:9000`
   - Worker API: `http://localhost:8080`
   - noVNC: `ws://localhost:6900` и `http://localhost:6900`
4. Тесты также можно прогнать внутри контейнеров:
   ```bash
   docker compose -f docker-compose.dev.yml run --rm --entrypoint pytest worker
   docker compose -f docker-compose.dev.yml run --rm --entrypoint pytest control-plane
   ```
   При необходимости дополнительные команды можно выполнять через `docker compose run --rm --entrypoint bash <service>`.

### Нативный запуск (опционально)

1. Установите Python 3.11+, Node.js 20+ и Docker.
2. Установите Playwright браузеры:
   ```bash
   pip install -e worker/
   python -m playwright install --with-deps
   ```
3. Worker:
   ```bash
   cd worker
   python -m camofleet_worker
   ```
   По умолчанию API доступен на `http://127.0.0.1:8080`, а VNC websocket — на `ws://127.0.0.1:6900`.
4. Control-plane:
   ```bash
   cd control-plane
   python -m camofleet_control
   ```
   По умолчанию сервис слушает `http://127.0.0.1:9000`. Список воркеров задаётся переменной `CONTROL_WORKERS`
   (см. `control-plane/camofleet_control/config.py`).
5. UI:
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
   - noVNC: ссылки появляются в UI; базовый адрес `http://localhost:6900`
4. Для остановки окружения выполните:
   ```powershell
   docker compose down
   ```

## Docker-образы

Сборка образов (замените `REGISTRY` на собственный реестр):

```bash
docker build -t REGISTRY/camofleet-worker:latest -f docker/Dockerfile.worker .
docker build -t REGISTRY/camofleet-control:latest -f docker/Dockerfile.control .
docker build -t REGISTRY/camofleet-ui:latest -f docker/Dockerfile.ui .
```

Worker-образ содержит VNC и запускает `python -m camofleet_worker`, одновременно стартуя noVNC слой.
UI-образ собирается в статический билд и обслуживается nginx с проксированием `/api` на control-plane.

## Kubernetes (k3s)

Манифесты расположены в `deploy/k8s`. Перед применением замените `REGISTRY/...` на ваши образы и
обновите `Ingress` хостнеймы. Затем выполните:

```bash
kubectl apply -k deploy/k8s
```

В результате будут созданы namespace `camofleet`, деплойменты/сервисы для всех компонентов и ingress с TLS.

## Переменные окружения

### Worker

| Переменная              | Значение по умолчанию | Описание                                   |
| ----------------------- | --------------------- | ------------------------------------------ |
| `WORKER_PORT`           | `8080`                | Порт HTTP API.                             |
| `WORKER_SESSION_DEFAULTS__HEADLESS` | `false` | Запускать браузеры в headless режиме.      |
| `WORKER_VNC_WS_BASE`    | `null`                | Базовый URL для WebSocket (прописывается в Kubernetes). |
| `WORKER_VNC_HTTP_BASE`  | `null`                | Базовый HTTP URL для noVNC (для UI).       |

### Control-plane

| Переменная         | Значение по умолчанию | Описание                                         |
| ------------------ | --------------------- | ------------------------------------------------ |
| `CONTROL_WORKERS`  | см. config            | JSON-массив с воркерами: `name`, `url`, `vnc_ws`, `vnc_http`. |
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
- `POST /sessions` — создание новой сессии.
- `GET /sessions/{id}` — детали, включая VNC ссылки.
- `POST /sessions/{id}/touch` — продлить TTL.
- `DELETE /sessions/{id}` — завершение.

### Control-plane

- `GET /workers` — статусы всех воркеров.
- `GET /sessions` — агрегированный список.
- `POST /sessions` — создать сессию на выбранном воркере или через round-robin.
- `GET /sessions/{worker}/{id}` — детали.
- `DELETE /sessions/{worker}/{id}` — завершение.

## Лицензия

MIT.
