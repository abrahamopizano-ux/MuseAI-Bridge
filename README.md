# MuseBridge (`MuseAI-Bridge`) — Puente Seguro de Control de PC y WhatsApp para Muse AI

**MuseBridge** (`v2.1.0`) es un puente local en Python (**FastAPI**) con **Centro de Control de Escritorio nativo y responsive** (Modo Oscuro *Carbon* y Modo Claro *Slate*), **Selector de Puertos Locales**, **Asistente de Instalación de Windows** y **Servicio Vigilante (Watchdog)** de auto-recuperación.

Permite que un asistente o agente autorizado (**Muse AI**) interactúe con una PC Windows de forma controlada y auditada: ver la pantalla en su resolución nativa real (`1920x1080`), controlar mouse y teclado, escribir textos largos en español con acentos y símbolos, abrir aplicaciones autorizadas, enviar mensajes confirmados en WhatsApp Desktop y gestionar archivos únicamente dentro de carpetas permitidas por el usuario, exponiendo el servicio de manera cifrada mediante el propio **Tailscale Funnel** del propietario.

---

## ¿Qué hace MuseBridge?

1. **Centro de Control de Escritorio Responsive (`desktop_app.py`)**
   - Interfaz moderna adaptable a cualquier tamaño de ventana (`1280x720`, `1024x768`, `800x600` y ventana pequeña), con barra lateral auto-colapsable a iconos (`58px`), tarjetas KPI auto-acomodables (`4` ➔ `2x2` ➔ `1`), botones auto-envolventes (`FlowFrame`), Modo Oscuro y Modo Claro, y **cero ventanas negras de consola**.
   - Tabla de **Auditoría Forense en Vivo** con filtro instantáneo, desplazamiento horizontal/vertical y tooltips con el detalle completo de cada petición.
2. **Captura de Pantalla en Resolución Nativa (`1920x1080`)**
   - `GET /pc/screenshot` y `GET /pc/screenshot-hd` devuelven la resolución real 1:1 del monitor (`scale_factor_x: 1.0`, `scale_factor_y: 1.0`) para que las coordenadas visuales coincidan exactamente con los clics reales, además de `GET /pc/screenshot-region` para recortes con zoom.
3. **Tipeo Unicode con Acentos y Automatización GUI**
   - `POST /pc/type-text` y `POST /pc/type-and-click` escriben textos largos de una sola vez con soporte completo para español (`á, é, í, ó, ú, ñ, ü, ¿, ¡`), símbolos y saltos de línea mediante Win32 `SendInput` (`KEYEVENTF_UNICODE`) y portapapeles UTF-16.
4. **Envío Verificado por WhatsApp Desktop**
   - `POST /whatsapp/send` abre el chat mediante `whatsapp://send`, localiza y enfoca la ventana de WhatsApp Desktop con Win32 `EnumWindows` / `SetForegroundWindow`, espera la carga del borrador y presiona `Enter` confirmando que la ventana activa recibió el envío (`confirmed: true`).
5. **Gestión de Archivos en Sandbox (8 Permisos Independientes + Papelera de Reciclaje)**
   - Cada operación tiene su propio interruptor en el panel (`allow_file_list`, `allow_file_read`, `allow_file_write`, `allow_file_mkdir`, `allow_file_move`, `allow_file_search`, `allow_file_transfer` y `allow_delete`).
   - Creación y edición verificada (`POST /files/write` y `PATCH /files/edit`) con **respaldo automático `.bak`** y comparación de hash **SHA-256** antes y después (devuelve `HTTP 422` si el archivo no cambió en disco).
   - Búsqueda (`POST /files/search`) por nombre y dentro del contenido de los archivos.
   - Eliminación segura (`POST /files/delete`): **jamás borra permanentemente**; envía los archivos exclusivamente a la **Papelera de Reciclaje de Windows** (`SHFileOperationW` con `FOF_ALLOWUNDO`).
6. **Catálogo de Aplicaciones Instaladas y Selector de Puertos Locales**
   - Escanea las aplicaciones instaladas en la PC (Menú Inicio, `Program Files` y Registro de Windows) con buscador y filtros (*Todas / Permitidas / Denegadas*). `POST /pc/open-app` solo abre las autorizadas.
   - Sección **Puertos de Red** que detecta conflictos (`Error 10048`), escanea rangos de puertos (`8000–8010`), muestra qué proceso ocupa cada puerto y permite cambiar el puerto local en caliente actualizando automáticamente Tailscale Funnel (`:8443`).
7. **Servicio Vigilante (`watchdog_service.py`)**
   - Supervisa en segundo plano que el servidor responda `200 OK` en `/health` y que Tailscale Funnel esté activo; si el proceso cae (y el Kill Switch no está activo), lo levanta automáticamente sin mostrar consola.

---

## Modelo de Seguridad (Zero-Trust)

MuseBridge está diseñado bajo el principio de **confianza cero y control físico exclusivo del propietario de la PC**:

- **1. Autenticación por Bearer Token (`API_SECRET_TOKEN`):**
  Todas las rutas operativas exigen el encabezado `Authorization: Bearer <token>`. El token se genera localmente con 256 bits de entropía (`secrets.token_hex`) en el archivo local `.env` (excluido del repositorio mediante `.gitignore`) y se valida en tiempo constante con `secrets.compare_digest`.
- **2. Todo Denegado por Defecto (`Default-Deny`):**
  Al instalarse, **todos los permisos están en `false`** (captura de pantalla, WhatsApp, mouse/teclado, apertura de apps, las 8 funciones de archivos, todas las aplicaciones del catálogo y la lista de carpetas autorizadas). Ningún endpoint de la API puede modificar permisos (`GET /permissions/status` es estrictamente de solo lectura).
- **3. Sandbox de Carpetas y Protección del Sistema:**
  Las operaciones de archivos solo pueden acceder a las rutas que el usuario agregue manualmente en el panel (`allowed_paths`). Las carpetas del sistema (`C:\Windows`, `Program Files`, `ProgramData`, `AppData`, la raíz `C:\` y la propia carpeta del puente) están bloqueadas por código incluso si se intentara acceder mediante enlaces simbólicos o rutas relativas (`..`).
- **4. Botón de Emergencia (`Kill Switch`) Exclusivo del Panel:**
  El botón rojo **`⏻ DETENER CONEXIÓN`** visible en la barra superior del panel apaga de inmediato el servidor local, cierra el túnel público de Tailscale Funnel y pausa el servicio vigilante. **Ninguna llamada a la API puede desactivar el Kill Switch ni volver a encender el puente**; únicamente el usuario físico desde el panel de escritorio puede reactivar la conexión.
- **5. Vinculación por Código QR con Caducidad y Confidencialidad:**
  El panel genera un código QR temporal que **caduca a los 15 minutos** y puede regenerarse en cualquier momento (además de permitir rotar el Bearer Token con un clic). El handshake (`POST /auth/qr-handshake`) valida la vigencia del QR e incluye la directiva `never_display_token_in_chat: true` para que el asistente confirme la conexión sin exhibir la clave secreta en el chat.
- **6. Cada Propietario en su Propio Tailnet:**
  MuseBridge no utiliza servidores centrales ni túneles compartidos. Cada usuario expone su puente a través de su propia cuenta y red privada de **Tailscale** (`Tailscale Funnel` en el puerto `:8443` con certificado TLS propio `*.ts.net`).

---

## Instalación y Uso

### Requisitos Previos
- **Windows 10 / 11** (64 bits)
- **Python 3.11+** instalado en el sistema
- **[Tailscale para Windows](https://tailscale.com/download/windows)** instalado, con sesión iniciada y **Tailscale Funnel** habilitado en la consola de administración de tu Tailnet.

### Opción A: Instalación con el Asistente Gráfico de Windows (Recomendado)
1. Clona o descarga este repositorio:
   ```powershell
   git clone https://github.com/abraham/MuseAI-Bridge.git
   cd MuseAI-Bridge
   ```
2. Ejecuta el asistente de instalación de 5 pasos:
   ```powershell
   py installer.py
   ```
3. El asistente te guiará en español por:
   - **Bienvenida**
   - **Accesos directos** (casillas para crear icono en el Escritorio y anclar a la Barra de tareas)
   - **Carpeta de instalación** (por defecto `%LOCALAPPDATA%\MuseAI-Bridge`, con botón *Examinar*)
   - **Progreso de instalación** (crea un entorno virtual `venv` real, instala `requirements.txt`, compila o copia los lanzadores sin ventana de consola y registra el auto-inicio con el servicio vigilante)
   - **Finalización** (abre directamente el panel **MuseBridge**)

### Opción B: Ejecución Directa desde Código Fuente
```powershell
# 1. Crear entorno virtual e instalar dependencias
py -m venv venv
.\venv\Scripts\pip install -r requirements.txt

# 2. Abrir el Centro de Control de Escritorio (sin ventana de consola)
Start-Process .\venv\Scripts\pythonw.exe -ArgumentList "desktop_app.py"
```
Desde el panel podrás iniciar el servidor local, activar Tailscale Funnel (`:8443`), elegir un puerto libre si el `8000` estuviera ocupado, autorizar tus permisos y escanear el código QR con tu Muse AI.

---

## Estructura del Repositorio

```text
MuseAI-Bridge/
├── app/
│   ├── main.py                     # API FastAPI v2.1 (/health, /permissions/status, /auth/qr-handshake)
│   ├── config.py                   # Carga de variables de entorno (.env) y configuración por defecto
│   ├── core/
│   │   ├── logger.py               # Bitácora forense en audit.log (JSONL con timestamp ISO-8601)
│   │   ├── permissions.py          # Motor RBAC Default-Deny, Kill Switch y validación de Sandbox
│   │   ├── port_utils.py           # Inspector de puertos (Libre/Ocupado/PID) y escáner de rangos
│   │   └── security.py             # Autenticación Bearer Token en tiempo constante
│   ├── routers/
│   │   ├── pc_controller.py        # Endpoints /pc/screenshot, /pc/type-text, /pc/type-and-click, /pc/open-app
│   │   ├── whatsapp_controller.py  # Endpoint /whatsapp/send con confirmación real de ventana
│   │   └── file_controller.py      # Endpoints /files/* (8 funciones en Sandbox + Papelera de Reciclaje)
│   ├── schemas/                    # Modelos Pydantic de validación de entrada/salida
│   └── services/                   # Servicios Win32 de captura nativa, tipeo Unicode, WhatsApp, salud y archivos
├── desktop_app.py                  # Centro de Control de Escritorio responsive (Modo Oscuro / Claro)
├── run_server.py                   # Lanzador de producción para Uvicorn compatible con pythonw.exe
├── watchdog_service.py             # Servicio Vigilante (auto-recuperación del servidor y Funnel :8443)
├── installer.py                    # Asistente de instalación estilo Windows (5 pasos en español)
├── uninstaller.py                  # Desinstalador con integración en Windows y opción de conservar permisos
├── iniciar-puente.bat              # Script auxiliar de arranque oculto
├── musebridge.ico                  # Icono oficial multi-resolución
├── requirements.txt                # Dependencias de Python
├── .env.example                    # Plantilla de variables de entorno (sin secretos reales)
└── .gitignore                      # Exclusión estricta de .env, permissions.json, logs, venv/ y binarios
```
