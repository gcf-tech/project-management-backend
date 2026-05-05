# Estrategia de auth para el integración CalDAV

**Estado:** Implementada (modo Bearer)
**Última revisión:** 2026-04-30
**ADR relacionado:** [ADR-005](adr/adr-005-calendar-bff-cache-aside.md)

---

## Pregunta a resolver

> ¿Cómo autentica el backend las llamadas CalDAV contra Nextcloud para cada
> usuario, sin pedir credenciales adicionales y sin almacenar contraseñas en
> claro?

---

## Opciones consideradas

### A) App Password (Basic Auth)

Cada usuario genera un App Password en Nextcloud → el backend lo cifra y lo
guarda en `users.nc_caldav_token_encrypted` → cada request CalDAV usa
`Basic Auth(nc_user_id, app_password)`.

| Pros | Contras |
|---|---|
| Compatibilidad universal con CalDAV | Onboarding adicional para cada usuario |
| App Passwords no caducan | Almacenar contraseñas (aunque sean app-passwords) ⇒ superficie de ataque |
| | No reusa el OAuth2 existente |

### B) Bearer OAuth2 token  ← **elegida**

Reutilizar el `access_token` que el frontend ya envía a la API en
`Authorization: Bearer …`. El backend lo extrae del header de la request
entrante y lo inyecta en la request CalDAV saliente.

| Pros | Contras |
|---|---|
| Cero credenciales nuevas | Requiere `allow_oauth2_in_caldav = true` en Nextcloud (ver abajo) |
| Refresh automático ya existe | Si el token expira durante una operación, hay que reintentar |
| El backend NO persiste tokens | |

### C) Proxy con credencial de servicio compartida

El backend usa una sola cuenta técnica para leer todos los calendarios. Se
descarta de inmediato — viola la frontera multi-tenant: no hay forma de
acotar la respuesta a "solo los calendarios de este usuario".

---

## Decisión

**Modo Bearer (Opción B)** activado por defecto vía
`CALDAV_AUTH_MODE=bearer`.

### Pre-requisito en Nextcloud

Editar `config/config.php`:

```php
'allow_local_remote_servers'  => true,
'oauth2.allow_dav'            => true,    // alias en releases recientes
// ó (versiones más antiguas)
'allow_oauth2_in_caldav'      => true,
```

Validación rápida (CLI desde el contenedor de Nextcloud):

```bash
sudo -u www-data php occ config:list system | grep -E 'oauth|dav'
```

### Cómo lo usa el backend

1. La request entrante trae `Authorization: Bearer <access_token>`.
2. `app/api/v1/calendar.py::list_events` valida el token contra
   `/ocs/v1.php/cloud/user` (vía `get_current_user`) y obtiene el `User`.
3. Construye `NextcloudCalDAVAdapter(nc_user_id=user.nc_user_id,
   access_token=<token>)`.
4. El adapter pasa el token tal cual al cliente CalDAV
   (`caldav.DAVClient(headers={"Authorization": f"Bearer {token}"})`).
5. Si el token expiró, el frontend recibe 401 y dispara su flujo de
   refresh automático (`refreshAccessToken`) — el siguiente intento usa el
   token nuevo.

---

## Plan de contingencia: cambiar a App Password

Si en producción Nextcloud rechaza Bearer (por config no aplicada o por
cambios upstream), el cambio para activar App Password es:

1. **Sin desplegar código nuevo:** setear `CALDAV_AUTH_MODE=app_password`.
   El adapter ya tiene la rama `_build_client()` que usa Basic Auth.
2. **Schema:** añadir columna cifrada (no implementada por defecto):
   ```sql
   ALTER TABLE users
       ADD COLUMN nc_caldav_token_encrypted VARBINARY(512) NULL;
   ```
3. **UI de onboarding:** primer ingreso del usuario tras la migración →
   modal pidiendo el App Password. Validar, cifrar (AES-GCM con key en
   `CALDAV_TOKEN_KMS_KEY`), guardar.
4. **Adapter:** en lugar de leer el header, leer
   `user.nc_caldav_token_encrypted`, descifrar y pasar al `DAVClient`.

---

## ¿Por qué NO almacenamos el OAuth2 token?

- El `access_token` vive 1 hora (Nextcloud default). Almacenarlo no aporta
  nada — la próxima request del usuario lo trae de vuelta.
- El `refresh_token` SÍ es de larga duración, pero ya lo gestiona el
  frontend (`localStorage.nc_refresh_token`). Duplicarlo en BD añade
  superficie de ataque.
- El backend solo necesita el token DURANTE la atención de la request, y
  el header lo trae cada vez. No hay caso de uso para "background jobs
  que necesitan acceso CalDAV mientras el usuario duerme".

---

## Pruebas

- `tests/test_calendar_cache.py` cubre el aislamiento del cache por usuario.
- Smoke test de auth (manual): hacer GET `/api/calendar/events?start=...&end=...`
  con un Bearer válido → debe responder 200 con `events: [...]`. Con un
  Bearer inválido → 401.

---

## Referencias

- ADR-002 (predecesor parcial)
- ADR-005 (decisión arquitectónica completa)
- `app/integrations/calendar/nextcloud.py::_build_client`
- `app/api/v1/calendar.py::_strip_bearer`
