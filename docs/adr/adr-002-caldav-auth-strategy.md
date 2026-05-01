# ADR-002: Estrategia de autenticación CalDAV contra Nextcloud

**Estado:** Propuesto (pre-Calendar)  
**Fecha:** 2026-04-30  
**Contexto:** Integración CalDAV para sincronización bidireccional con Nextcloud Calendar

---

## Contexto

La vista Calendar necesitará sincronizar eventos con Nextcloud vía CalDAV. El backend ya dispone de OAuth2 contra Nextcloud (token en tabla `users.nc_token`). Se evalúa cómo autenticar las llamadas CalDAV reutilizando esa credencial.

---

## Opciones evaluadas

### Opción A — App Password Nextcloud (Basic Auth)

Crear un App Password de Nextcloud para cada usuario, almacenarlo cifrado y usarlo en Basic Auth contra `https://<nc>/remote.php/dav/`.

**Pros:**
- Simple — CalDAV estándar con Basic Auth.
- Los App Passwords no caducan.

**Contras:**
- Requiere que el usuario genere el App Password manualmente (o que el backend lo genere vía OCS API).
- Almacenar contraseñas (aunque sean app-passwords) en BD añade superficie de ataque.
- No aprovecha el OAuth2 existente.

### Opción B — Bearer Token OAuth2 de Nextcloud en cabecera CalDAV

Pasar el `nc_token` (Bearer OAuth2) en la cabecera `Authorization` de las peticiones CalDAV.

**Pros:**
- Reutiliza el token OAuth2 ya almacenado.
- Sin credencial adicional que gestionar.

**Contras:**
- Nextcloud CalDAV acepta Bearer tokens solo cuando el servidor está configurado con `allow_oauth2_in_caldav = true` (no es el default).
- Si el token expira durante una operación CalDAV, hay que manejar el refresh y reintentar.

### Opción C — Proxy en el backend (elegida recomendada) ✅

El cliente JS llama al backend (`/api/calendar/events`). El backend hace la petición CalDAV usando el `nc_token`, maneja refresh y devuelve iCal/JSON al cliente.

**Pros:**
- El cliente nunca toca CalDAV directamente — sin CORS.
- El backend ya tiene la lógica de `token_refresh` (ver commit a759527).
- Permite cachear y transformar los datos CalDAV (e.g. iCal → JSON).
- Desacopla el cliente de la implementación de Nextcloud.

**Contras:**
- Añade latencia de un hop extra.
- El backend se convierte en proxy de eventos de calendario (más responsabilidad).
- Necesita manejo de errores CalDAV (404, 409 conflict en create/update).

---

## Decisión

Se recomienda **Opción C**. El backend ya gestiona el ciclo de vida del token OAuth2 y el proxy aísla al cliente de los detalles CalDAV (PROPFIND, REPORT, PUT, DELETE).

---

## Consecuencias

1. Crear endpoint `/api/calendar/events` (GET/POST/PATCH/DELETE) que traduzca entre la API interna y CalDAV.
2. Usar `httpx` (async) para las peticiones CalDAV; añadir como dependencia.
3. Implementar `caldav_client.py` en `app/services/` con manejo de PROPFIND y REPORT XML.
4. El token refresh actual en `a759527` debe extenderse para reintentar la llamada CalDAV tras refresh.
5. Los eventos CalDAV se sincronizarán con `WeeklyBlock` como fuente de verdad local; conflictos se resuelven con "last-write-wins" inicialmente.

---

## Referencias

- Nextcloud CalDAV API: `https://<nc>/remote.php/dav/calendars/<user>/`
- Commit `a759527` — token refresh implementation
- `app/api/dependencies.py` — `get_current_user` con manejo de token
