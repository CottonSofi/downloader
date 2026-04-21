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
