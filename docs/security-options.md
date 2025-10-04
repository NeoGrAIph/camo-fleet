# Keycloak integration strategies for Camo-fleet

## Исходные условия

Camo-fleet разворачивает несколько сервисов: worker, runner, control-plane, UI и VNC-gateway. В Kubernetes-манифестах Traefik публикует UI по `/`, REST API по `/api`, а VNC проксируется по `/vnc`. В Docker Compose сервисы также работают без аутентификации для локальной разработки и отладки. Любое решение должно:

- защищать UI, REST API control-plane и noVNC проксирование при публикации через Traefik и Keycloak;
- оставаться отключаемым, чтобы кластер мог работать без Keycloak, а локальный запуск через Docker Compose не требовал дополнительных настроек;
- не ломать существующую архитектуру sidecar между worker и runner.

## Варианты реализации

### 1. Traefik ForwardAuth + Keycloak (рекомендуемый)

**Идея.** Использовать middleware Traefik ForwardAuth c проверкой сессий/токенов через Keycloak. ForwardAuth может обращаться к сервису `traefik-forward-auth` (или `oauth2-proxy` в режиме OIDC) и пропускать трафик на backend только после успешной проверки.

**Особенности.**

- UI, `/api` и `/vnc` защищаются на уровне Ingress. Для Kubernetes достаточно добавить middleware в `IngressRoute` или аннотации `traefik.ingress.kubernetes.io/router.middlewares`. Для Docker Compose остаётся базовая конфигурация без middleware.
- ForwardAuth сервис при успешной проверке добавляет заголовки (`X-Forwarded-User`, `Authorization: Bearer ...`), которые можно пробрасывать в control-plane/worker для дальнейшей авторизации.
- noVNC (WebSocket) корректно защищается, если включить `authResponseHeaders` и `trustForwardHeader=true` в middleware, а также добавить опцию `forwardingHeaders.insecure=true` для Traefik, чтобы проксировать `Authorization` в WebSocket рукопожатии.

**Плюсы.**

- Единственная точка аутентификации на уровне Traefik; сами сервисы остаются неизменными и продолжают работать без Keycloak.
- Конфигурация включается/выключается аннотациями в Kubernetes (`kustomize` overlay) без правок Docker Compose.
- ForwardAuth поддерживает как cookie-сессии, так и чистый OIDC flow, упрощая интеграцию с UI.

**Минусы.**

- Потребуется дополнительный сервис (`traefik-forward-auth` / `oauth2-proxy`), который нужно мониторить и масштабировать.
- Для тонкой авторизации (например, ограничение действий в API) придётся читать заголовки с пользовательскими ролями и реализовывать проверку в control-plane.

### 2. OAuth2 Proxy / Keycloak Gatekeeper как reverse proxy перед сервисами

**Идея.** Развернуть отдельный reverse proxy (например, `oauth2-proxy`, `keycloak-proxy` или `pomerium`) на каждый публичный endpoint (`ui`, `control-plane`, `vnc-gateway`). Прокси выполняет OIDC flow, выставляет авторизационные cookie и пробрасывает трафик дальше.

**Особенности.**

- В Kubernetes каждый сервис оборачивается в Deployment/Service c proxy-контейнером или sidecar. Ingress указывает на прокси.
- В Docker Compose придётся добавлять дополнительные контейнеры и переменные для auth-прокси.

**Плюсы.**

- Гибкая настройка политики доступа для каждого сервиса (разные client_id, разные scope).
- Можно использовать готовые Keycloak адаптеры без ручной конфигурации Traefik.

**Минусы.**

- Усложнение deployment: больше манифестов, больше контейнеров, сложнее поддерживать.
- Для noVNC WebSocket придётся тонко настраивать прокси (поддержка WebSocket, корректные заголовки и таймауты).
- Локальная среда в Docker Compose становится тяжелее и требует Keycloak/прокси даже для разработки.

### 3. Нативная интеграция сервисов с Keycloak (через OpenID Connect)

**Идея.** Добавить в control-plane и worker поддержку проверки JWT из Keycloak (через `Authorization: Bearer`), а UI интегрировать с Keycloak через PKCE. Traefik остаётся простым reverse proxy.

**Особенности.**

- UI инициирует авторизацию, получает токен и обращается к `/api` с Bearer токеном. Для noVNC можно генерировать подписанные одноразовые URL, которые проверяет VNC gateway.
- Control-plane и worker должны валидировать токены, проверять роли и истечение срока.

**Плюсы.**

- Точная авторизационная логика реализована внутри API; нет зависимости от конкретного ingress-контроллера.
- Возможность использовать Keycloak только при необходимости, а без токена работать в «анонимном» режиме (требует настройки fallback).

**Минусы.**

- Существенные изменения кода (валидация JWT, управление ролями, refresh токены в UI).
- noVNC остаётся «дырой», если не реализовывать отдельную схему временных токенов.
- Усложняется локальная разработка: UI требует Keycloak даже при запуске через Docker Compose.

## Рекомендуемое решение: Traefik ForwardAuth + Keycloak

Этот подход минимально затрагивает текущую архитектуру и позволяет гибко включать/выключать аутентификацию.

### Архитектура

1. Разворачивается сервис `oauth2-proxy` (или лёгкий `traefik-forward-auth`) в namespace `camofleet`. Он настроен на Keycloak Realm/Client, выполняет OIDC код flow и выдает cookie-сессию.
2. В Kubernetes создаётся middleware `forward-auth@kubernetescrd` (или поименованный CRD), который обращается к proxy. Ingress Traefik для `/`, `/api`, `/vnc` получает аннотацию `traefik.ingress.kubernetes.io/router.middlewares: camofleet-forward-auth@kubernetescrd`.
3. Для WebSocket (noVNC) в middleware включается `authResponseHeaders=Authorization,X-Forwarded-User` и Traefik настраивается на проброс `Authorization` в backend, чтобы worker/vnc-gateway могли видеть токен или пользователя.
4. В локальном Docker Compose конфигурация остаётся без middleware. Auth включается только в Kubernetes overlay (например, `deploy/k8s/overlays/prod`).

### Почему это лучше

- **Нулевые изменения в коде**: текущие сервисы уже корректно работают без аутентификации (см. README), и им не требуются дополнительные зависимости. Это важно для sidecar-архитектуры worker/runner.
- **Совместимость с Traefik**: Ingress уже на Traefik, поэтому мы используем его штатные возможности (ForwardAuth), без внедрения новых прокси-слоёв.
- **Гибкая настройка в Kubernetes**: можно создать overlay, который добавляет middleware и разворачивает Keycloak/ForwardAuth только в нужных окружениях. Базовая конфигурация остаётся простой для dev-стенда и Docker Compose.
- **Поддержка WebSocket**: Traefik умеет применять ForwardAuth к WebSocket рукопожатию, что обеспечивает защиту канала noVNC без изменения gateway-кода.
- **Масштабируемость**: одна точка аутентификации позволяет централизованно управлять сессиями, логами и политиками Keycloak.

### Детали реализации

1. **Подготовить Keycloak**: создать realm, client (конфигурация `confidential` или `public`), настроить redirect URI на Traefik (`https://camofleet.local/oauth2/callback`).
2. **Развернуть oauth2-proxy**: helm chart или манифест с настройками `--provider=oidc`, `--oidc-issuer-url`, `--oidc-client-id`, `--oidc-client-secret`, `--cookie-secret`. В values включить `setXAuthRequest=true`, `passAuthorizationHeader=true`, `passAccessToken=true`, `cookieRefresh`/`cookieExpire`.
3. **Создать middleware**: пример CRD `Middleware` с ForwardAuth, указывающий `address: http://oauth2-proxy.camofleet.svc.cluster.local:4180`. В `authResponseHeaders` перечислить `Authorization,X-Forwarded-User,X-Forwarded-Groups`.
4. **Изменить Ingress**: добавить аннотацию `traefik.ingress.kubernetes.io/router.middlewares: camofleet-forward-auth@kubernetescrd`. Опционально включить `traefik.ingress.kubernetes.io/auth-type=forward` в случае использования аннотаций вместо CRD.
5. **Обработка в сервисах**: control-plane может считывать `X-Forwarded-User` (для аудита) или проверять `Authorization` при необходимости ролевой модели. При отсутствии заголовка сервисы продолжают работать (например, в dev окружении).
6. **Документация**: описать в README как включить auth, не меняя docker-compose (например, предоставить `kustomize` overlay `deploy/k8s/overlays/keycloak`).

Первый шаг уже попал в репозиторий: `deploy/traefik/oauth2-proxy.yaml` разворачивает `oauth2-proxy`, `deploy/traefik/camofleet-forward-auth.yaml` создаёт middleware, а в `deploy/traefik/camofleet-ingressroute.yaml` middleware подключен к UI-маршруту (`/`). Это позволяет проверить Keycloak аутентификацию для веб-интерфейса, пока `/api` и `/vnc` остаются открытыми. Последующие итерации могут переиспользовать ту же middleware-ссылку для остальных маршрутов или добавить отдельные политики при необходимости.

### Дополнительные рекомендации

- Для защиты WebSocket убедиться, что Traefik 2.9+ установлен и `forward-auth` middleware обрабатывает `WebSocket` handshake (`authResponseHeaders`).
- Использовать отдельный Keycloak client для UI и для API (Machine-to-machine) при необходимости. ForwardAuth может валидировать только интерактивных пользователей, а backend может принимать сервисные токены.
- Настроить Keycloak Role Mapping и передавать их через `X-Forwarded-Groups`, чтобы в будущем реализовать ролевую авторизацию на уровне API.
- Добавить health-check и aliveness для oauth2-proxy и Keycloak, чтобы избежать single point of failure.

Таким образом, Traefik ForwardAuth + Keycloak обеспечивает баланс между безопасностью и простотой, оставляя текущие сценарии развёртывания (как с Keycloak, так и без него) полностью рабочими.
