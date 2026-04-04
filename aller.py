import json
import importlib
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Ejecuta: pip install playwright && playwright install chromium")

Image = None
ImageTk = None
try:
    pil_image = importlib.import_module("PIL.Image")
    pil_imagetk = importlib.import_module("PIL.ImageTk")
    Image = pil_image
    ImageTk = pil_imagetk
except Exception:
    pass

APP_TITLE = "X Media Downloader (Turbo Fix)"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SETTINGS_FILE = "downloader_settings.json"

class XMediaApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("850x700")
        
        self.running = False
        self.log_queue = queue.Queue()
        self.seen_urls = set()
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self.total_detected = 0
        self.total_downloaded = 0
        self.processed_tweet_ids = set()
        
        self.x_user_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=os.path.join(os.getcwd(), "X_Media_Downloads"))
        self.cookies_dir_var = tk.StringVar(value=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies"))
        self.show_browser_var = tk.BooleanVar(value=True)
        self.auto_save_var = tk.BooleanVar(value=True)
        self.per_post_folder_var = tk.BooleanVar(value=True)
        self.download_delay_var = tk.StringVar(value="2")  # segundos entre descargas
        self.cookie_rotation_interval_var = tk.StringVar(value="120")  # segundos entre rotaciones
        self.post_records = {}
        self.active_cookie_path = ""
        self.pending_cookie_rotation = False
        self.last_cookie_snapshot = ()
        self.last_rotation_time = 0

        self._load_settings()
        
        self._setup_ui()
        self._bind_autosave_events()
        self._refresh_progress_widgets(force=True)
        self.root.after(100, self._drain_log_queue)

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        config = ttk.LabelFrame(main, text="Configuración de Sesión", padding=10)
        config.pack(fill="x", pady=5)

        ttk.Label(config, text="Usuario X:").grid(row=0, column=0, sticky="w")
        ttk.Entry(config, textvariable=self.x_user_var, width=25).grid(row=0, column=1, padx=5, sticky="w")
        ttk.Checkbutton(config, text="VER NAVEGADOR (Activo)", variable=self.show_browser_var).grid(row=0, column=2, padx=10)

        ttk.Label(config, text="Carpeta:").grid(row=1, column=0, sticky="w", pady=10)
        ttk.Entry(config, textvariable=self.output_dir_var, width=50).grid(row=1, column=1, padx=5, sticky="ew")
        ttk.Button(config, text="...", command=lambda: self.output_dir_var.set(filedialog.askdirectory())).grid(row=1, column=2)

        ttk.Label(config, text="Carpeta cookies:").grid(row=2, column=0, sticky="w")
        ttk.Entry(config, textvariable=self.cookies_dir_var, width=50).grid(row=2, column=1, padx=5, sticky="ew")
        ttk.Button(config, text="Seleccionar carpeta", command=lambda: self.cookies_dir_var.set(filedialog.askdirectory())).grid(row=2, column=2)

        ttk.Checkbutton(
            config,
            text="Autoguardar opciones (usuario, carpeta, cookies, navegador)",
            variable=self.auto_save_var,
            command=self._on_toggle_autosave,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Checkbutton(
            config,
            text="Guardar cada post en carpeta individual (recomendado para Galería)",
            variable=self.per_post_folder_var,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        ttk.Label(config, text="Delay entre descargas (seg):").grid(row=5, column=0, sticky="w", pady=(10, 5))
        ttk.Entry(config, textvariable=self.download_delay_var, width=15).grid(row=5, column=1, sticky="w", padx=5)

        ttk.Label(config, text="Intervalo rotación cookies (seg):").grid(row=6, column=0, sticky="w")
        ttk.Entry(config, textvariable=self.cookie_rotation_interval_var, width=15).grid(row=6, column=1, sticky="w", padx=5)

        config.columnconfigure(1, weight=1)

        ctrls = ttk.Frame(main, padding=10)
        ctrls.pack(fill="x")
        self.btn_start = ttk.Button(ctrls, text="INICIAR ESCANEO", command=self.start_process)
        self.btn_start.pack(side="left", padx=5)
        ttk.Button(ctrls, text="DETENER", command=self.stop_process).pack(side="left", padx=5)
        ttk.Button(ctrls, text="ABRIR GALERIA", command=self._open_gallery).pack(side="left", padx=5)
        ttk.Button(ctrls, text="REINICIAR APP", command=self._restart_application).pack(side="left", padx=5)

        progress_frame = ttk.Frame(main)
        progress_frame.pack(fill="x", pady=(0, 8))
        self.progress_label = ttk.Label(progress_frame, text="Progreso: 0 / 0")
        self.progress_label.pack(anchor="w")
        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", maximum=1, value=0)
        self.progress_bar.pack(fill="x", pady=(4, 0))

        self.log_widget = tk.Text(main, height=20, state="disabled", wrap="word", bg="#121212", fg="#00FF41", font=("Consolas", 10))
        self.log_widget.pack(fill="both", expand=True, pady=10)

    def log(self, msg):
        self.log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _drain_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.log_widget.config(state="normal")
            self.log_widget.insert("end", msg + "\n")
            self.log_widget.see("end")
            self.log_widget.config(state="disabled")
        self._refresh_progress_widgets()
        self.root.after(100, self._drain_log_queue)

    def _settings_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), SETTINGS_FILE)

    def _load_settings(self):
        path = self._settings_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.x_user_var.set(data.get("x_user", ""))
            self.output_dir_var.set(data.get("output_dir", self.output_dir_var.get()))
            cookies_dir = str(data.get("cookies_dir", "")).strip()
            if not cookies_dir:
                old_cookie_file = str(data.get("cookies_file", "")).strip()
                if old_cookie_file:
                    cookies_dir = os.path.dirname(old_cookie_file)
            if cookies_dir:
                self.cookies_dir_var.set(cookies_dir)
            self.show_browser_var.set(bool(data.get("show_browser", True)))
            self.auto_save_var.set(bool(data.get("auto_save", True)))
            self.per_post_folder_var.set(bool(data.get("per_post_folder", True)))
            self.download_delay_var.set(str(data.get("download_delay", "2")))
            self.cookie_rotation_interval_var.set(str(data.get("cookie_rotation_interval", "120")))
        except Exception as e:
            self.log(f"No se pudo cargar configuración: {e}")

    def _save_settings(self):
        data = {
            "x_user": self.x_user_var.get().strip(),
            "output_dir": self.output_dir_var.get().strip(),
            "cookies_dir": self.cookies_dir_var.get().strip(),
            "show_browser": bool(self.show_browser_var.get()),
            "auto_save": bool(self.auto_save_var.get()),
            "per_post_folder": bool(self.per_post_folder_var.get()),
            "download_delay": str(self.download_delay_var.get()).strip(),
            "cookie_rotation_interval": str(self.cookie_rotation_interval_var.get()).strip(),
        }
        try:
            with open(self._settings_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"No se pudo guardar configuración: {e}")

    def _bind_autosave_events(self):
        self.x_user_var.trace_add("write", lambda *_: self._autosave_if_enabled())
        self.output_dir_var.trace_add("write", lambda *_: self._autosave_if_enabled())
        self.cookies_dir_var.trace_add("write", lambda *_: self._autosave_if_enabled())
        self.show_browser_var.trace_add("write", lambda *_: self._autosave_if_enabled())
        self.per_post_folder_var.trace_add("write", lambda *_: self._autosave_if_enabled())

    def _list_cookie_files(self):
        folder = self.cookies_dir_var.get().strip()
        if not folder or not os.path.isdir(folder):
            return []
        out = []
        for name in os.listdir(folder):
            if name.lower().endswith(".txt"):
                out.append(os.path.abspath(os.path.join(folder, name)))
        out.sort()
        return out

    def _cookie_snapshot(self):
        rows = []
        for path in self._list_cookie_files():
            try:
                st = os.stat(path)
                rows.append((path, int(st.st_mtime), int(st.st_size)))
            except Exception:
                rows.append((path, 0, 0))
        return tuple(rows)

    def _initialize_cookie_rotation(self):
        files = self._list_cookie_files()
        if not files:
            self.active_cookie_path = ""
            self.last_cookie_snapshot = ()
            return
        if self.active_cookie_path not in files:
            self.active_cookie_path = random.choice(files)
        self.last_cookie_snapshot = self._cookie_snapshot()

    def _queue_cookie_rotation(self, reason=""):
        if not self.pending_cookie_rotation:
            self.pending_cookie_rotation = True
            if reason:
                self.log(f"Rotacion de cookies en cola: {reason}")

    def _check_cookie_folder_changes(self):
        current = self._cookie_snapshot()
        if current != self.last_cookie_snapshot:
            self.last_cookie_snapshot = current
            self._queue_cookie_rotation("cambios detectados en carpeta de cookies")

    def _apply_pending_cookie_rotation(self):
        if not self.pending_cookie_rotation:
            return False
        files = self._list_cookie_files()
        if not files:
            self.active_cookie_path = ""
            self.pending_cookie_rotation = False
            self.log("Rotacion pendiente cancelada: no hay cookies .txt en carpeta")
            return False

        current = self.active_cookie_path
        choices = [p for p in files if p != current]
        if not choices:
            choices = files
        self.active_cookie_path = random.choice(choices)
        self.pending_cookie_rotation = False
        self.last_cookie_snapshot = self._cookie_snapshot()
        self.log(f"Cookie activa rotada: {os.path.basename(self.active_cookie_path)}")
        return True

    def _active_cookie_for_use(self):
        path = str(self.active_cookie_path or "").strip()
        if path and os.path.isfile(path):
            return path
        files = self._list_cookie_files()
        if files:
            self.active_cookie_path = files[0]
            self.last_cookie_snapshot = self._cookie_snapshot()
            return self.active_cookie_path
        return ""

    def _on_toggle_autosave(self):
        if self.auto_save_var.get():
            self._save_settings()

    def _autosave_if_enabled(self):
        if self.auto_save_var.get():
            self._save_settings()

    def _set_progress_counts(self, detected=None, downloaded=None, increment_detected=0, increment_downloaded=0):
        with self.state_lock:
            if detected is not None:
                self.total_detected = int(detected)
            if downloaded is not None:
                self.total_downloaded = int(downloaded)
            if increment_detected:
                self.total_detected += int(increment_detected)
            if increment_downloaded:
                self.total_downloaded += int(increment_downloaded)

    def _refresh_progress_widgets(self, force=False):
        with self.state_lock:
            detected = self.total_detected
            downloaded = self.total_downloaded
        if not hasattr(self, "progress_bar"):
            return
        max_value = max(1, detected)
        self.progress_bar.configure(maximum=max_value)
        self.progress_bar.configure(value=min(downloaded, max_value))
        self.progress_label.configure(text=f"Progreso: {downloaded} / {detected}")

    def _parse_cookies(self, file_path):
        cookies = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.startswith('#') and line.strip():
                        parts = line.strip().split('\t')
                        if len(parts) >= 7:
                            cookies.append({
                                'name': parts[5],
                                'value': parts[6],
                                'domain': parts[0] if parts[0].startswith('.') else f".{parts[0]}",
                                'path': parts[2],
                                'expires': int(parts[4]) if int(parts[4]) != -1 else int(time.time() + 86400),
                                'secure': parts[3] == 'TRUE',
                                'sameSite': 'None'
                            })
            return cookies
        except Exception as e:
            self.log(f"Error cookies: {e}")
            return []

    def _dismiss_x_overlays(self, page):
        try:
            page.click(
                'div[role="button"]:has-text("Yes"), '
                'div[role="button"]:has-text("View"), '
                'div[role="button"]:has-text("Mostrar"), '
                'div[role="button"]:has-text("Ver"), '
                'div[role="button"]:has-text("Try again"), '
                'div[role="button"]:has-text("Reintentar"), '
                'div[role="button"]:has-text("Reload"), '
                'div[role="button"]:has-text("Recargar")',
                timeout=2500,
            )
        except Exception:
            pass

    def _load_x_page_with_retries(self, page, url, required_selector=None, label="pagina", max_attempts=4, goto_timeout=60000):
        for attempt in range(1, max_attempts + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
                page.wait_for_timeout(1800 + (attempt * 500))
                self._dismiss_x_overlays(page)

                if not required_selector:
                    return True

                try:
                    page.wait_for_selector(required_selector, timeout=6500)
                    return True
                except Exception:
                    # Estado frecuente de bloqueo temporal: logo de X grande o pantalla vacia.
                    article_count = page.locator("article").count()
                    status_link_count = page.locator('a[href*="/status/"]').count()
                    if article_count == 0 and status_link_count == 0:
                        self.log(f"{label}: intento {attempt}/{max_attempts} sin contenido util (posible bloqueo temporal), reintentando...")
                        self._queue_cookie_rotation("pagina no carga (logo/blank)")
                    else:
                        self.log(f"{label}: intento {attempt}/{max_attempts} sin selector esperado, reintentando...")

                if attempt < max_attempts:
                    self._apply_pending_cookie_rotation()
                    cookie_file = self._active_cookie_for_use()
                    if cookie_file:
                        cookies = self._parse_cookies(cookie_file)
                        if cookies:
                            try:
                                page.context.add_cookies(cookies)
                            except Exception:
                                pass
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1200 + (attempt * 400))
                        self._dismiss_x_overlays(page)
                    except Exception:
                        pass
            except Exception as e:
                self.log(f"{label}: intento {attempt}/{max_attempts} fallo al cargar ({e})")
                if attempt < max_attempts:
                    page.wait_for_timeout(1300 + (attempt * 450))

        return False

    def start_process(self):
        if not self.x_user_var.get(): return
        self.seen_urls.clear()
        self.processed_tweet_ids.clear()
        self.post_records = {}
        self.pending_cookie_rotation = False
        self._initialize_cookie_rotation()
        self._set_progress_counts(detected=0, downloaded=0)
        self._autosave_if_enabled()
        self.running = True
        self.stop_event.clear()
        threading.Thread(target=self._browser_scanner, daemon=True).start()

    def stop_process(self):
        self.stop_event.set()
        self.running = False
        self._save_settings()
        self.log("Deteniendo...")

    def _restart_application(self):
        try:
            if self.running:
                self.stop_process()
                self.root.update_idletasks()
            self._save_settings()
            python_exe = sys.executable
            os.execl(python_exe, python_exe, *sys.argv)
        except Exception as e:
            messagebox.showerror("Reiniciar", f"No se pudo reiniciar la app: {e}")

    def _browser_scanner(self):
        user = self.x_user_var.get().strip().replace("@", "")
        headless = not self.show_browser_var.get()
        
        with sync_playwright() as p:
            self.log("Lanzando navegador...")
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            cookie_file = self._active_cookie_for_use()
            if cookie_file:
                cookies = self._parse_cookies(cookie_file)
                if cookies:
                    context.add_cookies(cookies)
                    self.log(f"Cookie activa cargada ({os.path.basename(cookie_file)}): {len(cookies)}")

            page = context.new_page()
            detail_page = context.new_page()
            self.log(f"Accediendo a x.com/{user}/media...")
            
            try:
                media_url = f"https://x.com/{user}/media"
                ok_open = self._load_x_page_with_retries(
                    page,
                    media_url,
                    required_selector='a[href*="/status/"]',
                    label="Carga media X",
                    max_attempts=5,
                )
                if not ok_open:
                    self.log("No fue posible cargar /media tras varios intentos (posible bloqueo temporal).")
                    return

                last_height = 0
                stagnant_rounds = 0
                max_stagnant_rounds = 12
                sequence_counter = 0
                self.last_rotation_time = time.time()
                
                for i in range(220):
                    if self.stop_event.is_set(): break

                    detected_posts = {}  # {tweet_id: media_item}
                    
                    # Detectar posts en la grilla (solo toma 1 por post para referencias)
                    for media_item in self._extract_media_items(page, user):
                        tweet_id = media_item["tweet_id"]
                        if tweet_id not in detected_posts:
                            detected_posts[tweet_id] = media_item

                    pending_posts = [
                        (tweet_id, reference_item)
                        for tweet_id, reference_item in detected_posts.items()
                        if tweet_id not in self.processed_tweet_ids
                    ]
                    found_in_scroll = len(pending_posts)
                    if found_in_scroll:
                        self.log(f"Scroll {i+1}: +{found_in_scroll} posts nuevos detectados. Extrayendo media items...")

                        for tweet_id, reference_item in pending_posts:
                            if self.stop_event.is_set():
                                break
                            
                            # Preparar record del post
                            detail_page.wait_for_timeout(180)
                            post_record = self._prepare_post_record(detail_page, reference_item)

                            # Fast-path por disco: si ya hay media en la carpeta del post,
                            # evitamos sondeo de red (y pestaña extra) para no trabar el flujo.
                            post_folder = str((post_record or {}).get("folder") or "").strip()
                            existing_media = self._collect_media_files(post_folder) if post_folder else set()
                            if existing_media:
                                self.log(f"Post {tweet_id}: ya existe en disco ({len(existing_media)} archivos), omitiendo sondeo de red.")
                                self.processed_tweet_ids.add(tweet_id)
                                continue
                            
                            # Extraer TODOS los media items de este post desde la página detail
                            all_media_items = self._extract_media_items_from_detail_page(detail_page, user, tweet_id)
                            self.log(f"Post {tweet_id}: {len(all_media_items)} media items encontrados")
                            
                            # Descargar todos los items del mismo post
                            for media_item in all_media_items:
                                if self.stop_event.is_set():
                                    break
                                
                                item_key = media_item["key"]
                                if item_key not in self.seen_urls:
                                    self.seen_urls.add(item_key)
                                    self._set_progress_counts(increment_detected=1)
                                    sequence_counter += 1
                                    media_item["sequence"] = sequence_counter
                                    
                                    ok = self._download_item_with_retries(media_item, post_record, max_retries=2)
                                    if ok:
                                        self._set_progress_counts(increment_downloaded=1)
                                    
                                    # Aplicar delay entre descargas
                                    try:
                                        delay = float(self.download_delay_var.get() or "2")
                                    except:
                                        delay = 2
                                    if delay > 0:
                                        detail_page.wait_for_timeout(int(delay * 1000))
                                    
                                    # Revisar y aplicar rotación por tiempo (no por post)
                                    current_time = time.time()
                                    try:
                                        rotation_interval = float(self.cookie_rotation_interval_var.get() or "120")
                                    except:
                                        rotation_interval = 120
                                    
                                    if rotation_interval > 0 and (current_time - self.last_rotation_time) >= rotation_interval:
                                        self._queue_cookie_rotation(f"intervalo de tiempo transcurrido ({rotation_interval}s)")
                                        self.last_rotation_time = current_time
                                    
                                    # Aplicar rotación si está pendiente
                                    if self._apply_pending_cookie_rotation():
                                        next_cookie = self._active_cookie_for_use()
                                        if next_cookie:
                                            next_cookies = self._parse_cookies(next_cookie)
                                            if next_cookies:
                                                try:
                                                    context.add_cookies(next_cookies)
                                                except Exception:
                                                    pass

                            self.processed_tweet_ids.add(tweet_id)
                        
                        stagnant_rounds = 0
                    else:
                        self.log(f"Scroll {i+1}: sin posts nuevos en esta sesión.")
                        stagnant_rounds += 1
                    
                    # Scroll más robusto para timeline virtualizada.
                    page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.9));")
                    page.wait_for_timeout(900)
                    
                    new_height = page.evaluate("document.body.scrollHeight")
                    if new_height != last_height:
                        last_height = new_height
                    elif i > 8:
                        # Empujón extra para desbloquear más carga cuando se estanca.
                        page.keyboard.press("End")
                        page.wait_for_timeout(700)

                    if stagnant_rounds >= max_stagnant_rounds:
                        self.log("Sin contenido nuevo tras varios intentos; fin de escaneo.")
                        break

            except Exception as e:
                self.log(f"Error durante navegación: {str(e)}")

            self.log("Escaneo finalizado.")
            self.running = False
            time.sleep(2)
            browser.close()

    def _download_item_with_retries(self, item, post_record, max_retries=2):
        item_data = dict(item or {})
        media_type = item_data.get("type", "video")
        url = item_data.get("url")
        sequence = int(item_data.get("sequence", 0))
        filename_hint = item_data.get("filename_hint")
        media_key = str(item_data.get("key") or "").strip()

        if not url:
            return False

        out_dir = self.output_dir_var.get()
        if isinstance(post_record, dict) and post_record.get("folder"):
            out_dir = post_record["folder"]

        if isinstance(post_record, dict) and self._is_item_already_downloaded(post_record, item_data, out_dir):
            self.log(f"Ya existe, omitido: {url}")
            return True

        attempt = 0
        while attempt <= max_retries and not self.stop_event.is_set():
            if media_type == "photo":
                ok, retryable, saved_path = self._download_image(url, filename_hint, sequence=sequence, out_dir=out_dir)
            else:
                ok, retryable, saved_path = self._run_yt_dlp(url, sequence=sequence, out_dir=out_dir)

            if ok:
                if isinstance(post_record, dict):
                    self._register_downloaded_file(
                        post_record,
                        saved_path,
                        media_type,
                        sequence,
                        media_key=media_key,
                        source_url=url,
                        item_data=item_data,
                    )
                return True

            attempt += 1
            if attempt <= max_retries and retryable:
                self.log(f"Reintentando ({attempt}/{max_retries}): {url}")
            else:
                break
        return False

    def _extract_media_items(self, page, user):
        """Extrae elementos de multimedia (video/foto) desde la grilla de /media."""
        try:
            tiles = page.eval_on_selector_all(
                'li[id^="verticalGridItem-"] a[href*="/status/"]',
                '''els => els.map(a => {
                    const href = a.getAttribute("href") || "";
                    const img = (a.querySelector("img") && a.querySelector("img").getAttribute("src")) || "";
                    return { href, img };
                })''',
            )
        except Exception:
            return []

        items = []
        seen_keys = set()
        for tile in tiles:
            href = (tile or {}).get("href", "")
            img = (tile or {}).get("img", "")

            m = re.search(r"/status/(\d+)(?:/(photo|video)/(\d+))?", href)
            if not m:
                continue
            tweet_id = m.group(1)
            media_kind = m.group(2)
            media_idx = m.group(3) or "1"

            if media_kind == "video":
                download_url = f"https://x.com/{user}/status/{tweet_id}/video/{media_idx}"
                key = f"video:{tweet_id}:{media_idx}"
                item = {
                    "type": "video",
                    "url": download_url,
                    "status_url": f"https://x.com/{user}/status/{tweet_id}",
                    "key": key,
                    "tweet_id": tweet_id,
                }
            elif media_kind == "photo" and img:
                # pbs.twimg thumbnails -> intentar versión original.
                parsed = urllib.parse.urlparse(img)
                q = urllib.parse.parse_qs(parsed.query)
                fmt = (q.get("format") or [None])[0]
                ext = fmt or os.path.splitext(parsed.path)[1].replace(".", "") or "jpg"
                q["name"] = ["orig"]
                normalized_query = urllib.parse.urlencode(q, doseq=True)
                image_url = urllib.parse.urlunparse(parsed._replace(query=normalized_query))

                key = f"photo:{tweet_id}:{media_idx}"
                item = {
                    "type": "photo",
                    "url": image_url,
                    "status_url": f"https://x.com/{user}/status/{tweet_id}",
                    "key": key,
                    "tweet_id": tweet_id,
                    "filename_hint": f"{tweet_id}_photo_{media_idx}.{ext}",
                }
            else:
                continue

            if key in seen_keys:
                continue
            seen_keys.add(key)
            items.append(item)
        return items

    def _extract_media_items_from_detail_page(self, page, user, tweet_id):
        """Extrae TODOS los items media (fotos y videos) desde una página detail de un tweet específico.
        Esto es importante para posts con múltiples fotos - la grilla solo muestra 1 thumbnail."""
        items = []
        seen_keys = set()
        
        try:
            # Intentar encontrar todas las imágenes dentro del artículo del tweet
            try:
                # Buscar SOLO imágenes de media (no avatares, no otras)
                # Las imágenes reales del post tienen pbs.twimg.com/media/ en la URL
                img_elements = page.eval_on_selector_all(
                    'article img[src*="pbs.twimg.com/media"]',
                    '''els => els.map(img => ({
                        src: img.getAttribute("src") || "",
                        alt: img.getAttribute("alt") || ""
                    }))'''
                )
                
                photo_idx = 1
                for img_data in img_elements:
                    src = (img_data or {}).get("src", "")
                    if not src:
                        continue
                    
                    # Filtrar: solo media reales, no profile_images, no sticky, etc
                    if "profile_images" in src or "sticky" in src or "emoji" in src or "avatars" in src:
                        continue
                    
                    if "pbs.twimg.com/media" not in src:
                        continue
                    
                    # Convertir thumbnail a imagen original
                    parsed = urllib.parse.urlparse(src)
                    q = urllib.parse.parse_qs(parsed.query)
                    fmt = (q.get("format") or [None])[0]
                    ext = fmt or "jpg"
                    q["name"] = ["orig"]
                    normalized_query = urllib.parse.urlencode(q, doseq=True)
                    image_url = urllib.parse.urlunparse(parsed._replace(query=normalized_query))
                    media_id = os.path.splitext(os.path.basename(parsed.path))[0]
                    if not media_id:
                        media_id = f"idx{photo_idx}"
                    
                    key = f"photo:{tweet_id}:{media_id}"
                    if key not in seen_keys:
                        item = {
                            "type": "photo",
                            "url": image_url,
                            "status_url": f"https://x.com/{user}/status/{tweet_id}",
                            "key": key,
                            "tweet_id": tweet_id,
                            "filename_hint": f"{tweet_id}_photo_{media_id}.{ext}",
                            "photo_number": str(photo_idx),
                            "media_id": media_id,
                        }
                        seen_keys.add(key)
                        items.append(item)
                        photo_idx += 1
            except Exception as e:
                self.log(f"Error extrayendo fotos de detail: {e}")

            # Anti-bloqueo: desactivado el sondeo en pestaña secundaria (/photo/N)
            # para evitar abrir/recargar el mismo post repetidamente.
            
            # Buscar videos en la página detail
            try:
                video_els = page.eval_on_selector_all(
                    'article [data-testid="video"], article video',
                    '''els => els.map(v => ({ found: true }))'''
                )
                if video_els and len(video_els) > 0:
                    # Si hay un video, se descarga desde la URL de estado con /video/1
                    key = f"video:{tweet_id}:1"
                    if key not in seen_keys:
                        download_url = f"https://x.com/{user}/status/{tweet_id}/video/1"
                        item = {
                            "type": "video",
                            "url": download_url,
                            "status_url": f"https://x.com/{user}/status/{tweet_id}",
                            "key": key,
                            "tweet_id": tweet_id,
                        }
                        seen_keys.add(key)
                        items.append(item)
            except Exception:
                pass
        
        except Exception as e:
            self.log(f"Error extrayendo media items desde detail: {e}")
        
        return items

    def _download_image(self, url, filename_hint=None, sequence=0, out_dir=None):
        try:
            target_dir = out_dir or self.output_dir_var.get()
            os.makedirs(target_dir, exist_ok=True)
            if filename_hint:
                out_name = f"{sequence:05d}_{filename_hint}" if sequence else filename_hint
            else:
                parsed = urllib.parse.urlparse(url)
                q = urllib.parse.parse_qs(parsed.query)
                fmt = (q.get("format") or [None])[0]
                ext = fmt or "jpg"
                if sequence:
                    out_name = f"{sequence:05d}_image.{ext}"
                else:
                    out_name = f"image_{int(time.time()*1000)}.{ext}"

            out_path = os.path.join(target_dir, out_name)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as resp, open(out_path, "wb") as f:
                f.write(resp.read())
            self.log(f"Descargado OK: {out_name}")
            return True, True, out_path
        except Exception as e:
            err = str(e)
            self.log(f"Fallo imagen en {url}: {err}")
            low = err.lower()
            if "403" in low or "forbidden" in low or "rate" in low or "tempor" in low:
                self._queue_cookie_rotation("fallo de imagen por bloqueo/auth")
            return False, True, None

    def _run_yt_dlp(self, url, sequence=0, out_dir=None):
        # yt-dlp usará el mismo archivo de cookies para el login
        target_dir = out_dir or self.output_dir_var.get()
        os.makedirs(target_dir, exist_ok=True)
        before_files = set(os.listdir(target_dir))
        if sequence:
            out = os.path.join(target_dir, f"{sequence:05d}_%(id)s.%(ext)s")
        else:
            out = os.path.join(target_dir, "%(id)s.%(ext)s")
        args = ["yt-dlp", "--quiet", "--no-warnings", "-o", out]
        cookie_file = self._active_cookie_for_use()
        if cookie_file:
            args += ["--cookies", cookie_file]
        args.append(url)

        result = subprocess.run(
            args,
            creationflags=CREATE_NO_WINDOW,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            after_files = set(os.listdir(target_dir))
            new_files = [
                name for name in (after_files - before_files)
                if not name.endswith(".part") and not name.endswith(".ytdl")
            ]
            saved_path = None
            if new_files:
                new_files.sort(key=lambda n: os.path.getmtime(os.path.join(target_dir, n)), reverse=True)
                saved_path = os.path.join(target_dir, new_files[0])
            self.log(f"Descargado OK: {url}")
            return True, True, saved_path

        err = (result.stderr or result.stdout or "").strip()
        if err:
            err = err.splitlines()[-1]
        self.log(f"Fallo descarga ({result.returncode}) en {url}: {err or 'sin detalle'}")

        lower_err = (err or "").lower()
        non_retryable = (
            "no video could be found" in lower_err
            or "unsupported url" in lower_err
            or "unsupported" in lower_err
        )
        rate_limited = (
            "rate limit" in lower_err
            or "try again later" in lower_err
            or "temporarily" in lower_err
            or "cookie" in lower_err
            or "login" in lower_err
            or "403" in lower_err
        )
        if rate_limited:
            self._queue_cookie_rotation("fallo de red/autenticacion en descarga")
        return False, (not non_retryable), None

    def _sanitize_for_path(self, text):
        cleaned = re.sub(r'[\\/:*?"<>|]+', "_", str(text or "").strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned or "sin_dato"

    def _format_display_datetime(self, iso_value):
        if not iso_value:
            return "sin fecha"
        raw = str(iso_value).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return raw

    def _folder_name_from_post(self, posted_at_iso, tweet_id):
        base_dt = None
        if posted_at_iso:
            try:
                base_dt = datetime.fromisoformat(str(posted_at_iso).replace("Z", "+00:00")).astimezone()
            except Exception:
                base_dt = None
        if base_dt is None:
            base_dt = datetime.now()
        stamp = base_dt.strftime("%Y-%m-%d_%H-%M-%S")
        sid = self._sanitize_for_path(tweet_id)
        return f"{stamp}__tweet_{sid}"

    def _tweet_id_to_iso_datetime(self, tweet_id):
        try:
            num = int(str(tweet_id).strip())
            if num <= 0:
                return ""
            # Snowflake timestamp in ms: (id >> 22) + Twitter epoch.
            unix_ms = (num >> 22) + 1288834974657
            dt = datetime.fromtimestamp(unix_ms / 1000.0)
            return dt.isoformat()
        except Exception:
            return ""

    def _fetch_post_details(self, detail_page, status_url, tweet_id):
        result = {"text": "", "posted_at": "", "status_url": status_url or ""}
        if not status_url:
            return result
        try:
            ok_open = self._load_x_page_with_retries(
                detail_page,
                status_url,
                required_selector="article, time",
                label=f"Detalle post {tweet_id}",
                max_attempts=1,
                goto_timeout=15000,
            )
            if not ok_open:
                return result

            target_article = detail_page.locator(f'article:has(a[href*="/status/{tweet_id}"])').first
            if target_article.count() == 0:
                target_article = detail_page.locator("article").first

            if target_article.count() > 0:
                text_nodes = target_article.locator('[data-testid="tweetText"]')
                chunks = []
                for i in range(min(text_nodes.count(), 10)):
                    chunk = (text_nodes.nth(i).inner_text() or "").strip()
                    if chunk:
                        chunks.append(chunk)
                if chunks:
                    result["text"] = "\n".join(chunks)

                time_node = target_article.locator("time").first
                if time_node.count() > 0:
                    result["posted_at"] = (time_node.get_attribute("datetime") or "").strip()

                link_node = target_article.locator('a[href*="/status/"]').first
                if link_node.count() > 0:
                    href = (link_node.get_attribute("href") or "").strip()
                    if href:
                        result["status_url"] = urllib.parse.urljoin("https://x.com", href)

            if not result["posted_at"]:
                page_time = detail_page.locator("time").first
                if page_time.count() > 0:
                    result["posted_at"] = (page_time.get_attribute("datetime") or "").strip()
        except Exception as e:
            self.log(f"No se pudo leer texto del post {tweet_id}: {e}")
        return result

    def _write_post_record_files(self, record):
        post_dir = record.get("folder") or self.output_dir_var.get()
        os.makedirs(post_dir, exist_ok=True)
        metadata_path = os.path.join(post_dir, "post.json")
        text_path = os.path.join(post_dir, "post.txt")

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        with open(text_path, "w", encoding="utf-8") as f:
            f.write(record.get("text") or "")

    def _prepare_post_record(self, detail_page, media_item):
        tweet_id = str(media_item.get("tweet_id") or media_item.get("key") or "sin_id")
        cached = self.post_records.get(tweet_id)
        if cached:
            return cached

        # Reusar metadata local si ya existe para evitar recargas de red del mismo post.
        existing = self._find_existing_post_record(tweet_id)
        if existing:
            self.post_records[tweet_id] = existing
            return existing

        status_url = media_item.get("status_url") or ""
        details = self._fetch_post_details(detail_page, status_url, tweet_id)
        posted_at_iso = details.get("posted_at") or self._tweet_id_to_iso_datetime(tweet_id)
        posted_at_display = self._format_display_datetime(posted_at_iso)

        base_dir = self.output_dir_var.get()
        folder_name = self._folder_name_from_post(posted_at_iso, tweet_id)
        if self.per_post_folder_var.get():
            post_dir = os.path.join(base_dir, folder_name)
        else:
            post_dir = os.path.join(base_dir, "posts_meta", folder_name)

        record = {
            "tweet_id": tweet_id,
            "status_url": details.get("status_url") or status_url,
            "posted_at_iso": posted_at_iso,
            "posted_at_display": posted_at_display,
            "text": details.get("text") or "",
            "folder": post_dir,
            "files": [],
        }
        self._write_post_record_files(record)
        self.post_records[tweet_id] = record
        return record

    def _find_existing_post_record(self, tweet_id):
        tid = str(tweet_id or "").strip()
        if not tid:
            return None
        base_dir = self.output_dir_var.get().strip()
        if not base_dir or not os.path.isdir(base_dir):
            return None

        candidates = []
        for root_dir, _dirs, files in os.walk(base_dir):
            if "post.json" in files and f"__tweet_{tid}" in os.path.basename(root_dir):
                candidates.append(os.path.join(root_dir, "post.json"))

        for meta_path in candidates:
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    post = json.load(f)
                post_tweet_id = str(post.get("tweet_id") or "").strip()
                if post_tweet_id and post_tweet_id != tid:
                    continue
                folder = os.path.dirname(meta_path)
                post["tweet_id"] = tid
                post["folder"] = folder
                self._normalize_post_files(post)
                return post
            except Exception:
                continue
        return None

    def _item_source_signature(self, item_data):
        media_type = str((item_data or {}).get("type") or "").strip().lower()
        url = str((item_data or {}).get("url") or "").strip()
        tweet_id = str((item_data or {}).get("tweet_id") or "").strip()
        media_id = str((item_data or {}).get("media_id") or "").strip()

        if media_type == "photo" and url:
            try:
                parsed = urllib.parse.urlparse(url)
                path = str(parsed.path or "")
                if "/media/" in path:
                    token = os.path.splitext(os.path.basename(path))[0]
                    if token:
                        return f"photo:{tweet_id}:{token}"
            except Exception:
                pass
            if media_id:
                return f"photo:{tweet_id}:{media_id}"

        if media_type == "video" and tweet_id:
            m = re.search(r"/video/(\d+)", url)
            idx = m.group(1) if m else "1"
            return f"video:{tweet_id}:{idx}"

        return ""

    def _register_downloaded_file(self, record, saved_path, media_type, sequence, media_key="", source_url="", item_data=None):
        if not saved_path:
            return
        rel_name = os.path.basename(saved_path)
        files = record.setdefault("files", [])
        source_signature = self._item_source_signature(item_data or {})
        for row in files:
            if row.get("name") == rel_name:
                return
            if media_key and str(row.get("media_key") or "").strip() == media_key:
                return
            if source_signature and str(row.get("source_signature") or "").strip() == source_signature:
                return
        files.append({
            "name": rel_name,
            "path": saved_path,
            "type": media_type,
            "sequence": int(sequence or 0),
            "media_key": str(media_key or ""),
            "source_url": str(source_url or ""),
            "source_signature": str(source_signature or ""),
        })
        files.sort(key=lambda x: int(x.get("sequence") or 0))
        self._write_post_record_files(record)

    def _is_item_already_downloaded(self, post_record, item_data, out_dir):
        rows = self._normalize_post_files(post_record)
        media_key = str(item_data.get("key") or "").strip()
        media_type = str(item_data.get("type") or "").strip().lower()
        tweet_id = str(item_data.get("tweet_id") or "").strip()
        filename_hint = str(item_data.get("filename_hint") or "").strip()
        photo_number = str(item_data.get("photo_number") or "").strip()
        source_signature = self._item_source_signature(item_data)

        if source_signature:
            for row in rows:
                if str(row.get("source_signature") or "").strip() == source_signature:
                    return True

        if media_key:
            for row in rows:
                if str(row.get("media_key") or "").strip() == media_key:
                    return True

        if filename_hint:
            for row in rows:
                name = str(row.get("name") or "")
                if filename_hint in name:
                    return True

        media_files = self._collect_media_files(out_dir)
        if tweet_id:
            if media_type == "photo":
                # Buscar por número EXACTO de foto en el mismo post
                if photo_number:
                    exact_token = f"{tweet_id}_photo_{photo_number}"
                    for p in media_files:
                        basename = os.path.basename(p)
                        if exact_token in basename:
                            return True
                media_id = str(item_data.get("media_id") or "").strip()
                if media_id:
                    exact_media = f"{tweet_id}_photo_{media_id}"
                    for p in media_files:
                        basename = os.path.basename(p)
                        if exact_media in basename:
                            return True
            elif media_type == "video":
                video_ext = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
                for p in media_files:
                    name = os.path.basename(p)
                    ext = os.path.splitext(name)[1].lower()
                    if ext in video_ext and tweet_id in name:
                        return True

        return False

    def _open_local_path(self, path):
        if not path:
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Galeria", f"No se pudo abrir: {e}")

    def _collect_media_files(self, base_dir):
        out = set()
        if not os.path.isdir(base_dir):
            return out
        media_ext = {
            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
            ".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi",
        }
        for root, _dirs, files in os.walk(base_dir):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext in media_ext:
                    out.add(os.path.abspath(os.path.join(root, name)))
        return out

    def _media_type_from_path(self, path):
        ext = os.path.splitext(str(path or ""))[1].lower()
        if ext in {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}:
            return "video"
        return "photo"

    def _normalize_post_files(self, post):
        folder = post.get("__folder") or post.get("folder") or ""
        declared = post.get("files") or []
        merged = []
        seen = set()
        max_seq = 0

        for row in declared:
            if not isinstance(row, dict):
                continue
            seq = int(row.get("sequence") or 0)
            max_seq = max(max_seq, seq)
            name = str(row.get("name") or "").strip()
            path = str(row.get("path") or "").strip()
            if not path and name and folder:
                path = os.path.join(folder, name)
            if not path and folder and name:
                path = os.path.join(folder, name)
            if not path:
                continue
            abspath = os.path.abspath(path)
            if not os.path.exists(abspath):
                continue
            key = abspath.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "name": os.path.basename(abspath),
                "path": abspath,
                "type": str(row.get("type") or self._media_type_from_path(abspath)),
                "sequence": seq,
                "media_key": str(row.get("media_key") or "").strip(),
                "source_url": str(row.get("source_url") or "").strip(),
                "source_signature": str(row.get("source_signature") or "").strip(),
            })

        discovered = sorted(self._collect_media_files(folder))
        for media_path in discovered:
            key = media_path.lower()
            if key in seen:
                continue
            max_seq += 1
            merged.append({
                "name": os.path.basename(media_path),
                "path": media_path,
                "type": self._media_type_from_path(media_path),
                "sequence": max_seq,
                "media_key": "",
            })
            seen.add(key)

        merged.sort(key=lambda x: int(x.get("sequence") or 0))
        post["files"] = merged
        return merged

    def _update_post_meta_file(self, post):
        meta_path = str(post.get("__meta_path") or "").strip()
        if not meta_path:
            return
        try:
            payload = {k: v for k, v in post.items() if not str(k).startswith("__")}
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _open_gallery(self):
        base_dir = self.output_dir_var.get().strip()
        if not base_dir or not os.path.isdir(base_dir):
            messagebox.showinfo("Galeria", "La carpeta de salida no existe todavia.")
            return

        win = tk.Toplevel(self.root)
        win.title("Galeria de Posts Descargados")
        win.geometry("1050x700")
        win.configure(bg="#0f1419")

        left = ttk.Frame(win, padding=10)
        left.pack(side="left", fill="y")
        right = ttk.Frame(win, padding=10)
        right.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="Posts").pack(anchor="w")
        posts_list = tk.Listbox(left, width=42, height=30)
        posts_list.pack(fill="y", expand=True, pady=(6, 8))
        posts_list.configure(
            bg="#0f1419",
            fg="#e7e9ea",
            selectbackground="#1d9bf0",
            selectforeground="#ffffff",
            highlightthickness=0,
            borderwidth=0,
            font=("Segoe UI", 10),
        )

        header_var = tk.StringVar(value="Selecciona un post")
        ttk.Label(right, textvariable=header_var, font=("Segoe UI", 11, "bold")).pack(anchor="w")

        text_widget = tk.Text(right, height=10, wrap="word")
        text_widget.pack(fill="x", pady=(8, 8))
        text_widget.configure(
            bg="#0f1419",
            fg="#e7e9ea",
            insertbackground="#e7e9ea",
            relief="flat",
            font=("Segoe UI", 10),
        )

        media_list = tk.Listbox(right, height=14)
        media_list.pack(fill="both", expand=True)
        media_list.configure(
            bg="#0f1419",
            fg="#e7e9ea",
            selectbackground="#1d9bf0",
            selectforeground="#ffffff",
            highlightthickness=0,
            borderwidth=0,
            font=("Consolas", 10),
        )

        preview_title_var = tk.StringVar(value="Preview")
        ttk.Label(right, textvariable=preview_title_var).pack(anchor="w", pady=(6, 2))
        preview_label = ttk.Label(right, text="Selecciona un archivo para preview")
        preview_label.pack(anchor="w")
        preview_image_label = ttk.Label(right)
        preview_image_label.pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(8, 0))

        catalog = []
        media_rows = []
        preview_ref = {"img": None}

        def load_catalog():
            catalog.clear()
            posts_list.delete(0, "end")
            seen_folders = set()
            for root_dir, _, files in os.walk(base_dir):
                if "post.json" not in files:
                    continue
                path = os.path.join(root_dir, "post.json")
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        post = json.load(f)
                    post["__meta_path"] = path
                    post["__folder"] = root_dir
                    self._normalize_post_files(post)
                    self._update_post_meta_file(post)
                    catalog.append(post)
                    seen_folders.add(os.path.abspath(root_dir).lower())
                except Exception:
                    continue

            # Fallback: carpetas con media pero sin post.json tambien deben verse en galeria.
            for root_dir, _, files in os.walk(base_dir):
                abs_root = os.path.abspath(root_dir)
                if abs_root.lower() in seen_folders:
                    continue
                has_media = False
                for n in files:
                    ext = os.path.splitext(n)[1].lower()
                    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}:
                        has_media = True
                        break
                if not has_media:
                    continue

                post = {
                    "tweet_id": os.path.basename(root_dir) or "sin_id",
                    "status_url": "",
                    "posted_at_iso": "",
                    "posted_at_display": "sin fecha",
                    "text": "",
                    "folder": root_dir,
                    "files": [],
                    "__meta_path": "",
                    "__folder": root_dir,
                }
                self._normalize_post_files(post)
                catalog.append(post)
                seen_folders.add(abs_root.lower())

            catalog.sort(key=lambda p: str(p.get("posted_at_iso") or ""), reverse=True)
            for post in catalog:
                text_preview = str(post.get("text") or "").replace("\n", " ").strip()
                if text_preview:
                    text_preview = text_preview[:55] + ("..." if len(text_preview) > 55 else "")
                else:
                    text_preview = "(sin texto)"
                media_count = len(post.get("files") or [])
                label = f"{post.get('posted_at_display') or 'sin fecha'}  [{media_count}]  {text_preview}"
                posts_list.insert("end", label)

        def show_selected(_evt=None):
            media_rows.clear()
            media_list.delete(0, "end")
            text_widget.delete("1.0", "end")
            preview_label.configure(text="Selecciona un archivo para preview")
            preview_image_label.configure(image="")
            preview_ref["img"] = None

            idx = posts_list.curselection()
            if not idx:
                return
            post = catalog[idx[0]]

            header_var.set(
                f"{post.get('posted_at_display') or 'sin fecha'}  |  tweet_id={post.get('tweet_id') or 'sin_id'}"
            )
            text_widget.insert("1.0", post.get("text") or "(sin texto)")

            normalized_rows = self._normalize_post_files(post)
            self._update_post_meta_file(post)
            for row in normalized_rows:
                media_rows.append(row)
                media_list.insert("end", f"{row.get('sequence', 0):05d} - {row.get('type', 'media')} - {row.get('name', '')}")

        def show_media_preview(_evt=None):
            idx = media_list.curselection()
            if not idx:
                preview_label.configure(text="Selecciona un archivo para preview")
                preview_image_label.configure(image="")
                preview_ref["img"] = None
                return
            row = media_rows[idx[0]]
            media_path = str(row.get("path") or "")
            media_type = str(row.get("type") or self._media_type_from_path(media_path))
            preview_title_var.set(f"Preview: {row.get('name', '')}")

            if media_type == "photo" and Image is not None and ImageTk is not None and os.path.exists(media_path):
                try:
                    img = Image.open(media_path)
                    img.thumbnail((420, 280))
                    tk_img = ImageTk.PhotoImage(img)
                    preview_image_label.configure(image=tk_img)
                    preview_ref["img"] = tk_img
                    preview_label.configure(text=f"Imagen ({img.width}x{img.height})")
                    return
                except Exception:
                    pass

            preview_image_label.configure(image="")
            preview_ref["img"] = None
            if media_type == "video":
                preview_label.configure(text="Video detectado. Doble click para abrir.")
            else:
                if Image is None or ImageTk is None:
                    preview_label.configure(text="Instala Pillow para preview de imagenes (pip install pillow).")
                else:
                    preview_label.configure(text="No se pudo generar preview para este archivo.")

        def open_selected_media():
            idx_post = posts_list.curselection()
            idx_media = media_list.curselection()
            if not idx_post or not idx_media:
                return
            post = catalog[idx_post[0]]
            row = media_rows[idx_media[0]]
            media_path = row.get("path")
            if media_path and os.path.exists(media_path):
                self._open_local_path(media_path)
                return
            fallback = os.path.join(post.get("__folder", ""), row.get("name", ""))
            if os.path.exists(fallback):
                self._open_local_path(fallback)

        def open_post_folder():
            idx = posts_list.curselection()
            if not idx:
                return
            post = catalog[idx[0]]
            self._open_local_path(post.get("__folder", ""))

        def open_post_online():
            idx = posts_list.curselection()
            if not idx:
                return
            post = catalog[idx[0]]
            url = str(post.get("status_url") or "").strip()
            if url:
                webbrowser.open(url)

        ttk.Button(left, text="Actualizar", command=load_catalog).pack(fill="x")
        ttk.Button(actions, text="Abrir archivo", command=open_selected_media).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Abrir carpeta", command=open_post_folder).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Abrir en X", command=open_post_online).pack(side="left")

        posts_list.bind("<<ListboxSelect>>", show_selected)
        media_list.bind("<<ListboxSelect>>", show_media_preview)
        media_list.bind("<Double-Button-1>", lambda _e: open_selected_media())
        load_catalog()

if __name__ == "__main__":
    root = tk.Tk()
    app = XMediaApp(root)
    root.mainloop()