# ADR 0001: FastAPI application state management

## Status
Accepted

## Context
The worker, control-plane, and runner services all need long-lived state
objects to share configuration, clients, and telemetry registries across
request handlers. Until now each service initialised and exposed this
state differently, which made it hard to reason about lifecycle hooks and
resource cleanup.

## Decision
We standardise on storing the service-specific `AppState` instance on the
FastAPI application's `app.state` container. A helper `get_app_state()`
retrieves the typed state for a given `FastAPI` instance, while request
handlers receive the state through a `get_state` dependency that reads it
from the incoming `Request`. Startup and shutdown events interact with the
state via the helper so that resource initialisation and cleanup logic is
centralised in the state object.

## Consequences
* The way state is accessed is consistent across services, reducing the
  number of ad-hoc closures and globals.
* Tests can introspect or replace the state by reading or writing the
  `app.state.app_state` attribute, simplifying fixture setup.
* Shutdown hooks now use the shared helper, ensuring that cleanup logic is
  exercised even when the app instance changes during tests.
* Any service initialisation failure will surface clearly because the
  helper raises if the state has not been attached to the application.
