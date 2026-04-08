import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from urllib.request import urlopen
from urllib.parse import urlsplit, urlunsplit
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "YT-DLP Downloader (GUI)"
DEFAULT_OUTPUT_TEMPLATE = "%(title).120s [%(id)s].%(ext)s"
UI_LANGUAGE_OPTIONS = {
  "es": "Espa\u00f1ol",
  "en": "English",
}
UI_TEXTS = {
  "es": {
    "ui_language": "Idioma UI:",
    "source": "Fuente",
    "social": "Redes Sociales",
    "options": "Opciones",
    "downloads": "Descargas",
    "feed": "Feed Autom\u00e1tico",
    "log": "Log",
    "load_info": "Cargar info",
    "pick_folder": "Elegir carpeta",
    "best": "BEST (audio+video)",
    "video_quality": "Calidad video:",
    "audio_quality": "Calidad audio:",
    "audio_only": "Solo audio",
    "video_only": "Solo video",
    "restart_app": "Reiniciar app",
    "start_with_windows": "Iniciar con Windows",
    "ig_info": "Info Instagram",
    "ig_best": "Descargar IG BEST",
    "tw_info": "Info Twitter",
    "tw_best": "Descargar TW BEST",
    "paste": "Pegar",
    "image_url": "URL de imagen:",
    "image_output": "Salida imagenes:",
    "pick_image_folder": "Elegir carpeta imagenes",
    "download_image": "Descargar imagen URL",
    "clipboard_monitor": "Monitor portapapeles (auto)",
  },
  "en": {
    "ui_language": "UI Language:",
    "source": "Source",
    "social": "Social Networks",
    "options": "Options",
    "downloads": "Downloads",
    "feed": "Auto Feed",
    "log": "Log",
    "load_info": "Load info",
    "pick_folder": "Choose folder",
    "best": "BEST (audio+video)",
    "video_quality": "Video quality:",
    "audio_quality": "Audio quality:",
    "audio_only": "Audio only",
    "video_only": "Video only",
    "restart_app": "Restart app",
    "start_with_windows": "Start with Windows",
    "ig_info": "Instagram info",
    "ig_best": "Download IG BEST",
    "tw_info": "Twitter info",
    "tw_best": "Download TW BEST",
    "paste": "Paste",
    "image_url": "Image URL:",
    "image_output": "Image output:",
    "pick_image_folder": "Choose image folder",
    "download_image": "Download image URL",
    "clipboard_monitor": "Clipboard monitor (auto)",
  },
}
COOKIE_ERROR_MARKERS = (
  "Failed to decrypt with DPAPI",
  "Could not copy Chrome cookie database",
  "could not decrypt",
  "cookies-from-browser",
)
LANG_CODE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.IGNORECASE)
TWITTER_NO_VIDEO_MARKER = "No video could be found in this tweet"
SOCIAL_IMAGE_ONLY_ERROR_MARKERS = (
  "no video could be found in this tweet",
  "requested format is not available",
  "no video formats found",
  "this post does not contain any video",
)


def parse_time_to_seconds(value: str) -> int:
  text = (value or "").strip()
  if not text:
    return 0

  parts = text.split(":")
  if len(parts) == 1:
    return int(float(parts[0]))
  if len(parts) == 2:
    minutes = int(parts[0])
    seconds = int(float(parts[1]))
    return minutes * 60 + seconds
  if len(parts) == 3:
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(float(parts[2]))
    return hours * 3600 + minutes * 60 + seconds

  raise ValueError("Formato de tiempo no valido")


def format_seconds_to_clock(seconds: int) -> str:
  seconds = max(0, int(seconds))
  hours = seconds // 3600
  minutes = (seconds % 3600) // 60
  sec = seconds % 60
  if hours:
    return f"{hours:02}:{minutes:02}:{sec:02}"
  return f"{minutes:02}:{sec:02}"


class DownloaderApp:
  def __init__(self, root: tk.Tk):
    self.root = root
    self.root.title(APP_TITLE)
    self.root.geometry("900x680")
    self.root.minsize(840, 620)

    self.log_queue: queue.Queue[str] = queue.Queue()
    self.video_info = None
    self.available_languages = ["auto"]
    self.available_qualities = ["best"]
    self.available_audio_qualities = ["best audio"]
    self.available_subtitle_languages = ["auto", "all", "es", "en", "es.*", "en.*"]
    self.available_audio_languages = {"auto"}
    self.available_caption_languages = set()
    self.running = False
    self.cookies_broken = False
    self.updating_tools = False
    self.ffmpeg_location = ""
    self.log_history: list[str] = []
    self.log_write_lock = threading.Lock()
    self.log_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "downloader", "activity.log")

    self.url_var = tk.StringVar()
    self.instagram_url_var = tk.StringVar()
    self.twitter_url_var = tk.StringVar()
    self.image_url_var = tk.StringVar(value="")
    self.output_dir_var = tk.StringVar(value="")
    self.image_output_dir_var = tk.StringVar(value="")
    self.max_size_var = tk.StringVar(value="50")
    self.start_var = tk.StringVar(value="00:00")
    self.end_var = tk.StringVar(value="")
    self.duration_var = tk.StringVar(value="Duracion: -")
    self.selected_language_var = tk.StringVar(value="auto")
    self.selected_quality_var = tk.StringVar(value="best")
    self.selected_audio_quality_var = tk.StringVar(value="best audio")
    self.include_subtitles_var = tk.BooleanVar(value=False)
    self.embed_subtitles_var = tk.BooleanVar(value=True)
    self.audio_only_var = tk.BooleanVar(value=False)
    self.subtitle_lang_var = tk.StringVar(value="auto")
    self.compression_var = tk.StringVar(value="sin_compresion")
    self.use_cookies_var = tk.BooleanVar(value=False)
    self.cookies_browser_var = tk.StringVar(value="chrome")
    self.cookies_file_var = tk.StringVar(value="")
    self.start_with_windows_var = tk.BooleanVar(value=False)
    self.feed_twitter_creator_folders_var = tk.BooleanVar(value=False)
    self.metadata_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "downloader", "metadata")
    self.ui_language_var = tk.StringVar(value="es")
    self.clipboard_monitor_var = tk.BooleanVar(value=False)
    self.clipboard_poll_ms = 1200
    self.clipboard_seen_urls: set[str] = set()
    self.clipboard_pending_url: str | None = None

    self._load_start_with_windows_state()
    self._build_ui()
    self._prepare_log_file()
    self._auto_load_default_cookies_file()
    out_dir = self.output_dir_var.get().strip() or os.path.join(os.path.dirname(os.path.dirname(__file__)), "videos")
    os.makedirs(out_dir, exist_ok=True)

    self.root.after(150, self._drain_log_queue)
    self._check_dependencies()
    self.root.after(self.clipboard_poll_ms, self._poll_clipboard)

  def _is_module_available(self, import_name: str) -> bool:
    try:
      __import__(import_name)
      return True
    except Exception:
      return False

  def _tr(self, key: str, default: str) -> str:
    lang = (self.ui_language_var.get() or "es").strip().lower()
    return UI_TEXTS.get(lang, UI_TEXTS["es"]).get(key, default)

  def _startup_vbs_path(self) -> str:
    appdata = os.environ.get("APPDATA", "").strip()
    startup_dir = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    app_name = os.path.splitext(os.path.basename(__file__))[0]
    return os.path.join(startup_dir, f"{app_name}_autostart.vbs")

  def _legacy_startup_paths(self) -> list[str]:
    startup_path = self._startup_vbs_path()
    startup_dir = os.path.dirname(startup_path)
    app_name = os.path.splitext(os.path.basename(__file__))[0]
    return [
      os.path.join(startup_dir, f"{app_name}_autostart.cmd"),
      os.path.join(startup_dir, f"{app_name}_autostart.bat"),
    ]

  def _preferred_startup_pythonw(self) -> str:
    python_dir = os.path.dirname(os.path.abspath(sys.executable))
    pythonw_path = os.path.join(python_dir, "pythonw.exe")
    if os.path.isfile(pythonw_path):
      return pythonw_path
    return sys.executable

  def _remove_legacy_startup_files(self) -> None:
    for legacy_path in self._legacy_startup_paths():
      if os.path.isfile(legacy_path):
        os.remove(legacy_path)

  def _load_start_with_windows_state(self) -> None:
    try:
      startup_path = self._startup_vbs_path()
      legacy_paths = self._legacy_startup_paths()
      legacy_exists = any(os.path.isfile(path) for path in legacy_paths)
      self.start_with_windows_var.set(os.path.isfile(startup_path) or legacy_exists)
      if legacy_exists:
        self._set_start_with_windows_enabled(True)
        self.start_with_windows_var.set(True)
    except Exception:
      self.start_with_windows_var.set(False)

  def _set_start_with_windows_enabled(self, enabled: bool) -> None:
    startup_vbs = self._startup_vbs_path()
    startup_dir = os.path.dirname(startup_vbs)

    if enabled:
      os.makedirs(startup_dir, exist_ok=True)
      self._remove_legacy_startup_files()
      python_path = self._preferred_startup_pythonw()
      script_path = os.path.abspath(__file__)
      content = (
        'Set shell = CreateObject("WScript.Shell")\n'
        f'shell.Run Chr(34) & "{python_path}" & Chr(34) & " " & Chr(34) & "{script_path}" & Chr(34), 0, False\n'
      )
      with open(startup_vbs, "w", encoding="utf-8", errors="replace") as f:
        f.write(content)
      return

    if os.path.isfile(startup_vbs):
      os.remove(startup_vbs)
    self._remove_legacy_startup_files()

  def _on_start_with_windows_toggle(self) -> None:
    enabled = bool(self.start_with_windows_var.get())
    try:
      self._set_start_with_windows_enabled(enabled)
      if enabled:
        self.log("Inicio con Windows: activado")
      else:
        self.log("Inicio con Windows: desactivado")
    except Exception as exc:
      self.start_with_windows_var.set(not enabled)
      messagebox.showerror(APP_TITLE, f"No se pudo actualizar inicio con Windows: {exc}")

  def restart_application(self) -> None:
    try:
      script_path = os.path.abspath(__file__)
      subprocess.Popen([sys.executable, script_path], cwd=os.path.dirname(script_path))
      self.log("Reiniciando aplicacion...")
      self.root.after(150, self.root.destroy)
    except Exception as exc:
      messagebox.showerror(APP_TITLE, f"No se pudo reiniciar la aplicacion: {exc}")

  def _on_ui_language_change(self, _event=None) -> None:
    # Reconstruye UI para aplicar textos traducidos sin reiniciar app.
    try:
      children = list(self.root.winfo_children())
      for child in children:
        child.destroy()
      self._build_ui()
      if self.log_history:
        self.log_widget.insert("end", "\n".join(self.log_history[-500:]) + "\n")
        self.log_widget.see("end")
    except Exception as exc:
      self.log(f"Aviso idioma UI: {exc}")

  def _install_python_package(self, package_name: str) -> bool:
    proc = subprocess.run(
      [sys.executable, "-m", "pip", "install", package_name],
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      timeout=240,
      check=False,
    )
    return proc.returncode == 0

  def _yt_dlp_cmd(self) -> list[str]:
    yt_path = shutil.which("yt-dlp")
    if yt_path:
      return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]

  def _bootstrap_runtime_dependencies(self) -> None:
    required = [
      ("yt-dlp", "yt_dlp"),
      ("gallery-dl", "gallery_dl"),
      ("imageio-ffmpeg", "imageio_ffmpeg"),
      ("playwright", "playwright"),
    ]

    self.log("Verificando dependencias runtime...")
    for package_name, import_name in required:
      if self._is_module_available(import_name):
        continue
      self.log(f"Instalando dependencia faltante: {package_name}...")
      if self._install_python_package(package_name):
        self.log(f"OK: {package_name} instalado")
        # Para playwright, también necesitamos instalar browsers
        if package_name == "playwright":
          self.log("Instalando navegadores Playwright...")
          subprocess.run(
            [sys.executable, "-m", "playwright", "install"],
            capture_output=True,
            timeout=300,
            check=False,
          )
      else:
        self.log(f"Aviso: no se pudo instalar {package_name}")

    try:
      imageio_ffmpeg = __import__("imageio_ffmpeg")
      ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
      if ffmpeg_exe and os.path.isfile(ffmpeg_exe):
        self.ffmpeg_location = ffmpeg_exe
        self.log(f"FFmpeg runtime embebido listo: {ffmpeg_exe}")
    except Exception:
      pass

  def _build_ui(self) -> None:
    shell = ttk.Frame(self.root)
    shell.pack(fill="both", expand=True)

    canvas = tk.Canvas(shell, highlightthickness=0)
    v_scroll = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
    h_scroll = ttk.Scrollbar(shell, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

    h_scroll.pack(side="bottom", fill="x")
    v_scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    main = ttk.Frame(canvas, padding=12)
    main_window = canvas.create_window((0, 0), window=main, anchor="nw")

    def _on_main_configure(_event=None) -> None:
      canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_configure(event) -> None:
      # Evita forzar ancho por contenido y reduce glitches al redimensionar.
      target_width = max(360, event.width)
      canvas.itemconfigure(main_window, width=target_width)

    def _on_mousewheel(event) -> None:
      if getattr(event, "num", None) == 4:
        delta = 120
      elif getattr(event, "num", None) == 5:
        delta = -120
      else:
        delta = int(getattr(event, "delta", 0) or 0)
      if delta:
        canvas.yview_scroll(int(-1 * (delta / 120)), "units")

    main.bind("<Configure>", _on_main_configure)
    canvas.bind("<Configure>", _on_canvas_configure)
    self.root.bind_all("<MouseWheel>", _on_mousewheel)
    self.root.bind_all("<Button-4>", _on_mousewheel)
    self.root.bind_all("<Button-5>", _on_mousewheel)

    lang_row = ttk.Frame(main)
    lang_row.pack(fill="x", pady=(0, 6))
    ttk.Label(lang_row, text=self._tr("ui_language", "Idioma UI:")).pack(side="left", padx=(0, 8))
    lang_combo = ttk.Combobox(
      lang_row,
      values=[UI_LANGUAGE_OPTIONS["es"], UI_LANGUAGE_OPTIONS["en"]],
      state="readonly",
      width=12,
    )
    current_lang = (self.ui_language_var.get() or "es").strip().lower()
    lang_combo.set(UI_LANGUAGE_OPTIONS.get(current_lang, UI_LANGUAGE_OPTIONS["es"]))

    def _set_lang(_event=None) -> None:
      selected = (lang_combo.get() or "").strip()
      for code, label in UI_LANGUAGE_OPTIONS.items():
        if label == selected:
          self.ui_language_var.set(code)
          break
      self._on_ui_language_change()

    lang_combo.bind("<<ComboboxSelected>>", _set_lang)
    lang_combo.pack(side="left")

    top = ttk.LabelFrame(main, text=self._tr("source", "Fuente"))
    top.pack(fill="x", pady=(0, 10))

    ttk.Label(top, text="URL:").grid(row=0, column=0, sticky="w", padx=8, pady=8)
    ttk.Entry(top, textvariable=self.url_var).grid(row=0, column=1, sticky="ew", padx=8, pady=8)
    ttk.Button(top, text=self._tr("load_info", "Cargar info"), command=self.load_video_info).grid(
      row=0, column=2, padx=8, pady=8
    )
    ttk.Button(top, text=self._tr("paste", "Pegar"), command=lambda: self._paste_to_var(self.url_var)).grid(
      row=0, column=3, padx=8, pady=8
    )

    ttk.Label(top, text="Salida:").grid(row=1, column=0, sticky="w", padx=8, pady=8)
    ttk.Entry(top, textvariable=self.output_dir_var).grid(row=1, column=1, sticky="ew", padx=8, pady=8)
    ttk.Button(top, text=self._tr("pick_folder", "Elegir carpeta"), command=self.pick_output_folder).grid(
      row=1, column=2, padx=8, pady=8
    )

    ttk.Label(top, text=self._tr("image_output", "Salida imagenes:")).grid(row=2, column=0, sticky="w", padx=8, pady=8)
    ttk.Entry(top, textvariable=self.image_output_dir_var).grid(row=2, column=1, sticky="ew", padx=8, pady=8)
    ttk.Button(top, text=self._tr("pick_image_folder", "Elegir carpeta imagenes"), command=self.pick_image_output_folder).grid(
      row=2, column=2, padx=8, pady=8
    )

    ttk.Label(top, text=self._tr("image_url", "URL de imagen:")).grid(row=3, column=0, sticky="w", padx=8, pady=8)
    ttk.Entry(top, textvariable=self.image_url_var).grid(row=3, column=1, sticky="ew", padx=8, pady=8)
    ttk.Button(top, text=self._tr("download_image", "Descargar imagen URL"), command=self.download_image_url).grid(
      row=3, column=2, padx=8, pady=8
    )
    ttk.Button(top, text=self._tr("paste", "Pegar"), command=lambda: self._paste_to_var(self.image_url_var)).grid(
      row=3, column=3, padx=8, pady=8
    )

    top.columnconfigure(1, weight=1)

    social = ttk.LabelFrame(main, text=self._tr("social", "Redes Sociales"))
    social.pack(fill="x", pady=(0, 10))

    ttk.Label(social, text="Instagram URL:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
    ttk.Entry(social, textvariable=self.instagram_url_var).grid(
      row=0, column=1, sticky="ew", padx=8, pady=6
    )
    ttk.Button(social, text=self._tr("ig_info", "Info Instagram"), command=self.load_instagram_info).grid(
      row=0, column=2, padx=6, pady=6
    )
    ttk.Button(social, text=self._tr("ig_best", "Descargar IG BEST"), command=self.download_instagram_best).grid(
      row=0, column=3, padx=6, pady=6
    )
    ttk.Button(social, text=self._tr("paste", "Pegar"), command=lambda: self._paste_to_var(self.instagram_url_var)).grid(
      row=0, column=4, padx=6, pady=6
    )

    ttk.Label(social, text="Twitter/X URL:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
    ttk.Entry(social, textvariable=self.twitter_url_var).grid(
      row=1, column=1, sticky="ew", padx=8, pady=6
    )
    ttk.Button(social, text=self._tr("tw_info", "Info Twitter"), command=self.load_twitter_info).grid(
      row=1, column=2, padx=6, pady=6
    )
    ttk.Button(social, text=self._tr("tw_best", "Descargar TW BEST"), command=self.download_twitter_best).grid(
      row=1, column=3, padx=6, pady=6
    )
    ttk.Button(social, text=self._tr("paste", "Pegar"), command=lambda: self._paste_to_var(self.twitter_url_var)).grid(
      row=1, column=4, padx=6, pady=6
    )

    ttk.Checkbutton(
      social,
      text="Twitter: guardar por creador",
      variable=self.feed_twitter_creator_folders_var,
    ).grid(row=2, column=1, columnspan=2, sticky="w", padx=8, pady=6)

    social.columnconfigure(1, weight=1)

    cfg = ttk.LabelFrame(main, text=self._tr("options", "Opciones"))
    cfg.pack(fill="x", pady=(0, 10))

    ttk.Label(cfg, text="Tamano objetivo (MB):").grid(row=0, column=0, sticky="w", padx=8, pady=6)
    ttk.Entry(cfg, textvariable=self.max_size_var, width=14).grid(row=0, column=1, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, text="Inicio (mm:ss o hh:mm:ss):").grid(
      row=1, column=0, sticky="w", padx=8, pady=6
    )
    ttk.Entry(cfg, textvariable=self.start_var, width=14).grid(row=1, column=1, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, text="Fin (mm:ss o hh:mm:ss):").grid(row=1, column=2, sticky="w", padx=8, pady=6)
    ttk.Entry(cfg, textvariable=self.end_var, width=14).grid(row=1, column=3, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, text="Idioma audio:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
    self.language_combo = ttk.Combobox(
      cfg,
      textvariable=self.selected_language_var,
      values=self.available_languages,
      state="readonly",
      width=16,
    )
    self.language_combo.grid(row=2, column=1, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, text=self._tr("video_quality", "Calidad video:")).grid(row=2, column=2, sticky="e", padx=8, pady=6)
    self.quality_combo = ttk.Combobox(
      cfg,
      textvariable=self.selected_quality_var,
      values=self.available_qualities,
      state="readonly",
      width=12,
    )
    self.quality_combo.grid(row=2, column=3, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, text=self._tr("audio_quality", "Calidad audio:")).grid(row=2, column=4, sticky="e", padx=8, pady=6)
    self.audio_quality_combo = ttk.Combobox(
      cfg,
      textvariable=self.selected_audio_quality_var,
      values=self.available_audio_qualities,
      state="readonly",
      width=14,
    )
    self.audio_quality_combo.grid(row=2, column=5, sticky="w", padx=8, pady=6)

    ttk.Checkbutton(
      cfg,
      text=self._tr("audio_only", "Solo audio"),
      variable=self.audio_only_var,
    ).grid(row=2, column=6, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, textvariable=self.duration_var).grid(row=2, column=7, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, text="Compresion:").grid(row=3, column=0, sticky="w", padx=8, pady=6)
    ttk.Combobox(
      cfg,
      textvariable=self.compression_var,
      values=["sin_compresion", "baja", "media", "alta"],
      state="readonly",
      width=14,
    ).grid(row=3, column=1, sticky="w", padx=8, pady=6)

    ttk.Checkbutton(
      cfg,
      text="Descargar subtitulos",
      variable=self.include_subtitles_var,
    ).grid(row=3, column=2, sticky="w", padx=8, pady=6)
    ttk.Checkbutton(
      cfg,
      text="Embeber subtitulos en MP4 (toggle en reproductor)",
      variable=self.embed_subtitles_var,
    ).grid(row=3, column=3, columnspan=2, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, text="Idioma subtitulos:").grid(row=4, column=0, sticky="w", padx=8, pady=6)
    self.subtitle_combo = ttk.Combobox(
      cfg,
      textvariable=self.subtitle_lang_var,
      values=self.available_subtitle_languages,
      state="readonly",
      width=16,
    )
    self.subtitle_combo.grid(row=4, column=1, sticky="w", padx=8, pady=6)
    ttk.Label(cfg, text="Auto, all, idioma detectado o patron es.*/en.*").grid(row=4, column=2, columnspan=3, sticky="w", padx=8, pady=6)

    ttk.Checkbutton(
      cfg,
      text="Usar cookies del navegador",
      variable=self.use_cookies_var,
    ).grid(row=5, column=0, sticky="w", padx=8, pady=6)
    ttk.Label(cfg, text="Navegador:").grid(row=5, column=1, sticky="e", padx=8, pady=6)
    ttk.Combobox(
      cfg,
      textvariable=self.cookies_browser_var,
      values=["chrome", "edge", "firefox", "brave"],
      state="readonly",
      width=12,
    ).grid(row=5, column=2, sticky="w", padx=8, pady=6)

    ttk.Label(cfg, text="cookies.txt:").grid(row=6, column=0, sticky="w", padx=8, pady=6)
    ttk.Entry(cfg, textvariable=self.cookies_file_var, width=42).grid(row=6, column=1, columnspan=3, sticky="ew", padx=8, pady=6)
    ttk.Button(cfg, text="Elegir cookies", command=self.pick_cookies_file).grid(row=6, column=4, padx=8, pady=6)

    actions = ttk.LabelFrame(main, text=self._tr("downloads", "Descargas"))
    actions.pack(fill="x", pady=(0, 10))

    ttk.Button(actions, text=self._tr("best", "BEST (audio+video)"), command=self.download_best).pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text=self._tr("audio_only", "Solo audio"), command=self.download_audio_only).pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text=self._tr("video_only", "Solo video"), command=self.download_video_only).pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text="Limitar tamano", command=self.download_limited_size).pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text="Recortar segmento", command=self.download_trimmed).pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text=self._tr("restart_app", "Reiniciar app"), command=self.restart_application).pack(
      side="left", padx=8, pady=10
    )
    ttk.Checkbutton(
      actions,
      text=self._tr("start_with_windows", "Iniciar con Windows"),
      variable=self.start_with_windows_var,
      command=self._on_start_with_windows_toggle,
    ).pack(side="left", padx=10, pady=10)
    ttk.Checkbutton(
      actions,
      text=self._tr("clipboard_monitor", "Monitor portapapeles (auto)"),
      variable=self.clipboard_monitor_var,
    ).pack(side="left", padx=14, pady=10)

    log_frame = ttk.LabelFrame(main, text=self._tr("log", "Log"))
    log_frame.pack(fill="both", expand=True)

    self.log_widget = tk.Text(log_frame, height=20, wrap="word")
    self.log_widget.pack(side="left", fill="both", expand=True)

    scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_widget.yview)
    scrollbar.pack(side="right", fill="y")
    self.log_widget.configure(yscrollcommand=scrollbar.set)

    _on_main_configure()

  def _check_dependencies(self) -> None:
    yt = shutil.which("yt-dlp")
    ffmpeg = shutil.which("ffmpeg")
    gallery_dl = shutil.which("gallery-dl")
    yt_module = self._is_module_available("yt_dlp")
    gallery_module = self._is_module_available("gallery_dl")

    if yt:
      self.log(f"OK: yt-dlp detectado en {yt}")
    elif yt_module:
      self.log("OK: yt-dlp disponible como modulo Python (sin PATH)")
    else:
      self.log("ERROR: yt-dlp no disponible. Instala dependencias con requirements.txt en tu venv.")

    if ffmpeg:
      self.log(f"OK: ffmpeg detectado en {ffmpeg}")
    elif self.ffmpeg_location and os.path.isfile(self.ffmpeg_location):
      self.log(f"OK: ffmpeg embebido detectado en {self.ffmpeg_location}")
    else:
      self.log("ERROR: ffmpeg no disponible en PATH ni embebido")

    if gallery_dl:
      self.log(f"OK: gallery-dl detectado en {gallery_dl}")
    elif gallery_module:
      self.log("OK: gallery-dl disponible como modulo Python (sin PATH)")
    else:
      self.log("INFO: gallery-dl no detectado (fallback social opcional)")

  def log(self, message: str) -> None:
    self.log_history.append(message)
    if len(self.log_history) > 5000:
      self.log_history = self.log_history[-5000:]
    self._append_activity_log(message)
    self.log_queue.put(message)

  def _prepare_log_file(self) -> None:
    try:
      os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
      with open(self.log_file_path, "a", encoding="utf-8"):
        pass
      self._append_activity_log("=== Inicio de sesion ===")
    except Exception:
      pass

  def _append_activity_log(self, message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}\n"
    try:
      with self.log_write_lock:
        with open(self.log_file_path, "a", encoding="utf-8", errors="replace") as f:
          f.write(line)
    except Exception:
      pass

  def _drain_log_queue(self) -> None:
    while not self.log_queue.empty():
      message = self.log_queue.get_nowait()
      self.log_widget.insert("end", message + "\n")
      self.log_widget.see("end")
    self.root.after(150, self._drain_log_queue)

  def _safe_url(self) -> str:
    url = self.url_var.get().strip()
    if not url:
      raise ValueError("Debes pegar una URL")
    return url

  def _safe_url_from_var(self, value: str, platform_name: str) -> str:
    url = (value or "").strip()
    if not url:
      raise ValueError(f"Debes pegar URL de {platform_name}")
    return url

  def _safe_image_url(self) -> str:
    url = (self.image_url_var.get() or "").strip()
    if not url:
      raise ValueError("Debes pegar URL de imagen")
    if not self._is_http_url(url):
      raise ValueError("La URL de imagen debe iniciar con http:// o https://")
    return url

  def _is_http_url(self, text: str) -> bool:
    lower = (text or "").strip().lower()
    return lower.startswith("http://") or lower.startswith("https://")

  def _looks_like_image_url(self, url: str) -> bool:
    clean = (url or "").strip().lower()
    if not clean:
      return False

    parts = urlsplit(clean)
    path = (parts.path or "").lower()
    query = (parts.query or "").lower()
    exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".avif")

    if path.endswith(exts):
      return True

    if re.search(r"(?:^|[&?])(format|fm|ext|mime)=(?:image/)?(?:jpg|jpeg|png|webp|gif|bmp|tiff|avif)(?:&|$)", f"?{query}"):
      return True

    # Heuristicas para enlaces de imagen que no llevan extension en la ruta.
    if "pbs.twimg.com/media/" in clean or "twimg.com/media/" in clean:
      return True
    if "/photo/" in path:
      return True

    return False

  def _build_image_output_dir(self, url: str | None = None) -> str:
    out_dir = (self.image_output_dir_var.get() or "").strip()
    if not out_dir:
      out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "images")
    out_dir = self._apply_creator_subfolder(out_dir, url)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

  def pick_image_output_folder(self) -> None:
    selected = filedialog.askdirectory(initialdir=self.image_output_dir_var.get())
    if selected:
      self.image_output_dir_var.set(selected)

  def _paste_to_var(self, target_var: tk.StringVar) -> None:
    try:
      text = self.root.clipboard_get().strip()
    except Exception:
      text = ""
    if text:
      target_var.set(text)

  def _download_direct_image(self, url: str, out_dir: str) -> str | None:
    try:
      parts = urlsplit(url)
      name = os.path.basename(parts.path) or "image"
      if "." not in name:
        name = f"{name}.jpg"
      safe_name = re.sub(r'[<>:"/\\|?*]+', "_", name)
      target = os.path.join(out_dir, safe_name)
      base, ext = os.path.splitext(target)
      idx = 1
      while os.path.exists(target):
        target = f"{base}_{idx}{ext}"
        idx += 1

      with urlopen(url, timeout=30) as resp:
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "image/" not in content_type:
          return None
        data = resp.read()
      with open(target, "wb") as f:
        f.write(data)
      return target
    except Exception:
      return None

  def _download_image_url_internal(self, url: str, source_label: str) -> None:
    def task() -> None:
      self._download_image_url_blocking(url, source_label)

    self._run_background(task, f"DESCARGA IMAGEN ({source_label})")

  def _download_image_url_blocking(self, url: str, source_label: str) -> None:
    out_dir = self._build_image_output_dir(url)
    clean = (url or "").strip()
    if not self._is_http_url(clean):
      raise ValueError("La URL de imagen debe iniciar con http:// o https://")

    self.log(f"Imagen {source_label}: destino={out_dir}")

    if self._looks_like_image_url(clean):
      saved = self._download_direct_image(clean, out_dir)
      if saved:
        self.log(f"Imagen guardada directo: {saved}")
        return

    ok, detail = self._gallery_dl_download(clean, out_dir=out_dir)
    if ok:
      self.log("Imagen descargada via gallery-dl")
      return

    self.log(f"gallery-dl no pudo resolver imagen ({detail}). Reintentando con yt-dlp...")
    output_template = os.path.join(out_dir, DEFAULT_OUTPUT_TEMPLATE)
    self._run_yt_dlp_download(clean, ["-o", output_template], allow_social_fallback=True)

  def download_image_url(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_image_url()
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return
    self._download_image_url_internal(url, "manual")

  def _poll_clipboard(self) -> None:
    try:
      if not self.clipboard_monitor_var.get():
        return

      text = ""
      try:
        text = (self.root.clipboard_get() or "").strip()
      except Exception:
        text = ""

      if text and self._is_http_url(text) and text not in self.clipboard_seen_urls:
        self.clipboard_pending_url = text

      pending = (self.clipboard_pending_url or "").strip()
      if pending and not self.running:
        self.clipboard_seen_urls.add(pending)
        self.clipboard_pending_url = None
        if self._looks_like_image_url(pending):
          self._download_image_url_internal(pending, "clipboard")
        else:
          self.url_var.set(pending)
          self._download_best_for_url(pending, "Clipboard")
    finally:
      self.root.after(self.clipboard_poll_ms, self._poll_clipboard)

  def _ensure_not_running(self) -> None:
    if self.running:
      raise RuntimeError("Ya hay un proceso en ejecucion")

  def _build_output_template(self, url: str | None = None) -> str:
    out_dir = self.output_dir_var.get().strip() or os.path.join(os.path.dirname(os.path.dirname(__file__)), "videos")
    out_dir = self._apply_creator_subfolder(out_dir, url)
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, DEFAULT_OUTPUT_TEMPLATE)

  def _twitter_creator_from_url(self, url: str) -> str | None:
    match = re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/?#]+)/status/\d+", (url or ""), flags=re.IGNORECASE)
    if not match:
      return None
    creator = (match.group(1) or "").strip()
    if not creator:
      return None
    # Evita caracteres no validos en rutas de Windows.
    creator = re.sub(r'[<>:"/\\|?*]+', "_", creator)
    return creator or None

  def _apply_creator_subfolder(self, base_dir: str, url: str | None) -> str:
    out_dir = (base_dir or "").strip()
    if not out_dir:
      return out_dir
    if not self.feed_twitter_creator_folders_var.get():
      return out_dir
    if not self._is_twitter_url(url or ""):
      return out_dir
    creator = self._twitter_creator_from_url(url or "")
    if not creator:
      return out_dir
    return os.path.join(out_dir, creator)

  def pick_output_folder(self) -> None:
    selected = filedialog.askdirectory(initialdir=self.output_dir_var.get())
    if selected:
      self.output_dir_var.set(selected)

  def pick_cookies_file(self) -> None:
    base_dir = os.path.dirname(os.path.dirname(__file__))
    cookies_dir = os.path.join(base_dir, "downloader", "cookies")
    initial_dir = cookies_dir if os.path.isdir(cookies_dir) else os.path.join(base_dir, "downloader")

    selected = filedialog.askopenfilename(
      title="Selecciona archivo cookies.txt",
      initialdir=initial_dir,
      filetypes=[("Cookies", "*.txt"), ("Todos", "*.*")],
    )
    if selected:
      self.cookies_file_var.set(selected)

  def _auto_load_default_cookies_file(self) -> None:
    candidates = self._existing_cookie_files()
    if not candidates:
      return

    self.cookies_file_var.set(candidates[0])
    self.log(f"cookies detectadas automaticamente: principal={candidates[0]}")
    if len(candidates) > 1:
      self.log(f"cookies alterna detectada: {candidates[1]}")

  def _existing_cookie_files(self) -> list[str]:
    base_dir = os.path.dirname(os.path.dirname(__file__))
    manual = (self.cookies_file_var.get() or "").strip()
    candidates = [
      manual,
      os.path.join(base_dir, "downloader", "cookies", "cookies.txt"),
      os.path.join(base_dir, "downloader", "cookies", "cookies2.txt"),
      os.path.join(base_dir, "downloader", "cookies.txt"),
      os.path.join(base_dir, "downloader", "cookies2.txt"),
      os.path.join(base_dir, "cookies.txt"),
      os.path.join(base_dir, "cookies2.txt"),
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for path in candidates:
      clean = (path or "").strip()
      if not clean or not os.path.isfile(clean):
        continue
      key = os.path.normcase(os.path.abspath(clean))
      if key in seen:
        continue
      seen.add(key)
      unique.append(clean)
    return unique

  def _run_background(self, task, label: str) -> None:
    def wrapped():
      self.running = True
      self.log(f"=== {label} ===")
      try:
        task()
        self.log("Proceso completado")
      except Exception as exc:
        self.log(f"ERROR: {exc}")
        self.root.after(0, lambda: messagebox.showerror(APP_TITLE, str(exc)))
      finally:
        self.running = False

    threading.Thread(target=wrapped, daemon=True).start()

  def _run_command(self, cmd: list[str]) -> None:
    self.log("Comando: " + " ".join(cmd))
    process = subprocess.Popen(
      cmd,
      stdin=subprocess.DEVNULL,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
      encoding="utf-8",
      errors="replace",
    )

    started_at = time.monotonic()
    last_output_at = started_at
    phase_label = "ejecucion"
    phase_started_at = started_at
    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
      last_report = 0.0
      while not stop_heartbeat.wait(2.0):
        if process.poll() is not None:
          return
        now = time.monotonic()
        if now - last_output_at < 6.0:
          continue
        if now - last_report < 8.0:
          continue
        elapsed_phase = int(now - phase_started_at)
        elapsed_total = int(now - started_at)
        self.log(
          f"Progreso: procesando ({phase_label})... {elapsed_phase}s en esta fase, {elapsed_total}s total"
        )
        last_report = now

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()

    assert process.stdout is not None
    recent_lines: list[str] = []
    try:
      for line in process.stdout:
        cleaned = line.rstrip()
        last_output_at = time.monotonic()
        lowered = cleaned.lower()
        if "[merger]" in lowered and phase_label != "merge":
          phase_label = "merge"
          phase_started_at = last_output_at
          self.log("Fase detectada: merge de audio+video")
        elif "[videoconvertor]" in lowered and phase_label != "recodificacion":
          phase_label = "recodificacion"
          phase_started_at = last_output_at
          self.log("Fase detectada: recodificacion")
        elif "[download]" in lowered and phase_label != "descarga":
          phase_label = "descarga"
          phase_started_at = last_output_at

        recent_lines.append(cleaned)
        if len(recent_lines) > 30:
          recent_lines.pop(0)
        self.log(cleaned)
      code = process.wait()
    finally:
      stop_heartbeat.set()
      heartbeat_thread.join(timeout=0.2)

    if code != 0:
      detail = "\n".join(recent_lines[-8:]).strip()
      raise RuntimeError(f"Fallo comando con codigo {code}. {detail}")

  def _is_cookie_error(self, text: str) -> bool:
    lowered = (text or "").lower()
    for marker in COOKIE_ERROR_MARKERS:
      if marker.lower() in lowered:
        return True
    return False

  def _common_net_args(
    self,
    url: str,
    include_cookies: bool = True,
    cookie_file_override: str | None = None,
  ) -> list[str]:
    args = [
      "--ignore-config",
      "--no-playlist",
      "--force-overwrites",
      "--socket-timeout",
      "20",
      "--retries",
      "2",
      "--fragment-retries",
      "2",
    ]

    if self.ffmpeg_location and os.path.isfile(self.ffmpeg_location):
      args += ["--ffmpeg-location", self.ffmpeg_location]

    lower = (url or "").lower()
    is_social = self._is_twitter_url(url) or self._is_instagram_url(url)
    if "instagram.com" in lower:
      args += [
        "--add-header",
        "Referer:https://www.instagram.com/",
        "--add-header",
        "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      ]
    elif "twitter.com" in lower or "x.com" in lower:
      args += [
        "--add-header",
        "Referer:https://x.com/",
        "--add-header",
        "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      ]

    if include_cookies:
      cookies_file = (cookie_file_override or "").strip()
      if not cookies_file:
        cookies_file = (self.cookies_file_var.get() or "").strip()
      if not cookies_file:
        existing = self._existing_cookie_files()
        if existing:
          cookies_file = existing[0]

      if cookies_file and os.path.isfile(cookies_file):
        if is_social:
          self.log(f"Usando cookies para red social: {cookies_file}")
        args += ["--cookies", cookies_file]
      elif self.use_cookies_var.get() and not self.cookies_broken:
        browser = (self.cookies_browser_var.get() or "chrome").strip().lower()
        args += ["--cookies-from-browser", browser]

    return args

  def _disable_broken_cookies(self, reason: str) -> None:
    if self.cookies_broken:
      return
    self.cookies_broken = True
    self.root.after(0, lambda: self.use_cookies_var.set(False))
    self.log("Aviso: cookies del navegador desactivadas automaticamente por error de lectura.")
    self.log(f"Detalle cookies: {reason}")

  def _social_auth_hint(self) -> str:
    cookies_file = (self.cookies_file_var.get() or "").strip()
    if cookies_file and os.path.isfile(cookies_file):
      return ""
    return (
      "\nSugerencia: este enlace parece requerir sesion/login en X o restriccion de edad. "
      "Carga un archivo cookies.txt exportado desde tu navegador en la opcion cookies.txt."
    )

  def _auto_update_tools_async(self) -> None:
    if self.updating_tools:
      return

    def worker() -> None:
      self.updating_tools = True
      self.log("Actualizando herramientas (yt-dlp, gallery-dl, ffmpeg)...")
      try:
        pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "gallery-dl"]
        pip_proc = subprocess.run(
          pip_cmd,
          capture_output=True,
          text=True,
          encoding="utf-8",
          errors="replace",
          timeout=240,
          check=False,
        )
        if pip_proc.returncode == 0:
          self.log("Actualizacion Python OK: yt-dlp/gallery-dl al dia.")
        else:
          detail = (pip_proc.stderr or pip_proc.stdout or "sin detalle").strip()
          self.log(f"Aviso: no pude actualizar yt-dlp/gallery-dl automaticamente: {detail}")

        winget_check = subprocess.run(
          ["winget", "--version"],
          capture_output=True,
          text=True,
          encoding="utf-8",
          errors="replace",
          timeout=20,
          check=False,
        )
        if winget_check.returncode == 0:
          ffmpeg_ids = [
            "yt-dlp.FFmpeg_Microsoft.Winget.Source",
            "Gyan.FFmpeg",
            "BtbN.FFmpeg",
          ]
          ffmpeg_updated = False
          for ffmpeg_id in ffmpeg_ids:
            up = subprocess.run(
              [
                "winget",
                "upgrade",
                "--id",
                ffmpeg_id,
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--silent",
              ],
              capture_output=True,
              text=True,
              encoding="utf-8",
              errors="replace",
              timeout=180,
              check=False,
            )
            if up.returncode == 0:
              ffmpeg_updated = True
              self.log(f"Actualizacion FFmpeg OK ({ffmpeg_id}).")
              break
          if not ffmpeg_updated:
            self.log("Aviso: FFmpeg no se pudo actualizar automaticamente por winget (puede ya estar al dia).")
        else:
          self.log("Aviso: winget no disponible; omitiendo auto-update de FFmpeg.")
      except Exception as exc:
        self.log(f"Aviso: error durante auto-update de herramientas: {exc}")
      finally:
        self.updating_tools = False

    threading.Thread(target=worker, daemon=True).start()

  def _normalize_social_url(self, url: str) -> str:
    text = (url or "").strip()
    if not text:
      return text
    try:
      parts = urlsplit(text)
      if self._is_twitter_url(text) or self._is_instagram_url(text):
        # Quita query params de tracking (?s=20, etc.) para mejorar compatibilidad.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
      return text
    return text

  def _is_twitter_url(self, url: str) -> bool:
    lower = (url or "").lower()
    return "x.com/" in lower or "twitter.com/" in lower

  def _is_instagram_url(self, url: str) -> bool:
    return "instagram.com/" in (url or "").lower()

  def _is_tiktok_url(self, url: str) -> bool:
    lower = (url or "").lower()
    return "tiktok.com/" in lower or "vm.tiktok.com/" in lower

  def _is_social_media_url(self, url: str) -> bool:
    lower = (url or "").lower()
    return any(
      marker in lower
      for marker in (
        "x.com/",
        "twitter.com/",
        "instagram.com/",
        "tiktok.com/",
        "vm.tiktok.com/",
        "threads.net/",
        "facebook.com/",
        "fb.watch/",
        "pinterest.",
        "reddit.com/",
        "tumblr.com/",
      )
    )

  def _is_image_only_social_error(self, url: str, error_text: str) -> bool:
    if not self._is_social_media_url(url):
      return False

    lowered = (error_text or "").lower()
    if not lowered:
      return False
    return any(marker in lowered for marker in SOCIAL_IMAGE_ONLY_ERROR_MARKERS)

  def _social_url_likely_image_post(self, url: str) -> bool:
    lower = (url or "").lower()
    if not self._is_social_media_url(lower):
      return False
    return any(
      marker in lower
      for marker in (
        "/photo/",
        "instagram.com/p/",
        "tiktok.com/@",
      )
    )

  def _social_candidate_urls(self, url: str) -> list[str]:
    normalized = self._normalize_social_url(url)
    candidates = [normalized, url]
    lower = (url or "").lower()

    if self._is_twitter_url(url):
      candidates.append(re.sub(r"https?://(?:www\.)?(?:x|twitter)\.com/", "https://fxtwitter.com/", url, flags=re.IGNORECASE))
      candidates.append(re.sub(r"https?://(?:www\.)?(?:x|twitter)\.com/", "https://vxtwitter.com/", url, flags=re.IGNORECASE))
    elif self._is_instagram_url(url):
      candidates.append(re.sub(r"https?://(?:www\.)?instagram\.com/", "https://ddinstagram.com/", url, flags=re.IGNORECASE))

    unique = []
    seen = set()
    for item in candidates:
      key = (item or "").strip()
      if key and key not in seen:
        seen.add(key)
        unique.append(key)
    return unique

  def _run_gallery_dl_raw(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    commands = []
    if shutil.which("gallery-dl"):
      commands.append(["gallery-dl", *args])
    commands.append([sys.executable, "-m", "gallery_dl", *args])

    last = None
    for command in commands:
      proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
      )
      last = proc
      if proc.returncode == 0:
        return proc

      detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).lower()
      if "no module named" in detail and "gallery_dl" in detail:
        self.log("Aviso: gallery-dl no esta instalado en este entorno (sin auto-install en runtime).")

    assert last is not None
    return last

  def _collect_files_recursive(self, root_dir: str) -> set[str]:
    files: set[str] = set()
    if not os.path.isdir(root_dir):
      return files
    for current_root, _, names in os.walk(root_dir):
      for name in names:
        files.add(os.path.join(current_root, name))
    return files

  def _archive_sidecar_metadata_files(self, paths: list[str]) -> None:
    json_paths = [p for p in paths if p.lower().endswith(".json") and os.path.isfile(p)]
    if not json_paths:
      return

    os.makedirs(self.metadata_dir, exist_ok=True)
    moved = 0
    for source in json_paths:
      try:
        target = os.path.join(self.metadata_dir, os.path.basename(source))
        if os.path.abspath(source) == os.path.abspath(target):
          continue
        if os.path.exists(target):
          base, ext = os.path.splitext(os.path.basename(source))
          suffix = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
          target = os.path.join(self.metadata_dir, f"{base}_{suffix}{ext}")
        shutil.move(source, target)
        moved += 1
      except Exception as exc:
        self.log(f"Aviso: no se pudo mover metadata sidecar '{source}': {exc}")

    if moved:
      self.log(f"Metadata sidecar movida a: {self.metadata_dir} ({moved} archivo(s))")

  def _gallery_dl_common_args(self, cookie_file_override: str | None = None) -> list[str]:
    args: list[str] = []
    cookies_file = (cookie_file_override or "").strip()
    if not cookies_file:
      cookies_file = (self.cookies_file_var.get() or "").strip()
    if not cookies_file:
      existing = self._existing_cookie_files()
      if existing:
        cookies_file = existing[0]

    if cookies_file and os.path.isfile(cookies_file):
      args += ["--cookies", cookies_file]
      self.log(f"gallery-dl usando cookies: {cookies_file}")
    return args

  def _gallery_dl_download(self, url: str, out_dir: str | None = None) -> tuple[bool, str]:
    out_dir = (out_dir or "").strip() or self.output_dir_var.get().strip() or os.path.join(os.path.dirname(os.path.dirname(__file__)), "videos")
    os.makedirs(out_dir, exist_ok=True)
    cookie_candidates = self._existing_cookie_files() or [""]
    last_detail = "sin detalle"

    for idx, cookie_file in enumerate(cookie_candidates, start=1):
      before_files = self._collect_files_recursive(out_dir)
      args = [*self._gallery_dl_common_args(cookie_file_override=(cookie_file or None)), "-D", out_dir, url]
      proc = self._run_gallery_dl_raw(args, timeout=180)
      after_files = self._collect_files_recursive(out_dir)
      new_files = sorted(after_files - before_files)
      if new_files:
        self._archive_sidecar_metadata_files(new_files)

      if proc.returncode == 0:
        self.log("Fallback gallery-dl completado correctamente.")
        return True, "ok"

      last_detail = (proc.stderr or proc.stdout or "sin detalle").strip()
      if cookie_file and idx < len(cookie_candidates):
        self.log(f"gallery-dl reintentando con cookie alterna ({idx + 1}/{len(cookie_candidates)})...")

    self.log(f"Fallback gallery-dl fallo: {last_detail}")
    return False, last_detail

  def _extract_info_gallery_dl(self, url: str) -> dict | None:
    cookie_candidates = self._existing_cookie_files() or [""]
    proc = None
    for idx, cookie_file in enumerate(cookie_candidates, start=1):
      current = self._run_gallery_dl_raw(
        [*self._gallery_dl_common_args(cookie_file_override=(cookie_file or None)), "--dump-json", url],
        timeout=120,
      )
      if current.returncode == 0:
        proc = current
        break
      if cookie_file and idx < len(cookie_candidates):
        self.log(f"gallery-dl info: reintentando con cookie alterna ({idx + 1}/{len(cookie_candidates)})...")

    if proc is None or proc.returncode != 0:
      return None

    first_item = None
    full_stdout = (proc.stdout or "").strip()

    try:
      parsed = json.loads(full_stdout)
      if isinstance(parsed, dict):
        first_item = parsed
      elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        first_item = parsed[0]
    except Exception:
      pass

    if first_item is None:
      for line in (proc.stdout or "").splitlines():
        line = line.strip().rstrip(",")
        if not line:
          continue
        try:
          obj = json.loads(line)
        except Exception:
          continue
        if isinstance(obj, dict):
          first_item = obj
          break

    if not first_item:
      return None

    title = first_item.get("content") or first_item.get("tweet_id") or first_item.get("id") or "social-media-post"
    return {
      "title": str(title),
      "duration": 0,
      "formats": [],
      "subtitles": {},
      "automatic_captions": {},
    }

  def _run_yt_dlp_download(
    self,
    url: str,
    specific_args: list[str],
    allow_social_fallback: bool = False,
    social_fallback_out_dir: str | None = None,
  ) -> None:
    candidates = self._social_candidate_urls(url)
    last_error = None
    last_social_detail = ""
    cookie_candidates = self._existing_cookie_files() or [""]

    for candidate in candidates:
      for idx, cookie_file in enumerate(cookie_candidates, start=1):
        cmd = [
          *self._yt_dlp_cmd(),
          *self._common_net_args(candidate, include_cookies=True, cookie_file_override=(cookie_file or None)),
          *specific_args,
          candidate,
        ]
        try:
          self._run_command(cmd)
          return
        except Exception as exc:
          last_error = exc

          if self.use_cookies_var.get() and self._is_cookie_error(str(exc)):
            self._disable_broken_cookies(str(exc))
            self.log("Aviso: fallo cookies del navegador. Reintentando sin cookies...")
            fallback_cmd = [
              *self._yt_dlp_cmd(),
              *self._common_net_args(candidate, include_cookies=False),
              *specific_args,
              candidate,
            ]
            try:
              self._run_command(fallback_cmd)
              return
            except Exception as second_exc:
              last_error = second_exc

          if cookie_file and idx < len(cookie_candidates):
            self.log(f"yt-dlp reintentando con cookie alterna ({idx + 1}/{len(cookie_candidates)})...")

      if candidate != url:
        self.log(f"Mirror fallido: {candidate}")

    if allow_social_fallback and self._is_social_media_url(url):
      self.log("yt-dlp no pudo extraer media social. Intentando fallback con gallery-dl...")
      fallback_out_dir = (social_fallback_out_dir or "").strip() or None
      if fallback_out_dir is None and (
        self._social_url_likely_image_post(url)
        or (last_error is not None and self._is_image_only_social_error(url, str(last_error)))
      ):
        fallback_out_dir = self._build_image_output_dir(url)
        self.log("Post social sin video detectado: fallback gallery-dl usara salida de imagenes.")

      ok, detail = self._gallery_dl_download(url, out_dir=fallback_out_dir)
      if ok:
        return
      last_social_detail = detail

    if last_error is not None and last_social_detail:
      raise RuntimeError(
        "No se pudo descargar ese enlace social. "
        f"Detalle yt-dlp: {last_error}. "
        f"Detalle gallery-dl: {last_social_detail}"
        + self._social_auth_hint()
      )
    if last_error is not None:
      raise last_error
    raise RuntimeError("No se pudo descargar media")

  def _load_info_for_url(self, url: str, source_label: str) -> None:
    def task() -> None:
      self.log(f"Cargando info {source_label}... (si tarda mucho, revisa cookies/red)")
      info = self._extract_info(url)
      self.video_info = info

      title = info.get("title", "(sin titulo)")
      duration = int(info.get("duration") or 0)
      self.root.after(0, lambda: self.duration_var.set(f"Duracion: {format_seconds_to_clock(duration)}"))
      self.root.after(0, lambda: self.end_var.set(format_seconds_to_clock(duration)))
      self.root.after(0, lambda: self._update_language_selector(info))

      self.log(f"Titulo: {title}")
      self.log(f"Duracion (s): {duration}")
      merged, audio, captions = self._collect_all_languages(info)
      self.log(f"Idiomas detectados (audio+subs): {', '.join(merged)}")
      self.log(f"Idiomas de audio: {', '.join(sorted(audio))}")
      self.log(f"Idiomas de subtitulos/captions: {', '.join(sorted(captions))}")
      self.log(f"Calidades detectadas: {', '.join(self._collect_video_qualities(info))}")
      self.log(f"Calidades de audio detectadas: {', '.join(self._collect_audio_qualities(info))}")

    self._run_background(task, f"CARGAR INFO {source_label.upper()}")

  def _download_best_for_url(self, url: str, source_label: str) -> None:
    out = self._build_output_template(url)

    def task() -> None:
      specific_args = [
        "-f",
        self._language_format_selector(),
        "--merge-output-format",
        "mp4",
        "-o",
        out,
      ]
      compression_args = self._compression_postprocessor_args()
      base_reencode_args = compression_args or (
        "ffmpeg:-c:v libx264 -preset veryfast -crf 20 -threads 0 "
        "-vf setsar=1 -c:a aac -b:a 160k -movflags +faststart"
      )
      post_args = self._postprocessor_args_for_url(url, base_reencode_args) or base_reencode_args
      specific_args += ["--recode-video", "mp4", "--postprocessor-args", post_args]
      specific_args += self._subtitle_download_args()
      self.log("Postprocesado BEST: recodificacion optimizada para compatibilidad y ratio estable.")
      if compression_args:
        self.log(f"Compresion aplicada en {source_label.upper()} BEST: {self.compression_var.get()}")

      if self.include_subtitles_var.get():
        if self.embed_subtitles_var.get():
          self.log("Subtitulos: activados y embebidos en MP4 (se pueden activar/desactivar en reproductor).")
        else:
          self.log("Subtitulos: activados como archivo separado (.srt/.vtt).")

      self.log(f"Modo BEST {source_label.upper()}: no aplica limite de tamano ni recorte")
      allow_social_fallback = self._is_social_media_url(url)
      try:
        self._run_yt_dlp_download(url, specific_args, allow_social_fallback=allow_social_fallback)
      except Exception as exc:
        # Social sin video: cambia a pipeline de imagen automaticamente.
        err = str(exc).lower()
        if self._is_image_only_social_error(url, err):
          self.log("Post social sin video detectado en BEST. Reintentando en modo imagen...")
          self._download_image_url_blocking(url, f"{source_label}-tweet-imagen")
          return
        raise

    self._run_background(task, f"DESCARGA {source_label.upper()} BEST")

  def load_instagram_info(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url_from_var(self.instagram_url_var.get(), "Instagram")
      self.url_var.set(url)
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return

    self._load_info_for_url(url, "Instagram")

  def load_twitter_info(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url_from_var(self.twitter_url_var.get(), "Twitter/X")
      self.url_var.set(url)
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return

    self._load_info_for_url(url, "Twitter")

  def download_instagram_best(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url_from_var(self.instagram_url_var.get(), "Instagram")
      self.url_var.set(url)
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return

    self._download_best_for_url(url, "Instagram")

  def download_twitter_best(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url_from_var(self.twitter_url_var.get(), "Twitter/X")
      self.url_var.set(url)
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return

    self._download_best_for_url(url, "Twitter")

  def _extract_info(self, url: str) -> dict:
    self.log("Leyendo metadata del video...")

    def run_info_command(command: list[str]) -> subprocess.CompletedProcess[str]:
      return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
      )

    cookie_candidates = self._existing_cookie_files() or [""]

    for candidate in self._social_candidate_urls(url):
      process = None
      detail = "sin detalle"
      for idx, cookie_file in enumerate(cookie_candidates, start=1):
        cmd = [
          *self._yt_dlp_cmd(),
          "-J",
          "--skip-download",
          "--no-warnings",
          *self._common_net_args(candidate, include_cookies=True, cookie_file_override=(cookie_file or None)),
          candidate,
        ]
        current = run_info_command(cmd)
        if current.returncode == 0:
          return json.loads(current.stdout)
        process = current

        stderr = (current.stderr or "").strip()
        stdout = (current.stdout or "").strip()
        detail = stderr or stdout or "sin detalle"
        if cookie_file and idx < len(cookie_candidates):
          self.log(f"Info yt-dlp: reintentando con cookie alterna ({idx + 1}/{len(cookie_candidates)})...")

      assert process is not None

      stderr = (process.stderr or "").strip()
      stdout = (process.stdout or "").strip()
      detail = stderr or stdout or detail

      if self.use_cookies_var.get() and self._is_cookie_error(detail):
        self._disable_broken_cookies(detail)
        self.log("Aviso: fallo cookies del navegador en metadata. Reintentando sin cookies...")
        fallback = [
          *self._yt_dlp_cmd(),
          "-J",
          "--skip-download",
          "--no-warnings",
          *self._common_net_args(candidate, include_cookies=False),
          candidate,
        ]
        process = run_info_command(fallback)
        if process.returncode == 0:
          return json.loads(process.stdout)
        stderr = (process.stderr or "").strip()
        stdout = (process.stdout or "").strip()
        detail = stderr or stdout or "sin detalle"

      if candidate != url:
        self.log(f"Mirror info fallido: {candidate}")

      if self._is_twitter_url(url) and TWITTER_NO_VIDEO_MARKER in detail:
        info = self._extract_info_gallery_dl(url)
        if info:
          self.log("Info obtenida via fallback gallery-dl (tweet puede no tener video directo).")
          return info
        raise RuntimeError(
          "No se encontro video en ese tweet. Puede ser un tweet sin video, eliminado, privado, "
          "restringido por edad/sesion, o con media no compatible."
          + self._social_auth_hint()
        )

    if self._is_twitter_url(url) or self._is_instagram_url(url):
      info = self._extract_info_gallery_dl(url)
      if info:
        self.log("Info obtenida via fallback gallery-dl para red social.")
        return info

    raise RuntimeError("No se pudo cargar info con extractores disponibles")

  def _normalize_lang(self, value: object) -> str | None:
    if value is None:
      return None
    text = str(value).strip().lower().replace("_", "-")
    if not text or text in {"none", "unknown", "und", "null"}:
      return None
    if not LANG_CODE_PATTERN.match(text):
      return None
    return text

  def _collect_audio_languages(self, info: dict) -> list[str]:
    langs = {"auto"}

    for fmt in info.get("formats", []) or []:
      acodec = fmt.get("acodec")
      if not acodec or acodec == "none":
        continue

      lang = self._normalize_lang(fmt.get("language"))
      if lang:
        langs.add(lang)

      track = fmt.get("audio_track") or {}
      if isinstance(track, dict):
        for key in ("id", "display_name", "name", "language"):
          candidate = self._normalize_lang(track.get(key))
          if candidate:
            langs.add(candidate)

    for candidate in info.get("audio_languages", []) or []:
      normalized = self._normalize_lang(candidate)
      if normalized:
        langs.add(normalized)

    # Algunas respuestas traen idioma principal como "es", "en", etc.
    main_lang = self._normalize_lang(info.get("language"))
    if main_lang:
      langs.add(main_lang)

    return sorted(langs)

  def _collect_caption_languages(self, info: dict) -> list[str]:
    langs = set()

    subtitles = info.get("subtitles") or {}
    if isinstance(subtitles, dict):
      for key in subtitles.keys():
        normalized = self._normalize_lang(key)
        if normalized:
          langs.add(normalized)

    automatic = info.get("automatic_captions") or {}
    if isinstance(automatic, dict):
      for key in automatic.keys():
        normalized = self._normalize_lang(key)
        if normalized:
          langs.add(normalized)

    return sorted(langs)

  def _collect_all_languages(self, info: dict) -> tuple[list[str], set[str], set[str]]:
    audio = set(self._collect_audio_languages(info))
    captions = set(self._collect_caption_languages(info))
    merged = {"auto"}
    merged.update(audio)
    merged.update(captions)
    return sorted(merged), audio, captions

  def _collect_subtitle_selector_values(self, captions: set[str]) -> list[str]:
    base = ["auto", "all", "es", "en", "es.*", "en.*"]
    values = [*base]
    for item in sorted(captions):
      if item not in values:
        values.append(item)
    return values

  def _collect_video_qualities(self, info: dict) -> list[str]:
    heights = set()
    for fmt in info.get("formats", []) or []:
      vcodec = fmt.get("vcodec")
      if not vcodec or vcodec == "none":
        continue
      height = fmt.get("height")
      if isinstance(height, int) and height > 0:
        heights.add(height)

    sorted_heights = sorted(heights, reverse=True)
    if not sorted_heights:
      return ["best"]
    return ["best", *[f"{height}p" for height in sorted_heights]]

  def _collect_audio_qualities(self, info: dict) -> list[str]:
    bitrates: set[int] = set()
    for fmt in info.get("formats", []) or []:
      acodec = fmt.get("acodec")
      if not acodec or acodec == "none":
        continue

      abr = fmt.get("abr")
      if isinstance(abr, (int, float)) and abr > 0:
        bitrates.add(int(round(float(abr))))

    values = [f"{kbps}k" for kbps in sorted(bitrates, reverse=True)]
    values.append("best audio")
    return values

  def _update_language_selector(self, info: dict) -> None:
    merged, audio, captions = self._collect_all_languages(info)
    self.available_languages = merged
    self.available_audio_languages = audio
    self.available_caption_languages = captions
    self.language_combo.configure(values=self.available_languages)

    if self.selected_language_var.get() not in self.available_languages:
      self.selected_language_var.set("auto")

    self.available_qualities = self._collect_video_qualities(info)
    self.quality_combo.configure(values=self.available_qualities)
    if self.selected_quality_var.get() not in self.available_qualities:
      self.selected_quality_var.set("best")

    self.available_audio_qualities = self._collect_audio_qualities(info)
    self.audio_quality_combo.configure(values=self.available_audio_qualities)
    if self.selected_audio_quality_var.get() not in self.available_audio_qualities:
      self.selected_audio_quality_var.set("best audio")

    self.available_subtitle_languages = self._collect_subtitle_selector_values(captions)
    self.subtitle_combo.configure(values=self.available_subtitle_languages)
    if self.subtitle_lang_var.get() not in self.available_subtitle_languages:
      self.subtitle_lang_var.set("auto")

  def load_video_info(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url()
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return
    self._load_info_for_url(url, "General")

  def _selected_quality_height(self) -> int | None:
    quality = (self.selected_quality_var.get() or "best").strip().lower()
    if quality == "best":
      return None
    if quality.endswith("p") and quality[:-1].isdigit():
      return int(quality[:-1])
    return None

  def _selected_audio_bitrate(self) -> int | None:
    quality = (self.selected_audio_quality_var.get() or "best audio").strip().lower()
    if quality in {"best", "best audio", "bestaudio"}:
      return None
    match = re.match(r"^(\d+)\s*k$", quality)
    if match:
      return int(match.group(1))
    if quality.isdigit():
      return int(quality)
    return None

  def _audio_only_format_selector(self) -> str:
    bitrate = self._selected_audio_bitrate()
    if bitrate is None:
      return "bestaudio"
    return f"ba[abr<={bitrate}]/bestaudio"

  def _video_only_format_selector(self) -> str:
    target_height = self._selected_quality_height()
    if target_height is None:
      return "bestvideo/bv*"
    return f"bv*[height<={target_height}]/bestvideo[height<={target_height}]/bv*"

  def _compression_postprocessor_args(self) -> str | None:
    mode = (self.compression_var.get() or "sin_compresion").strip().lower()
    if mode == "sin_compresion":
      return None

    cq = 20
    if mode == "media":
      cq = 24
    elif mode == "alta":
      cq = 28

    return f"ffmpeg:-c:v h264_nvenc -cq {cq} -preset p5 -threads 0 -vf setsar=1 -c:a aac -b:a 128k -movflags +faststart"

  def _is_youtube_shorts_url(self, url: str) -> bool:
    lower = (url or "").lower()
    return "youtube.com/shorts/" in lower or "m.youtube.com/shorts/" in lower

  def _shorts_fix_vf(self) -> str:
    # Escala para cubrir 9:16 y recorta al centro. Esta forma es robusta en Windows.
    return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"

  def _apply_vf_to_postprocessor_args(self, post_args: str, vf_expr: str) -> str:
    if not post_args:
      return post_args
    if post_args.startswith("ffmpeg:"):
      if " -vf " in post_args:
        return f"{post_args},{vf_expr}"
      return f"{post_args} -vf {vf_expr}"
    return post_args

  def _postprocessor_args_for_url(self, url: str, base_args: str | None) -> str | None:
    args = base_args
    if self._is_youtube_shorts_url(url):
      vf_expr = self._shorts_fix_vf()
      if args:
        args = self._apply_vf_to_postprocessor_args(args, vf_expr)
      else:
        args = (
          f"ffmpeg:-c:v h264_nvenc -cq 22 -preset p5 -threads 0 -vf {vf_expr} "
          "-c:a aac -b:a 128k -movflags +faststart"
        )
      self.log("YouTube Shorts: aplicado fix de aspect ratio (crop/scale 9:16 sin bordes).")
    return args

  def _subtitle_download_args(self) -> list[str]:
    if not self.include_subtitles_var.get():
      return []

    lang_raw = (self.subtitle_lang_var.get() or "auto").strip()
    lang = lang_raw.lower()
    args = [
      "--write-subs",
      "--write-auto-subs",
      "--sub-format",
      "srt/vtt/best",
    ]

    if lang not in {"", "auto", "all", "*"}:
      args += ["--sub-langs", lang_raw]
    else:
      args += ["--sub-langs", "all,-live_chat"]

    if self.embed_subtitles_var.get():
      args.append("--embed-subs")

    return args

  def _mp4_compatible_audio_selector(self, language: str | None = None) -> str:
    if language and language != "auto":
      return (
        f"ba[language={language}][ext=m4a]"
        f"/ba[language={language}][acodec^=mp4a]"
        f"/ba[language={language}][acodec*=aac]"
        f"/ba[language={language}]"
        f"/ba[ext=m4a]"
        f"/ba[acodec^=mp4a]"
        f"/ba[acodec*=aac]"
        f"/ba"
      )
    return "ba[ext=m4a]/ba[acodec^=mp4a]/ba[acodec*=aac]/ba"

  def _language_format_selector(self) -> str:
    language = self.selected_language_var.get().strip() or "auto"
    target_height = self._selected_quality_height()
    video_selector = "bv*" if target_height is None else f"bv*[height<={target_height}]"
    audio_selector = self._mp4_compatible_audio_selector(None if language == "auto" else language)

    if language == "auto":
      if target_height is None:
        return f"bv*+{audio_selector}/b"
      return f"{video_selector}+{audio_selector}/b[height<={target_height}]/b"

    # Si el idioma elegido existe solo en captions/subs y no en audio,
    # evitar fallo de formato y usar auto para audio/video.
    if language not in self.available_audio_languages:
      self.log(
        f"Aviso: '{language}' no tiene pista de audio directa en este video. Se usara auto para audio/video."
      )
      if target_height is None:
        return f"bv*+{self._mp4_compatible_audio_selector()}/b"
      return f"{video_selector}+{self._mp4_compatible_audio_selector()}/b[height<={target_height}]/b"

    if target_height is None:
      return f"bv*+{audio_selector}/b[language={language}]/bv*+{self._mp4_compatible_audio_selector()}/b"

    return (
      f"{video_selector}+{audio_selector}"
      f"/b[height<={target_height}][language={language}]"
      f"/{video_selector}+{self._mp4_compatible_audio_selector()}"
      f"/b[height<={target_height}]"
      f"/b"
    )

  def download_best(self) -> None:
    if self.audio_only_var.get():
      self.download_audio_only()
      return
    try:
      self._ensure_not_running()
      url = self._safe_url()
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return
    self._download_best_for_url(url, "General")

  def download_audio_only(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url()
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return

    out = self._build_output_template(url)

    def task() -> None:
      specific_args = [
        "-f",
        self._audio_only_format_selector(),
        "-o",
        out,
      ]
      self.log(f"Modo SOLO AUDIO: calidad={self.selected_audio_quality_var.get()}")
      self._run_yt_dlp_download(url, specific_args, allow_social_fallback=self._is_social_media_url(url))

    self._run_background(task, "DESCARGA SOLO AUDIO")

  def download_video_only(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url()
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return

    out = self._build_output_template(url)

    def task() -> None:
      specific_args = [
        "-f",
        self._video_only_format_selector(),
        "-o",
        out,
      ]
      self.log(f"Modo SOLO VIDEO: calidad={self.selected_quality_var.get()}")
      self._run_yt_dlp_download(url, specific_args, allow_social_fallback=self._is_social_media_url(url))

    self._run_background(task, "DESCARGA SOLO VIDEO")

  def _resolve_full_duration(self) -> int:
    if not self.video_info:
      raise RuntimeError("Primero pulsa 'Cargar info' para obtener duracion")

    full_duration = int(self.video_info.get("duration") or 0)
    if full_duration <= 0:
      raise RuntimeError("No pude leer duracion del video")
    return full_duration

  def download_limited_size(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url()
      out = self._build_output_template(url)
      size_mb = float(self.max_size_var.get().strip())
      if size_mb <= 1:
        raise ValueError("Tamano objetivo debe ser > 1 MB")
      duration = self._resolve_full_duration()
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return

    def task() -> None:
      total_kbps = int((size_mb * 8192) / duration)
      audio_kbps = 128
      video_kbps = max(total_kbps - audio_kbps, 150)
      self.log(
        f"Calculo bitrate -> total={total_kbps}k, video={video_kbps}k, audio={audio_kbps}k"
      )

      ffmpeg_args = (
        f"ffmpeg:-c:v h264_nvenc -preset p5 -threads 0 -b:v {video_kbps}k "
        f"-maxrate {video_kbps}k -bufsize {video_kbps * 2}k "
        f"-vf setsar=1 -c:a aac -b:a {audio_kbps}k -movflags +faststart"
      )
      ffmpeg_args = self._postprocessor_args_for_url(url, ffmpeg_args) or ffmpeg_args

      specific_args = [
        "-f",
        self._language_format_selector(),
        "--merge-output-format",
        "mp4",
        "--recode-video",
        "mp4",
        "--postprocessor-args",
        ffmpeg_args,
        "-o",
        out,
      ]
      specific_args += self._subtitle_download_args()
      self.log("Modo LIMITE: solo controla tamano, no recorta")
      self.log("Compresion en LIMITE: determinada por tamano objetivo")
      if self.include_subtitles_var.get():
        if self.embed_subtitles_var.get():
          self.log("Subtitulos: activados y embebidos en MP4 (se pueden activar/desactivar en reproductor).")
        else:
          self.log("Subtitulos: activados como archivo separado (.srt/.vtt).")
      self._run_yt_dlp_download(url, specific_args)

    self._run_background(task, "DESCARGA CON LIMITE")

  def download_trimmed(self) -> None:
    try:
      self._ensure_not_running()
      url = self._safe_url()
      out = self._build_output_template(url)
      start = self.start_var.get().strip() or "00:00"
      end = self.end_var.get().strip()
      if not end:
        raise ValueError("Debes indicar tiempo final para recortar")

      parse_time_to_seconds(start)
      parse_time_to_seconds(end)
    except Exception as exc:
      messagebox.showerror(APP_TITLE, str(exc))
      return

    def task() -> None:
      section = f"*{start}-{end}"
      compression_args = self._postprocessor_args_for_url(url, self._compression_postprocessor_args())
      specific_args = [
        "-f",
        self._language_format_selector(),
        "--download-sections",
        section,
        "--merge-output-format",
        "mp4",
        "-o",
        out,
      ]
      specific_args += self._subtitle_download_args()
      if compression_args:
        specific_args += ["--recode-video", "mp4", "--postprocessor-args", compression_args]
        self.log(f"Compresion aplicada en RECORTE: {self.compression_var.get()}")

      self.log("Modo RECORTE: descarga solo el segmento indicado")
      if self.include_subtitles_var.get():
        if self.embed_subtitles_var.get():
          self.log("Subtitulos: activados y embebidos en MP4 (se pueden activar/desactivar en reproductor).")
        else:
          self.log("Subtitulos: activados como archivo separado (.srt/.vtt).")
      self._run_yt_dlp_download(url, specific_args)

    self._run_background(task, "RECORTE DE SEGMENTO")


def main() -> None:
  root = tk.Tk()
  DownloaderApp(root)
  root.mainloop()


if __name__ == "__main__":
  main()
