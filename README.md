# Downloader Module

Este modulo contiene las apps relacionadas con descarga de contenido.

## Archivos principales

- `app.py`: Aplicacion principal moderna en PyQt5. Incluye descargas + feed monitor + gestion avanzada de instancias X.
- `downloader.py`: Aplicacion PyQt5 enfocada solo en descargas de archivos (sin flujo de feed scraping en la UI).
- `aller.py`: App dedicada a descarga media completa de X (usa su propio flujo Playwright).
- `feed_scraper.py`: Motor de scraping de feed usado por `app.py` y `app_oldgui.py`.
- `app_oldgui.py`: Version Tkinter heredada para compatibilidad.

## Objetivo de `downloader.py`

`downloader.py` es la variante de trabajo para descargas manuales y semiautomaticas:

- Descarga BEST / audio / solo video / limite por tamano / recorte por tiempo.
- Soporte de subtitulos (incluidos y embebidos).
- Soporte de cookies (navegador, carpeta y archivo seleccionado).
- Monitoreo de portapapeles para encolar URLs.
- Interfaz moderna en PyQt5 reutilizando la logica estable del core.

No expone controles de feed scraping ni panel de instancias X en la UI.

## Ejecucion

Desde la carpeta `downloader/`:

```bash
python downloader.py
```

## Dependencias

Instala requerimientos del modulo:

```bash
pip install -r requirements.txt
```

Si se usa Playwright en otras apps del modulo:

```bash
playwright install chromium
```

## Notas de mantenimiento (2026-04-22)

- Se corrigio la cadencia del Monitor X para respetar el intervalo configurado entre revisiones.
- Se corrigio un bug de flujo en el monitor que podia repetir baseline en cada vuelta.
- Se reforzo el worker de feed para X con fallbacks de descarga cuando yt-dlp falla en GraphQL.
- Se mejoro la deteccion de cookies tras mover el proyecto:
	- El pool de cookies ya no queda limitado a una subcarpeta social (por ejemplo `cookies/tiktok`).
	- Al seleccionar una cookie puntual, la carpeta se normaliza al raiz `cookies` cuando aplica.
	- Se amplio el escaneo recursivo de cookies en `app.py`, `downloader.py`, `app_oldgui.py` y `feed_scraper.py`.

	## Notas de mantenimiento (2026-04-23)

	- Feed X: la rama de descarga preferente de imagen tambien activa fallback estricto para status de Twitter/X cuando yt-dlp falla (incluye ruta GraphQL).
	- Feed X: se agrego un guard anti-atasco para evitar quedarse ciclando en el mismo post tras carrusel/imagen.
	- PREV en feed item-por-item: ahora usa un buffer temporal en memoria (100 posts recientes con geometria basica) para retroceso mas estable.
	- El buffer temporal de PREV se limpia al iniciar/detener/cerrar instancia (no persiste entre sesiones).
