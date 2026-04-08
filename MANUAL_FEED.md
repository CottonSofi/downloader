# 🔥 Manual: AUTOMATIZADOR DE FEEDS

## ¿Qué es?

Extensión del **YT-DLP Downloader** que automatiza la descarga de videos desde feeds de redes sociales.

El programa:
1. Abre el feed en un navegador real (no bot detection)
2. Detecta videos automáticamente
3. Evita anuncios
4. Descarga todo solo
5. Evita duplicados

---

## 🚀 ¿Cómo usar?

### PASO 1: Inicia la app

```bash
python downloader/app.py
```

### PASO 2: Aparece la UI con nueva sección "Feed Automático"

Verás botones para:
- ✅ Iniciar Feed IG (Instagram)
- ✅ Iniciar Feed TikTok
- ✅ Iniciar Feed Twitter/X
- ✅ Iniciar Feed YouTube Shorts
- ❌ STOP Feed (para detener)

### PASO 3: Haz clic en una plataforma

**IMPORTANTE**: Se abrirá un navegador en modo VISIBLE.

👉 **DEBES ESTAR LOGUEADO** en esa plataforma en el navegador que se abre.

### PASO 4: El programa hace:

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

### PASO 5: Para detener

Haz clic en **STOP Feed**

---

## 🔐 SEGURIDAD: LOGIN

El programa usa **modo visible (headless=False)** que:
- ✅ Abre navegador real (no bot)
- ✅ Conserva tu sesión entre ejecuciones
- ✅ Permite login manual si expira

Los datos se guardan en: `./browser_profile/`

---

## 🛡️ DETECCIÓN DE ANUNCIOS

El programa automáticamente ignora:
- "Sponsored"
- "Ad"
- "Promoted"
- "Patrocinado"
- "Publicidad"
- "Anuncio"

---

## 📊 OPCIONES RELEVANTES

Antes de iniciar Feed, configura:

1. **Compresión**: sin_compresion / baja / media / alta
2. **Cookies.txt**: Si tienes para X/Instagram
3. **Idioma audio**: auto o específico
4. **Calidad**: best o específica (720p, etc)

Todas estas opciones se aplican a cada video descargado.

---

## 🖥️ PLATAFORMAS SOPORTADAS

### Instagram
- Detecta videos del feed
- Navega por scroll automático
- Descarga directamente

### Twitter/X
- Tweets con video
- Incluye retweets con video
- Requiere cookies.txt para contenido restringido

### TikTok
- Videos del For You Page
- Navegación automática
- Descarga directa

### YouTube Shorts
- Links a shorts
- Descarga directa
- Se une a playlist

---

## ⚠️ LIMITACIONES

1. **Bloqueos**: Instagram/TikTok pueden bloquear bots
   - Solución: Usar headless=False (ya lo hacemos)
   - No spamear demasiado rápido

2. **Cookies**: Algunas plataformas (Instagram) requieren login
   - El navegador se abre visible para que ingreses

3. **Velocidad**: Respeta delays entre acciones
   - Sleep de 2s entre scrolls
   - No es instantáneo pero es estable

---

## 🐛 TROUBLESHOOTING

### "Error: 'Page' object has no attribute..."
→ Playwright no se instaló correctamente
```bash
python -m pip install playwright
python -m playwright install
```

### "El navegador no abre"
→ Firewall o permisos
```bash
# Ejecuta como admin en PowerShell
```

### "No detecta videos"
→ La plataforma cambió el HTML
→ Abre issue o modifica los selectores en `feed_scraper.py`

### "Se descarga todo 2 veces"
→ Recarga la págind y `seen_urls` no persiste entre sesiones
→ Mira el archivo `./browser_profile/` para historial

---

## 🔧 PERSONALIZACIÓN AVANZADA

### Modificar velocidad de scroll

En `feed_scraper.py`, línea ~200:

```python
# Menos agresivo
self.page.mouse.wheel(0, 1000)  # en vez de 3000
time.sleep(3)  # en vez de 2
```

### Agregar más palabras de anuncio

En `_is_ad()`:

```python
ad_keywords = [
    "sponsored",
    "tu_palabra_aqui",
]
```

### Cambiar selectores (si plataforma cambió)

En `_detect_videos()` y `_extract_url()`:

```python
# Nuevos selectores desde DevTools (F12)
elements = self.page.query_selector_all("tu_selector")
```

---

## 📝 FICHERO DE LOG

Todo se guarda en el log de la UI.

Puedes copiar y guardar si necesitas:

```
Click derecho → Seleccionar todo → Ctrl+C
```

---

## 🚀 PRÓXIMOS PASOS

Si quieres más features:

- [ ] Guardar historial en archivo (persistente)
- [ ] Límite de descargas por sesión
- [ ] Filtro por duración mínima
- [ ] Filtro por palabras clave
- [ ] Descarga paralela (múltiples simultáneos)

---

## 💬 ¿DUDAS?

Lee el código en `feed_scraper.py` - está comentado y es fácil de entender.

O modifica `download_from_feed()` en `app.py` para cambiar qué se descarga.

---

**¡Que disfrutes! 😈**
