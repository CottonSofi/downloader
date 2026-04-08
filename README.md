# Downloader GUI + Feed Scraper

Este documento explica como usar y mantener los archivos principales:
- `app.py` (aplicacion GUI principal)
- `feed_scraper.py` (scraper de feed para deteccion automatica)

Tambien incluye las copias de seguridad:
- `app2.py`
- `scrapper2.py`

## 1) Que hace cada archivo

### `app.py`
Aplicacion principal con interfaz Tkinter para:
- Descargar enlaces manuales con `yt-dlp`
- Usar fallback social con `gallery-dl`
- Manejar cookies (`cookies.txt` y `cookies2.txt`)
- Iniciar/parar feed automatico (Instagram, TikTok, Twitter/X, YouTube Shorts)

### `feed_scraper.py`
Motor de scraping/automatizacion del feed con Playwright para:
- Abrir el feed objetivo en navegador
- Detectar items visibles
- Enviar URLs detectadas a `app.py` para descarga en cola

### `app2.py` y `scrapper2.py`
Copias de seguridad por si necesitas volver a una version funcional rapidamente.

## 2) Guia de instalacion

### Requisitos minimos

- Windows 10/11
- Python 3.10+ (recomendado)
- Internet en la primera instalacion de dependencias

### Instalacion recomendada

Desde la raiz del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Si ya tienes una `.venv` creada, basta con activarla y reinstalar dependencias si cambias de equipo.

### Arranque en Windows

El proyecto incluye lanzadores ocultos en `launchers/` para evitar abrir ventanas de `cmd`.
Usa el acceso directo o el script correspondiente, por ejemplo:

- `launchers\feed.vbs` para `downloader/app.py`
- `launchers\downloader.vbs` para `downloader/downloader.py`
- `launchers\aller.vbs` para `downloader/aller.py`

Estos lanzadores usan `pythonw.exe` cuando existe para no mostrar consola.

## 3) Ejecucion recomendada (1 click)

Desde la raiz del proyecto, abre el lanzador correspondiente en `launchers/`.
La GUI principal es `downloader/app.py`.

## 4) Ejecucion manual

Desde la raiz del proyecto:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python downloader/app.py
```

Si usas entorno virtual:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
python downloader/app.py
```

## 5) Dependencias

El proyecto usa:
- `yt-dlp`
- `gallery-dl`
- `imageio-ffmpeg`
- `playwright`

Estan declaradas en `requirements.txt`.

Nota: no se recomienda instalar dependencias automaticamente en cada arranque. Si falta algo, la app mostrara un aviso claro para que lo instales en la misma `.venv`.

## 6) Cookies

Rutas soportadas automaticamente:
- `downloader/cookies.txt`
- `downloader/cookies2.txt`

La app detecta principal y alterna. Si una falla, reintenta con la otra en varios flujos.

## 7) Logs y diagnostico

- Log de actividad: `downloader/activity.log`
- En la GUI puedes abrir "Ver log en vivo" para inspeccion en tiempo real.

Si algo falla, comparte el bloque del log desde el inicio del evento hasta el error para depuracion precisa.

## 8) Recuperacion rapida (backup)

Si una actualizacion rompe algo:

1. Cierra la app
2. Copia backup sobre el archivo principal

Ejemplo:

```powershell
Copy-Item downloader\app2.py downloader\app.py -Force
Copy-Item downloader\scrapper2.py downloader\feed_scraper.py -Force
```

## 9) Estructura recomendada

Mantener un solo README para este modulo es lo ideal.

Ventajas:
- Menos duplicacion
- Una sola fuente de verdad
- Facil mantenimiento

Si luego el proyecto crece mucho, puedes separar documentacion en:
- `README.md` principal
- `docs/feed.md`
- `docs/troubleshooting.md`

## 10) Comandos utiles

Compilar y validar sintaxis:

```powershell
python -m py_compile downloader/app.py downloader/feed_scraper.py downloader/app2.py downloader/scrapper2.py
```

Actualizar dependencias:

```powershell
python -m pip install --upgrade -r requirements.txt
python -m playwright install chromium
```
