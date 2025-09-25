# Camo-fleet

Минимальный набор сервисов для запуска Camoufox/Firefox сессий с live-просмотром через VNC.
Архитектура построена на sidecar-паттерне: воркер отвечает за API, а браузерный слой вынесен в отдельный
runner-контейнер. Репозиторий содержит четыре приложения и Kubernetes-манифесты для k3s кластера:

- **worker** — API-воркер, который проксирует запросы к локальному Camoufox runner'у и отдаёт `wsEndpoint`.
- **runner** — сервис, запускающий Camoufox и управляющий Playwright server'ом; выпускается в двух вариантах
  образов (headless и с VNC/noVNC).
- **control-plane** — облегчённый оркестратор, проксирующий HTTP-запросы к воркерам и предоставляющий
  единый REST API для UI.
- **ui** — React SPA с панелью: список сессий, запуск новых и ссылки на WebSocket/VNC подключения; данные обновляются периодическим REST polling'ом.

## Возможности

- Direct-сессии (`wsEndpoint`) для Camoufox/Firefox (антидетект).
- TTL и авто-завершение простаивающих сессий.
- Простое round-robin распределение сессий между воркерами/runner'ами.
- Live-экран через VNC/noVNC слой (включается флагом для воркеров с поддержкой VNC).
- Helm chart разворачивает все сервисы, оставляя публикацию наружу через ingress/Traefik на ваше
  усмотрение. Ниже описано, как воспроизвести прежние маршруты вручную с пояснениями по каждому
  значению.
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

## Развёртывание в k3s через Helm

Скрипт ниже автоматизирует подготовку образов и установку Helm release `camofleet` в кластере k3s.
Ingress/IngressRoute объекты теперь не создаются чартом автоматически, поэтому после установки
воспользуйтесь инструкциями из раздела «Ручное создание Traefik IngressRoute».

```bash
#!/usr/bin/env bash
set -euo pipefail

# --- настройте под себя ---
GIT_URL="https://github.com/NeoGrAIph/camo-fleet.git" # адрес репозитория
WORKDIR="$HOME/helm/repo/camo-fleet"                  # куда клонировать
NAMESPACE="camofleet"                                 # namespace в k3s
CONTAINERD_REF_PREFIX="docker.io/library"             # префикс ref при импорте в containerd
IMAGES=(
  "camofleet-runner:docker/Dockerfile.runner"
  "camofleet-runner-vnc:docker/Dockerfile.runner-vnc"
  "camofleet-worker:docker/Dockerfile.worker"
  "camofleet-control:docker/Dockerfile.control"
  "camofleet-ui:docker/Dockerfile.ui"
)

# --- функции ---
log() {
  echo -e "\033[1;32m[INFO]\033[0m $*"
}

err() {
  echo -e "\033[1;31m[ERROR]\033[0m $*" >&2
}

# --- подготовка репозитория ---
mkdir -p "$(dirname "$WORKDIR")"
if [ ! -d "$WORKDIR/.git" ]; then
  log "Клонирование репозитория..."
  git clone "$GIT_URL" "$WORKDIR"
else
  log "Обновление репозитория..."
  cd "$WORKDIR"
  git fetch --all
  git reset --hard origin/main
fi
cd "$WORKDIR"

# --- сборка образов ---
for mapping in "${IMAGES[@]}"; do
  IMAGE="${mapping%%:*}"
  DOCKERFILE="${mapping#*:}"
  IMAGE_TAG="${IMAGE}:latest"
  CONTAINERD_REF="${CONTAINERD_REF_PREFIX}/${IMAGE_TAG}"

  log "Удаление старого образа $IMAGE_TAG (если есть)..."
  sudo ctr -n k8s.io images rm "${CONTAINERD_REF}" || true
  docker rmi "${IMAGE_TAG}" || true

  log "Сборка образа $IMAGE_TAG..."
  docker build -t "${IMAGE_TAG}" -f "${DOCKERFILE}" .

  log "Импорт образа $IMAGE_TAG в containerd..."
  docker save "${IMAGE_TAG}" -o "${IMAGE}.tar"
  sudo ctr -n k8s.io images import "${IMAGE}.tar"
  rm -f "${IMAGE}.tar"
done

# --- значения Helm ---
HELM_ARGS=(
  --namespace "${NAMESPACE}"
  --create-namespace
)

# --- деплой через helm ---
log "Установка/обновление helm release camofleet..."
helm upgrade --install camofleet deploy/helm/camo-fleet "${HELM_ARGS[@]}"

log "Готово ✅"
```

### Ручное создание Traefik IngressRoute

Helm release публикует сервисы `camofleet-camo-fleet-ui`, `camofleet-camo-fleet-control` и
`camofleet-camo-fleet-worker-vnc` (для релиза `camofleet`). Такие имена формируются функциями в
[`deploy/helm/camo-fleet/templates/_helpers.tpl`](deploy/helm/camo-fleet/templates/_helpers.tpl), а
порты (80 для UI, 9000 для control, 8080 + диапазоны VNC) заданы в сервисных шаблонах
[`ui-service.yaml`](deploy/helm/camo-fleet/templates/ui-service.yaml),
[`control-service.yaml`](deploy/helm/camo-fleet/templates/control-service.yaml) и
[`worker-vnc-service.yaml`](deploy/helm/camo-fleet/templates/worker-vnc-service.yaml). Используйте
эти значения при создании Traefik-маршрутов.

1. **HTTP IngressRoute для UI и API.** UI отдает SPA с корня (`/`), а API control-plane слушает на
   `/api`. Воспроизведите прежнюю конфигурацию Helm через `IngressRoute`:

   ```yaml
   apiVersion: traefik.io/v1alpha1
   kind: IngressRoute
   metadata:
     name: camofleet
     namespace: camofleet
   spec:
     entryPoints:
       - websecure            # подставьте ваши entryPoints
     routes:
       - match: Host(`camofleet.example.com`) && PathPrefix(`/`)
         kind: Rule
         services:
           - name: camofleet-camo-fleet-ui
             port: 80
       - match: Host(`camofleet.example.com`) && PathPrefix(`/api`)
         kind: Rule
         services:
           - name: camofleet-camo-fleet-control
             port: 9000
     tls:
       secretName: camofleet-tls  # опционально: certResolver вместо secretName
   ```

2. **Middleware для VNC.** VNC-гейтвей теперь обслуживает все подключения через единый порт
   `6900`. Traefik снимает префикс `/vnc/{id}`, а приложение извлекает идентификатор сессии из
   заголовка `X-Forwarded-Prefix`. Настройте `StripPrefixRegex`:

   ```yaml
   apiVersion: traefik.io/v1alpha1
   kind: Middleware
   metadata:
     name: camofleet-worker-vnc-strip
     namespace: camofleet
   spec:
     stripPrefixRegex:
       regex:
         - ^/vnc/[0-9]+
   ```

3. **IngressRoute для VNC и WebSocket.** Сервис `worker-vnc` слушает HTTP на `8080`, прокси на
   `6900` и raw-VNC на `5900-5904`. Создайте маршрут для страницы noVNC и WebSocket-прокси (обратите
   внимание на абсолютный `/websockify`):

   ```yaml
   apiVersion: traefik.io/v1alpha1
   kind: IngressRoute
   metadata:
     name: camofleet-worker-vnc
     namespace: camofleet
    spec:
      entryPoints:
        - websecure
      routes:
        - match: Host(`camofleet.example.com`) && PathPrefix(`/vnc/`)
          kind: Rule
          middlewares:
            - name: camofleet-worker-vnc-strip
          services:
            - name: camofleet-camo-fleet-worker-vnc
              port: 6900
      tls:
        secretName: camofleet-tls

    ---
    apiVersion: traefik.io/v1alpha1
    kind: IngressRoute
    metadata:
      name: camofleet-worker-vnc-websockify
      namespace: camofleet
    spec:
      entryPoints:
        - websecure
      routes:
        - match: Host(`camofleet.example.com`) && PathPrefix(`/websockify`)
          kind: Rule
          services:
            - name: camofleet-camo-fleet-worker-vnc
              port: 6900
      tls:
        secretName: camofleet-tls
   ```

4. **Передача публичных URL в control-plane.** Control-plane использует JSON в
   `CONTROL_WORKERS` для отдачи VNC-ссылок UI. Чарт по умолчанию формирует их из сервисных адресов,
   но для внешнего ingress задайте overrides, сохранив плейсхолдер `{id}` (это идентификатор VNC,
   соответствующий порту `590x`), например:

   ```sh
   helm upgrade --install camofleet deploy/helm/camo-fleet \
     --namespace camofleet --create-namespace \
    --set "workerVnc.controlOverrides.ws=wss://camofleet.example.com/websockify?token={id}" \
    --set "workerVnc.controlOverrides.http=https://camofleet.example.com/vnc/{id}"
   ```

   Логика формирования JSON хранится в
   [`control-configmap.yaml`](deploy/helm/camo-fleet/templates/control-configmap.yaml): при наличии
   overrides они напрямую попадают в конфиг, иначе используются внутрикластерные адреса
  `ws://camofleet-camo-fleet-worker-vnc:6900/websockify?token={id}` и
  `http://camofleet-camo-fleet-worker-vnc:6900/vnc/{id}`.


Переиспользуйте примеры из каталога [`deploy/traefik`](deploy/traefik) как шаблон и адаптируйте
host/entryPoints/TLS под свою инфраструктуру.

## Архитектура взаимодействия

### Потоки запросов

1. **UI → Control-plane.** Клиентское приложение запрашивает `/workers` и `/sessions` у control-plane каждые 5 секунд. При действиях пользователя (создание, touch, завершение) отправляются POST/DELETE запросы на REST API. WebSocket-подключения к `ws_endpoint` проходят через control-plane.
2. **Control-plane → Worker.** Control-plane использует общий пул `httpx.AsyncClient` (см. `control-plane/camofleet_control/service.py`) для проксирования вызовов. Метрики (`Histogram`, `Counter`, `Gauge`) отражают время и успешность каждого обращения.
3. **Worker → Runner.** Worker валидирует запросы, применяет значения по умолчанию и проксирует их к локальному runner'у. Для WebSocket трафика используется общий мост `shared.websocket_bridge`.
4. **Runner.** Управляет жизненным циклом Camoufox, поддерживает prewarm пул, TTL, VNC toolchain и выдаёт `ws_endpoint`/`vnc_info` для каждой сессии.

### Основные модели

- **Runner → Worker:** `SessionSummary` и `SessionDetail` (`runner/camoufox_runner/models.py`). Поле `vnc` показывает, запущена ли VNC-надстройка для сессии, а `vnc_info` содержит словарь с ключами `ws`, `http`, `password_protected`.
- **Worker → Control-plane:** `SessionDetail` (`worker/camofleet_worker/models.py`). Worker конвертирует `vnc` в `vnc_enabled` (булево) и прокидывает `vnc_info` как поле `vnc` для дальнейшей публикации наружу.
- **Control-plane → UI:** `SessionDescriptor` и `CreateSessionResponse` (`control-plane/camofleet_control/models.py`). Дополнительно control-plane вычисляет публичные `ws_endpoint` и, если заданы, подменяет базовые URL VNC.

### Общие модули

- `shared.websocket_bridge` — двунаправленный мост FastAPI WebSocket ↔ `websockets.WebSocketClientProtocol`, используемый и воркером, и control-plane.
- `shared.tests` — юнит-тесты общих утилит (при наличии) и вспомогательная инфраструктура.

### Live-обновления UI

UI не использует SSE: актуальность данных поддерживается 5-секундным polling'ом REST API. Интерфейс отображает состояние последнего обновления, а все критичные действия (создание, touch, завершение) сразу обновляют локальное состояние без ожидания очередного опроса.

### Поля VNC в API

- `vnc` — опциональный флаг в запросах на создание и в ответах runner'а, который показывает, что для сессии запущен VNC toolchain.
- `vnc_enabled` — булево поле в ответах worker/control-plane/UI, сигнализирующее, что предпросмотр доступен (комбинация `http`/`ws` ссылок не пустая).
- `vnc_info` — внутреннее поле runner'а с подробностями подключения (`ws`, `http`, `password_protected`); worker передаёт его наружу как поле `vnc`.

## Стиль кодирования

- Python-код форматируется [Black](https://black.readthedocs.io/) с длиной строки 100 символов.
- Для статического анализа используется [Ruff](https://docs.astral.sh/ruff/) (правила: `E`, `F`, `W`, `I`, `B`, `UP`).
- Перед коммитом выполните автоформатирование и проверку:

  ```bash
  pip install black ruff
  black .
  ruff check .
  ```

CI проверяет, что оба инструмента выполнены (`black --check .` и `ruff check .`).

## Локальный запуск

Все сервисы загружают переменные окружения из файла `.env`, расположенного в корне репозитория. Такой
механизм одинаково работает как при запуске модулей напрямую (`python -m camofleet_worker`,
`python -m camofleet_control`, `python -m camoufox_runner`), так и внутри Docker/Compose окружений, где
корень проекта примонтирован в контейнер.

### Полностью в Docker (без Python на хосте)

1. Установите [Docker](https://docs.docker.com/get-docker/) и Docker Compose.
2. Скопируйте `.env.example` в `.env`, чтобы переопределить базовые адреса VNC/runner'ов локально:
   ```bash
   cp .env.example .env
   ```
3. Запустите локальное окружение с дев-зависимостями:
   ```bash
   docker compose -f docker-compose.dev.yml up --build
   ```
   Будут собраны образы Camoufox runner'ов, воркеров, control-plane и UI. По умолчанию поднимаются два воркера:
   headless и VNC (с собственными runner sidecar'ами). Дополнительных зависимостей на хосте не нужно.
4. После запуска:
   - UI: `http://localhost:5173`
   - Control-plane API: `http://localhost:9000`
   - Control-plane metrics: `http://localhost:9000/metrics`
   - Headless worker API: `http://localhost:8080`
   - VNC worker API: `http://localhost:8081`
   - noVNC: `http://localhost:69xx` (`ws://localhost:69xx`) — порт выдаётся динамически из диапазона 6900–6999
5. Тесты также можно прогнать внутри контейнеров:
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
   Copy-Item .env.example .env
   ```
   (значения можно адаптировать под локальные адреса runner'ов/воркеров).
3. Запустите окружение:
   ```powershell
   cd path\to\Camo-fleet
   docker compose up --build
   ```
   Первая сборка займёт время (Playwright качает браузеры ~1–2 ГБ).
4. После старта сервисов:
   - UI: `http://localhost:8080`
   - Control-plane API: `http://localhost:9000`
  - noVNC: просмотр доступен через `http://localhost:6900/vnc/<id>` (гейтвей определяет целевой VNC по идентификатору)
5. Для остановки окружения выполните:
   ```powershell
   docker compose down
   ```

## Аутентификация и доступ

- Вся панель и API теперь доступны без аутентификации — маршруты Traefik не используют Keycloak middleware и пропускают запросы напрямую.
- Если требуется ограничить доступ, настройте собственные middleware/ingress-правила поверх базовых манифестов в `deploy/k8s` или `deploy/traefik`.

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

В результате будут созданы namespace `camofleet`, деплойменты/сервисы для всех компонентов и ingress с TLS. Для
локальных override переменных (например, `CONTROL_WORKERS`) можно использовать значения из `.env.example`,
передавая их через `envFrom`/ConfigMap или Secrets при необходимости.

## Пример .env

Файл `.env.example` содержит минимальные значения для runner'ов, воркеров и control-plane. Скопируйте его в `.env`
и адаптируйте под свою инфраструктуру перед запуском Docker Compose или локальных манифестов:

```bash
cp .env.example .env
```

Поле `CONTROL_WORKERS` ожидает JSON-массив объектов. Ключевые поля:

- `name` — человеко-понятное имя воркера, отображается в UI и логах.
- `url` — адрес HTTP API воркера.
- `supports_vnc` — умеет ли воркер обрабатывать VNC-сессии (`true`/`false`).
- `vnc_ws` — базовый WebSocket URL предпросмотра (обязателен, если `supports_vnc=true`).
- `vnc_http` — HTTP URL к noVNC iframe (опционально, если требуется веб-доступ).

Значения `vnc_ws` и `vnc_http` поддерживают плейсхолдеры `{id}` и `{host}`. Плейсхолдеры могут
использоваться в хосте, пути или query-строке: control-plane подставит фактический идентификатор
VNC-сессии, сохраняя остальные части URL. Это позволяет настраивать, например, публичные прокси
через единый домен (`https://public.example/proxy/{id}`) или динамические поддомены
(`wss://vnc-{id}.example`).

## Переменные окружения

### Runner

| Переменная | Значение по умолчанию | Описание |
| ---------- | --------------------- | -------- |
| `RUNNER_CORS_ORIGINS` | `['*']` | Список origin'ов (JSON-массив или через запятую) для CORS. Используйте конкретные домены в production; значение `*` автоматически отключает `allow_credentials`. |
| `RUNNER_VNC_WS_BASE` | `None` | Базовый адрес (со схемой и хостом) внутреннего VNC-гейтвея, например `ws://camofleet-worker-vnc:6900`. |
| `RUNNER_VNC_HTTP_BASE` | `None` | Аналогично `RUNNER_VNC_WS_BASE`, но для HTTP-страницы (`http://camofleet-worker-vnc:6900`). |
| `RUNNER_VNC_DISPLAY_MIN` / `RUNNER_VNC_DISPLAY_MAX` | `100` / `199` | Диапазон виртуальных `DISPLAY`, выделяемых Xvfb. |
| `RUNNER_VNC_PORT_MIN` / `RUNNER_VNC_PORT_MAX` | `5900` / `5999` | Диапазон TCP-портов для `x11vnc`. |
| `RUNNER_VNC_WS_PORT_MIN` / `RUNNER_VNC_WS_PORT_MAX` | `6900` / `6999` | Диапазон идентификаторов VNC-сессий (используется гейтвеем для сопоставления с портами `x11vnc`). |
| `RUNNER_VNC_RESOLUTION` | `1920x1080x24` | Разрешение виртуального дисплея. |
| `RUNNER_VNC_WEB_ASSETS_PATH` | `/usr/share/novnc` | Устаревшее значение (используется только при `RUNNER_VNC_LEGACY=1`). |
| `RUNNER_VNC_LEGACY` | `0` | При значении `1` включает прежний режим с одним глобальным VNC-сервером (`vnc-start.sh`). |
| `RUNNER_PREWARM_HEADLESS` | `1` | Количество тёплых резервов без VNC (используется headless=true). |
| `RUNNER_PREWARM_VNC` | `1` | Количество тёплых резервов c VNC (Xvfb+x11vnc); автоматически отключается, если инструменты VNC недоступны в образе. |
| `RUNNER_PREWARM_CHECK_INTERVAL_SECONDS` | `2.0` | Период проверки/дополнения пула тёплых резервов. |
| `RUNNER_START_URL_WAIT` | `load` | Как долго ждать загрузку `start_url`: `none` (не грузить), `domcontentloaded`, `load`. При значении `none` навигация выполняется клиентом и стартовая вкладка останется пустой (включая VNC). |

Порты и `DISPLAY` выделяются на каждую сессию. Убедитесь, что выбранные диапазоны проброшены наружу (Docker: `6900:6900` для гейтвея и `5900-5999:5900-5999` для raw-VNC; Kubernetes — Ingress на `/vnc/{id}` и `/websockify`). Для headless‑резервов prewarm используется `headless=true`.

### Worker

| Переменная              | Значение по умолчанию | Описание                                   |
| ----------------------- | --------------------- | ------------------------------------------ |
| `WORKER_CORS_ORIGINS`   | `['*']`                | Список origin'ов для CORS (JSON/CSV). При `*` `allow_credentials` отключается; в production задайте конкретные хосты UI/API. |
| `WORKER_PORT`           | `8080`                | Порт HTTP API.                             |
| `WORKER_SESSION_DEFAULTS__HEADLESS` | `false` | Значение по умолчанию для headless.        |
| `WORKER_RUNNER_BASE_URL`| `http://127.0.0.1:8070` | Адрес sidecar runner'а внутри Pod/Compose. |
| `WORKER_SUPPORTS_VNC`   | `false`               | Помечает воркер как умеющий работать с VNC. |

### VNC gateway

| Переменная                | Значение по умолчанию | Описание |
| ------------------------- | --------------------- | -------- |
| `HTTP_PORT`               | `6900`                | Порт HTTP/WS сервера. |
| `VNC_DEFAULT_HOST`        | `127.0.0.1`           | Хост, на котором доступен `x11vnc` (runner). |
| `VNC_WEB_RANGE`           | `6900-6904`           | Диапазон идентификаторов `<id>` для проксирования. |
| `VNC_BASE_PORT`           | `5900`                | Базовый порт VNC; фактический рассчитывается как `base + (id - min)`. |
| `VNC_MAP_JSON`            | пусто                 | JSON-объект с явными соответствиями `<id> → {host, port}`. |
| `WS_READ_TIMEOUT_MS`      | `120000`              | Таймаут чтения WebSocket. |
| `WS_WRITE_TIMEOUT_MS`     | `120000`              | Таймаут записи WebSocket. |
| `TCP_CONNECT_TIMEOUT_MS`  | `5000`                | Таймаут подключения к upstream. |
| `TCP_IDLE_TIMEOUT_MS`     | `300000`              | Максимальное время простоя соединения. |
| `MAX_CONCURRENT_SESSIONS` | `1000`                | Лимит одновременных прокси. |
| `SHUTDOWN_GRACE_MS`       | `30000`               | Время дренажа активных соединений при остановке. |

### Control-plane

| Переменная         | Значение по умолчанию | Описание                                         |
| ------------------ | --------------------- | ------------------------------------------------ |
| `CONTROL_CORS_ORIGINS` | `['*']`              | Origin'ы, которым разрешён доступ к API. При `*` `allow_credentials` отключается; для production перечислите конкретные домены. |
| `CONTROL_WORKERS`  | см. config            | JSON-массив с воркерами (`name`, `url`, `supports_vnc`, `vnc_ws`, `vnc_http`). Поля `vnc_ws` и `vnc_http` поддерживают плейсхолдеры `{id}` и `{host}` для подстановки адреса активной сессии. |
| `CONTROL_PORT`     | `9000`                | Порт HTTP API.                                   |
| `CONTROL_METRICS_ENDPOINT` | `/metrics`     | Путь, на котором публикуются Prometheus-метрики. |

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
- `POST /sessions` — создание новой сессии. Поддерживает `vnc=true` для запроса VNC-предпросмотра, `start_url` и `start_url_wait` (при значениях `domcontentloaded` / `load` раннер откроет URL асинхронно, при `none` страница не будет загружена автоматически — например, VNC покажет пустой профиль, пока вы не перейдёте на адрес вручную).
- `GET /sessions/{id}` — детали, включая словарь `vnc` (runner `vnc_info`), булев флаг `vnc_enabled` и режим ожидания `start_url_wait`.
- `POST /sessions/{id}/touch` — продлить TTL.
- `DELETE /sessions/{id}` — завершение.

### Control-plane

- `GET /workers` — статусы всех воркеров.
- `GET /sessions` — агрегированный список: каждый элемент содержит публичный `ws_endpoint`, флаг `vnc_enabled` и словарь `vnc` с конечными точками предпросмотра.
- `POST /sessions` — создать сессию на выбранном воркере или через round-robin (прокидывает `vnc`, `start_url` и `start_url_wait` дальше к воркерам и runner'у).
- `GET /sessions/{worker}/{id}` — детали.
- `DELETE /sessions/{worker}/{id}` — завершение.

## Лицензия

MIT.
