# ADR 0010 · Acceso federado para cuentas existentes

## Contexto

Plataforma Elemental usa django-allauth para Google. Su adaptador completa el primer vínculo por correo verificado si encuentra una única cuenta activa, o por una solicitud de acceso aprobada. Cuadratura aprovisiona usuarios y permisos mediante sus propios mecanismos y necesita que el titular de cada cuenta confirme el primer vínculo.

## Decisión

- django-allauth gestiona OAuth, el estado y PKCE. Cuadratura expone rutas propias para entrar, vincular y recibir el retorno de Google.
- El usuario entra con su cuenta existente, abre **Métodos de acceso**, confirma su contraseña local y autoriza Google. El retorno exige la misma sesión y un correo verificado por el proveedor. La dirección local puede ser distinta de la de Google.
- `FederatedIdentity` conserva el proveedor, su identificador estable y el usuario. PostgreSQL garantiza la unicidad por proveedor e identificador y admite una identidad por usuario y proveedor. La política vive en `accounts/federation.py`, separada de los perfiles y tokens de allauth.
- El ingreso recupera la cuenta activa por identidad vinculada y abre una sesión Django. Las membresías y roles de esa cuenta siguen gestionados por Cuadratura.
- La desvinculación exige la contraseña local y revoca ingresos federados posteriores. Las sesiones ya abiertas siguen el ciclo de vida normal de Django.
- Los eventos de vínculo y desvinculación se escriben en el log de aplicación tras confirmar la transacción, con proveedor y UUID interno. Los tokens, correos y perfiles OAuth quedan fuera del registro del vínculo.

## Configuración

1. Crea un cliente OAuth de tipo aplicación web para Cuadratura en Google Cloud. Registra como URI de retorno exacta `https://cuadratura.avx.cl/accounts/google/login/callback/` para ese host o `http://localhost:8000/accounts/google/login/callback/` para desarrollo en ese origen.
2. Configura `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` y `GOOGLE_AUTH_ENABLED=1` en el entorno privado de la aplicación. Con credenciales incompletas, el proveedor queda deshabilitado.
3. Instala las dependencias, aplica las migraciones y reinicia la aplicación. Comprueba que el proxy entregue el host y esquema correctos y que los registros de acceso del callback excluyan su query string. La plantilla Nginx del repositorio deshabilita el access log para esa ruta.
4. Entra con contraseña, vincula Google, cierra sesión y vuelve a entrar con Google. Para suspender ese proveedor, configura `GOOGLE_AUTH_ENABLED=0` y reinicia.

Para agregar otro proveedor, incorpora su integración allauth, sus credenciales y su ruta de callback. Regístralo en `FEDERATED_LOGIN_PROVIDERS` y comprueba que sus identificadores estables y su señal de correo verificado satisfagan la misma política. Cada instancia configurada debe tener un identificador de proveedor propio si sus sujetos pertenecen a espacios de identidad diferentes.
