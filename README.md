# Downloader GUI + Feed Scraper

## Archivos principales

### `app.py` 🖥️
Aplicacion principal con interfaz Tkinter para:
- Descargar enlaces manuales con `yt-dlp`
- Usar fallback social con `gallery-dl`
- Manejar cookies (`cookies.txt`, `cookies2.txt`, carpeta `cookies/`)
- Iniciar/parar feed automático (Instagram, TikTok, Twitter/X, YouTube Shorts)
- Configuración persistente en `downloader_settings.json`

### `feed_scraper.py` 🤖
Motor de scraping/automatización del feed con Playwright para:
- Abrir feed en navegador real (visible, no detectable como bot)
- Detectar videos automáticamente durante scroll
- Filtrar anuncios ("Sponsored", "Ad", "Patrocinado", etc.)
- Conservar sesión entre ejecuciones en `browser_profile/`
- Enviar URLs detectadas a `app.py` para descarga en cola

### `downloader.py` & `aller.py`
Utilidades complementarias para procesamiento de enlaces

## Instalación

### Requisitos mínimos

- Windows 10/11
- Python 3.10+ (recomendado)
- Internet en la primera instalación de dependencias

### Recomendado: crear entorno virtual

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

### Lanzadores en Windows

El proyecto incluye lanzadores VBS en `../launchers/` para ejecutar sin consola:
- `iniciar-downloader.vbs`
- `iniciar-feed.vbs`
- `iniciar-aller.vbs`

## Ejecución

### Opción 1: Usar lanzador (recomendado)
Desde `message/launchers/`, doble-clic en `iniciar-downloader.vbs`

### Opción 2: Línea de comando

Con entorno virtual:
```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Sin entorno virtual (requiere dependencias globales):
```powershell
python app.py
```

## Dependencias

El proyecto usa:
- `yt-dlp` - descarga de videos
- `gallery-dl` - descarga de imágenes/multimedia (fallback)
- `imageio-ffmpeg` - procesamiento de video
- `playwright` - automatización de navegador

Están declaradas en `requirements.txt`.

## Cookies y autenticación

El proyecto soporta cookies en la carpeta `cookies/`:
- `cookies/twitter/` - Cookies X/Twitter
- `cookies/instagram/` - Instagram cookies
- `cookies/tiktok/` - TikTok cookies
- `cookies/youtube/` - YouTube cookies
- `cookies/general/` - Cookies genéricas

La app detecta y selecciona automáticamente según necesidad.

**Sesión de navegador**: Se guarda en `browser_profile/` para reutilización entre ejecuciones.

## Automatizador de Feeds 🔥

### ¿Qué es?

Extensión que automatiza la descarga de videos desde feeds en tiempo real.

### Plataformas soportadas
- **Instagram** - Feed y reels
- **TikTok** - Feed automático
- **Twitter/X** - Timeline
- **YouTube Shorts** - Shorts automático

### ¿Cómo usar?

**PASO 1**: Inicia la app
```powershell
python app.py
```

**PASO 2**: Verás botones de "Feed Automático" para cada plataforma

**PASO 3**: DEBES ESTAR LOGUEADO en la plataforma en el navegador que se abre (es visible, no bot)

**PASO 4**: El programa hace:
```
1. Navega al feed
2. Espera carga inicial
3. Hace scroll automático
4. Detecta videos
5. Extrae URLs
6. Descarga con tu app
7. Evita duplicados
8. Continúa scrolling
```

**PASO 5**: Para detener, haz clic en **STOP Feed**

### Configuración antes de iniciar feed

- **Compresión**: sin_compresion / baja / media / alta
- **Cookies.txt**: Si tienes en `cookies/`
- **Idioma audio**: auto o específico
- **Calidad**: best o específica (720p, 1080p, etc)

Todas las opciones se aplican automáticamente a cada video descargado.

### Seguridad y detección

El navegador usa **modo visible (headless=False)**:
- ✅ Abre navegador real (no parece bot)
- ✅ Conserva sesión entre ejecuciones
- ✅ Permite login manual si expira

Anuncios ignorados automáticamente:
- "Sponsored" / "Ad" / "Promoted"
- "Patrocinado" / "Publicidad" / "Anuncio"

## Logs y diagnóstico

- Log de actividad: `activity.log`
- En la GUI puedes abrir "Ver log en vivo" para inspección en tiempo real.

Si algo falla, comparte el bloque del log desde el inicio del evento hasta el error para depuración precisa.

## Estructura y mantenimiento

Compilar y validar sintaxis:

```powershell
python -m py_compile downloader/app.py downloader/feed_scraper.py downloader/app2.py downloader/scrapper2.py
```

Actualizar dependencias:

```powershell
python -m pip install --upgrade -r requirements.txt
python -m playwright install chromium
```
