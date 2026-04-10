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
import ctypes
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit
from tkinter import filedialog, messagebox, ttk

try:
  from feed_scraper import FeedScraper
except Exception:
  from downloader.feed_scraper import FeedScraper

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
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
    "stop_feed": "STOP Feed",
    "live_log": "Ver log en vivo",
    "ig_info": "Info Instagram",
    "ig_best": "Descargar IG BEST",
    "tw_info": "Info Twitter",
    "tw_best": "Descargar TW BEST",
    "feed_yt": "Iniciar Feed YouTube Shorts",
    "paste": "Pegar",
    "image_url": "URL de imagen:",
    "image_output": "Salida imagenes:",
    "pick_image_folder": "Elegir carpeta imagenes",
    "download_image": "Descargar imagen URL",
    "clipboard_monitor": "Monitor portapapeles (auto)",
    "remember_window_position": "Recordar ubicacion de la ventana",
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
    "stop_feed": "STOP Feed",
    "live_log": "Live log",
    "ig_info": "Instagram info",
    "ig_best": "Download IG BEST",
    "tw_info": "Twitter info",
    "tw_best": "Download TW BEST",
    "feed_yt": "Start YouTube Shorts Feed",
    "paste": "Paste",
    "image_url": "Image URL:",
    "image_output": "Image output:",
    "pick_image_folder": "Choose image folder",
    "download_image": "Download image URL",
    "clipboard_monitor": "Clipboard monitor (auto)",
    "remember_window_position": "Remember window position",
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
X_ACTION_RANGE = 12


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
    self.root.geometry("1120x760")
    self.root.minsize(980, 680)

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
    self.scraper: FeedScraper | None = None
    self.is_scraping = False
    self.feed_download_queue: queue.Queue[dict] = queue.Queue()
    self.feed_urls_queued: set[str] = set()
    self.last_success_cookie_path = None
    self.feed_worker_running = False
    self.log_history: list[str] = []
    self.live_log_window: tk.Toplevel | None = None
    self.live_log_widget: tk.Text | None = None
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
    self.subtitle_lang_var = tk.StringVar(value="auto")
    self.compression_var = tk.StringVar(value="sin_compresion")
    self.use_cookies_var = tk.BooleanVar(value=False)
    self.cookies_browser_var = tk.StringVar(value="chrome")
    self.cookies_file_var = tk.StringVar(value="")
    self.cookies_folder_var = tk.StringVar(value="")
    self.start_with_windows_var = tk.BooleanVar(value=False)
    self.auto_save_defaults_var = tk.BooleanVar(value=False)
    self.remember_window_position_var = tk.BooleanVar(value=False)
    self.feed_image_seconds_var = tk.StringVar(value="10")
    self.feed_scroll_pause_var = tk.StringVar(value="1.5")
    self.feed_scroll_px_var = tk.StringVar(value="900")
    self.feed_wait_video_end_var = tk.BooleanVar(value=True)
    self.feed_max_video_wait_var = tk.StringVar(value="300")
    self.feed_tiktok_likes_only_var = tk.BooleanVar(value=False)
    self.feed_twitter_creator_folders_var = tk.BooleanVar(value=False)
    self.x_actions_likes_var = tk.BooleanVar(value=False)
    self.x_actions_retweets_var = tk.BooleanVar(value=False)
    self.x_actions_bookmarks_var = tk.BooleanVar(value=False)
    self.x_actions_profile_var = tk.BooleanVar(value=False)
    self.x_actions_user_var = tk.StringVar(value="")
    self.x_actions_poll_seconds_var = tk.StringVar(value="45")
    self.x_actions_running = False
    self.x_actions_stop_event = threading.Event()
    self.x_actions_thread: threading.Thread | None = None
    self.x_actions_seen_urls: set[str] = set()
    self.x_actions_seen_urls_by_label: dict[str, set[str]] = {}
    self.x_actions_seen_status_ids_by_label: dict[str, set[int]] = {}
    self.x_actions_bootstrapped = False
    self.x_actions_reference_ids: dict[str, int] = {}
    self.twitter_creator_cache: dict[int, str] = {}
    self.x_retweet_html_cache: dict[str, tuple[float, list[str]]] = {}
    self.downloaded_status_ids: set[int] = set()
    self.feed_metadata_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "downloader", "feed_metadata")
    self.ui_language_var = tk.StringVar(value="es")
    self.clipboard_monitor_var = tk.BooleanVar(value=False)
    self.clipboard_poll_ms = 1200
    self.clipboard_seen_urls: set[str] = set()
    self.clipboard_pending_url: str | None = None
    self.monitors: list[dict] = self._detect_monitors()
    self._last_monitors_signature: tuple = tuple(self._monitor_identity(m) for m in self.monitors)
    self.twitter_instances: dict[int, dict] = {}
    self.twitter_instance_seq = 1
    self.twitter_instances_lock = threading.Lock()
    self.twitter_monitor_cookie_choice: dict[int, str] = {}
    self.twitter_global_cookie_choice = tk.StringVar(value="")
    self.x_instances_panel: ttk.LabelFrame | None = None
    self.x_instances_canvas: tk.Canvas | None = None
    self.x_instances_canvas_window: int | None = None
    self.x_instances_h_scroll: ttk.Scrollbar | None = None
    self.x_instances_v_scroll: ttk.Scrollbar | None = None
    self.x_instances_table: ttk.Frame | None = None
    self.x_instances_hint_var = tk.StringVar(value="")
    self.log_frame_widget: ttk.LabelFrame | None = None
    self.x_instances_signature: tuple | None = None
    self._last_feed_runtime_signature: tuple | None = None
    self.settings_file_path = os.path.join(os.path.dirname(__file__), "downloader_settings.json")
    self._settings_hooks_bound = False
    self._loading_persisted_settings = False
    self._window_geometry_save_job: str | None = None
    self._saved_window_geometry = ""

    self._load_start_with_windows_state()
    self._load_persisted_settings()
    self._configure_ui_theme()
    self._build_ui()
    self._bind_settings_autosave_hooks()
    self.root.protocol("WM_DELETE_WINDOW", self._on_main_close)
    self.root.after(0, self._apply_initial_window_state)
    self._prepare_log_file()
    self._auto_load_default_cookies_file()
    out_dir = self.output_dir_var.get().strip() or os.path.join(os.path.dirname(os.path.dirname(__file__)), "videos")
    os.makedirs(out_dir, exist_ok=True)

    self.root.after(150, self._drain_log_queue)
    self._check_dependencies()
    self.root.after(1500, self._refresh_x_instances_ui_tick)
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

  def _configure_ui_theme(self) -> None:
    self.root.configure(bg="#f3f6fb")
    style = ttk.Style(self.root)
    try:
      style.theme_use("clam")
    except Exception:
      pass

    base_font = ("Segoe UI", 10)
    heading_font = ("Segoe UI Semibold", 10)

    style.configure(".", font=base_font, background="#f3f6fb", foreground="#172033")
    style.configure("TFrame", background="#f3f6fb")
    style.configure("TLabel", background="#f3f6fb", foreground="#172033")
    style.configure("TCheckbutton", background="#f3f6fb", foreground="#172033")
    style.map("TCheckbutton", background=[("active", "#f3f6fb")])

    style.configure(
      "Card.TLabelframe",
      background="#ffffff",
      borderwidth=1,
      relief="solid",
      bordercolor="#d6deea",
      lightcolor="#d6deea",
      darkcolor="#d6deea",
    )
    style.configure("Card.TLabelframe.Label", background="#f3f6fb", foreground="#0f172a", font=heading_font)

    style.configure(
      "TEntry",
      fieldbackground="#ffffff",
      foreground="#111827",
      bordercolor="#c8d3e5",
      lightcolor="#c8d3e5",
      darkcolor="#c8d3e5",
      padding=6,
    )
    style.configure(
      "TCombobox",
      fieldbackground="#ffffff",
      foreground="#111827",
      bordercolor="#c8d3e5",
      lightcolor="#c8d3e5",
      darkcolor="#c8d3e5",
      padding=5,
    )

    style.configure("TButton", padding=(12, 7), font=("Segoe UI Semibold", 9))
    style.configure("Accent.TButton", background="#0f62fe", foreground="#ffffff", bordercolor="#0b4fd1")
    style.map(
      "Accent.TButton",
      background=[("pressed", "#0a3ea7"), ("active", "#0b4fd1")],
      foreground=[("disabled", "#cbd5e1"), ("!disabled", "#ffffff")],
    )

    style.configure("Danger.TButton", background="#dc2626", foreground="#ffffff", bordercolor="#b91c1c")
    style.map(
      "Danger.TButton",
      background=[("pressed", "#b91c1c"), ("active", "#dc2626")],
      foreground=[("disabled", "#cbd5e1"), ("!disabled", "#ffffff")],
    )

    style.configure("Subtle.TButton", background="#e7edf7", foreground="#0f172a", bordercolor="#c8d3e5")
    style.map("Subtle.TButton", background=[("active", "#d7e2f2")])

    style.configure("App.TNotebook", background="#f3f6fb", borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure(
      "App.TNotebook.Tab",
      padding=(14, 8),
      font=("Segoe UI Semibold", 9),
      background="#dfe7f4",
      foreground="#263246",
    )
    style.map(
      "App.TNotebook.Tab",
      background=[("selected", "#ffffff"), ("active", "#eaf0f9")],
      foreground=[("selected", "#0f172a"), ("active", "#1d2a3f")],
    )

    style.configure("HeroTitle.TLabel", background="#f3f6fb", foreground="#0f172a", font=("Segoe UI Semibold", 16))
    style.configure("HeroSub.TLabel", background="#f3f6fb", foreground="#475569", font=("Segoe UI", 10))

  def _detect_monitors(self) -> list[dict]:
    monitors: list[dict] = []

    if sys.platform == "win32":
      try:
        from ctypes import wintypes

        class RECT(ctypes.Structure):
          _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
          ]

        class MONITORINFOEXW(ctypes.Structure):
          _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", ctypes.c_wchar * 32),
          ]

        monitor_enum_proc = ctypes.WINFUNCTYPE(
          ctypes.c_int,
          wintypes.HMONITOR,
          wintypes.HDC,
          ctypes.POINTER(RECT),
          wintypes.LPARAM,
        )

        user32 = ctypes.windll.user32

        def _callback(hmonitor, _hdc, _rect, _lparam):
          info = MONITORINFOEXW()
          info.cbSize = ctypes.sizeof(MONITORINFOEXW)
          if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            left = int(info.rcMonitor.left)
            top = int(info.rcMonitor.top)
            right = int(info.rcMonitor.right)
            bottom = int(info.rcMonitor.bottom)
            width = max(1, right - left)
            height = max(1, bottom - top)
            monitors.append(
              {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "primary": bool(int(info.dwFlags) & 1),
                "device": str(info.szDevice or ""),
              }
            )
          return 1

        user32.EnumDisplayMonitors(0, 0, monitor_enum_proc(_callback), 0)
      except Exception:
        monitors = []

    if not monitors:
      monitors = [
        {
          "left": 0,
          "top": 0,
          "width": int(self.root.winfo_screenwidth()),
          "height": int(self.root.winfo_screenheight()),
          "primary": True,
          "device": "DISPLAY1",
        }
      ]

    monitors.sort(key=lambda item: (not bool(item.get("primary", False)), int(item.get("left", 0)), int(item.get("top", 0))))
    for idx, item in enumerate(monitors, start=1):
      item["id"] = idx
      item["label"] = (
        f"Monitor {idx}"
        f" ({int(item.get('width', 0))}x{int(item.get('height', 0))}"
        f" @ {int(item.get('left', 0))},{int(item.get('top', 0))})"
      )
    return monitors

  def _has_active_twitter_instances(self) -> bool:
    with self.twitter_instances_lock:
      for item in self.twitter_instances.values():
        scraper = item.get("scraper")
        if scraper and scraper.is_running():
          return True
    return False

  def _update_scraping_state(self) -> None:
    has_single = bool(self.scraper and self.scraper.is_running())
    self.is_scraping = bool(has_single or self._has_active_twitter_instances())

  def _feed_runtime_config(self) -> dict:
    image_seconds = float((self.feed_image_seconds_var.get() or "10").strip())
    scroll_pause = float((self.feed_scroll_pause_var.get() or "1.5").strip())
    scroll_px = int(float((self.feed_scroll_px_var.get() or "900").strip()))
    max_video_wait = float((self.feed_max_video_wait_var.get() or "300").strip())
    if image_seconds < 1:
      raise ValueError("Tiempo por imagen debe ser >= 1")
    if scroll_pause < 0.2:
      raise ValueError("Pausa de scroll debe ser >= 0.2")
    if scroll_px < 100:
      raise ValueError("Pixeles por scroll debe ser >= 100")
    if max_video_wait < 5:
      raise ValueError("Maximo espera de video debe ser >= 5")

    return {
      "image_seconds": image_seconds,
      "scroll_pause": scroll_pause,
      "scroll_px": scroll_px,
      "max_video_wait": max_video_wait,
    }

  def _find_monitor_by_id(self, monitor_id: int) -> dict | None:
    for monitor in self.monitors:
      if int(monitor.get("id", -1)) == int(monitor_id):
        return monitor
    return None

  def _monitor_identity(self, monitor: dict) -> tuple:
    return (
      str(monitor.get("device") or "").strip().lower(),
      int(monitor.get("left", 0)),
      int(monitor.get("top", 0)),
      int(monitor.get("width", 0)),
      int(monitor.get("height", 0)),
    )

  def _refresh_monitors_if_changed(self) -> bool:
    try:
      detected = self._detect_monitors()
    except Exception:
      return False

    new_signature = tuple(self._monitor_identity(item) for item in detected)
    if new_signature == self._last_monitors_signature:
      return False

    old_cookie_by_identity: dict[tuple, str] = {}
    for old_monitor in self.monitors:
      old_id = int(old_monitor.get("id", 0))
      if old_id in self.twitter_monitor_cookie_choice:
        old_cookie_by_identity[self._monitor_identity(old_monitor)] = (
          self.twitter_monitor_cookie_choice.get(old_id, "") or ""
        ).strip()

    self.monitors = detected
    remapped_choices: dict[int, str] = {}
    for monitor in self.monitors:
      match = old_cookie_by_identity.get(self._monitor_identity(monitor), "")
      if match or self._monitor_identity(monitor) in old_cookie_by_identity:
        remapped_choices[int(monitor.get("id", 0))] = match
    self.twitter_monitor_cookie_choice = remapped_choices

    self._last_monitors_signature = new_signature
    self.x_instances_signature = None
    self.log(f"Monitores actualizados: {len(self.monitors)} detectado(s)")
    return True

  def _cookie_pool_default_dir(self) -> str:
    base_dir = os.path.dirname(os.path.dirname(__file__))
    candidates = [
      os.path.join(base_dir, "downloader", "cookies", "twitter"),
      os.path.join(base_dir, "downloader", "cookies"),
      os.path.join(base_dir, "cookies"),
    ]
    for candidate in candidates:
      if os.path.isdir(candidate):
        return candidate
    return candidates[0]

  def _cookie_pool_files(self) -> list[str]:
    manual = (self.cookies_file_var.get() or "").strip()
    folder = (self.cookies_folder_var.get() or "").strip()
    if not folder:
      folder = self._cookie_pool_default_dir()
      self.cookies_folder_var.set(folder)

    out: list[str] = []
    seen: set[str] = set()

    def add_file(path: str) -> None:
      clean = (path or "").strip()
      if not clean or not os.path.isfile(clean):
        return
      key = os.path.normcase(os.path.abspath(clean))
      if key in seen:
        return
      seen.add(key)
      out.append(clean)

    if manual:
      add_file(manual)

    if os.path.isdir(folder):
      try:
        for name in sorted(os.listdir(folder), key=lambda item: item.lower()):
          lower = name.lower()
          if (lower.endswith(".txt") or lower.endswith(".json")) and "cookie" in lower:
            add_file(os.path.join(folder, name))
      except Exception:
        pass

    return out

  def pick_cookies_folder(self) -> None:
    initial = (self.cookies_folder_var.get() or "").strip() or self._cookie_pool_default_dir()
    selected = filedialog.askdirectory(initialdir=initial)
    if selected:
      self.cookies_folder_var.set(selected)
      pool = self._cookie_pool_files()
      self.log(f"Carpeta cookies activa: {selected} ({len(pool)} archivo(s))")
      self._refresh_x_instances_ui()

  def _cookie_label(self, path: str) -> str:
    if not path:
      return "Sin cookies"
    name = os.path.basename(path)
    folder = os.path.basename(os.path.dirname(path))
    return f"{name} ({folder})"

  def _open_instance_start_menu(self, monitor_id: int, anchor_widget) -> None:
    monitor = self._find_monitor_by_id(monitor_id)
    if not monitor:
      return

    menu = tk.Menu(self.root, tearoff=0)
    menu.add_command(
      label="Seleccionar: usar cookie global",
      command=lambda: self._clear_monitor_cookie_choice(monitor_id),
    )
    menu.add_command(
      label="Seleccionar: sin cookies",
      command=lambda: self._set_monitor_cookie_choice(monitor_id, ""),
    )
    pool = self._cookie_pool_files()
    if pool:
      menu.add_separator()
      for cookie_file in pool:
        menu.add_command(
          label=f"Seleccionar: {self._cookie_label(cookie_file)}",
          command=lambda chosen=cookie_file: self._set_monitor_cookie_choice(monitor_id, chosen),
        )
    menu.add_separator()
    menu.add_command(label="Elegir cookies manualmente...", command=lambda: self._choose_cookie_for_monitor(monitor_id))

    x = anchor_widget.winfo_rootx()
    y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
    try:
      menu.tk_popup(x, y)
    finally:
      menu.grab_release()

  def _set_monitor_cookie_choice(self, monitor_id: int, cookie_file: str) -> None:
    self.twitter_monitor_cookie_choice[int(monitor_id)] = (cookie_file or "").strip()
    self._refresh_x_instances_ui()

  def _clear_monitor_cookie_choice(self, monitor_id: int) -> None:
    self.twitter_monitor_cookie_choice.pop(int(monitor_id), None)
    self._refresh_x_instances_ui()

  def _set_global_cookie_choice(self, cookie_file: str) -> None:
    self.twitter_global_cookie_choice.set((cookie_file or "").strip())
    self._refresh_x_instances_ui()

  def _selected_global_cookie(self) -> str:
    return (self.twitter_global_cookie_choice.get() or "").strip()

  def _selected_cookie_for_monitor(self, monitor_id: int) -> str:
    monitor_key = int(monitor_id)
    if monitor_key in self.twitter_monitor_cookie_choice:
      return (self.twitter_monitor_cookie_choice.get(monitor_key, "") or "").strip()
    return self._selected_global_cookie()

  def _monitor_cookie_display(self, monitor_id: int) -> str:
    monitor_key = int(monitor_id)
    if monitor_key in self.twitter_monitor_cookie_choice:
      selected = (self.twitter_monitor_cookie_choice.get(monitor_key, "") or "").strip()
      return self._cookie_label(selected)
    selected_global = self._selected_global_cookie()
    if selected_global:
      return f"{self._cookie_label(selected_global)} [global]"
    return self._cookie_label("")

  def _choose_global_cookie(self) -> None:
    initial_dir = (self.cookies_folder_var.get() or "").strip() or self._cookie_pool_default_dir()
    selected = filedialog.askopenfilename(
      title="Selecciona cookie global para instancias X",
      initialdir=initial_dir,
      filetypes=[("Cookies", "*.txt *.json"), ("Todos", "*.*")],
    )
    if selected:
      self._set_global_cookie_choice(selected)
      self.log(f"Cookie global X: {self._cookie_label(selected)}")

  def _apply_global_cookie_to_all_monitors(self) -> None:
    selected_global = self._selected_global_cookie()
    for monitor in self.monitors:
      self.twitter_monitor_cookie_choice[int(monitor.get("id", 0))] = selected_global
    self._refresh_x_instances_ui()
    self.log(
      "Cookie global aplicada a todos los monitores"
      if selected_global
      else "Se aplico sin cookies a todos los monitores"
    )

  def _open_global_cookie_menu(self, anchor_widget) -> None:
    menu = tk.Menu(self.root, tearoff=0)
    menu.add_command(
      label="Seleccionar global: sin cookies",
      command=lambda: self._set_global_cookie_choice(""),
    )
    pool = self._cookie_pool_files()
    if pool:
      menu.add_separator()
      for cookie_file in pool:
        menu.add_command(
          label=f"Seleccionar global: {self._cookie_label(cookie_file)}",
          command=lambda chosen=cookie_file: self._set_global_cookie_choice(chosen),
        )
    menu.add_separator()
    menu.add_command(label="Elegir cookie global manualmente...", command=self._choose_global_cookie)

    x = anchor_widget.winfo_rootx()
    y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
    try:
      menu.tk_popup(x, y)
    finally:
      menu.grab_release()

  def _choose_cookie_for_monitor(self, monitor_id: int) -> None:
    initial_dir = (self.cookies_folder_var.get() or "").strip() or self._cookie_pool_default_dir()
    selected = filedialog.askopenfilename(
      title="Selecciona cookies para monitor",
      initialdir=initial_dir,
      filetypes=[("Cookies", "*.txt *.json"), ("Todos", "*.*")],
    )
    if selected:
      self._set_monitor_cookie_choice(monitor_id, selected)

  def _start_twitter_feed_instance(self, monitor_id: int, selected_cookie_file: str | None = None) -> None:
    monitor = self._find_monitor_by_id(monitor_id)
    if not monitor:
      messagebox.showerror(APP_TITLE, f"No se encontro monitor {monitor_id}")
      return

    try:
      cfg = self._feed_runtime_config()
    except Exception as exc:
      messagebox.showerror(APP_TITLE, f"Configuracion de feed invalida: {exc}")
      return

    instance_id = self.twitter_instance_seq
    self.twitter_instance_seq += 1
    instance_name = f"X#{instance_id}"
    chosen_cookie = (selected_cookie_file or "").strip() or self._selected_cookie_for_monitor(monitor_id)
    cookie_candidates = [chosen_cookie] if chosen_cookie else []

    scraper = FeedScraper(
      self.download_from_feed,
      poll_seconds=cfg["scroll_pause"],
      scroll_px=cfg["scroll_px"],
      cookies_file=chosen_cookie or (self.cookies_file_var.get() or "").strip(),
      image_dwell_seconds=cfg["image_seconds"],
      scroll_pause_seconds=cfg["scroll_pause"],
      wait_video_end=self.feed_wait_video_end_var.get(),
      max_video_wait_seconds=cfg["max_video_wait"],
      only_visible=True,
      start_maximized=True,
      tiktok_likes_only=self.feed_tiktok_likes_only_var.get(),
      cookie_candidates=cookie_candidates,
      monitor_bounds={
        "left": int(monitor.get("left", 0)),
        "top": int(monitor.get("top", 0)),
        "width": int(monitor.get("width", 0)),
        "height": int(monitor.get("height", 0)),
      },
      instance_name=instance_name,
    )
    scraper.set_log_callback(self.log)
    scraper.start("twitter")

    with self.twitter_instances_lock:
      self.twitter_instances[instance_id] = {
        "id": instance_id,
        "name": instance_name,
        "monitor_id": int(monitor.get("id", 0)),
        "monitor_label": str(monitor.get("label") or f"Monitor {monitor_id}"),
        "cookie_file": chosen_cookie,
        "scraper": scraper,
      }

    if chosen_cookie:
      self.log(f"Instancia {instance_name} iniciada en {monitor.get('label')} con cookies: {self._cookie_label(chosen_cookie)}")
    else:
      self.log(f"Instancia {instance_name} iniciada en {monitor.get('label')} (sin cookies dedicadas)")
    self._update_scraping_state()
    self._refresh_x_instances_ui()

    if not self.feed_worker_running:
      self.feed_worker_running = True
      threading.Thread(target=self._feed_download_worker, daemon=True).start()

  def _get_instance_item(self, instance_id: int) -> dict | None:
    with self.twitter_instances_lock:
      return self.twitter_instances.get(int(instance_id))

  def _pause_resume_instance(self, instance_id: int) -> None:
    item = self._get_instance_item(instance_id)
    if not item:
      return
    scraper = item.get("scraper")
    if not scraper:
      return
    scraper.toggle_pause()
    self._refresh_x_instances_ui()

  def _toggle_mute_instance(self, instance_id: int) -> None:
    item = self._get_instance_item(instance_id)
    if not item:
      return
    scraper = item.get("scraper")
    if not scraper:
      return
    scraper.toggle_muted()
    self._refresh_x_instances_ui()

  def _skip_instance(self, instance_id: int) -> None:
    item = self._get_instance_item(instance_id)
    if not item:
      return
    scraper = item.get("scraper")
    if scraper:
      scraper.request_skip()
      self.log(f"Skip solicitado para {item.get('name')}")

  def _prev_instance(self, instance_id: int) -> None:
    item = self._get_instance_item(instance_id)
    if not item:
      return
    scraper = item.get("scraper")
    if scraper:
      scraper.request_prev()
      self.log(f"Prev solicitado para {item.get('name')}")

  def _like_instance_current_post(self, instance_id: int) -> None:
    item = self._get_instance_item(instance_id)
    if not item:
      return
    scraper = item.get("scraper")
    if not scraper:
      return
    ok = scraper.request_like_current_twitter_post()
    if not ok:
      self.log(f"Like no disponible para {item.get('name')} (instancia no activa)")

  def _retweet_instance_current_post(self, instance_id: int) -> None:
    item = self._get_instance_item(instance_id)
    if not item:
      return
    scraper = item.get("scraper")
    if not scraper:
      return
    ok = scraper.request_retweet_current_twitter_post()
    if not ok:
      self.log(f"Retweet no disponible para {item.get('name')} (instancia no activa)")

  def _stop_instance(self, instance_id: int) -> None:
    with self.twitter_instances_lock:
      item = self.twitter_instances.pop(int(instance_id), None)
    if not item:
      return
    scraper = item.get("scraper")
    try:
      if scraper:
        scraper.stop()
    except Exception as exc:
      self.log(f"Aviso stop instancia {item.get('name')}: {exc}")
    self.log(f"Instancia {item.get('name')} detenida")
    self._update_scraping_state()
    self._refresh_x_instances_ui()

  def _kill_instance(self, instance_id: int) -> None:
    with self.twitter_instances_lock:
      item = self.twitter_instances.pop(int(instance_id), None)
    if not item:
      return
    scraper = item.get("scraper")
    try:
      if scraper:
        scraper.kill()
    except Exception as exc:
      self.log(f"Aviso kill instancia {item.get('name')}: {exc}")
    self.log(f"Instancia {item.get('name')} finalizada (kill)")
    self._update_scraping_state()
    self._refresh_x_instances_ui()

  def _kill_all_twitter_instances(self) -> None:
    with self.twitter_instances_lock:
      ids = list(self.twitter_instances.keys())
    for instance_id in ids:
      self._kill_instance(instance_id)
    self.log("Kill global aplicado a instancias X")

  def _skip_all_twitter_instances(self) -> None:
    with self.twitter_instances_lock:
      items = list(self.twitter_instances.values())
    for item in items:
      scraper = item.get("scraper")
      if scraper and scraper.is_running():
        scraper.request_skip()
    if items:
      self.log("Skip global aplicado a instancias X")

  def _prev_all_twitter_instances(self) -> None:
    with self.twitter_instances_lock:
      items = list(self.twitter_instances.values())
    for item in items:
      scraper = item.get("scraper")
      if scraper and scraper.is_running():
        scraper.request_prev()
    if items:
      self.log("Prev global aplicado a instancias X")

  def _set_instance_fullscreen(self, instance_id: int, enabled: bool | None = None) -> None:
    item = self._get_instance_item(instance_id)
    if not item:
      return
    scraper = item.get("scraper")
    if not scraper:
      return
    if enabled is None:
      scraper.toggle_window_fullscreen()
    else:
      scraper.set_window_fullscreen(bool(enabled))
    self._refresh_x_instances_ui()

  def _set_all_instances_fullscreen(self, enabled: bool) -> None:
    with self.twitter_instances_lock:
      items = list(self.twitter_instances.values())
    changed = 0
    for item in items:
      scraper = item.get("scraper")
      if not scraper or not scraper.is_running():
        continue
      scraper.set_window_fullscreen(enabled)
      changed += 1
    if changed:
      self.log("F11 global aplicado a instancias X" if enabled else "Salir F11 global aplicado a instancias X")
    self._refresh_x_instances_ui()

  def _prune_dead_twitter_instances(self) -> None:
    removed: list[str] = []
    with self.twitter_instances_lock:
      stale_ids = []
      for instance_id, item in self.twitter_instances.items():
        scraper = item.get("scraper")
        if scraper and scraper.is_running():
          continue
        stale_ids.append(instance_id)
      for instance_id in stale_ids:
        item = self.twitter_instances.pop(instance_id, None)
        if item:
          removed.append(str(item.get("name") or f"X#{instance_id}"))
    for name in removed:
      self.log(f"Instancia finalizada: {name}")
    if removed:
      self._update_scraping_state()

  def _refresh_x_instances_ui_tick(self) -> None:
    try:
      self._refresh_monitors_if_changed()
      self._apply_live_feed_runtime_updates()
      self._prune_dead_twitter_instances()
      self._refresh_x_instances_ui()
    finally:
      self.root.after(1500, self._refresh_x_instances_ui_tick)

  def _current_feed_runtime_payload(self) -> dict | None:
    try:
      cfg = self._feed_runtime_config()
    except Exception:
      return None

    return {
      "poll_seconds": float(cfg["scroll_pause"]),
      "scroll_pause_seconds": float(cfg["scroll_pause"]),
      "scroll_px": int(cfg["scroll_px"]),
      "image_dwell_seconds": float(cfg["image_seconds"]),
      "wait_video_end": bool(self.feed_wait_video_end_var.get()),
      "max_video_wait_seconds": float(cfg["max_video_wait"]),
      "tiktok_likes_only": bool(self.feed_tiktok_likes_only_var.get()),
    }

  def _apply_live_feed_runtime_updates(self) -> None:
    payload = self._current_feed_runtime_payload()
    if not payload:
      return

    signature = (
      payload["poll_seconds"],
      payload["scroll_pause_seconds"],
      payload["scroll_px"],
      payload["image_dwell_seconds"],
      payload["wait_video_end"],
      payload["max_video_wait_seconds"],
      payload["tiktok_likes_only"],
    )
    if signature == self._last_feed_runtime_signature:
      return
    self._last_feed_runtime_signature = signature

    updated = 0

    if self.scraper and self.scraper.is_running():
      try:
        if self.scraper.update_runtime_settings(**payload):
          updated += 1
      except Exception as exc:
        self.log(f"Aviso actualizacion feed principal: {exc}")

    with self.twitter_instances_lock:
      items = list(self.twitter_instances.values())

    for item in items:
      scraper = item.get("scraper")
      if not scraper or not scraper.is_running():
        continue
      try:
        if scraper.update_runtime_settings(**payload):
          updated += 1
      except Exception as exc:
        self.log(f"Aviso actualizacion {item.get('name')}: {exc}")

    if updated:
      self.log(
        "Feed runtime actualizado en vivo: "
        f"pausa={payload['scroll_pause_seconds']:.2f}s, "
        f"scroll={payload['scroll_px']}, "
        f"imagen={payload['image_dwell_seconds']:.2f}s, "
        f"max_video={payload['max_video_wait_seconds']:.1f}s"
      )

  def _sync_x_instances_canvas_region(self) -> None:
    canvas = self.x_instances_canvas
    table = self.x_instances_table
    window_id = self.x_instances_canvas_window
    if canvas is None or table is None or window_id is None:
      return
    if not canvas.winfo_exists() or not table.winfo_exists():
      return

    table.update_idletasks()
    table_width = table.winfo_reqwidth()
    canvas_width = canvas.winfo_width()
    target_width = max(table_width, canvas_width)

    canvas.itemconfigure(window_id, width=target_width)
    canvas.configure(scrollregion=canvas.bbox("all"))

  def _refresh_x_instances_ui(self) -> None:
    panel = self.x_instances_panel
    table = self.x_instances_table
    if panel is None or table is None:
      return
    if not panel.winfo_exists() or not table.winfo_exists():
      return

    with self.twitter_instances_lock:
      items = sorted(self.twitter_instances.values(), key=lambda row: int(row.get("id", 0)))

    monitor_cookie_signature = tuple(
      (
        int(monitor.get("id", 0)),
        (self.twitter_monitor_cookie_choice.get(int(monitor.get("id", 0)), "") or "").strip(),
      )
      for monitor in self.monitors
    )
    global_cookie_signature = self._selected_global_cookie()
    pool_count = len(self._cookie_pool_files())

    items_signature = tuple(
      (
        int(item.get("id", 0)),
        str(item.get("monitor_label") or ""),
        str(item.get("cookie_file") or ""),
        bool(item.get("scraper") and item.get("scraper").is_running()),
        bool(item.get("scraper") and item.get("scraper").is_paused()),
        bool(item.get("scraper") and item.get("scraper").is_muted()),
        bool(item.get("scraper") and item.get("scraper").is_window_fullscreen()),
      )
      for item in items
    )
    signature = (monitor_cookie_signature, global_cookie_signature, pool_count, items_signature)

    if not panel.winfo_manager():
      if self.log_frame_widget is not None:
        panel.pack(fill="x", pady=(0, 10), before=self.log_frame_widget)
      else:
        panel.pack(fill="x", pady=(0, 10))

    if self.x_instances_signature == signature:
      return
    self.x_instances_signature = signature

    for child in table.winfo_children():
      child.destroy()

    title_row = ttk.Frame(table)
    title_row.pack(fill="x", pady=(0, 6))
    ttk.Label(
      title_row,
      text="Gestion de instancias de Feed X por monitor",
      font=("Segoe UI Semibold", 10),
      foreground="#0f172a",
    ).pack(side="left")
    ttk.Label(
      title_row,
      text=f"Cookies detectadas: {pool_count}",
      foreground="#475569",
    ).pack(side="right")

    global_cookie_row = ttk.Frame(table)
    global_cookie_row.pack(fill="x", pady=(0, 8))
    global_cookie_text = self._cookie_label(self._selected_global_cookie())
    ttk.Label(global_cookie_row, text=f"Cookie global: {global_cookie_text}", foreground="#475569").pack(side="left", padx=(0, 8))
    global_trigger = ttk.Button(
      global_cookie_row,
      text="☰",
      style="Subtle.TButton",
      width=3,
      command=lambda: None,
    )
    global_trigger.configure(command=lambda btn=global_trigger: self._open_global_cookie_menu(btn))
    global_trigger.pack(side="left", padx=(0, 6))
    ttk.Button(
      global_cookie_row,
      text="Aplicar global a monitores",
      style="Subtle.TButton",
      command=self._apply_global_cookie_to_all_monitors,
    ).pack(side="left")

    monitors_box = ttk.Frame(table)
    monitors_box.pack(fill="x", pady=(0, 10))
    for monitor in self.monitors:
      row = ttk.Frame(monitors_box)
      row.pack(fill="x", pady=2)
      monitor_id = int(monitor.get("id", 0))
      cookie_text = self._monitor_cookie_display(monitor_id)

      ttk.Label(row, text=str(monitor.get("label") or "Monitor")).pack(side="left", padx=(0, 8))
      ttk.Label(row, text=f"Cookie seleccionada: {cookie_text}", foreground="#475569").pack(side="left", padx=(0, 8))
      trigger = ttk.Button(
        row,
        text="☰",
        style="Subtle.TButton",
        width=3,
        command=lambda: None,
      )
      trigger.configure(command=lambda m_id=monitor_id, btn=trigger: self._open_instance_start_menu(m_id, btn))
      trigger.pack(side="left", padx=(0, 6))
      ttk.Button(
        row,
        text="Iniciar",
        style="Accent.TButton",
        command=lambda m_id=monitor_id: self._start_twitter_feed_instance(m_id),
      ).pack(side="left")

    global_row = ttk.Frame(table)
    global_row.pack(fill="x", pady=(0, 8))
    ttk.Button(global_row, text="PREV global", style="Subtle.TButton", command=self._prev_all_twitter_instances).pack(side="left", padx=(0, 8))
    ttk.Button(global_row, text="Skip global", style="Subtle.TButton", command=self._skip_all_twitter_instances).pack(side="left", padx=(0, 8))
    ttk.Button(global_row, text="F11 global", style="Subtle.TButton", command=lambda: self._set_all_instances_fullscreen(True)).pack(side="left", padx=(0, 8))
    ttk.Button(global_row, text="Salir F11 global", style="Subtle.TButton", command=lambda: self._set_all_instances_fullscreen(False)).pack(side="left", padx=(0, 8))
    ttk.Button(global_row, text="Kill global", style="Danger.TButton", command=self._kill_all_twitter_instances).pack(side="left")

    for item in items:
      scraper = item.get("scraper")
      instance_id = int(item.get("id", 0))
      name = str(item.get("name") or f"X#{instance_id}")
      monitor_label = str(item.get("monitor_label") or "Monitor")
      running = bool(scraper and scraper.is_running())
      paused = bool(scraper and scraper.is_paused())
      muted = bool(scraper and scraper.is_muted())
      fullscreen = bool(scraper and scraper.is_window_fullscreen())
      status = "Activo"
      if paused:
        status = "Pausado"
      if not running:
        status = "Detenido"
      cookie_text = self._cookie_label(str(item.get("cookie_file") or ""))

      card = ttk.Frame(table)
      card.pack(fill="x", pady=3)
      left = ttk.Frame(card)
      left.pack(side="left", fill="x", expand=True)
      ttk.Label(left, text=f"{name} - {monitor_label}", font=("Segoe UI Semibold", 9)).pack(side="top", anchor="w")
      ttk.Label(left, text=f"Estado: {status} | Audio: {'Mute' if muted else 'On'} | Ventana: {'F11' if fullscreen else 'Max'}").pack(side="top", anchor="w")
      ttk.Label(left, text=f"Cookie: {cookie_text}", foreground="#475569").pack(side="top", anchor="w")

      controls = ttk.Frame(card)
      controls.pack(side="right")

      pause_text = "Reanudar" if paused else "Pausar"
      mute_text = "Unmute" if muted else "Mute"
      screen_text = "Salir F11" if fullscreen else "F11"

      ttk.Button(controls, text=pause_text, style="Subtle.TButton", command=lambda iid=instance_id: self._pause_resume_instance(iid)).pack(side="left", padx=2)
      ttk.Button(controls, text=mute_text, style="Subtle.TButton", command=lambda iid=instance_id: self._toggle_mute_instance(iid)).pack(side="left", padx=2)
      ttk.Button(controls, text=screen_text, style="Subtle.TButton", command=lambda iid=instance_id: self._set_instance_fullscreen(iid)).pack(side="left", padx=2)
      ttk.Button(controls, text="Like", style="Subtle.TButton", command=lambda iid=instance_id: self._like_instance_current_post(iid)).pack(side="left", padx=2)
      ttk.Button(controls, text="Retweet", style="Subtle.TButton", command=lambda iid=instance_id: self._retweet_instance_current_post(iid)).pack(side="left", padx=2)
      ttk.Button(controls, text="PREV", style="Subtle.TButton", command=lambda iid=instance_id: self._prev_instance(iid)).pack(side="left", padx=2)
      ttk.Button(controls, text="Skip", style="Subtle.TButton", command=lambda iid=instance_id: self._skip_instance(iid)).pack(side="left", padx=2)
      ttk.Button(controls, text="Detener", style="Danger.TButton", command=lambda iid=instance_id: self._stop_instance(iid)).pack(side="left", padx=2)
      ttk.Button(controls, text="Kill", style="Danger.TButton", command=lambda iid=instance_id: self._kill_instance(iid)).pack(side="left", padx=2)

    self._sync_x_instances_canvas_region()

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
      subprocess.Popen(
        [sys.executable, script_path],
        cwd=os.path.dirname(script_path),
        creationflags=CREATE_NO_WINDOW,
      )
      self.log("Reiniciando aplicacion...")
      self.root.after(150, self.root.destroy)
    except Exception as exc:
      messagebox.showerror(APP_TITLE, f"No se pudo reiniciar la aplicacion: {exc}")

  def _on_ui_language_change(self, _event=None) -> None:
    try:
      children = list(self.root.winfo_children())
      for child in children:
        child.destroy()
      self._build_ui()
      if self.log_history:
        self.log_widget.insert("end", "\n".join(self.log_history[-500:]) + "\n")
        self.log_widget.see("end")
      self._autosave_if_enabled()
    except Exception as exc:
      self.log(f"Aviso idioma UI: {exc}")

  def _load_persisted_settings(self) -> None:
    path = self.settings_file_path
    if not os.path.isfile(path):
      return

    try:
      with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    except Exception as exc:
      self.log(f"Aviso ajustes: no se pudo leer {path}: {exc}")
      return

    if not isinstance(data, dict):
      return

    self._loading_persisted_settings = True
    try:
      auto_save = bool(data.get("auto_save_defaults", data.get("auto_save", False)))
      self.auto_save_defaults_var.set(auto_save)
      self.output_dir_var.set(str(data.get("output_dir", self.output_dir_var.get()) or "").strip())
      self.image_output_dir_var.set(str(data.get("image_output_dir", self.image_output_dir_var.get()) or "").strip())
      self.cookies_file_var.set(str(data.get("cookies_file", self.cookies_file_var.get()) or "").strip())

      cookies_dir = str(data.get("cookies_folder", "") or "").strip()
      if not cookies_dir:
        cookies_dir = str(data.get("cookies_dir", self.cookies_folder_var.get()) or "").strip()
      self.cookies_folder_var.set(cookies_dir)

      self.use_cookies_var.set(bool(data.get("use_cookies", self.use_cookies_var.get())))
      self.cookies_browser_var.set(str(data.get("cookies_browser", self.cookies_browser_var.get()) or "chrome"))
      self.selected_language_var.set(str(data.get("selected_language", self.selected_language_var.get()) or "auto"))
      self.selected_quality_var.set(str(data.get("selected_quality", self.selected_quality_var.get()) or "best"))
      self.selected_audio_quality_var.set(str(data.get("selected_audio_quality", self.selected_audio_quality_var.get()) or "best audio"))
      self.include_subtitles_var.set(bool(data.get("include_subtitles", self.include_subtitles_var.get())))
      self.embed_subtitles_var.set(bool(data.get("embed_subtitles", self.embed_subtitles_var.get())))
      self.subtitle_lang_var.set(str(data.get("subtitle_lang", self.subtitle_lang_var.get()) or "auto"))
      self.compression_var.set(str(data.get("compression", self.compression_var.get()) or "sin_compresion"))
      self.feed_image_seconds_var.set(str(data.get("feed_image_seconds", self.feed_image_seconds_var.get()) or "10"))
      self.feed_scroll_pause_var.set(str(data.get("feed_scroll_pause", self.feed_scroll_pause_var.get()) or "1.5"))
      self.feed_scroll_px_var.set(str(data.get("feed_scroll_px", self.feed_scroll_px_var.get()) or "900"))
      self.feed_wait_video_end_var.set(bool(data.get("feed_wait_video_end", self.feed_wait_video_end_var.get())))
      self.feed_max_video_wait_var.set(str(data.get("feed_max_video_wait", self.feed_max_video_wait_var.get()) or "300"))
      self.feed_tiktok_likes_only_var.set(bool(data.get("feed_tiktok_likes_only", self.feed_tiktok_likes_only_var.get())))
      self.feed_twitter_creator_folders_var.set(bool(data.get("feed_twitter_creator_folders", self.feed_twitter_creator_folders_var.get())))
      self.x_actions_user_var.set(str(data.get("x_actions_user", data.get("x_user", self.x_actions_user_var.get())) or "").strip())
      self.x_actions_poll_seconds_var.set(str(data.get("x_actions_poll_seconds", self.x_actions_poll_seconds_var.get()) or "45"))
      self.x_actions_bookmarks_var.set(bool(data.get("x_actions_bookmarks", self.x_actions_bookmarks_var.get())))
      self.x_actions_likes_var.set(bool(data.get("x_actions_likes", self.x_actions_likes_var.get())))
      self.x_actions_retweets_var.set(bool(data.get("x_actions_retweets", self.x_actions_retweets_var.get())))
      self.x_actions_profile_var.set(bool(data.get("x_actions_profile", self.x_actions_profile_var.get())))
      self.ui_language_var.set(str(data.get("ui_language", self.ui_language_var.get()) or "es"))
      self.clipboard_monitor_var.set(bool(data.get("clipboard_monitor", self.clipboard_monitor_var.get())))
      self.clipboard_seen_urls = set(data.get("clipboard_seen_urls", []))
      self.remember_window_position_var.set(bool(data.get("remember_window_position", self.remember_window_position_var.get())))
      self._saved_window_geometry = str(data.get("window_geometry", "") or "").strip()
    finally:
      self._loading_persisted_settings = False

  def _settings_payload(self) -> dict:
    return {
      "auto_save": bool(self.auto_save_defaults_var.get()),
      "auto_save_defaults": bool(self.auto_save_defaults_var.get()),
      "output_dir": (self.output_dir_var.get() or "").strip(),
      "image_output_dir": (self.image_output_dir_var.get() or "").strip(),
      "cookies_file": (self.cookies_file_var.get() or "").strip(),
      "cookies_dir": (self.cookies_folder_var.get() or "").strip(),
      "cookies_folder": (self.cookies_folder_var.get() or "").strip(),
      "use_cookies": bool(self.use_cookies_var.get()),
      "cookies_browser": (self.cookies_browser_var.get() or "").strip(),
      "selected_language": (self.selected_language_var.get() or "").strip(),
      "selected_quality": (self.selected_quality_var.get() or "").strip(),
      "selected_audio_quality": (self.selected_audio_quality_var.get() or "").strip(),
      "include_subtitles": bool(self.include_subtitles_var.get()),
      "embed_subtitles": bool(self.embed_subtitles_var.get()),
      "subtitle_lang": (self.subtitle_lang_var.get() or "").strip(),
      "compression": (self.compression_var.get() or "").strip(),
      "feed_image_seconds": (self.feed_image_seconds_var.get() or "").strip(),
      "feed_scroll_pause": (self.feed_scroll_pause_var.get() or "").strip(),
      "feed_scroll_px": (self.feed_scroll_px_var.get() or "").strip(),
      "feed_wait_video_end": bool(self.feed_wait_video_end_var.get()),
      "feed_max_video_wait": (self.feed_max_video_wait_var.get() or "").strip(),
      "feed_tiktok_likes_only": bool(self.feed_tiktok_likes_only_var.get()),
      "feed_twitter_creator_folders": bool(self.feed_twitter_creator_folders_var.get()),
      "x_user": (self.x_actions_user_var.get() or "").strip(),
      "x_actions_user": (self.x_actions_user_var.get() or "").strip(),
      "x_actions_poll_seconds": (self.x_actions_poll_seconds_var.get() or "").strip(),
      "x_actions_bookmarks": bool(self.x_actions_bookmarks_var.get()),
      "x_actions_likes": bool(self.x_actions_likes_var.get()),
      "x_actions_retweets": bool(self.x_actions_retweets_var.get()),
      "x_actions_profile": bool(self.x_actions_profile_var.get()),
      "ui_language": (self.ui_language_var.get() or "").strip(),
      "clipboard_monitor": bool(self.clipboard_monitor_var.get()),
      "clipboard_seen_urls": list(self.clipboard_seen_urls),
      "remember_window_position": bool(self.remember_window_position_var.get()),
      "window_geometry": self._current_window_geometry() if self.remember_window_position_var.get() else "",
      "window_state": self._current_window_state(),
    }

  def _save_persisted_settings(self, silent: bool = True) -> None:
    payload = self._settings_payload()
    path = self.settings_file_path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
      json.dump(payload, f, indent=2, ensure_ascii=True)
    if not silent:
      self.log(f"Configuracion guardada en {path}")

  def _autosave_if_enabled(self) -> None:
    if self._loading_persisted_settings:
      return
    if not self.auto_save_defaults_var.get():
      return
    try:
      self._save_persisted_settings(silent=True)
    except Exception as exc:
      self.log(f"Aviso autosave: {exc}")

  def _bind_settings_autosave_hooks(self) -> None:
    if self._settings_hooks_bound:
      return

    watched_vars = [
      self.auto_save_defaults_var,
      self.output_dir_var,
      self.image_output_dir_var,
      self.cookies_file_var,
      self.cookies_folder_var,
      self.use_cookies_var,
      self.cookies_browser_var,
      self.selected_language_var,
      self.selected_quality_var,
      self.selected_audio_quality_var,
      self.include_subtitles_var,
      self.embed_subtitles_var,
      self.subtitle_lang_var,
      self.compression_var,
      self.feed_image_seconds_var,
      self.feed_scroll_pause_var,
      self.feed_scroll_px_var,
      self.feed_wait_video_end_var,
      self.feed_max_video_wait_var,
      self.feed_tiktok_likes_only_var,
      self.feed_twitter_creator_folders_var,
      self.x_actions_user_var,
      self.x_actions_poll_seconds_var,
      self.x_actions_bookmarks_var,
      self.x_actions_likes_var,
      self.x_actions_retweets_var,
      self.x_actions_profile_var,
      self.ui_language_var,
      self.clipboard_monitor_var,
      self.remember_window_position_var,
    ]

    for var in watched_vars:
      var.trace_add("write", lambda *_: self._autosave_if_enabled())

    self._settings_hooks_bound = True

  def _on_auto_save_defaults_toggle(self) -> None:
    enabled = bool(self.auto_save_defaults_var.get())
    if enabled:
      try:
        self._save_persisted_settings(silent=False)
      except Exception as exc:
        self.auto_save_defaults_var.set(False)
        messagebox.showerror(APP_TITLE, f"No se pudo activar guardado automatico: {exc}")
        return
      self.log("Guardado automatico de predeterminados: activado")
      return

    self.log("Guardado automatico de predeterminados: desactivado")

  def _current_window_geometry(self) -> str:
    try:
      geometry = str(self.root.geometry() or "").strip()
    except Exception:
      geometry = ""
    return geometry

  def _current_window_state(self) -> str:
    try:
      state = str(self.root.state() or "").strip().lower()
    except Exception:
      state = ""
    return state

  def _maximize_main_window(self) -> None:
    try:
      self.root.state("zoomed")
      return
    except Exception:
      pass
    try:
      self.root.attributes("-zoomed", True)
    except Exception:
      pass

  def _apply_initial_window_state(self) -> None:
    if self.remember_window_position_var.get() and self._saved_window_geometry:
      try:
        self.root.geometry(self._saved_window_geometry)
      except Exception:
        pass
    self.root.after(100, self._maximize_main_window)

  def _schedule_window_geometry_save(self) -> None:
    if self._loading_persisted_settings:
      return
    if not self.remember_window_position_var.get():
      return
    try:
      if self._window_geometry_save_job:
        self.root.after_cancel(self._window_geometry_save_job)
    except Exception:
      pass
    self._window_geometry_save_job = self.root.after(600, self._save_window_geometry)

  def _save_window_geometry(self) -> None:
    self._window_geometry_save_job = None
    if self._loading_persisted_settings or not self.remember_window_position_var.get():
      return
    try:
      self._save_persisted_settings(silent=True)
    except Exception:
      pass

  def _on_main_close(self) -> None:
    try:
      if self.remember_window_position_var.get():
        self._save_persisted_settings(silent=True)
    except Exception:
      pass
    self.root.destroy()

  def _install_python_package(self, package_name: str) -> bool:
    proc = subprocess.run(
      [sys.executable, "-m", "pip", "install", package_name],
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      timeout=240,
      check=False,
      creationflags=CREATE_NO_WINDOW
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
        if package_name == "playwright":
          self.log("Instalando navegadores Playwright...")
          subprocess.run(
            [sys.executable, "-m", "playwright", "install"],
            capture_output=True,
            timeout=300,
            check=False,
            creationflags=CREATE_NO_WINDOW
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

  def _queue_feed_download(
    self,
    url: str,
    prefer_image_output: bool = False,
    creator_hint: str | None = None,
    media_kind: str | None = None,
    media_urls: list[str] | None = None,
  ) -> None:
    clean = (url or "").strip()
    if not clean:
      return

    if clean in self.feed_urls_queued:
      return

    self.feed_urls_queued.add(clean)
    self.feed_download_queue.put(
      {
        "url": clean,
        "prefer_image_output": bool(prefer_image_output),
        "creator_hint": (creator_hint or "").strip(),
        "media_kind": (media_kind or "").strip(),
        "media_urls": [str(item).strip() for item in (media_urls or []) if str(item).strip()],
      }
    )
    self.log(f"Encolado para descarga automatica: {clean}")

    if not self.feed_worker_running:
      self.feed_worker_running = True
      threading.Thread(target=self._feed_download_worker, daemon=True).start()

  def download_from_feed(self, payload) -> None:
    if isinstance(payload, dict):
      url = str(payload.get("url") or "").strip()
      self._queue_feed_download(
        url,
        prefer_image_output=bool(payload.get("prefer_image_output", False)),
        creator_hint=str(payload.get("creator_hint") or "").strip() or None,
        media_kind=str(payload.get("media_kind") or "").strip() or None,
        media_urls=list(payload.get("media_urls") or []),
      )
      return

    self._queue_feed_download(str(payload or "").strip(), prefer_image_output=False, creator_hint=None)

  def _feed_download_worker(self) -> None:
    self.log("Worker de feed iniciado")
    try:
      while True:
        try:
          payload = self.feed_download_queue.get(timeout=1.0)
        except queue.Empty:
          if not self.is_scraping and not self.x_actions_running:
            break
          continue

        try:
          if isinstance(payload, dict):
            url = str(payload.get("url") or "").strip()
            prefer_image_output = bool(payload.get("prefer_image_output", False))
            creator_hint = str(payload.get("creator_hint") or "").strip() or None
            media_kind = str(payload.get("media_kind") or "").strip().lower()
            media_urls = [str(item).strip() for item in (payload.get("media_urls") or []) if str(item).strip()]
          else:
            url = str(payload or "").strip()
            prefer_image_output = False
            creator_hint = None
            media_kind = ""
            media_urls = []

          if not url:
            continue

          is_twitter = self._is_twitter_url(url)
          if is_twitter:
            if not creator_hint:
              creator_hint = self._twitter_creator_from_url(url)
            if media_kind in {"image", "carousel"} or media_urls:
              prefer_image_output = True
            if not (media_kind in {"image", "carousel"} or media_urls):
              media_probe = self._probe_twitter_status_media(url)
              if not creator_hint and media_probe.get("author_handle"):
                creator_hint = str(media_probe.get("author_handle") or "").strip() or None
              if bool(media_probe.get("has_image", False)) and not bool(media_probe.get("has_video", False)):
                prefer_image_output = True

          if prefer_image_output:
            feed_out_dir = self._build_image_output_dir(url, creator_hint=creator_hint)
            self.log(f"Feed: item detectado como imagen/carrusel, usando salida imagenes: {feed_out_dir}")
          else:
            feed_out_dir = self._feed_output_dir_for_url(url, creator_hint=creator_hint)

          feed_output_template = os.path.join(feed_out_dir, DEFAULT_OUTPUT_TEMPLATE)
          if is_twitter:
            if prefer_image_output and media_urls:
              saved = self._download_twitter_media_urls(media_urls, feed_out_dir)
              if saved:
                self._remember_downloaded_status(url)
                self.log(f"OK feed: {url}")
                continue

            self.log("Feed Twitter/X: intentando primero con gallery-dl (mejor para imagenes/carruseles)...")
            ok, detail = self._gallery_dl_download(url, out_dir=feed_out_dir)
            if ok:
              self._remember_downloaded_status(url)
              self.log(f"OK feed: {url}")
              continue
            self.log(f"Feed Twitter/X: gallery-dl no pudo extraer este item ({detail}).")

            if prefer_image_output:
              raise RuntimeError(f"No se pudo descargar imagen/carrusel. Detalle gallery-dl: {detail}")
            self.log("Feed Twitter/X: reintentando con yt-dlp...")

          compression_args = self._compression_postprocessor_args()
          strict_video_mode = not prefer_image_output
          if strict_video_mode:
            specific_args = [
              "-f",
              self._language_format_selector(),
              "--merge-output-format",
              "mp4",
              "-o",
              feed_output_template,
            ]
            if compression_args:
              specific_args += ["--recode-video", "mp4", "--postprocessor-args", compression_args]
          else:
            specific_args = ["-o", feed_output_template]
            self.log("Feed Twitter/X: ruta de imagen/carrusel detectada, omitiendo modo estricto de video.")

          self.log(f"Descargando desde feed: {url}")
          try:
            self._run_yt_dlp_download(url, specific_args, allow_social_fallback=True)
          except Exception as strict_exc:
            if not strict_video_mode:
              raise
            self.log(
              "Feed: modo video/audio no aplico para este item. "
              "Reintentando en modo compatible (incluye imagenes)."
            )
            self.log(f"Detalle modo estricto: {strict_exc}")
            fallback_args = ["-o", feed_output_template]
            self._run_yt_dlp_download(url, fallback_args, allow_social_fallback=True)

          self._remember_downloaded_status(url)
          self.log(f"OK feed: {url}")
        except Exception as exc:
          self.log(f"ERROR feed {url}: {exc}")
        finally:
          self.feed_download_queue.task_done()
    finally:
      self.feed_worker_running = False
      self.log("Worker de feed detenido")

  def _extract_twitter_username_from_text(self, value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
      return None

    if raw.startswith("@"):
      raw = raw[1:]

    match = re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/?#]+)", raw, flags=re.IGNORECASE)
    if match:
      raw = match.group(1)

    clean = raw.strip().strip("/")
    if not clean:
      return None
    if clean.lower() in {"home", "i", "explore", "search"}:
      return None
    if not re.match(r"^[A-Za-z0-9_]{1,15}$", clean):
      return None
    return clean

  def _resolve_x_actions_user(self) -> str | None:
    candidates = [
      self.x_actions_user_var.get(),
      self.twitter_url_var.get(),
      self.url_var.get(),
    ]
    for value in candidates:
      parsed = self._extract_twitter_username_from_text(value or "")
      if parsed:
        return parsed
    return None

  def _x_actions_sources(self) -> list[tuple[str, str]]:
    user = self._resolve_x_actions_user()
    sources: list[tuple[str, str]] = []

    if self.x_actions_bookmarks_var.get():
      sources.append(("guardados", "https://x.com/i/bookmarks"))

    if self.x_actions_likes_var.get():
      if user:
        sources.append(("likes", f"https://x.com/{user}/likes"))

    if self.x_actions_retweets_var.get():
      if user:
        sources.append(("retweets", "https://x.com/home"))

    if self.x_actions_profile_var.get():
      if user:
        sources.append(("perfil", f"https://x.com/{user}"))
      sources.append(("perfil", "https://x.com/home"))

    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, source_url in sources:
      key = source_url.strip().lower()
      if not key or key in seen:
        continue
      seen.add(key)
      unique.append((label, source_url))
    return unique

  def _canonical_twitter_status_url(self, url: str) -> str | None:
    text = (url or "").strip()
    if not text:
      return None
    fallback_match = re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/i(?:/web)?/status/(\d+)", text, flags=re.IGNORECASE)
    if fallback_match:
      return f"https://x.com/i/web/status/{fallback_match.group(1)}"

    match = re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/?#]+)/status/(\d+)", text, flags=re.IGNORECASE)
    if not match:
      return None
    user = match.group(1)
    status_id = match.group(2)
    if user.lower() == "i":
      return f"https://x.com/i/web/status/{status_id}"
    return f"https://x.com/{user}/status/{status_id}"

  def _status_id_from_url(self, url: str) -> int | None:
    text = (url or "").strip()
    if not text:
      return None
    match = re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/(?:[^/?#]+/status|i(?:/web)?/status)/(\d+)", text, flags=re.IGNORECASE)
    if not match:
      return None
    try:
      return int(match.group(1))
    except Exception:
      return None

  def _probe_twitter_status_media(self, status_url: str) -> dict:
    result = {
      "has_video": False,
      "has_image": False,
      "has_media": False,
      "author_handle": None,
    }
    if not self._is_twitter_url(status_url or ""):
      return result

    args = [
      *self._gallery_dl_common_args(log_usage=False),
      "--range",
      "1-3",
      "--dump-json",
      status_url,
    ]
    proc = self._run_gallery_dl_raw(args, timeout=70)
    if proc.returncode != 0:
      return result

    rows = self._urls_from_gallery_dl_output(status_url, proc.stdout or "")
    target_id = self._status_id_from_url(status_url)
    for row in rows:
      row_id = row.get("status_id")
      if isinstance(target_id, int) and isinstance(row_id, int) and row_id != target_id:
        continue
      result["has_video"] = bool(result["has_video"] or row.get("has_video", False))
      result["has_image"] = bool(result["has_image"] or row.get("has_image", False))
      result["has_media"] = bool(result["has_media"] or row.get("has_media", False))
      if not result["author_handle"] and row.get("author_handle"):
        result["author_handle"] = str(row.get("author_handle") or "").strip()

    if result["has_image"] or result["has_video"]:
      result["has_media"] = True
    return result

  def _scan_downloaded_status_ids(self) -> set[int]:
    pattern = re.compile(r"\[(\d{8,25})\]")
    roots: list[str] = []

    output_root = (self.output_dir_var.get() or "").strip()
    image_root = (self.image_output_dir_var.get() or "").strip()
    if output_root:
      roots.append(output_root)
    if image_root:
      roots.append(image_root)

    base_dir = os.path.dirname(os.path.dirname(__file__))
    roots.extend([
      os.path.join(base_dir, "videos"),
      os.path.join(base_dir, "downloads"),
      os.path.join(base_dir, "images"),
    ])

    unique_roots: list[str] = []
    seen_roots: set[str] = set()
    for root in roots:
      clean = (root or "").strip()
      if not clean or not os.path.isdir(clean):
        continue
      key = os.path.normcase(os.path.abspath(clean))
      if key in seen_roots:
        continue
      seen_roots.add(key)
      unique_roots.append(clean)

    found: set[int] = set()
    for root in unique_roots:
      try:
        for _dirpath, _dirnames, filenames in os.walk(root):
          for name in filenames:
            for match in pattern.findall(name):
              try:
                found.add(int(match))
              except Exception:
                pass
      except Exception:
        continue
    return found

  def _is_status_already_downloaded(self, status_url: str) -> bool:
    status_id = self._status_id_from_url(status_url)
    if not isinstance(status_id, int):
      return False
    return status_id in self.downloaded_status_ids

  def _remember_downloaded_status(self, status_url: str) -> None:
    status_id = self._status_id_from_url(status_url)
    if isinstance(status_id, int):
      self.downloaded_status_ids.add(status_id)

  def _gallery_item_has_video(self, item: dict) -> bool:
    if not isinstance(item, dict):
      return False

    explicit_type = str(item.get("type") or "").strip().lower()
    if explicit_type in {"video", "animated_gif"}:
      return True

    if item.get("is_video") is True:
      return True

    videos = item.get("videos")
    if isinstance(videos, list) and videos:
      return True
    if isinstance(videos, dict) and videos:
      return True

    for key in ("video_url", "video_id", "video_info"):
      value = item.get(key)
      if isinstance(value, str) and value.strip():
        return True
      if isinstance(value, dict) and value:
        return True

    media = item.get("media")
    if isinstance(media, list):
      for media_item in media:
        if not isinstance(media_item, dict):
          continue
        m_type = str(media_item.get("type") or "").strip().lower()
        if m_type in {"video", "animated_gif"}:
          return True

    return False

  def _gallery_item_has_image(self, item: dict) -> bool:
    if not isinstance(item, dict):
      return False

    explicit_type = str(item.get("type") or "").strip().lower()
    if explicit_type in {"image", "photo"}:
      return True

    for key in ("image", "image_url", "image_urls", "thumbnail", "thumbnails"):
      value = item.get(key)
      if isinstance(value, str) and value.strip():
        return True
      if isinstance(value, list) and value:
        return True
      if isinstance(value, dict) and value:
        return True

    media = item.get("media")
    if isinstance(media, list) and media:
      for media_item in media:
        if not isinstance(media_item, dict):
          continue
        m_type = str(media_item.get("type") or "").strip().lower()
        if m_type in {"image", "photo"}:
          return True
        m_url = media_item.get("url") or media_item.get("image") or media_item.get("src")
        if isinstance(m_url, str) and m_url.strip():
          return True

    return False

  def _gallery_item_has_media(self, item: dict) -> bool:
    return self._gallery_item_has_video(item) or self._gallery_item_has_image(item)

  def _urls_from_gallery_dl_output(self, source_url: str, stdout: str) -> list[dict]:
    def extract_handle(raw_value: object) -> str | None:
      if isinstance(raw_value, str):
        maybe = raw_value.strip().lstrip("@")
        if re.match(r"^[A-Za-z0-9_]{1,15}$", maybe):
          return maybe
      elif isinstance(raw_value, dict):
        for key in ("nick", "screen_name", "username", "name"):
          maybe = str(raw_value.get(key) or "").strip().lstrip("@")
          if re.match(r"^[A-Za-z0-9_]{1,15}$", maybe):
            return maybe
      return None

    def collect_from_item(item: dict, bag: list[dict]) -> None:
      candidates = []
      for key in ("url", "post_url", "tweet_url", "original_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
          candidates.append(value)

      tweet_id = item.get("tweet_id") or item.get("id")
      raw_user = item.get("author") or item.get("user") or item.get("screen_name")
      user_candidate = extract_handle(raw_user)
      actor_candidate = extract_handle(item.get("user"))
      text_candidates: list[str] = []
      for text_key in ("content", "text", "full_text", "description", "title"):
        raw_text = item.get(text_key)
        if isinstance(raw_text, str) and raw_text.strip():
          text_candidates.append(raw_text.strip())

      has_rt_prefix = any(text.lower().startswith("rt @") for text in text_candidates)
      has_retweeted_flag = bool(item.get("retweeted", False))
      has_retweeted_status = item.get("retweeted_status") is not None

      is_retweet_candidate = bool(
        actor_candidate
        and user_candidate
        and actor_candidate.lower() != user_candidate.lower()
      ) or has_rt_prefix or has_retweeted_flag or has_retweeted_status

      reply_id = item.get("reply_id")
      try:
        reply_id_int = int(str(reply_id))
      except Exception:
        reply_id_int = None

      if tweet_id:
        if user_candidate:
          candidates.append(f"https://x.com/{user_candidate}/status/{tweet_id}")
        candidates.append(f"https://x.com/i/web/status/{tweet_id}")

      has_video = self._gallery_item_has_video(item)
      has_image = self._gallery_item_has_image(item)
      has_media = has_video or has_image

      for candidate in candidates:
        canonical = self._canonical_twitter_status_url(str(candidate))
        if canonical:
          bag.append(
            {
              "url": canonical,
              "status_id": self._status_id_from_url(canonical),
              "has_video": has_video,
              "has_image": has_image,
              "has_media": has_media,
              "author_handle": user_candidate,
              "actor_handle": actor_candidate,
              "is_retweet_candidate": is_retweet_candidate,
              "reply_id": reply_id_int,
            }
          )

    out: list[dict] = []

    def collect_from_unknown(payload: object) -> None:
      if isinstance(payload, dict):
        collect_from_item(payload, out)
        return
      if isinstance(payload, list):
        for entry in payload:
          if isinstance(entry, dict):
            collect_from_item(entry, out)
        for entry in payload:
          if isinstance(entry, list):
            collect_from_unknown(entry)

    full_text = (stdout or "").strip()
    if full_text:
      try:
        parsed = json.loads(full_text)
        collect_from_unknown(parsed)
      except Exception:
        pass

    for line in (stdout or "").splitlines():
      row = line.strip().rstrip(",")
      if not row:
        continue
      try:
        item = json.loads(row)
      except Exception:
        continue
      collect_from_unknown(item)

    unique: list[dict] = []
    seen_by_status: dict[int, int] = {}
    seen_by_url: dict[str, int] = {}
    for item in out:
      url = str(item.get("url") or "").strip()
      if not url:
        continue
      has_video = bool(item.get("has_video", False))
      status_id = item.get("status_id")

      if isinstance(status_id, int):
        idx = seen_by_status.get(status_id)
        if idx is not None:
          row = unique[idx]
          if has_video and not bool(row.get("has_video", False)):
            row["has_video"] = True
          if bool(item.get("has_image", False)) and not bool(row.get("has_image", False)):
            row["has_image"] = True
          if bool(item.get("has_media", False)) and not bool(row.get("has_media", False)):
            row["has_media"] = True
          if bool(item.get("is_retweet_candidate", False)) and not bool(row.get("is_retweet_candidate", False)):
            row["is_retweet_candidate"] = True
          if not row.get("actor_handle") and item.get("actor_handle"):
            row["actor_handle"] = item.get("actor_handle")
          if not row.get("author_handle") and item.get("author_handle"):
            row["author_handle"] = item.get("author_handle")
          if row.get("reply_id") is None and item.get("reply_id") is not None:
            row["reply_id"] = item.get("reply_id")
          current_url = str(row.get("url") or "")
          current_is_iweb = "/i/web/status/" in current_url.lower()
          incoming_is_iweb = "/i/web/status/" in url.lower()
          if incoming_is_iweb and not current_is_iweb:
            row["url"] = url
          continue

      if url in seen_by_url:
        row = unique[seen_by_url[url]]
        if has_video and not bool(row.get("has_video", False)):
          row["has_video"] = True
        if bool(item.get("has_image", False)) and not bool(row.get("has_image", False)):
          row["has_image"] = True
        if bool(item.get("has_media", False)) and not bool(row.get("has_media", False)):
          row["has_media"] = True
        if bool(item.get("is_retweet_candidate", False)) and not bool(row.get("is_retweet_candidate", False)):
          row["is_retweet_candidate"] = True
        if not row.get("actor_handle") and item.get("actor_handle"):
          row["actor_handle"] = item.get("actor_handle")
        if not row.get("author_handle") and item.get("author_handle"):
          row["author_handle"] = item.get("author_handle")
        if row.get("reply_id") is None and item.get("reply_id") is not None:
          row["reply_id"] = item.get("reply_id")
        continue

      unique.append(item)
      new_index = len(unique) - 1
      seen_by_url[url] = new_index
      if isinstance(status_id, int):
        seen_by_status[status_id] = new_index
    return unique

  def _resolve_twitter_creator_for_status_url(self, url: str) -> str | None:
    if not self._is_twitter_url(url or ""):
      return None

    status_id = self._status_id_from_url(url)
    if isinstance(status_id, int):
      cached = self.twitter_creator_cache.get(status_id)
      if cached:
        return cached

    direct = self._twitter_creator_from_url(url)
    if direct and direct.lower() not in {"i", "web"}:
      if isinstance(status_id, int):
        self.twitter_creator_cache[status_id] = direct
      return direct

    args = [
      *self._gallery_dl_common_args(log_usage=False),
      "--range",
      "1-2",
      "--dump-json",
      url,
    ]
    proc = self._run_gallery_dl_raw(args, timeout=90)
    if proc.returncode != 0:
      return None

    target_status_id = self._status_id_from_url(url)
    creator_from_raw = self._creator_from_gallery_dl_stdout(proc.stdout or "", target_status_id)
    if creator_from_raw:
      if isinstance(status_id, int):
        self.twitter_creator_cache[status_id] = creator_from_raw
      return creator_from_raw

    rows = self._urls_from_gallery_dl_output(url, proc.stdout or "")
    for row in rows:
      candidate_url = str(row.get("url") or "").strip()
      creator = self._twitter_creator_from_url(candidate_url)
      if creator and creator.lower() not in {"i", "web"}:
        if isinstance(status_id, int):
          self.twitter_creator_cache[status_id] = creator
        return creator
    return None

  def _creator_from_gallery_item(self, item: dict, target_status_id: int | None) -> str | None:
    if not isinstance(item, dict):
      return None

    if target_status_id is not None:
      raw_id = item.get("tweet_id") or item.get("id")
      try:
        if int(str(raw_id)) != int(target_status_id):
          return None
      except Exception:
        return None

    candidates: list[str] = []

    direct_author = item.get("author")
    if isinstance(direct_author, str):
      candidates.append(direct_author)
    elif isinstance(direct_author, dict):
      for key in ("nick", "screen_name", "username", "name"):
        value = direct_author.get(key)
        if value is not None:
          candidates.append(str(value))

    for field in ("screen_name", "username", "user"):
      value = item.get(field)
      if isinstance(value, str):
        candidates.append(value)
      elif isinstance(value, dict):
        for key in ("nick", "screen_name", "username", "name"):
          nested = value.get(key)
          if nested is not None:
            candidates.append(str(nested))

    for raw in candidates:
      clean = (raw or "").strip().lstrip("@")
      if re.match(r"^[A-Za-z0-9_]{1,15}$", clean):
        return clean
    return None

  def _creator_from_gallery_dl_stdout(self, stdout: str, target_status_id: int | None = None) -> str | None:
    def walk(payload: object) -> str | None:
      if isinstance(payload, dict):
        found = self._creator_from_gallery_item(payload, target_status_id)
        if found:
          return found
        for value in payload.values():
          nested = walk(value)
          if nested:
            return nested
        return None

      if isinstance(payload, list):
        for value in payload:
          nested = walk(value)
          if nested:
            return nested
      return None

    text = (stdout or "").strip()
    if not text:
      return None

    try:
      parsed = json.loads(text)
      found = walk(parsed)
      if found:
        return found
    except Exception:
      pass
    for line in text.splitlines():
      row = line.strip().rstrip(",")
      if not row:
        continue
      try:
        obj = json.loads(row)
      except Exception:
        continue
      found = walk(obj)
      if found:
        return found

    return None

  def _fetch_x_action_urls(self, source_url: str, limit: int = X_ACTION_RANGE) -> list[dict]:
    args = [
      *self._gallery_dl_common_args(log_usage=False),
      "--range",
      f"1-{max(1, int(limit))}",
      "--dump-json",
      source_url,
    ]
    proc = self._run_gallery_dl_raw(args, timeout=90)
    if proc.returncode != 0:
      detail = (proc.stderr or proc.stdout or "sin detalle").strip()
      self.log(f"X monitor: no se pudo leer {source_url} con gallery-dl ({detail})")
      return []
    rows = self._urls_from_gallery_dl_output(source_url, proc.stdout or "")
    if not rows:
      preview = (proc.stdout or "").strip().splitlines()
      snippet = " | ".join(preview[:2]) if preview else "sin salida parseable"
      self.log(f"X monitor: 0 URLs parseadas en {source_url}. Muestra salida: {snippet}")
    return rows

  def _playwright_cookie_candidates(self) -> list[str]:
    return [p for p in self._existing_cookie_files() if p.lower().endswith(".txt")]

  def _load_netscape_cookies_for_playwright(self, file_path: str) -> list[dict]:
    cookies: list[dict] = []
    try:
      with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
          line = raw.strip()
          if not line or line.startswith("#"):
            continue

          parts = line.split("\t")
          if len(parts) < 7:
            continue

          domain, _, path, secure_flag, expires, name, value = parts[:7]
          if not name or not domain:
            continue

          cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": str(secure_flag).upper() == "TRUE",
          }

          try:
            exp = int(float(expires))
            if exp > 0:
              cookie["expires"] = exp
          except Exception:
            pass

          cookies.append(cookie)
    except Exception as exc:
      self.log(f"X monitor: no pude leer cookies para Playwright ({exc})")
    return cookies

  def _fetch_retweet_urls_from_html(self, user: str, limit: int = 60) -> list[str]:
    clean_user = (user or "").strip().lstrip("@")
    if not clean_user:
      return []

    now = time.time()
    cached = self.x_retweet_html_cache.get(clean_user.lower())
    if cached and (now - float(cached[0])) < 40.0:
      return list(cached[1])

    try:
      from playwright.sync_api import sync_playwright
    except Exception as exc:
      self.log(f"X monitor: Playwright no disponible para fallback retweets ({exc})")
      return []

    urls: list[str] = []
    cookie_files = self._playwright_cookie_candidates()

    try:
      with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
          viewport={"width": 1360, "height": 900},
          user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
          ),
        )

        merged_cookies: list[dict] = []
        for cookie_file in cookie_files:
          merged_cookies.extend(self._load_netscape_cookies_for_playwright(cookie_file))
        if merged_cookies:
          try:
            context.add_cookies(merged_cookies)
          except Exception as cookie_exc:
            self.log(f"X monitor: no pude aplicar cookies Playwright ({cookie_exc})")

        page = context.new_page()
        page.goto(f"https://x.com/{clean_user}", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1800)

        for _ in range(2):
          page.mouse.wheel(0, 1400)
          page.wait_for_timeout(550)

        raw_urls = page.evaluate(
          """
          (maxCount) => {
            const normalizeStatus = (href) => {
              if (!href) return null;
              const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/(?:([^\\/?#]+)\\/status|i(?:\\/web)?\\/status)\\/(\\d+)/i);
              if (!m) return null;
              if ((m[1] || '').toLowerCase() === 'i') {
                return `https://x.com/i/web/status/${m[2]}`;
              }
              return m[1] ? `https://x.com/${m[1]}/status/${m[2]}` : `https://x.com/i/web/status/${m[2]}`;
            };

            const repostRegex = /(reposted|repost|reposte[oó]|reposteaste|repostaste)/i;
            const out = [];
            const seen = new Set();
            const articles = Array.from(document.querySelectorAll('article'));
            for (const article of articles) {
              const text = (article.innerText || '').trim();
              if (!repostRegex.test(text)) {
                continue;
              }

              let chosen = null;
              const timeNode = article.querySelector('time');
              if (timeNode && timeNode.parentElement && timeNode.parentElement.tagName === 'A') {
                chosen = timeNode.parentElement;
              }
              if (!chosen) {
                chosen = article.querySelector('a[href*="/status/"]');
              }
              if (!chosen) {
                continue;
              }

              const abs = chosen.href || chosen.getAttribute('href') || '';
              const canonical = normalizeStatus(abs);
              if (!canonical || seen.has(canonical)) {
                continue;
              }
              seen.add(canonical);
              out.push(canonical);
              if (out.length >= maxCount) {
                return out;
              }
            }
            return out;
          }
          """,
          int(max(10, limit)),
        )

        if isinstance(raw_urls, list):
          urls = [str(u).strip() for u in raw_urls if isinstance(u, str) and u.strip()]

        context.close()
        browser.close()
    except Exception as exc:
      self.log(f"X monitor: fallback HTML retweets fallo ({exc})")
      return []

    self.x_retweet_html_cache[clean_user.lower()] = (time.time(), urls)
    return urls

  def _run_x_actions_monitor(self) -> None:
    self.log("X monitor invisible: iniciado (sin abrir navegador)")
    while not self.x_actions_stop_event.is_set():
      sources = self._x_actions_sources()
      if not sources:
        self.log("X monitor: activa al menos un checkbox (guardados/likes/retweets/perfil).")
      else:
        user = self._resolve_x_actions_user()
        if user:
          self.log(f"X monitor: usuario detectado={user}")
        else:
          needs_user = self.x_actions_likes_var.get() or self.x_actions_retweets_var.get() or self.x_actions_profile_var.get()
          if needs_user:
            self.log(
              "X monitor: no pude inferir usuario para likes/retweets/perfil. "
              "Pega tu @usuario o URL en 'Usuario X'."
            )
          else:
            self.log("X monitor: sin usuario explicito; se procesaran solo fuentes que no lo requieren.")

        if not self._existing_cookie_files():
          self.log("X monitor: no hay cookies disponibles, no se puede leer acciones privadas.")
        else:
          for label, source_url in sources:
            if self.x_actions_stop_event.is_set():
              break
            self.log(f"X monitor: consultando {label} -> {source_url}")
            label_key = label.lower()
            limit = max(X_ACTION_RANGE, 40) if label_key == "retweets" else X_ACTION_RANGE
            found_rows = self._fetch_x_action_urls(source_url, limit=limit)
            if not found_rows:
              self.log(f"X monitor: sin items detectables en {label} para esta revision.")
              continue
            total_items = len(found_rows)
            video_rows = [row for row in found_rows if bool(row.get("has_video", False))]
            image_rows = [row for row in found_rows if bool(row.get("has_image", False))]
            media_rows = [row for row in found_rows if bool(row.get("has_media", False))]
            self.log(
              f"X monitor: {label} devolvio {total_items} item(s), media={len(media_rows)}, "
              f"videos={len(video_rows)}, imagenes={len(image_rows)}."
            )

            is_action_reference_flow = label_key in {"guardados", "likes", "retweets"}

            if is_action_reference_flow:
              candidate_rows_unique: list[dict] = []
              seen_candidate_urls: set[str] = set()
              seen_candidate_status_ids: set[int] = set()
              monitored_user = (user or "").strip().lstrip("@").lower()
              if label_key == "retweets":
                media_hint_by_status: dict[int, dict] = {}
                for row in found_rows:
                  sid = row.get("status_id")
                  if not isinstance(sid, int):
                    sid = self._status_id_from_url(str(row.get("url") or ""))
                  if not isinstance(sid, int):
                    continue
                  prev = media_hint_by_status.get(sid)
                  if not prev:
                    media_hint_by_status[sid] = {
                      "has_video": bool(row.get("has_video", False)),
                      "has_image": bool(row.get("has_image", False)),
                      "has_media": bool(row.get("has_media", False)),
                      "author_handle": str(row.get("author_handle") or "").strip(),
                    }
                  else:
                    prev["has_video"] = bool(prev.get("has_video", False) or row.get("has_video", False))
                    prev["has_image"] = bool(prev.get("has_image", False) or row.get("has_image", False))
                    prev["has_media"] = bool(prev.get("has_media", False) or row.get("has_media", False))
                    if not prev.get("author_handle") and row.get("author_handle"):
                      prev["author_handle"] = str(row.get("author_handle") or "").strip()

                html_urls = self._fetch_retweet_urls_from_html(monitored_user, limit=90) if monitored_user else []
                for html_url in html_urls:
                  canonical = self._canonical_twitter_status_url(html_url)
                  if not canonical or canonical in seen_candidate_urls:
                    continue
                  status_id = self._status_id_from_url(canonical)
                  if not isinstance(status_id, int):
                    continue
                  if status_id in seen_candidate_status_ids:
                    continue
                  media_hint = media_hint_by_status.get(status_id) or {}
                  has_video = bool(media_hint.get("has_video", False))
                  has_image = bool(media_hint.get("has_image", False))
                  has_media = bool(media_hint.get("has_media", False)) or has_video or has_image
                  if not has_media:
                    has_media = True
                  author_hint = str(media_hint.get("author_handle") or "").strip() or self._twitter_creator_from_url(canonical)

                  seen_candidate_status_ids.add(status_id)
                  seen_candidate_urls.add(canonical)
                  candidate_rows_unique.append(
                    {
                      "url": canonical,
                      "status_id": status_id,
                      "has_media": has_media,
                      "has_image": has_image,
                      "has_video": has_video,
                      "author_handle": author_hint,
                      "actor_handle": monitored_user,
                      "is_retweet_candidate": True,
                      "from_html_retweet": True,
                    }
                  )
                self.log(f"X monitor: retweets HTML detecto {len(candidate_rows_unique)} URL(s).")
              else:
                for row in found_rows:
                  item_url = str(row.get("url") or "").strip()
                  if not item_url:
                    continue
                  if not bool(row.get("has_media", False)):
                    continue
                  if item_url in seen_candidate_urls:
                    continue
                  seen_candidate_urls.add(item_url)
                  candidate_rows_unique.append(row)

              candidate_urls = [str(r.get("url") or "").strip() for r in candidate_rows_unique]
              candidate_media_count = sum(1 for r in candidate_rows_unique if bool(r.get("has_media", False)))

              if label_key == "retweets":
                self.log(
                  f"X monitor: retweets candidatos tras filtro={len(candidate_urls)}, "
                  f"con_media={candidate_media_count}, fuente={source_url}"
                )

              seen_for_label = self.x_actions_seen_urls_by_label.setdefault(label_key, set())
              seen_status_ids_for_label = self.x_actions_seen_status_ids_by_label.setdefault(label_key, set())

              if not seen_for_label:
                seen_for_label.update(candidate_urls)
                self.x_actions_seen_urls.update(candidate_urls)
                if label_key == "retweets":
                  baseline_ids = {
                    sid for sid in (self._status_id_from_url(u) for u in candidate_urls)
                    if isinstance(sid, int)
                  }
                  seen_status_ids_for_label.update(baseline_ids)
                if label_key == "retweets":
                  self.log(
                    f"X monitor: baseline por URL cargado para {label} "
                    f"({len(candidate_urls)} URL(s), media={candidate_media_count})."
                  )
                else:
                  self.log(f"X monitor: baseline por URL cargado para {label} ({len(candidate_urls)} URL(s) con media).")
                continue

              new_items = [u for u in candidate_urls if u not in seen_for_label]
              if label_key == "retweets":
                row_by_url = {
                  str(r.get("url") or "").strip(): r
                  for r in candidate_rows_unique
                }
                new_by_status: list[str] = []
                for item_url in new_items:
                  row = row_by_url.get(item_url) or {}
                  status_id = row.get("status_id")
                  if not isinstance(status_id, int):
                    status_id = self._status_id_from_url(item_url)
                  if isinstance(status_id, int) and status_id in seen_status_ids_for_label:
                    continue
                  new_by_status.append(item_url)
                new_items = new_by_status

                filtered_items: list[str] = []
                skipped_old = 0
                for item_url in new_items:
                  if self._is_status_already_downloaded(item_url):
                    skipped_old += 1
                    continue
                  filtered_items.append(item_url)
                if skipped_old:
                  self.log(f"X monitor: retweets omitio {skipped_old} URL(s) ya descargadas en disco.")
                new_items = filtered_items
              if new_items:
                if label_key == "retweets":
                  self.log(f"X monitor: detectadas {len(new_items)} URL(s) nuevas en {label}.")
                else:
                  self.log(f"X monitor: detectadas {len(new_items)} URL(s) nuevas con media en {label}.")
                row_by_url = {
                  str(r.get("url") or "").strip(): r
                  for r in candidate_rows_unique
                }
                for item_url in reversed(new_items):
                  if item_url in self.x_actions_seen_urls and label_key != "retweets":
                    continue
                  self.x_actions_seen_urls.add(item_url)
                  row = row_by_url.get(item_url) or {}
                  has_video = bool(row.get("has_video", False))
                  has_image = bool(row.get("has_image", False))
                  has_media = bool(row.get("has_media", False))
                  creator_hint = str(row.get("author_handle") or "").strip() or None
                  if label_key == "retweets" and not has_media:
                    has_media = True
                  prefer_image_output = has_image
                  self._queue_feed_download(
                    item_url,
                    prefer_image_output=prefer_image_output,
                    creator_hint=creator_hint,
                  )
              else:
                if label_key == "retweets":
                  self.log(f"X monitor: sin URLs nuevas en {label}.")
                else:
                  self.log(f"X monitor: sin URLs nuevas con media en {label}.")

              seen_for_label.update(candidate_urls)
              if label_key == "retweets":
                latest_ids = {
                  sid for sid in (self._status_id_from_url(u) for u in candidate_urls)
                  if isinstance(sid, int)
                }
                seen_status_ids_for_label.update(latest_ids)
              continue

            if not self.x_actions_bootstrapped:
              urls = [str(row.get("url") or "").strip() for row in found_rows]
              self.x_actions_seen_urls.update([u for u in urls if u])
              self.log(f"X monitor: baseline cargado para {label} ({len(found_rows)} items).")
              continue

            urls = [str(row.get("url") or "").strip() for row in found_rows]
            new_items = [u for u in urls if u and u not in self.x_actions_seen_urls]
            if new_items:
              self.log(f"X monitor: detectados {len(new_items)} nuevos en {label}.")
            for item in reversed(new_items):
              self.x_actions_seen_urls.add(item)
              self.download_from_feed(item)

          self.x_actions_bootstrapped = True

      try:
        poll_seconds = max(10.0, float((self.x_actions_poll_seconds_var.get() or "45").strip()))
      except Exception:
        poll_seconds = 45.0

      self.x_actions_stop_event.wait(poll_seconds)

    self.log("X monitor invisible: detenido")
    self.x_actions_running = False

  def start_x_actions_monitor(self) -> None:
    if self.x_actions_running:
      self.log("X monitor: ya esta en ejecucion")
      return

    sources = self._x_actions_sources()
    if not sources:
      messagebox.showerror(APP_TITLE, "Activa al menos un checkbox de acciones X.")
      return

    self.x_actions_seen_urls.clear()
    self.x_actions_seen_urls_by_label.clear()
    self.x_actions_seen_status_ids_by_label.clear()
    self.x_actions_reference_ids.clear()
    self.twitter_creator_cache.clear()
    self.downloaded_status_ids = self._scan_downloaded_status_ids()
    self.log(f"X monitor: indice local de status descargados={len(self.downloaded_status_ids)}")
    self.x_actions_bootstrapped = False
    self.x_actions_stop_event.clear()
    self.x_actions_running = True
    self.x_actions_thread = threading.Thread(target=self._run_x_actions_monitor, daemon=True)
    self.x_actions_thread.start()

    if not self.feed_worker_running:
      self.feed_worker_running = True
      threading.Thread(target=self._feed_download_worker, daemon=True).start()

  def stop_x_actions_monitor(self) -> None:
    if not self.x_actions_running:
      return
    self.x_actions_stop_event.set()
    if self.x_actions_thread and self.x_actions_thread.is_alive():
      self.x_actions_thread.join(timeout=3)
    self.x_actions_thread = None

  def start_feed(self, platform: str) -> None:
    if FeedScraper is None:
        messagebox.showerror(APP_TITLE, "Falta el archivo 'feed_scraper.py'.")
        return

    try:
      cfg = self._feed_runtime_config()
    except Exception as exc:
      messagebox.showerror(APP_TITLE, f"Configuracion de feed invalida: {exc}")
      return

    clean_platform = (platform or "").strip().lower()
    if clean_platform == "twitter":
      primary = next((m for m in self.monitors if bool(m.get("primary", False))), None)
      default_monitor_id = int((primary or self.monitors[0]).get("id", 1))
      self._start_twitter_feed_instance(default_monitor_id)
      return

    self.is_scraping = True
    self.scraper = FeedScraper(
      self.download_from_feed,
      poll_seconds=cfg["scroll_pause"],
      scroll_px=cfg["scroll_px"],
      cookies_file=(self.cookies_file_var.get() or "").strip(),
      image_dwell_seconds=cfg["image_seconds"],
      scroll_pause_seconds=cfg["scroll_pause"],
      wait_video_end=self.feed_wait_video_end_var.get(),
      max_video_wait_seconds=cfg["max_video_wait"],
      only_visible=True,
      start_maximized=True,
      tiktok_likes_only=self.feed_tiktok_likes_only_var.get(),
    )
    self.scraper.set_log_callback(self.log)
    self.scraper.start(platform)
    self._update_scraping_state()
    self.log(f"Scraper iniciado para {platform}")

  def stop_feed(self) -> None:
    stopped_any = False

    if self.scraper:
      self.scraper.stop()
      self.scraper = None
      stopped_any = True

    with self.twitter_instances_lock:
      instance_ids = list(self.twitter_instances.keys())
    for instance_id in instance_ids:
      self._stop_instance(instance_id)
      stopped_any = True

    self._update_scraping_state()
    if stopped_any:
      self.feed_urls_queued.clear()
      while not self.feed_download_queue.empty():
        try:
          self.feed_download_queue.get_nowait()
          self.feed_download_queue.task_done()
        except Exception:
          break
      self.log("Scraper detenido")
    else:
      messagebox.showinfo(APP_TITLE, "No hay scraper en ejecución")

    if not self.x_actions_running:
      self.feed_urls_queued.clear()

  def _build_ui(self) -> None:
    shell = ttk.Frame(self.root)
    shell.pack(fill="both", expand=True)

    canvas = tk.Canvas(shell, highlightthickness=0, bg="#f3f6fb")
    v_scroll = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
    h_scroll = ttk.Scrollbar(shell, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

    h_scroll.pack(side="bottom", fill="x")
    v_scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    main = ttk.Frame(canvas, padding=18)
    main_window = canvas.create_window((0, 0), window=main, anchor="nw")

    def _on_main_configure(_event=None) -> None:
      canvas.configure(scrollregion=canvas.bbox("all"))
      self._schedule_window_geometry_save()

    def _on_canvas_configure(event) -> None:
      target_width = max(360, event.width, main.winfo_reqwidth())
      canvas.itemconfigure(main_window, width=target_width)

    def _on_mousewheel(event) -> None:
      if getattr(event, "num", None) == 4:
        delta = 120
      elif getattr(event, "num", None) == 5:
        delta = -120
      else:
        delta = int(getattr(event, "delta", 0) or 0)
      if delta:
        shift_pressed = bool(int(getattr(event, "state", 0) or 0) & 0x0001)
        if shift_pressed:
          canvas.xview_scroll(int(-1 * (delta / 120)), "units")
        else:
          canvas.yview_scroll(int(-1 * (delta / 120)), "units")

    main.bind("<Configure>", _on_main_configure)
    self.root.bind("<Configure>", _on_main_configure, add="+")
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
    ttk.Button(
      lang_row,
      text=self._tr("restart_app", "Reiniciar app"),
      command=self.restart_application,
      style="Danger.TButton",
    ).pack(side="right")

    hero = ttk.Frame(main)
    hero.pack(fill="x", pady=(0, 10))
    ttk.Label(hero, text="Downloader Control Center", style="HeroTitle.TLabel").pack(anchor="w")
    ttk.Label(
      hero,
      text="Gestiona descargas, automatizacion de feeds y monitoreo X desde una sola interfaz.",
      style="HeroSub.TLabel",
    ).pack(anchor="w", pady=(2, 0))

    notebook = ttk.Notebook(main, style="App.TNotebook")
    notebook.pack(fill="both", expand=True)

    tab_downloads = ttk.Frame(notebook, padding=8)
    tab_automation = ttk.Frame(notebook, padding=8)
    tab_activity = ttk.Frame(notebook, padding=8)
    notebook.add(tab_downloads, text="Descargas")
    notebook.add(tab_automation, text="Automatizacion")
    notebook.add(tab_activity, text="Actividad")

    top = ttk.LabelFrame(tab_downloads, text=self._tr("source", "Fuente"), style="Card.TLabelframe", padding=10)
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

    ttk.Checkbutton(
      top,
      text="Guardar predeterminados automaticamente",
      variable=self.auto_save_defaults_var,
      command=self._on_auto_save_defaults_toggle,
    ).grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 8))
    ttk.Checkbutton(
      top,
      text=self._tr("remember_window_position", "Recordar ubicacion de la ventana"),
      variable=self.remember_window_position_var,
      command=self._schedule_window_geometry_save,
    ).grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))

    top.columnconfigure(1, weight=1)

    social = ttk.LabelFrame(tab_downloads, text=self._tr("social", "Redes Sociales"), style="Card.TLabelframe", padding=10)
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

    social.columnconfigure(1, weight=1)

    cfg = ttk.LabelFrame(tab_downloads, text=self._tr("options", "Opciones"), style="Card.TLabelframe", padding=10)
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

    ttk.Label(cfg, textvariable=self.duration_var).grid(row=2, column=6, sticky="w", padx=8, pady=6)

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
    ttk.Label(cfg, text="Carpeta cookies:").grid(row=7, column=0, sticky="w", padx=8, pady=6)
    ttk.Entry(cfg, textvariable=self.cookies_folder_var, width=42).grid(row=7, column=1, columnspan=3, sticky="ew", padx=8, pady=6)
    ttk.Button(cfg, text="Elegir carpeta", command=self.pick_cookies_folder).grid(row=7, column=4, padx=8, pady=6)

    actions = ttk.LabelFrame(tab_downloads, text=self._tr("downloads", "Descargas"), style="Card.TLabelframe", padding=10)
    actions.pack(fill="x", pady=(0, 10))

    ttk.Button(actions, text=self._tr("best", "BEST (audio+video)"), command=self.download_best, style="Accent.TButton").pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text="Audio", command=self.download_audio_only, style="Subtle.TButton").pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text=self._tr("video_only", "Solo video"), command=self.download_video_only, style="Subtle.TButton").pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text="Limitar tamano", command=self.download_limited_size, style="Subtle.TButton").pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text="Recortar segmento", command=self.download_trimmed, style="Subtle.TButton").pack(
      side="left", padx=8, pady=10
    )
    ttk.Button(actions, text=self._tr("restart_app", "Reiniciar app"), command=self.restart_application, style="Danger.TButton").pack(
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

    feed = ttk.LabelFrame(tab_automation, text=self._tr("feed", "Feed Automático"), style="Card.TLabelframe", padding=10)
    feed.pack(fill="x", pady=(0, 10))

    for col in range(6):
      feed.columnconfigure(col, weight=1)

    ttk.Label(feed, text="Imagen (s):").grid(row=0, column=0, sticky="w", padx=8, pady=6)
    ttk.Entry(feed, textvariable=self.feed_image_seconds_var, width=8).grid(row=0, column=1, sticky="w", padx=4, pady=6)

    ttk.Label(feed, text="Pausa scroll (s):").grid(row=0, column=2, sticky="w", padx=8, pady=6)
    ttk.Entry(feed, textvariable=self.feed_scroll_pause_var, width=8).grid(row=0, column=3, sticky="w", padx=4, pady=6)

    ttk.Label(feed, text="Pixeles scroll:").grid(row=0, column=4, sticky="w", padx=8, pady=6)
    ttk.Entry(feed, textvariable=self.feed_scroll_px_var, width=8).grid(row=0, column=5, sticky="w", padx=4, pady=6)

    ttk.Checkbutton(
      feed,
      text="Esperar video hasta final",
      variable=self.feed_wait_video_end_var,
    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=6)

    ttk.Label(feed, text="Max video (s):").grid(row=1, column=2, sticky="e", padx=8, pady=6)
    ttk.Entry(feed, textvariable=self.feed_max_video_wait_var, width=8).grid(row=1, column=3, sticky="w", padx=4, pady=6)

    ttk.Checkbutton(
      feed,
      text="TikTok: solo videos con like",
      variable=self.feed_tiktok_likes_only_var,
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=6)

    ttk.Checkbutton(
      feed,
      text="Twitter: guardar por creador",
      variable=self.feed_twitter_creator_folders_var,
    ).grid(row=2, column=2, columnspan=2, sticky="w", padx=8, pady=6)

    ttk.Label(feed, text="Usuario X (likes/retweets/perfil):").grid(row=3, column=0, sticky="w", padx=8, pady=6)
    ttk.Entry(feed, textvariable=self.x_actions_user_var, width=22).grid(row=3, column=1, sticky="ew", padx=4, pady=6)
    ttk.Label(feed, text="Chequeo (s):").grid(row=3, column=2, sticky="e", padx=8, pady=6)
    ttk.Entry(feed, textvariable=self.x_actions_poll_seconds_var, width=8).grid(row=3, column=3, sticky="w", padx=4, pady=6)

    ttk.Checkbutton(feed, text="X Guardados", variable=self.x_actions_bookmarks_var).grid(row=4, column=0, sticky="w", padx=8, pady=6)
    ttk.Checkbutton(feed, text="X Likes", variable=self.x_actions_likes_var).grid(row=4, column=1, sticky="w", padx=8, pady=6)
    ttk.Checkbutton(feed, text="X Retweets", variable=self.x_actions_retweets_var).grid(row=4, column=2, sticky="w", padx=8, pady=6)
    ttk.Checkbutton(feed, text="X Perfil", variable=self.x_actions_profile_var).grid(row=4, column=3, sticky="w", padx=8, pady=6)

    ttk.Label(feed, text="Monitor X: invisible, sin abrir navegador.").grid(row=5, column=0, columnspan=4, sticky="w", padx=8, pady=6)

    ttk.Label(feed, text="Ruta descarga: usa campo 'Salida' arriba").grid(row=6, column=0, columnspan=6, sticky="w", padx=8, pady=6)

    ttk.Button(feed, text="Iniciar Feed IG", command=lambda: self.start_feed("instagram"), style="Accent.TButton").grid(
      row=7, column=0, padx=8, pady=8, sticky="ew"
    )
    ttk.Button(feed, text="Iniciar Feed TikTok", command=lambda: self.start_feed("tiktok"), style="Accent.TButton").grid(
      row=7, column=1, padx=8, pady=8, sticky="ew"
    )
    ttk.Button(feed, text="Iniciar Feed Twitter/X", command=lambda: self.start_feed("twitter"), style="Accent.TButton").grid(
      row=7, column=2, padx=8, pady=8, sticky="ew"
    )
    ttk.Button(feed, text=self._tr("feed_yt", "Iniciar Feed YouTube Shorts"), command=lambda: self.start_feed("youtube"), style="Accent.TButton").grid(
      row=7, column=3, padx=8, pady=8, sticky="ew"
    )
    ttk.Button(feed, text=self._tr("stop_feed", "STOP Feed"), command=self.stop_feed, style="Danger.TButton").grid(
      row=7, column=4, padx=8, pady=8, sticky="ew"
    )
    ttk.Button(feed, text=self._tr("live_log", "Ver log en vivo"), command=self.open_live_log_window, style="Subtle.TButton").grid(
      row=7, column=5, padx=8, pady=8, sticky="ew"
    )

    ttk.Button(feed, text="Iniciar Monitor X", command=self.start_x_actions_monitor, style="Accent.TButton").grid(
      row=8, column=0, columnspan=2, padx=8, pady=8, sticky="ew"
    )
    ttk.Button(feed, text="Detener Monitor X", command=self.stop_x_actions_monitor, style="Danger.TButton").grid(
      row=8, column=2, columnspan=2, padx=8, pady=8, sticky="ew"
    )
    ttk.Button(
      feed,
      text=self._tr("restart_app", "Reiniciar app"),
      command=self.restart_application,
      style="Danger.TButton",
    ).grid(row=8, column=4, columnspan=2, padx=8, pady=8, sticky="ew")

    self.x_instances_panel = ttk.LabelFrame(tab_automation, text="Instancias Feed X", style="Card.TLabelframe", padding=10)
    self.x_instances_panel.pack(fill="x", pady=(0, 10))

    canvas_shell = ttk.Frame(self.x_instances_panel)
    canvas_shell.pack(fill="both", expand=True)

    self.x_instances_canvas = tk.Canvas(canvas_shell, highlightthickness=0, bg="#f3f6fb", height=240)
    self.x_instances_h_scroll = ttk.Scrollbar(self.x_instances_panel, orient="horizontal", command=self.x_instances_canvas.xview)
    self.x_instances_v_scroll = ttk.Scrollbar(canvas_shell, orient="vertical", command=self.x_instances_canvas.yview)
    self.x_instances_canvas.configure(xscrollcommand=self.x_instances_h_scroll.set, yscrollcommand=self.x_instances_v_scroll.set)

    self.x_instances_canvas.pack(side="left", fill="both", expand=True)
    self.x_instances_v_scroll.pack(side="right", fill="y")
    self.x_instances_h_scroll.pack(fill="x", pady=(6, 0))

    self.x_instances_table = ttk.Frame(self.x_instances_canvas)
    self.x_instances_canvas_window = self.x_instances_canvas.create_window((0, 0), window=self.x_instances_table, anchor="nw")

    def _on_x_instances_table_configure(_event=None) -> None:
      self._sync_x_instances_canvas_region()

    def _on_x_instances_canvas_configure(_event=None) -> None:
      self._sync_x_instances_canvas_region()

    self.x_instances_table.bind("<Configure>", _on_x_instances_table_configure)
    self.x_instances_canvas.bind("<Configure>", _on_x_instances_canvas_configure)

    def _on_x_instances_shift_wheel(event) -> str:
      try:
        delta = event.delta
      except Exception:
        delta = 0
      if delta:
        self.x_instances_canvas.xview_scroll(int(-1 * (delta / 120)), "units")
      return "break"

    def _on_x_instances_wheel(event) -> str:
      try:
        delta = event.delta
      except Exception:
        delta = 0
      if delta:
        self.x_instances_canvas.yview_scroll(int(-1 * (delta / 120)), "units")
      return "break"

    self.x_instances_canvas.bind("<MouseWheel>", _on_x_instances_wheel)
    self.x_instances_canvas.bind("<Shift-MouseWheel>", _on_x_instances_shift_wheel)
    self.x_instances_signature = None

    log_frame = ttk.LabelFrame(tab_activity, text=self._tr("log", "Log"), style="Card.TLabelframe", padding=10)
    log_frame.pack(fill="both", expand=True)
    self.log_frame_widget = log_frame

    self.log_widget = tk.Text(
      log_frame,
      height=20,
      wrap="word",
      bg="#0b1220",
      fg="#dbe7ff",
      insertbackground="#dbe7ff",
      relief="flat",
      padx=12,
      pady=10,
    )
    self.log_widget.pack(side="left", fill="both", expand=True)

    scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_widget.yview)
    scrollbar.pack(side="right", fill="y")
    self.log_widget.configure(yscrollcommand=scrollbar.set)

    self._refresh_x_instances_ui()

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

  def open_live_log_window(self) -> None:
    if self.live_log_window and self.live_log_window.winfo_exists():
      self.live_log_window.lift()
      self.live_log_window.focus_force()
      return

    win = tk.Toplevel(self.root)
    win.title("Log en vivo - Feed/Descargas")
    win.geometry("980x520")
    win.minsize(760, 320)

    frame = ttk.Frame(win, padding=8)
    frame.pack(fill="both", expand=True)

    text = tk.Text(frame, wrap="word", state="normal")
    text.pack(side="left", fill="both", expand=True)
    scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    scroll.pack(side="right", fill="y")
    text.configure(yscrollcommand=scroll.set)

    if self.log_history:
      text.insert("end", "\n".join(self.log_history) + "\n")
      text.see("end")

    self.live_log_window = win
    self.live_log_widget = text

    def on_close() -> None:
      self.live_log_window = None
      self.live_log_widget = None
      win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)

  def _drain_log_queue(self) -> None:
    while not self.log_queue.empty():
      message = self.log_queue.get_nowait()
      self.log_widget.insert("end", message + "\n")
      self.log_widget.see("end")
      if self.live_log_widget and self.live_log_window and self.live_log_window.winfo_exists():
        self.live_log_widget.insert("end", message + "\n")
        self.live_log_widget.see("end")
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

    if "pbs.twimg.com/media/" in clean or "twimg.com/media/" in clean:
      return True
    if "/photo/" in path:
      return True

    return False

  def _build_image_output_dir(self, url: str | None = None, creator_hint: str | None = None) -> str:
    out_dir = (self.image_output_dir_var.get() or "").strip()
    if not out_dir:
      out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "images")
    out_dir = self._apply_creator_subfolder(out_dir, url, creator_hint=creator_hint)
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

      request = Request(
        url,
        headers={
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
          "Referer": "https://x.com/",
        },
      )
      with urlopen(request, timeout=30) as resp:
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "image/" not in content_type:
          return None
        data = resp.read()
      with open(target, "wb") as f:
        f.write(data)
      return target
    except Exception:
      return None

  def _download_twitter_media_urls(self, media_urls: list[str], out_dir: str) -> list[str]:
    saved: list[str] = []
    for media_url in media_urls:
      clean = (media_url or "").strip()
      if not clean:
        continue
      if not self._looks_like_image_url(clean):
        continue
      saved_path = self._download_direct_image(clean, out_dir)
      if saved_path:
        saved.append(saved_path)
    if saved:
      self.log(f"Imagenes descargadas directo: {len(saved)} archivo(s)")
    return saved

  def _download_image_url_internal(self, url: str, source_label: str) -> None:
    def task() -> None:
      self._download_image_url_blocking(url, source_label)

    self._run_background(task, f"DESCARGA IMAGEN ({source_label})")

  def _download_image_url_blocking(self, url: str, source_label: str) -> None:
    creator_hint = self._resolve_twitter_creator_for_status_url(url) or self._twitter_creator_from_url(url)
    out_dir = self._build_image_output_dir(url, creator_hint=creator_hint)
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

  def _build_output_template(self, url: str | None = None, creator_hint: str | None = None) -> str:
    out_dir = self.output_dir_var.get().strip() or os.path.join(os.path.dirname(os.path.dirname(__file__)), "videos")
    out_dir = self._apply_creator_subfolder(out_dir, url, creator_hint=creator_hint)
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, DEFAULT_OUTPUT_TEMPLATE)

  def _twitter_creator_from_url(self, url: str) -> str | None:
    match = re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/?#]+)/status/\d+", (url or ""), flags=re.IGNORECASE)
    if not match:
      return None
    creator = (match.group(1) or "").strip()
    if not creator:
      return None
    creator = re.sub(r'[<>:"/\\|?*]+', "_", creator)
    return creator or None

  def _apply_creator_subfolder(self, base_dir: str, url: str | None, creator_hint: str | None = None) -> str:
    out_dir = (base_dir or "").strip()
    if not out_dir:
      return out_dir
    if not self.feed_twitter_creator_folders_var.get():
      return out_dir
    if not self._is_twitter_url(url or "") and not (creator_hint or "").strip():
      return out_dir

    creator = (creator_hint or "").strip().lstrip("@")
    if creator:
      creator = re.sub(r'[<>:"/\\|?*]+', "_", creator)
      return os.path.join(out_dir, creator)

    creator = self._resolve_twitter_creator_for_status_url(url or "") or self._twitter_creator_from_url(url or "")
    if not creator:
      return out_dir
    return os.path.join(out_dir, creator)

  def _feed_output_dir_for_url(self, url: str, creator_hint: str | None = None) -> str:
    out_dir = self.output_dir_var.get().strip() or os.path.join(os.path.dirname(os.path.dirname(__file__)), "videos")
    out_dir = self._apply_creator_subfolder(out_dir, url, creator_hint=creator_hint)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

  def pick_output_folder(self) -> None:
    selected = filedialog.askdirectory(initialdir=self.output_dir_var.get())
    if selected:
      self.output_dir_var.set(selected)

  def pick_cookies_file(self) -> None:
    initial_dir = (self.cookies_folder_var.get() or "").strip() or self._cookie_pool_default_dir()

    selected = filedialog.askopenfilename(
      title="Selecciona archivo cookies.txt",
      initialdir=initial_dir,
      filetypes=[("Cookies", "*.txt *.json"), ("Todos", "*.*")],
    )
    if selected:
      self.cookies_file_var.set(selected)
      self.cookies_folder_var.set(os.path.dirname(selected))

  def _auto_load_default_cookies_file(self) -> None:
    if not (self.cookies_folder_var.get() or "").strip():
      self.cookies_folder_var.set(self._cookie_pool_default_dir())
    candidates = self._existing_cookie_files()
    if not candidates:
      return

    current_cookie = (self.cookies_file_var.get() or "").strip()
    if not current_cookie:
      self.cookies_file_var.set(candidates[0])
      self.log(f"cookies detectadas automaticamente: principal={candidates[0]}")
      if len(candidates) > 1:
        self.log(f"cookies alterna detectada: {candidates[1]}")
    elif not os.path.isfile(current_cookie):
      self.cookies_file_var.set(candidates[0])
      self.log(f"cookies guardadas no encontradas. Usando principal detectada: {candidates[0]}")

    if not self._selected_global_cookie():
      self._set_global_cookie_choice(self.cookies_file_var.get() or candidates[0])

  def _existing_cookie_files(self) -> list[str]:
    base_dir = os.path.dirname(os.path.dirname(__file__))
    manual = (self.cookies_file_var.get() or "").strip()
    folder = (self.cookies_folder_var.get() or "").strip()

    dirs_to_scan = [
      folder,
      os.path.join(base_dir, "downloader", "cookies", "twitter"),
      os.path.join(base_dir, "downloader", "cookies"),
      os.path.join(base_dir, "cookies"),
    ]

    out: list[str] = []
    seen: set[str] = set()

    def add_file(path: str):
      clean = (path or "").strip()
      if clean and os.path.isfile(clean):
        key = os.path.normcase(os.path.abspath(clean))
        if key not in seen:
          seen.add(key)
          out.append(clean)

    if manual:
      add_file(manual)

    for d in dirs_to_scan:
      clean_dir = (d or "").strip()
      if not clean_dir or not os.path.isdir(clean_dir):
        continue
      try:
        for f in sorted(os.listdir(clean_dir), key=lambda item: item.lower()):
          lower = f.lower()
          if lower.endswith(".txt") and "cookie" in lower:
            add_file(os.path.join(clean_dir, f))
      except Exception:
        pass

    if out:
      final_list = list(out)

      last_success = getattr(self, "last_success_cookie_path", None)
      if last_success and last_success in final_list:
        final_list.remove(last_success)
        final_list.insert(0, last_success)

      return final_list

    return []

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

      if cookies_file and os.path.isfile(cookies_file) and cookies_file.lower().endswith(".txt"):
        if is_social:
          self.log(f"Usando cookies para red social: {cookies_file}")
        args += ["--cookies", cookies_file]
      elif cookies_file and os.path.isfile(cookies_file):
        self.log(f"Aviso: formato de cookies no compatible para yt-dlp ({cookies_file}). Usa .txt Netscape.")
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
          creationflags=CREATE_NO_WINDOW
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
          creationflags=CREATE_NO_WINDOW
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
              creationflags=CREATE_NO_WINDOW
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
        creationflags=CREATE_NO_WINDOW
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

    os.makedirs(self.feed_metadata_dir, exist_ok=True)
    moved = 0
    for source in json_paths:
      try:
        target = os.path.join(self.feed_metadata_dir, os.path.basename(source))
        if os.path.abspath(source) == os.path.abspath(target):
          continue
        if os.path.exists(target):
          base, ext = os.path.splitext(os.path.basename(source))
          suffix = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
          target = os.path.join(self.feed_metadata_dir, f"{base}_{suffix}{ext}")
        shutil.move(source, target)
        moved += 1
      except Exception as exc:
        self.log(f"Aviso: no se pudo mover metadata sidecar '{source}': {exc}")

    if moved:
      self.log(f"Metadata sidecar movida a: {self.feed_metadata_dir} ({moved} archivo(s))")

  def _gallery_dl_common_args(self, cookie_file_override: str | None = None, log_usage: bool = True) -> list[str]:
    args: list[str] = []
    cookies_file = (cookie_file_override or "").strip()
    if not cookies_file:
      cookies_file = (self.cookies_file_var.get() or "").strip()
    if not cookies_file:
      existing = self._existing_cookie_files()
      if existing:
        cookies_file = existing[0]

    if cookies_file and os.path.isfile(cookies_file) and cookies_file.lower().endswith(".txt"):
      args += ["--cookies", cookies_file]
      if log_usage:
        self.log(f"gallery-dl usando cookies: {cookies_file}")
    elif cookies_file and os.path.isfile(cookies_file):
      self.log(f"Aviso: gallery-dl requiere cookies .txt Netscape. Ignorando: {cookies_file}")
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
        self.last_success_cookie_path = cookie_file if cookie_file else None
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
        self.last_success_cookie_path = cookie_file if cookie_file else None
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
          self.last_success_cookie_path = cookie_file if cookie_file else None
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
      specific_args += [
        "--recode-video",
        "mp4",
        "--postprocessor-args",
        post_args,
      ]

      specific_args += self._subtitle_download_args()
      self.log(f"Modo BEST {source_label.upper()}: no aplica limite de tamano ni recorte")
      self.log("Postprocesado BEST: recodificacion optimizada para compatibilidad y ratio estable.")
      if self.include_subtitles_var.get():
        if self.embed_subtitles_var.get():
          self.log("Subtitulos: activados y embebidos en MP4 (se pueden activar/desactivar en reproductor).")
        else:
          self.log("Subtitulos: activados como archivo separado (.srt/.vtt).")
      allow_social_fallback = self._is_social_media_url(url)
      try:
        self._run_yt_dlp_download(url, specific_args, allow_social_fallback=allow_social_fallback)
      except Exception as exc:
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
        creationflags=CREATE_NO_WINDOW
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
          self.last_success_cookie_path = cookie_file if cookie_file else None
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
          f"ffmpeg:-c:v h264_nvenc -cq 22 -preset p5 -vf {vf_expr} "
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
        "bestaudio/best",
        "-x",
        "--audio-format",
        "mp3",
        "--postprocessor-args",
        "ffmpeg:-b:a 320k",
        "-o",
        out,
      ]
      self.log(f"Descargando AUDIO (MP3 320kbps)...")
      self._run_yt_dlp_download(url, specific_args, allow_social_fallback=self._is_social_media_url(url))

    self._run_background(task, "DESCARGA AUDIO")

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
        f"ffmpeg:-c:v libx264 -preset veryfast -threads 0 -b:v {video_kbps}k "
        f"-maxrate {video_kbps}k -bufsize {video_kbps * 2}k "
        f"-c:a aac -b:a {audio_kbps}k -movflags +faststart"
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
      ffmpeg_args = (
        f"ffmpeg:-c:v libx264 -preset veryfast -threads 0 -b:v 1500k -maxrate 1500k -bufsize 3000k "
        f"-c:a aac -b:a 128k -movflags +faststart"
      )
      compression_args = self._postprocessor_args_for_url(url, ffmpeg_args) or ffmpeg_args
      specific_args = [
        "-f",
        self._language_format_selector(),
        "--download-sections",
        section,
        "--merge-output-format",
        "mp4",
        "--recode-video",
        "mp4",
        "--postprocessor-args",
        compression_args,
        "-o",
        out,
      ]
      specific_args += self._subtitle_download_args()
      self.log("Modo RECORTE: descarga solo el segmento indicado")
      if self.include_subtitles_var.get():
        if self.embed_subtitles_var.get():
          self.log("Subtitulos: activados y embebidos en MP4 (se pueden activar/desactivar en reproductor).")
        else:
          self.log("Subtitulos: activados como archivo separado (.srt/.vtt).")
      self._run_yt_dlp_download(url, specific_args)

    self._run_background(task, "RECORTE DE SEGMENTO")


def main() -> None:
  import argparse
  parser = argparse.ArgumentParser()
  parser.add_argument('--filetype', type=str, default='mp4', nargs='?', help='Tipo de archivo a descargar (mp4, mp3, webm, link, etc)')
  args, unknown = parser.parse_known_args()

  root = tk.Tk()
  app = DownloaderApp(root)
  if hasattr(app, 'set_filetype'):
    app.set_filetype(args.filetype)
  elif hasattr(app, 'filetype'):
    app.filetype = args.filetype
  root.mainloop()


if __name__ == "__main__":
  main()
