import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import QFont, QGuiApplication, QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from feed_scraper import FeedScraper
except Exception:
    FeedScraper = None

APP_TITLE = "Downloader Control Center"
X_ACTION_RANGE = 12


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")


class CoreBridge:
    """Pure engine for the PyQt UI."""

    def __init__(self):
        self.app = self
        self._shutdown = False
        self._log_lock = threading.Lock()
        self._workers_lock = threading.Lock()
        self._settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloader_settings.json")
        self.log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity.log")
        self.log_history: list[str] = []

        self.available_languages = ["auto"]
        self.available_qualities = ["best"]
        self.available_audio_qualities = ["best audio"]
        self.available_subtitle_languages = ["auto", "all", "es", "en", "es.*", "en.*"]
        self._last_duration_seconds = 0

        self.twitter_instances_lock = threading.Lock()
        self.twitter_instances: dict[int, dict[str, Any]] = {}
        self._next_instance_id = 1
        self._monitor_cookie_overrides: dict[int, str] = {}
        self._global_cookie_choice = ""

        self.scraper = None
        self.is_scraping = False
        self.feed_download_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.feed_urls_queued: set[str] = set()
        self.feed_worker_running = False
        self._last_feed_runtime_signature: tuple | None = None

        self.x_actions_running = False
        self.x_actions_stop_event = threading.Event()
        self.x_actions_thread: threading.Thread | None = None
        self.x_actions_seen_urls: set[str] = set()
        self.x_actions_seen_urls_by_label: dict[str, set[str]] = {}
        self.x_actions_seen_status_ids_by_label: dict[str, set[int]] = {}
        self.downloaded_status_ids: set[int] = set()
        self.x_actions_bootstrapped = False
        self.x_retweet_html_cache: dict[str, tuple[float, list[str]]] = {}

        self._feed_workers: dict[str, dict[str, Any]] = {}
        self._x_actions_worker: dict[str, Any] | None = None

        self._last_clipboard_check = 0.0
        self._clipboard_seen_urls: set[str] = set()
        self._last_monitor_refresh = 0.0

        self._vars: dict[str, Any] = {
            "url_var": "",
            "instagram_url_var": "",
            "twitter_url_var": "",
            "image_url_var": "",
            "output_dir_var": "",
            "image_output_dir_var": "",
            "max_size_var": "50",
            "start_var": "00:00",
            "end_var": "",
            "duration_var": "Duracion: -",
            "selected_language_var": "auto",
            "selected_quality_var": "best",
            "selected_audio_quality_var": "best audio",
            "include_subtitles_var": False,
            "embed_subtitles_var": True,
            "subtitle_lang_var": "auto",
            "compression_var": "sin_compresion",
            "use_cookies_var": False,
            "cookies_browser_var": "chrome",
            "cookies_file_var": "",
            "cookies_folder_var": "",
            "auto_save_defaults_var": False,
            "remember_window_position_var": False,
            "start_with_windows_var": False,
            "ui_language_var": "es",
            "ui_theme_var": "dark",
            "clipboard_monitor_var": False,
            "feed_image_seconds_var": "10",
            "feed_scroll_pause_var": "1.5",
            "feed_scroll_px_var": "900",
            "feed_wait_video_end_var": True,
            "feed_max_video_wait_var": "300",
            "feed_tiktok_likes_only_var": False,
            "feed_twitter_creator_folders_var": True,
            "x_actions_user_var": "",
            "x_actions_poll_seconds_var": "30",
            "x_actions_bookmarks_var": True,
            "x_actions_likes_var": True,
            "x_actions_retweets_var": False,
            "x_actions_profile_var": False,
        }

        self._prepare_log_file()
        self._load_persisted_settings()
        self._load_start_with_windows_state()
        self.monitors = self._detect_monitors()
        self._last_monitors_signature = tuple(self._monitor_identity(item) for item in self.monitors)
        self._ensure_default_paths()
        if not self._global_cookie_choice:
            self._global_cookie_choice = str(self.get_var("cookies_file_var", "") or "").strip()
        self._log("Motor PyQt inicializado")

    def _prepare_log_file(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
            if not os.path.exists(self.log_file_path):
                with open(self.log_file_path, "w", encoding="utf-8", errors="replace") as f:
                    f.write("")
        except Exception:
            pass

    def _log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {str(message)}"
        with self._log_lock:
            self.log_history.append(line)
            if len(self.log_history) > 4000:
                self.log_history = self.log_history[-2000:]
            try:
                with open(self.log_file_path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def _ensure_default_paths(self) -> None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if not str(self.get_var("output_dir_var", "")).strip():
            self.set_var("output_dir_var", os.path.join(base_dir, "videos"))
        if not str(self.get_var("image_output_dir_var", "")).strip():
            self.set_var("image_output_dir_var", os.path.join(base_dir, "images"))
        if not str(self.get_var("cookies_folder_var", "")).strip():
            self.set_var("cookies_folder_var", os.path.join(base_dir, "cookies"))
        os.makedirs(str(self.get_var("output_dir_var", "")).strip(), exist_ok=True)
        os.makedirs(str(self.get_var("image_output_dir_var", "")).strip(), exist_ok=True)

    def _settings_payload(self) -> dict[str, Any]:
        keys = [
            "url_var",
            "instagram_url_var",
            "twitter_url_var",
            "output_dir_var",
            "image_output_dir_var",
            "max_size_var",
            "start_var",
            "end_var",
            "selected_language_var",
            "selected_quality_var",
            "selected_audio_quality_var",
            "include_subtitles_var",
            "embed_subtitles_var",
            "subtitle_lang_var",
            "compression_var",
            "use_cookies_var",
            "cookies_browser_var",
            "cookies_file_var",
            "cookies_folder_var",
            "auto_save_defaults_var",
            "remember_window_position_var",
            "start_with_windows_var",
            "ui_language_var",
            "ui_theme_var",
            "clipboard_monitor_var",
            "feed_image_seconds_var",
            "feed_scroll_pause_var",
            "feed_scroll_px_var",
            "feed_wait_video_end_var",
            "feed_max_video_wait_var",
            "feed_tiktok_likes_only_var",
            "feed_twitter_creator_folders_var",
            "x_actions_user_var",
            "x_actions_poll_seconds_var",
            "x_actions_bookmarks_var",
            "x_actions_likes_var",
            "x_actions_retweets_var",
            "x_actions_profile_var",
        ]
        return {key: self._vars.get(key) for key in keys}

    def _save_persisted_settings(self) -> None:
        try:
            with open(self._settings_path, "w", encoding="utf-8", errors="replace") as f:
                json.dump(self._settings_payload(), f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._log(f"WARN guardando settings: {exc}")

    def _load_persisted_settings(self) -> None:
        if not os.path.isfile(self._settings_path):
            return
        try:
            with open(self._settings_path, "r", encoding="utf-8", errors="replace") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if key in self._vars:
                        self._vars[key] = value
        except Exception as exc:
            self._log(f"WARN cargando settings: {exc}")

    def _startup_vbs_path(self) -> str:
        appdata = os.environ.get("APPDATA", "").strip()
        startup_dir = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        return os.path.join(startup_dir, "downloader_app_autostart.vbs")

    def _set_start_with_windows_enabled(self, enabled: bool) -> None:
        startup_path = self._startup_vbs_path()
        startup_dir = os.path.dirname(startup_path)
        if enabled:
            os.makedirs(startup_dir, exist_ok=True)
            python_exe = sys.executable
            script_path = os.path.abspath(__file__)
            content = (
                'Set shell = CreateObject("WScript.Shell")\n'
                f'shell.Run Chr(34) & "{python_exe}" & Chr(34) & " " & Chr(34) & "{script_path}" & Chr(34), 0, False\n'
            )
            with open(startup_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(content)
            return
        if os.path.isfile(startup_path):
            os.remove(startup_path)

    def _load_start_with_windows_state(self) -> None:
        try:
            self._vars["start_with_windows_var"] = bool(os.path.isfile(self._startup_vbs_path()))
        except Exception:
            self._vars["start_with_windows_var"] = False

    def _detect_monitors(self) -> list[dict[str, Any]]:
        screens = list(QGuiApplication.screens())
        if not screens:
            return [
                {
                    "id": 1,
                    "label": "Monitor 1",
                    "left": 0,
                    "top": 0,
                    "width": 1920,
                    "height": 1080,
                    "primary": True,
                    "device": "default",
                }
            ]

        primary_screen = QGuiApplication.primaryScreen()
        out: list[dict[str, Any]] = []
        for screen in screens:
            geom = screen.geometry()
            out.append(
                {
                    "device": str(screen.name() or ""),
                    "left": int(geom.x()),
                    "top": int(geom.y()),
                    "width": int(geom.width()),
                    "height": int(geom.height()),
                    "primary": bool(screen == primary_screen),
                }
            )

        out.sort(key=lambda item: (not bool(item.get("primary", False)), int(item.get("left", 0)), int(item.get("top", 0))))
        for idx, item in enumerate(out, start=1):
            item["id"] = idx
            item["label"] = (
                f"Monitor {idx}"
                f" ({int(item.get('width', 0))}x{int(item.get('height', 0))}"
                f" @ {int(item.get('left', 0))},{int(item.get('top', 0))})"
            )

        return out

    def _monitor_identity(self, monitor: dict[str, Any]) -> tuple:
        return (
            str(monitor.get("device") or "").strip().lower(),
            int(monitor.get("left", 0)),
            int(monitor.get("top", 0)),
            int(monitor.get("width", 0)),
            int(monitor.get("height", 0)),
        )

    def _find_monitor_by_id(self, monitor_id: int) -> dict[str, Any] | None:
        for monitor in self.monitors:
            if int(monitor.get("id", -1)) == int(monitor_id):
                return monitor
        return None

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
            if old_id in self._monitor_cookie_overrides:
                old_cookie_by_identity[self._monitor_identity(old_monitor)] = (
                    self._monitor_cookie_overrides.get(old_id, "") or ""
                ).strip()

        self.monitors = detected
        remapped_choices: dict[int, str] = {}
        for monitor in self.monitors:
            match = old_cookie_by_identity.get(self._monitor_identity(monitor), "")
            if match or self._monitor_identity(monitor) in old_cookie_by_identity:
                remapped_choices[int(monitor.get("id", 0))] = match
        self._monitor_cookie_overrides = remapped_choices
        self._last_monitors_signature = new_signature

        self._log(f"Monitores actualizados: {len(self.monitors)} detectado(s)")
        return True

    def _looks_like_url(self, text: str) -> bool:
        value = str(text or "").strip().lower()
        return value.startswith("http://") or value.startswith("https://")

    def _yt_dlp_cmd(self) -> list[str]:
        binary = shutil.which("yt-dlp")
        if binary:
            return [binary]
        return [sys.executable, "-m", "yt_dlp"]

    def _cookie_args(self) -> list[str]:
        if not bool(self.get_var("use_cookies_var", False)):
            return []
        cookie_file = str(self.get_var("cookies_file_var", "")).strip()
        if cookie_file and os.path.isfile(cookie_file):
            return ["--cookies", cookie_file]
        browser = str(self.get_var("cookies_browser_var", "chrome")).strip() or "chrome"
        return ["--cookies-from-browser", browser]

    def _subtitle_args(self) -> list[str]:
        args: list[str] = []
        if bool(self.get_var("include_subtitles_var", False)):
            args.extend(["--write-subs", "--write-auto-subs"])
            lang = str(self.get_var("subtitle_lang_var", "auto")).strip() or "auto"
            args.extend(["--sub-langs", lang])
            if bool(self.get_var("embed_subtitles_var", True)):
                args.append("--embed-subs")
        return args

    def _safe_output_dir(self, for_images: bool = False) -> str:
        key = "image_output_dir_var" if for_images else "output_dir_var"
        default_name = "images" if for_images else "videos"
        path = str(self.get_var(key, "")).strip()
        if not path:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), default_name)
            self.set_var(key, path)
        os.makedirs(path, exist_ok=True)
        return path

    def _run_command_async(self, label: str, cmd: list[str], cwd: str | None = None) -> None:
        def worker() -> None:
            try:
                self._log(f"[{label}] Ejecutando comando...")
                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.stdout is not None:
                    for line in proc.stdout:
                        text = str(line).strip()
                        if text:
                            self._log(f"[{label}] {text}")
                code = proc.wait()
                if code == 0:
                    self._log(f"[{label}] Finalizado OK")
                else:
                    self._log(f"[{label}] Finalizado con error (code={code})")
            except Exception as exc:
                self._log(f"[{label}] ERROR: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _format_duration(self, seconds: int) -> str:
        total = max(0, int(seconds or 0))
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        if hh > 0:
            return f"{hh:02}:{mm:02}:{ss:02}"
        return f"{mm:02}:{ss:02}"

    def _extract_json(self, raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else None
        except Exception:
            pass
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                continue
        return None

    def _build_quality_lists(self, info: dict[str, Any]) -> None:
        qualities = {"best"}
        audio_qualities = {"best audio"}
        langs = {"auto"}
        subs = {"auto", "all"}

        for fmt in list(info.get("formats") or []):
            height = fmt.get("height")
            abr = fmt.get("abr")
            language = str(fmt.get("language") or "").strip()
            if isinstance(height, int) and height > 0:
                qualities.add(f"{height}p")
            if isinstance(abr, (int, float)) and abr > 0:
                audio_qualities.add(f"{int(abr)} kbps")
            if language:
                langs.add(language)

        for lang in list((info.get("subtitles") or {}).keys()):
            if lang:
                subs.add(str(lang))
        for lang in list((info.get("automatic_captions") or {}).keys()):
            if lang:
                subs.add(str(lang))

        self.available_qualities = sorted(qualities, key=lambda x: (x != "best", x), reverse=False)
        def _quality_sort(value: str) -> int:
            m = re.search(r"(\d+)", value)
            if m:
                return -int(m.group(1))
            return 1
        self.available_qualities = sorted(self.available_qualities, key=_quality_sort)

        self.available_audio_qualities = sorted(audio_qualities, key=_quality_sort)
        if "best audio" in self.available_audio_qualities:
            self.available_audio_qualities.remove("best audio")
            self.available_audio_qualities.insert(0, "best audio")

        self.available_languages = sorted(langs)
        if "auto" in self.available_languages:
            self.available_languages.remove("auto")
            self.available_languages.insert(0, "auto")

        self.available_subtitle_languages = sorted(subs)
        for special in ("auto", "all"):
            if special in self.available_subtitle_languages:
                self.available_subtitle_languages.remove(special)
        self.available_subtitle_languages.insert(0, "all")
        self.available_subtitle_languages.insert(0, "auto")

    def _video_format_selector(self) -> str:
        quality = str(self.get_var("selected_quality_var", "best")).strip().lower()
        if not quality or quality == "best":
            return "bestvideo*+bestaudio/best"
        m = re.search(r"(\d{3,4})", quality)
        if m:
            max_h = int(m.group(1))
            return f"bestvideo[height<={max_h}]+bestaudio/best[height<={max_h}]"
        return "bestvideo*+bestaudio/best"

    def _audio_format_selector(self) -> str:
        quality = str(self.get_var("selected_audio_quality_var", "best audio")).strip().lower()
        if not quality or "best" in quality:
            return "bestaudio"
        m = re.search(r"(\d{2,3})", quality)
        if m:
            abr = int(m.group(1))
            return f"bestaudio[abr<={abr}]"
        return "bestaudio"

    def _download_sections_value(self) -> str | None:
        start = str(self.get_var("start_var", "")).strip()
        end = str(self.get_var("end_var", "")).strip()
        if not start and not end:
            return None
        if not start:
            start = "00:00"
        if end:
            return f"*{start}-{end}"
        return f"*{start}-inf"

    def _common_download_args(self, for_audio_only: bool = False) -> list[str]:
        output_dir = self._safe_output_dir(for_images=False)
        output_template = os.path.join(output_dir, "%(title).120s [%(id)s].%(ext)s")
        args = ["--no-playlist", "-o", output_template]
        args.extend(self._cookie_args())
        if not for_audio_only:
            args.extend(self._subtitle_args())
        return args

    def _download_with_format(self, label: str, format_selector: str, extra_args: list[str] | None = None) -> None:
        url = str(self.get_var("url_var", "")).strip()
        if not self._looks_like_url(url):
            self._log(f"[{label}] URL invalida")
            return
        cmd = self._yt_dlp_cmd() + self._common_download_args() + ["-f", format_selector]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)
        self._run_command_async(label, cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

    def _download_with_audio_format(self, label: str, extra_args: list[str] | None = None) -> None:
        url = str(self.get_var("url_var", "")).strip()
        if not self._looks_like_url(url):
            self._log(f"[{label}] URL invalida")
            return
        audio_selector = self._audio_format_selector()
        cmd = self._yt_dlp_cmd() + self._common_download_args(for_audio_only=True) + [
            "-f",
            audio_selector,
            "-x",
            "--audio-format",
            "mp3",
        ]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)
        self._run_command_async(label, cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

    def get_var(self, var_name: str, default: Any = "") -> Any:
        return self._vars.get(var_name, default)

    def set_var(self, var_name: str, value: Any) -> None:
        self._vars[var_name] = value
        if bool(self.get_var("auto_save_defaults_var", False)):
            self._save_persisted_settings()

    def call(self, method_name: str, *args, **kwargs) -> Any:
        method = getattr(self.app, method_name)
        return method(*args, **kwargs)

    def process_due_callbacks(self, max_callbacks: int = 200) -> None:
        if self._shutdown:
            return
        _ = max_callbacks

        now = time.monotonic()
        if now - self._last_monitor_refresh > 2.0:
            self._refresh_monitors_if_changed()
            self._last_monitor_refresh = now

        self._apply_live_feed_runtime_updates()
        self._prune_dead_twitter_instances()
        self._update_scraping_state()

        if bool(self.get_var("clipboard_monitor_var", False)) and now - self._last_clipboard_check > 1.2:
            self._last_clipboard_check = now
            try:
                clip = str(QGuiApplication.clipboard().text() or "").strip()
            except Exception:
                clip = ""
            if self._looks_like_url(clip) and clip not in self._clipboard_seen_urls:
                self._clipboard_seen_urls.add(clip)
                if len(self._clipboard_seen_urls) > 500:
                    self._clipboard_seen_urls = set(list(self._clipboard_seen_urls)[-200:])
                self.set_var("url_var", clip)
                self._log("Clipboard monitor: URL detectada, iniciando BEST")
                self.download_best()

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

    def _feed_runtime_config(self) -> dict[str, Any]:
        image_seconds = float((str(self.get_var("feed_image_seconds_var", "10")) or "10").strip())
        scroll_pause = float((str(self.get_var("feed_scroll_pause_var", "1.5")) or "1.5").strip())
        scroll_px = int(float((str(self.get_var("feed_scroll_px_var", "900")) or "900").strip()))
        max_video_wait = float((str(self.get_var("feed_max_video_wait_var", "300")) or "300").strip())

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

    def _current_feed_runtime_payload(self) -> dict[str, Any] | None:
        try:
            cfg = self._feed_runtime_config()
        except Exception:
            return None

        return {
            "poll_seconds": float(cfg["scroll_pause"]),
            "scroll_pause_seconds": float(cfg["scroll_pause"]),
            "scroll_px": int(cfg["scroll_px"]),
            "image_dwell_seconds": float(cfg["image_seconds"]),
            "wait_video_end": bool(self.get_var("feed_wait_video_end_var", True)),
            "max_video_wait_seconds": float(cfg["max_video_wait"]),
            "tiktok_likes_only": bool(self.get_var("feed_tiktok_likes_only_var", False)),
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
                self._log(f"Aviso actualizacion feed principal: {exc}")

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
                self._log(f"Aviso actualizacion {item.get('name')}: {exc}")

        if updated:
            self._log(
                "Feed runtime actualizado en vivo: "
                f"pausa={payload['scroll_pause_seconds']:.2f}s, "
                f"scroll={payload['scroll_px']}, "
                f"imagen={payload['image_dwell_seconds']:.2f}s, "
                f"max_video={payload['max_video_wait_seconds']:.1f}s"
            )

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
            self._log(f"Instancia finalizada: {name}")
        if removed:
            self._update_scraping_state()

    def _is_twitter_url(self, url: str) -> bool:
        lower = str(url or "").lower()
        return "x.com/" in lower or "twitter.com/" in lower

    def _twitter_creator_from_url(self, url: str) -> str | None:
        match = re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/?#]+)/status/\d+", str(url or ""), flags=re.IGNORECASE)
        if not match:
            return None
        creator = (match.group(1) or "").strip().lstrip("@")
        if not creator:
            return None
        creator = re.sub(r'[<>:"/\\|?*]+', "_", creator)
        return creator or None

    def _apply_creator_subfolder(self, base_dir: str, url: str | None, creator_hint: str | None = None) -> str:
        out_dir = str(base_dir or "").strip()
        if not out_dir:
            return out_dir
        if not bool(self.get_var("feed_twitter_creator_folders_var", True)):
            return out_dir
        if not self._is_twitter_url(url or "") and not (creator_hint or "").strip():
            return out_dir

        creator = (creator_hint or "").strip().lstrip("@")
        if not creator:
            creator = self._twitter_creator_from_url(url or "") or ""
        creator = re.sub(r'[<>:"/\\|?*]+', "_", creator).strip()
        if not creator:
            return out_dir
        return os.path.join(out_dir, creator)

    def _build_image_output_dir(
        self,
        url: str | None = None,
        creator_hint: str | None = None,
        root_override: str | None = None,
    ) -> str:
        out_dir = str(root_override or self.get_var("image_output_dir_var", "")).strip()
        if not out_dir:
            out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
        out_dir = self._apply_creator_subfolder(out_dir, url, creator_hint=creator_hint)
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _feed_output_dir_for_url(
        self,
        url: str,
        creator_hint: str | None = None,
        root_override: str | None = None,
    ) -> str:
        out_dir = str(root_override or self.get_var("output_dir_var", "")).strip()
        if not out_dir:
            out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos")
        out_dir = self._apply_creator_subfolder(out_dir, url, creator_hint=creator_hint)
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _looks_like_image_url(self, url: str) -> bool:
        clean = str(url or "").strip().lower()
        if not clean:
            return False
        without_query = clean.split("?", 1)[0]
        if without_query.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".avif")):
            return True
        if "pbs.twimg.com/media/" in clean or "twimg.com/media/" in clean:
            return True
        if "/photo/" in without_query:
            return True
        return False

    def _download_direct_image(self, url: str, out_dir: str) -> str | None:
        try:
            tail = str(url or "").split("?", 1)[0].rstrip("/").split("/")[-1]
            if not tail:
                tail = "image"
            if "." not in tail:
                tail += ".jpg"
            safe_name = re.sub(r'[<>:"/\\|?*]+', "_", tail)
            target = os.path.join(out_dir, safe_name)
            base, ext = os.path.splitext(target)
            index = 1
            while os.path.exists(target):
                target = f"{base}_{index}{ext}"
                index += 1

            request = urllib.request.Request(
                str(url).strip(),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://x.com/",
                },
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "image/" not in content_type:
                    return None
                data = response.read()
            with open(target, "wb") as f:
                f.write(data)
            return target
        except Exception:
            return None

    def _download_twitter_media_urls(self, media_urls: list[str], out_dir: str) -> list[str]:
        saved: list[str] = []
        for media_url in media_urls:
            clean = str(media_url or "").strip()
            if not clean or not self._looks_like_image_url(clean):
                continue
            saved_path = self._download_direct_image(clean, out_dir)
            if saved_path:
                saved.append(saved_path)
        return saved

    def _queue_feed_download(
        self,
        url: str,
        prefer_image_output: bool = False,
        creator_hint: str | None = None,
        media_kind: str | None = None,
        media_urls: list[str] | None = None,
        output_dir_override: str | None = None,
        image_output_dir_override: str | None = None,
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
                "media_kind": (media_kind or "").strip().lower(),
                "media_urls": [str(item).strip() for item in (media_urls or []) if str(item).strip()],
                "output_dir_override": (output_dir_override or "").strip(),
                "image_output_dir_override": (image_output_dir_override or "").strip(),
            }
        )
        self._log(f"Encolado para descarga automatica: {clean}")

        if not self.feed_worker_running:
            self.feed_worker_running = True
            threading.Thread(target=self._feed_download_worker, daemon=True).start()

    def download_from_feed(self, payload: Any) -> None:
        if isinstance(payload, dict):
            url = str(payload.get("url") or "").strip()
            self._queue_feed_download(
                url,
                prefer_image_output=bool(payload.get("prefer_image_output", False)),
                creator_hint=str(payload.get("creator_hint") or "").strip() or None,
                media_kind=str(payload.get("media_kind") or "").strip() or None,
                media_urls=list(payload.get("media_urls") or []),
                output_dir_override=str(payload.get("output_dir_override") or "").strip() or None,
                image_output_dir_override=str(payload.get("image_output_dir_override") or "").strip() or None,
            )
            return
        self._queue_feed_download(str(payload or "").strip(), prefer_image_output=False, creator_hint=None)

    def _instance_download_from_feed(self, instance_id: int, payload: Any) -> None:
        item = self._get_instance_item(int(instance_id))
        if not item:
            self.download_from_feed(payload)
            return

        video_dir = str(item.get("video_output_dir") or "").strip() or None
        image_dir = str(item.get("image_output_dir") or "").strip() or None

        if isinstance(payload, dict):
            merged = dict(payload)
            merged["instance_id"] = int(instance_id)
            if video_dir:
                merged["output_dir_override"] = video_dir
            if image_dir:
                merged["image_output_dir_override"] = image_dir
            self.download_from_feed(merged)
            return

        self.download_from_feed(
            {
                "url": str(payload or "").strip(),
                "instance_id": int(instance_id),
                "output_dir_override": video_dir or "",
                "image_output_dir_override": image_dir or "",
            }
        )

    def _drain_feed_download_queue(self) -> None:
        while not self.feed_download_queue.empty():
            try:
                self.feed_download_queue.get_nowait()
                self.feed_download_queue.task_done()
            except Exception:
                break

    def _feed_download_worker(self) -> None:
        self._log("Worker de feed iniciado")
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
                        output_dir_override = str(payload.get("output_dir_override") or "").strip() or None
                        image_output_dir_override = str(payload.get("image_output_dir_override") or "").strip() or None
                    else:
                        url = str(payload or "").strip()
                        prefer_image_output = False
                        creator_hint = None
                        media_kind = ""
                        media_urls = []
                        output_dir_override = None
                        image_output_dir_override = None

                    if not url:
                        continue

                    if self._is_twitter_url(url) and not creator_hint:
                        creator_hint = self._twitter_creator_from_url(url)

                    if media_kind in {"image", "carousel"} or media_urls:
                        prefer_image_output = True

                    if prefer_image_output:
                        out_dir = self._build_image_output_dir(url, creator_hint=creator_hint, root_override=image_output_dir_override)
                        saved = self._download_twitter_media_urls(media_urls, out_dir)
                        if saved:
                            self._log(f"Imagenes guardadas ({len(saved)}): {url}")
                            self._remember_downloaded_status(url)
                            continue

                        if self._looks_like_image_url(url):
                            saved_single = self._download_direct_image(url, out_dir)
                            if saved_single:
                                self._log(f"Imagen guardada: {saved_single}")
                                self._remember_downloaded_status(url)
                                continue

                        output_template = os.path.join(out_dir, "%(title).120s [%(id)s].%(ext)s")
                        cmd = self._yt_dlp_cmd() + ["--no-playlist", "-o", output_template] + self._cookie_args() + [url]
                    else:
                        out_dir = self._feed_output_dir_for_url(url, creator_hint=creator_hint, root_override=output_dir_override)
                        output_template = os.path.join(out_dir, "%(title).120s [%(id)s].%(ext)s")
                        cmd = (
                            self._yt_dlp_cmd()
                            + [
                                "--no-playlist",
                                "-f",
                                "bestvideo*+bestaudio/best",
                                "--merge-output-format",
                                "mp4",
                                "-o",
                                output_template,
                            ]
                            + self._cookie_args()
                            + [url]
                        )

                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        cwd=os.path.dirname(os.path.abspath(__file__)),
                        check=False,
                    )
                    if proc.returncode == 0:
                        self._remember_downloaded_status(url)
                        self._log(f"Feed descargado: {url}")
                    else:
                        raw_error = str(proc.stdout or proc.stderr or "").strip()
                        summary = raw_error.splitlines()[-1].strip() if raw_error else "sin detalle"
                        self._log(f"Feed fallo ({url}): {summary}")
                except Exception as exc:
                    self._log(f"Feed worker error: {exc}")
                finally:
                    self.feed_download_queue.task_done()
        finally:
            self.feed_worker_running = False
            self._log("Worker de feed detenido")

    def _check_dependencies(self) -> None:
        yt = shutil.which("yt-dlp")
        gd = shutil.which("gallery-dl")
        ff = shutil.which("ffmpeg")
        if yt:
            self._log(f"Dependencia OK: yt-dlp ({yt})")
        else:
            self._log("Falta yt-dlp en PATH")
        if gd:
            self._log(f"Dependencia OK: gallery-dl ({gd})")
        else:
            try:
                __import__("gallery_dl")
                self._log("Dependencia OK: gallery-dl (modulo Python)")
            except Exception:
                self._log("Falta gallery-dl en el entorno actual")
        if ff:
            self._log(f"Dependencia OK: ffmpeg ({ff})")
        else:
            self._log("Falta ffmpeg en PATH")
        try:
            __import__("playwright")
            self._log("Dependencia OK: playwright")
        except Exception:
            self._log("Falta playwright en el entorno actual")

    def _on_auto_save_defaults_toggle(self) -> None:
        if bool(self.get_var("auto_save_defaults_var", False)):
            self._save_persisted_settings()
            self._log("Autoguardado activado")
        else:
            self._log("Autoguardado desactivado")

    def _schedule_window_geometry_save(self) -> None:
        if bool(self.get_var("auto_save_defaults_var", False)):
            self._save_persisted_settings()

    def _on_start_with_windows_toggle(self) -> None:
        enabled = bool(self.get_var("start_with_windows_var", False))
        try:
            self._set_start_with_windows_enabled(enabled)
            self._log("Inicio con Windows activado" if enabled else "Inicio con Windows desactivado")
        except Exception as exc:
            self.set_var("start_with_windows_var", not enabled)
            self._log(f"WARN inicio con Windows: {exc}")

    def _on_ui_language_change(self) -> None:
        lang = str(self.get_var("ui_language_var", "es")).strip().lower()
        self._log(f"Idioma UI actualizado: {lang}")

    def _read_video_info(self, url: str) -> dict[str, Any] | None:
        cmd = self._yt_dlp_cmd() + ["--dump-single-json", "--no-playlist", "--skip-download"] + self._cookie_args() + [url]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.abspath(__file__)),
            check=False,
        )
        if proc.returncode != 0:
            self._log(f"Cargar info fallo: {proc.stdout or proc.stderr}")
            return None
        return self._extract_json(proc.stdout)

    def load_video_info(self) -> None:
        url = str(self.get_var("url_var", "")).strip()
        if not self._looks_like_url(url):
            self._log("Cargar info: URL invalida")
            return

        def worker() -> None:
            info = self._read_video_info(url)
            if not isinstance(info, dict):
                self._log("No se pudo leer metadatos")
                return
            duration = int(info.get("duration") or 0)
            self._last_duration_seconds = max(0, duration)
            self.set_var("duration_var", f"Duracion: {self._format_duration(self._last_duration_seconds)}")
            self._build_quality_lists(info)
            self._log("Metadatos cargados")

        threading.Thread(target=worker, daemon=True).start()

    def load_instagram_info(self) -> None:
        url = str(self.get_var("instagram_url_var", "")).strip()
        if url:
            self.set_var("url_var", url)
        self.load_video_info()

    def load_twitter_info(self) -> None:
        url = str(self.get_var("twitter_url_var", "")).strip()
        if url:
            self.set_var("url_var", url)
        self.load_video_info()

    def download_best(self) -> None:
        self._download_with_format(
            "BEST",
            self._video_format_selector(),
            extra_args=["--merge-output-format", "mp4"],
        )

    def download_audio_only(self) -> None:
        self._download_with_audio_format("AUDIO")

    def download_video_only(self) -> None:
        selector = self._video_format_selector().split("+")[0]
        self._download_with_format("VIDEO_ONLY", selector, extra_args=["--merge-output-format", "mp4"])

    def download_limited_size(self) -> None:
        max_size_mb = str(self.get_var("max_size_var", "")).strip()
        extra: list[str] = ["--merge-output-format", "mp4", "--recode-video", "mp4"]
        try:
            max_mb = float(max_size_mb)
        except Exception:
            max_mb = 0.0
        if max_mb > 1 and self._last_duration_seconds > 0:
            total_kbps = max(256, int((max_mb * 8192.0) / max(1, self._last_duration_seconds) * 0.92))
            video_kbps = max(160, total_kbps - 128)
            extra.extend(
                [
                    "--postprocessor-args",
                    f"ffmpeg:-b:v {video_kbps}k -maxrate {video_kbps}k -bufsize {video_kbps * 2}k -b:a 128k",
                ]
            )
        else:
            self._log("Limitar tamano: usando modo aproximado (falta duracion o tamano valido)")
        self._download_with_format("LIMIT_SIZE", self._video_format_selector(), extra_args=extra)

    def download_trimmed(self) -> None:
        sections = self._download_sections_value()
        if not sections:
            self._log("Recortar segmento: falta Inicio/Fin")
            return
        self._download_with_format(
            "TRIM",
            self._video_format_selector(),
            extra_args=["--download-sections", sections, "--merge-output-format", "mp4"],
        )

    def download_instagram_best(self) -> None:
        url = str(self.get_var("instagram_url_var", "")).strip()
        if url:
            self.set_var("url_var", url)
        self.download_best()

    def download_twitter_best(self) -> None:
        url = str(self.get_var("twitter_url_var", "")).strip()
        if url:
            self.set_var("url_var", url)
        self.download_best()

    def download_image_url(self) -> None:
        url = str(self.get_var("image_url_var", "")).strip() or str(self.get_var("url_var", "")).strip()
        if not self._looks_like_url(url):
            self._log("Descargar imagen: URL invalida")
            return

        def worker() -> None:
            try:
                out_dir = self._safe_output_dir(for_images=True)
                tail = url.split("?")[0].rstrip("/").split("/")[-1].strip()
                if not tail or "." not in tail:
                    tail = f"image_{int(time.time())}.jpg"
                target = os.path.join(out_dir, tail)
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=40) as response:
                    data = response.read()
                with open(target, "wb") as f:
                    f.write(data)
                self._log(f"Imagen descargada: {target}")
            except Exception as exc:
                self._log(f"Descargar imagen fallo: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _cookie_pool_files(self) -> list[str]:
        folder = str(self.get_var("cookies_folder_var", "")).strip()
        if not folder:
            folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies")
        if not os.path.isdir(folder):
            return []
        out: list[str] = []
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and (name.lower().endswith(".txt") or name.lower().endswith(".json")):
                out.append(path)
        out.sort()
        return out

    def _cookie_label(self, path: str) -> str:
        path = str(path or "").strip()
        if not path:
            return "sin cookies"
        name = os.path.basename(path)
        folder = os.path.basename(os.path.dirname(path))
        return f"{name} ({folder})"

    def _set_monitor_cookie_choice(self, monitor_id: int, path: str) -> None:
        self._monitor_cookie_overrides[int(monitor_id)] = str(path or "")
        self._log(f"Cookie monitor {monitor_id}: {self._cookie_label(path)}")

    def _clear_monitor_cookie_choice(self, monitor_id: int) -> None:
        self._monitor_cookie_overrides.pop(int(monitor_id), None)
        self._log(f"Cookie monitor {monitor_id}: usar global")

    def _set_global_cookie_choice(self, path: str) -> None:
        self._global_cookie_choice = str(path or "")
        self._log(f"Cookie global: {self._cookie_label(path)}")

    def _selected_global_cookie(self) -> str:
        return str(self._global_cookie_choice or "")

    def _selected_cookie_for_monitor(self, monitor_id: int) -> str:
        monitor_id = int(monitor_id)
        if monitor_id in self._monitor_cookie_overrides:
            return str(self._monitor_cookie_overrides.get(monitor_id, "") or "")
        return str(self._global_cookie_choice or "")

    def _monitor_cookie_display(self, monitor_id: int) -> str:
        monitor_id = int(monitor_id)
        if monitor_id in self._monitor_cookie_overrides:
            val = self._monitor_cookie_overrides.get(monitor_id, "")
            return self._cookie_label(val)
        global_label = self._cookie_label(self._global_cookie_choice)
        return f"usar global ({global_label})"

    def _apply_global_cookie_to_all_monitors(self) -> None:
        global_cookie = str(self._global_cookie_choice or "")
        for monitor in self.monitors:
            monitor_id = int(monitor.get("id", 0))
            if monitor_id <= 0:
                continue
            self._monitor_cookie_overrides[monitor_id] = global_cookie
        self._log("Cookie global aplicada a todos los monitores")

    def _existing_cookie_files(self) -> list[str]:
        base_dir = os.path.dirname(os.path.dirname(__file__))
        manual = (str(self.get_var("cookies_file_var", "")) or "").strip()
        folder = (str(self.get_var("cookies_folder_var", "")) or "").strip()

        dirs_to_scan = [
            folder,
            os.path.join(base_dir, "downloader", "cookies", "twitter"),
            os.path.join(base_dir, "downloader", "cookies"),
            os.path.join(base_dir, "cookies"),
        ]

        out: list[str] = []
        seen: set[str] = set()

        def add_file(path: str) -> None:
            clean = (path or "").strip()
            if clean and os.path.isfile(clean):
                key = os.path.normcase(os.path.abspath(clean))
                if key not in seen:
                    seen.add(key)
                    out.append(clean)

        if manual:
            add_file(manual)

        for entry in dirs_to_scan:
            clean_dir = (entry or "").strip()
            if not clean_dir or not os.path.isdir(clean_dir):
                continue
            try:
                for name in sorted(os.listdir(clean_dir), key=lambda item: item.lower()):
                    lower = name.lower()
                    if lower.endswith(".txt") and "cookie" in lower:
                        add_file(os.path.join(clean_dir, name))
            except Exception:
                pass

        return out

    def _run_gallery_dl_raw(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
        commands: list[list[str]] = []
        if shutil.which("gallery-dl"):
            commands.append(["gallery-dl", *args])
        commands.append([sys.executable, "-m", "gallery_dl", *args])

        last: subprocess.CompletedProcess[str] | None = None
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
                self._log("Aviso: gallery-dl no esta instalado en este entorno.")

        if last is None:
            raise RuntimeError("No se pudo ejecutar gallery-dl")
        return last

    def _gallery_dl_common_args(self, cookie_file_override: str | None = None, log_usage: bool = False) -> list[str]:
        args: list[str] = []
        cookies_file = (cookie_file_override or "").strip()
        if not cookies_file:
            cookies_file = (str(self.get_var("cookies_file_var", "")) or "").strip()
        if not cookies_file:
            existing = self._existing_cookie_files()
            if existing:
                cookies_file = existing[0]

        if cookies_file and os.path.isfile(cookies_file) and cookies_file.lower().endswith(".txt"):
            args += ["--cookies", cookies_file]
            if log_usage:
                self._log(f"gallery-dl usando cookies: {cookies_file}")
        elif cookies_file and os.path.isfile(cookies_file):
            self._log(f"Aviso: gallery-dl requiere cookies .txt Netscape. Ignorando: {cookies_file}")
        return args

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
            str(self.get_var("x_actions_user_var", "") or ""),
            str(self.get_var("twitter_url_var", "") or ""),
            str(self.get_var("url_var", "") or ""),
        ]
        for value in candidates:
            parsed = self._extract_twitter_username_from_text(value)
            if parsed:
                return parsed
        return None

    def _x_actions_sources(self) -> list[tuple[str, str]]:
        user = self._resolve_x_actions_user()
        sources: list[tuple[str, str]] = []

        if bool(self.get_var("x_actions_bookmarks_var", False)):
            sources.append(("guardados", "https://x.com/i/bookmarks"))

        if bool(self.get_var("x_actions_likes_var", False)) and user:
            sources.append(("likes", f"https://x.com/{user}/likes"))

        if bool(self.get_var("x_actions_retweets_var", False)) and user:
            sources.append(("retweets", "https://x.com/home"))

        if bool(self.get_var("x_actions_profile_var", False)):
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

        fallback_match = re.search(
            r"https?://(?:www\.)?(?:x|twitter)\.com/i(?:/web)?/status/(\d+)",
            text,
            flags=re.IGNORECASE,
        )
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
        match = re.search(
            r"https?://(?:www\.)?(?:x|twitter)\.com/(?:[^/?#]+/status|i(?:/web)?/status)/(\d+)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _scan_downloaded_status_ids(self) -> set[int]:
        pattern = re.compile(r"\[(\d{8,25})\]")
        roots: list[str] = []

        output_root = str(self.get_var("output_dir_var", "") or "").strip()
        image_root = str(self.get_var("image_output_dir_var", "") or "").strip()
        if output_root:
            roots.append(output_root)
        if image_root:
            roots.append(image_root)

        base_dir = os.path.dirname(os.path.dirname(__file__))
        roots.extend(
            [
                os.path.join(base_dir, "videos"),
                os.path.join(base_dir, "downloads"),
                os.path.join(base_dir, "images"),
            ]
        )

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

    def _gallery_item_has_video(self, item: dict[str, Any]) -> bool:
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

    def _gallery_item_has_image(self, item: dict[str, Any]) -> bool:
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

    def _gallery_item_has_media(self, item: dict[str, Any]) -> bool:
        return self._gallery_item_has_video(item) or self._gallery_item_has_image(item)

    def _urls_from_gallery_dl_output(self, source_url: str, stdout: str) -> list[dict[str, Any]]:
        _ = source_url

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

        def collect_from_item(item: dict[str, Any], bag: list[dict[str, Any]]) -> None:
            candidates: list[str] = []
            for key in ("url", "post_url", "tweet_url", "original_url"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value)

            tweet_id = item.get("tweet_id") or item.get("id")
            raw_user = item.get("author") or item.get("user") or item.get("screen_name")
            user_candidate = extract_handle(raw_user)
            actor_candidate = extract_handle(item.get("user"))

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
                        }
                    )

        out: list[dict[str, Any]] = []

        def collect_from_unknown(payload: object) -> None:
            if isinstance(payload, dict):
                collect_from_item(payload, out)
                return
            if isinstance(payload, list):
                for entry in payload:
                    if isinstance(entry, dict):
                        collect_from_item(entry, out)

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

        unique: list[dict[str, Any]] = []
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
                    row["has_video"] = bool(row.get("has_video", False) or has_video)
                    row["has_image"] = bool(row.get("has_image", False) or item.get("has_image", False))
                    row["has_media"] = bool(row.get("has_media", False) or item.get("has_media", False))
                    if not row.get("author_handle") and item.get("author_handle"):
                        row["author_handle"] = item.get("author_handle")
                    continue

            if url in seen_by_url:
                row = unique[seen_by_url[url]]
                row["has_video"] = bool(row.get("has_video", False) or has_video)
                row["has_image"] = bool(row.get("has_image", False) or item.get("has_image", False))
                row["has_media"] = bool(row.get("has_media", False) or item.get("has_media", False))
                if not row.get("author_handle") and item.get("author_handle"):
                    row["author_handle"] = item.get("author_handle")
                continue

            unique.append(item)
            new_index = len(unique) - 1
            seen_by_url[url] = new_index
            if isinstance(status_id, int):
                seen_by_status[status_id] = new_index
        return unique

    def _fetch_x_action_urls(self, source_url: str, limit: int = X_ACTION_RANGE) -> list[dict[str, Any]]:
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
            self._log(f"X monitor: no se pudo leer {source_url} con gallery-dl ({detail})")
            return []
        rows = self._urls_from_gallery_dl_output(source_url, proc.stdout or "")
        if not rows:
            preview = (proc.stdout or "").strip().splitlines()
            snippet = " | ".join(preview[:2]) if preview else "sin salida parseable"
            self._log(f"X monitor: 0 URLs parseadas en {source_url}. Muestra salida: {snippet}")
        return rows

    def _playwright_cookie_candidates(self) -> list[str]:
        return [path for path in self._existing_cookie_files() if path.lower().endswith(".txt")]

    def _load_netscape_cookies_for_playwright(self, file_path: str) -> list[dict[str, Any]]:
        cookies: list[dict[str, Any]] = []
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

                    cookie: dict[str, Any] = {
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
            self._log(f"X monitor: no pude leer cookies para Playwright ({exc})")
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
            self._log(f"X monitor: Playwright no disponible para fallback retweets ({exc})")
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

                merged_cookies: list[dict[str, Any]] = []
                for cookie_file in cookie_files:
                    merged_cookies.extend(self._load_netscape_cookies_for_playwright(cookie_file))
                if merged_cookies:
                    try:
                        context.add_cookies(merged_cookies)
                    except Exception as cookie_exc:
                        self._log(f"X monitor: no pude aplicar cookies Playwright ({cookie_exc})")

                page = context.new_page()
                page.goto(f"https://x.com/{clean_user}", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1800)

                for _ in range(2):
                    page.mouse.wheel(0, 1400)
                    page.wait_for_timeout(550)

                raw_urls = page.evaluate(
                    r"""
                    (maxCount) => {
                        const normalizeStatus = (href) => {
                            if (!href) return null;
                            const m = String(href).match(/https?:\/\/(?:www\.)?(?:x|twitter)\.com\/(?:([^\/?#]+)\/status|i(?:\/web)?\/status)\/(\d+)/i);
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
                    urls = [str(item).strip() for item in raw_urls if isinstance(item, str) and item.strip()]

                context.close()
                browser.close()
        except Exception as exc:
            self._log(f"X monitor: fallback HTML retweets fallo ({exc})")
            return []

        self.x_retweet_html_cache[clean_user.lower()] = (time.time(), urls)
        return urls

    def _run_x_actions_monitor(self) -> None:
        self._log("X monitor invisible: iniciado (sin abrir navegador)")
        try:
            while not self.x_actions_stop_event.is_set():
                sources = self._x_actions_sources()
                if not sources:
                    self._log("X monitor: activa al menos un checkbox (guardados/likes/retweets/perfil).")
                else:
                    user = self._resolve_x_actions_user()
                    if user:
                        self._log(f"X monitor: usuario detectado={user}")
                    else:
                        needs_user = bool(self.get_var("x_actions_likes_var", False)) or bool(self.get_var("x_actions_retweets_var", False)) or bool(self.get_var("x_actions_profile_var", False))
                        if needs_user:
                            self._log("X monitor: no pude inferir usuario para likes/retweets/perfil. Pega tu @usuario o URL en 'Usuario X'.")
                        else:
                            self._log("X monitor: sin usuario explicito; se procesaran solo fuentes que no lo requieren.")

                    if not self._existing_cookie_files():
                        self._log("X monitor: no hay cookies disponibles, no se puede leer acciones privadas.")
                    else:
                        for label, source_url in sources:
                            if self.x_actions_stop_event.is_set():
                                break

                            self._log(f"X monitor: consultando {label} -> {source_url}")
                            label_key = label.lower()
                            limit = max(X_ACTION_RANGE, 40) if label_key == "retweets" else X_ACTION_RANGE
                            found_rows = self._fetch_x_action_urls(source_url, limit=limit)
                            if not found_rows:
                                self._log(f"X monitor: sin items detectables en {label} para esta revision.")
                                continue

                            total_items = len(found_rows)
                            video_rows = [row for row in found_rows if bool(row.get("has_video", False))]
                            image_rows = [row for row in found_rows if bool(row.get("has_image", False))]
                            media_rows = [row for row in found_rows if bool(row.get("has_media", False))]
                            self._log(
                                f"X monitor: {label} devolvio {total_items} item(s), media={len(media_rows)}, "
                                f"videos={len(video_rows)}, imagenes={len(image_rows)}."
                            )

                            is_action_reference_flow = label_key in {"guardados", "likes", "retweets"}
                            if is_action_reference_flow:
                                candidate_rows_unique: list[dict[str, Any]] = []
                                seen_candidate_urls: set[str] = set()
                                seen_candidate_status_ids: set[int] = set()
                                monitored_user = (user or "").strip().lstrip("@").lower()

                                if label_key == "retweets":
                                    media_hint_by_status: dict[int, dict[str, Any]] = {}
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
                                        if not isinstance(status_id, int) or status_id in seen_candidate_status_ids:
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
                                            }
                                        )

                                    if not candidate_rows_unique:
                                        for row in found_rows:
                                            item_url = self._canonical_twitter_status_url(str(row.get("url") or ""))
                                            if not item_url or item_url in seen_candidate_urls:
                                                continue
                                            if not bool(row.get("has_media", False)):
                                                continue
                                            seen_candidate_urls.add(item_url)
                                            row_copy = dict(row)
                                            row_copy["url"] = item_url
                                            candidate_rows_unique.append(row_copy)

                                    self._log(f"X monitor: retweets HTML detecto {len(candidate_rows_unique)} URL(s).")
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
                                    self._log(
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
                                            sid
                                            for sid in (self._status_id_from_url(url_item) for url_item in candidate_urls)
                                            if isinstance(sid, int)
                                        }
                                        seen_status_ids_for_label.update(baseline_ids)
                                    if label_key == "retweets":
                                        self._log(
                                            f"X monitor: baseline por URL cargado para {label} "
                                            f"({len(candidate_urls)} URL(s), media={candidate_media_count})."
                                        )
                                    else:
                                        self._log(f"X monitor: baseline por URL cargado para {label} ({len(candidate_urls)} URL(s) con media).")
                                    continue

                                new_items = [url_item for url_item in candidate_urls if url_item not in seen_for_label]
                                if label_key == "retweets":
                                    row_by_url = {str(r.get("url") or "").strip(): r for r in candidate_rows_unique}
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
                                        self._log(f"X monitor: retweets omitio {skipped_old} URL(s) ya descargadas en disco.")
                                    new_items = filtered_items

                                if new_items:
                                    if label_key == "retweets":
                                        self._log(f"X monitor: detectadas {len(new_items)} URL(s) nuevas en {label}.")
                                    else:
                                        self._log(f"X monitor: detectadas {len(new_items)} URL(s) nuevas con media en {label}.")

                                    row_by_url = {str(r.get("url") or "").strip(): r for r in candidate_rows_unique}
                                    for item_url in reversed(new_items):
                                        if item_url in self.x_actions_seen_urls and label_key != "retweets":
                                            continue
                                        self.x_actions_seen_urls.add(item_url)
                                        row = row_by_url.get(item_url) or {}
                                        has_image = bool(row.get("has_image", False))
                                        has_media = bool(row.get("has_media", False))
                                        creator_hint = str(row.get("author_handle") or "").strip() or None
                                        if label_key == "retweets" and not has_media:
                                            has_media = True
                                        self._queue_feed_download(
                                            item_url,
                                            prefer_image_output=has_image,
                                            creator_hint=creator_hint,
                                        )
                                else:
                                    if label_key == "retweets":
                                        self._log(f"X monitor: sin URLs nuevas en {label}.")
                                    else:
                                        self._log(f"X monitor: sin URLs nuevas con media en {label}.")

                                seen_for_label.update(candidate_urls)
                                if label_key == "retweets":
                                    latest_ids = {
                                        sid
                                        for sid in (self._status_id_from_url(url_item) for url_item in candidate_urls)
                                        if isinstance(sid, int)
                                    }
                                    seen_status_ids_for_label.update(latest_ids)
                                continue

                            if not self.x_actions_bootstrapped:
                                urls = [str(row.get("url") or "").strip() for row in found_rows]
                                self.x_actions_seen_urls.update([url_item for url_item in urls if url_item])
                                self._log(f"X monitor: baseline cargado para {label} ({len(found_rows)} items).")
                                continue

                            urls = [str(row.get("url") or "").strip() for row in found_rows]
                            new_items = [url_item for url_item in urls if url_item and url_item not in self.x_actions_seen_urls]
                            if new_items:
                                self._log(f"X monitor: detectados {len(new_items)} nuevos en {label}.")
                            for item_url in reversed(new_items):
                                self.x_actions_seen_urls.add(item_url)
                                self.download_from_feed(item_url)

                        self.x_actions_bootstrapped = True

                try:
                    poll_seconds = max(10.0, float((str(self.get_var("x_actions_poll_seconds_var", "45")) or "45").strip()))
                except Exception:
                    poll_seconds = 45.0
                self.x_actions_stop_event.wait(poll_seconds)
        finally:
            self._log("X monitor invisible: detenido")
            self.x_actions_running = False
            self.x_actions_thread = None

    def _monitor_label(self, monitor_id: int) -> str:
        for monitor in self.monitors:
            if int(monitor.get("id", 0)) == int(monitor_id):
                return str(monitor.get("label") or f"Monitor {monitor_id}")
        return f"Monitor {monitor_id}"

    def _start_twitter_feed_instance(
        self,
        monitor_id: int,
        cookie_file: str | None = None,
        browser_color_scheme: str | None = None,
        video_output_dir: str | None = None,
        image_output_dir: str | None = None,
    ) -> int:
        if FeedScraper is None:
            raise RuntimeError("Falta 'feed_scraper.py' o sus dependencias (playwright).")

        monitor = self._find_monitor_by_id(int(monitor_id))
        if not monitor:
            raise ValueError(f"No se encontro monitor {monitor_id}")

        cfg = self._feed_runtime_config()
        chosen_cookie = str(cookie_file or "").strip() or self._selected_cookie_for_monitor(int(monitor_id))
        if not chosen_cookie:
            chosen_cookie = str(self.get_var("cookies_file_var", "") or "").strip()

        theme_mode = str(browser_color_scheme or self.get_var("ui_theme_var", "dark") or "dark").strip().lower()
        if theme_mode not in {"dark", "light", "no-preference"}:
            theme_mode = "dark"

        resolved_video_dir = str(video_output_dir or self.get_var("output_dir_var", "") or "").strip()
        resolved_image_dir = str(image_output_dir or self.get_var("image_output_dir_var", "") or "").strip()

        instance_id = self._next_instance_id
        self._next_instance_id += 1
        instance_name = f"X#{instance_id}"
        cookie_candidates = [chosen_cookie] if chosen_cookie else []

        def on_detected(payload: Any) -> None:
            self._instance_download_from_feed(instance_id, payload)

        scraper = FeedScraper(
            on_detected,
            poll_seconds=cfg["scroll_pause"],
            scroll_px=cfg["scroll_px"],
            cookies_file=chosen_cookie,
            image_dwell_seconds=cfg["image_seconds"],
            scroll_pause_seconds=cfg["scroll_pause"],
            wait_video_end=bool(self.get_var("feed_wait_video_end_var", True)),
            max_video_wait_seconds=cfg["max_video_wait"],
            only_visible=True,
            start_maximized=True,
            tiktok_likes_only=bool(self.get_var("feed_tiktok_likes_only_var", False)),
            cookie_candidates=cookie_candidates,
            browser_color_scheme=theme_mode,
            monitor_bounds={
                "left": int(monitor.get("left", 0)),
                "top": int(monitor.get("top", 0)),
                "width": int(monitor.get("width", 0)),
                "height": int(monitor.get("height", 0)),
            },
            instance_name=instance_name,
        )
        scraper.set_log_callback(self._log)
        scraper.start("twitter")

        with self.twitter_instances_lock:
            self.twitter_instances[instance_id] = {
                "id": instance_id,
                "name": instance_name,
                "monitor_id": int(monitor.get("id", 0)),
                "monitor_label": str(monitor.get("label") or f"Monitor {monitor_id}"),
                "cookie_file": chosen_cookie,
                "theme_mode": theme_mode,
                "video_output_dir": resolved_video_dir,
                "image_output_dir": resolved_image_dir,
                "scraper": scraper,
            }

        if chosen_cookie:
            self._log(f"Instancia {instance_name} iniciada en {monitor.get('label')} con cookies: {self._cookie_label(chosen_cookie)}")
        else:
            self._log(f"Instancia {instance_name} iniciada en {monitor.get('label')} (sin cookies dedicadas)")

        self._update_scraping_state()
        if not self.feed_worker_running:
            self.feed_worker_running = True
            threading.Thread(target=self._feed_download_worker, daemon=True).start()
        return instance_id

    def _get_instance_item(self, instance_id: int) -> dict[str, Any] | None:
        with self.twitter_instances_lock:
            return self.twitter_instances.get(int(instance_id))

    def _pause_resume_instance(self, instance_id: int) -> None:
        item = self._get_instance_item(instance_id)
        scraper = item.get("scraper") if item else None
        if scraper:
            scraper.toggle_pause()

    def _toggle_mute_instance(self, instance_id: int) -> None:
        item = self._get_instance_item(instance_id)
        scraper = item.get("scraper") if item else None
        if scraper:
            scraper.toggle_muted()

    def _skip_instance(self, instance_id: int) -> None:
        item = self._get_instance_item(instance_id)
        scraper = item.get("scraper") if item else None
        if scraper:
            scraper.request_skip()
            self._log(f"Skip solicitado para {item.get('name')}")

    def _prev_instance(self, instance_id: int) -> None:
        item = self._get_instance_item(instance_id)
        scraper = item.get("scraper") if item else None
        if scraper:
            scraper.request_prev()
            self._log(f"Prev solicitado para {item.get('name')}")

    def _like_instance_current_post(self, instance_id: int) -> None:
        item = self._get_instance_item(instance_id)
        scraper = item.get("scraper") if item else None
        if not scraper:
            return
        ok = scraper.request_like_current_twitter_post()
        if not ok:
            self._log(f"Like no disponible para {item.get('name')} (instancia no activa)")

    def _retweet_instance_current_post(self, instance_id: int) -> None:
        item = self._get_instance_item(instance_id)
        scraper = item.get("scraper") if item else None
        if not scraper:
            return
        ok = scraper.request_retweet_current_twitter_post()
        if not ok:
            self._log(f"Retweet no disponible para {item.get('name')} (instancia no activa)")

    def _stop_instance(self, instance_id: int) -> None:
        with self.twitter_instances_lock:
            item = self.twitter_instances.pop(int(instance_id), None)
        scraper = item.get("scraper") if item else None
        if scraper:
            try:
                scraper.stop()
            except Exception as exc:
                self._log(f"Aviso stop instancia {item.get('name')}: {exc}")
        if item:
            self._log(f"Instancia {item.get('name')} detenida")
        self._update_scraping_state()

    def _kill_instance(self, instance_id: int) -> None:
        with self.twitter_instances_lock:
            item = self.twitter_instances.pop(int(instance_id), None)
        scraper = item.get("scraper") if item else None
        if scraper:
            try:
                scraper.kill()
            except Exception as exc:
                self._log(f"Aviso kill instancia {item.get('name')}: {exc}")
        if item:
            self._log(f"Instancia {item.get('name')} finalizada (kill)")
        self._update_scraping_state()

    def _set_instance_fullscreen(self, instance_id: int, enabled: bool | None = None) -> None:
        item = self._get_instance_item(instance_id)
        scraper = item.get("scraper") if item else None
        if not scraper:
            return
        if enabled is None:
            scraper.toggle_window_fullscreen()
            return
        scraper.set_window_fullscreen(bool(enabled))

    def _set_all_instances_fullscreen(self, enabled: bool) -> None:
        with self.twitter_instances_lock:
            items = list(self.twitter_instances.values())
        changed = 0
        for item in items:
            scraper = item.get("scraper")
            if scraper and scraper.is_running():
                scraper.set_window_fullscreen(bool(enabled))
                changed += 1
        if changed:
            if enabled:
                self._log("F11 global aplicado a instancias X")
            else:
                self._log("Salir F11 global aplicado a instancias X")

    def _skip_all_twitter_instances(self) -> None:
        with self.twitter_instances_lock:
            items = list(self.twitter_instances.values())
        for item in items:
            scraper = item.get("scraper")
            if scraper and scraper.is_running():
                scraper.request_skip()
        if items:
            self._log("Skip global aplicado a instancias X")

    def _prev_all_twitter_instances(self) -> None:
        with self.twitter_instances_lock:
            items = list(self.twitter_instances.values())
        for item in items:
            scraper = item.get("scraper")
            if scraper and scraper.is_running():
                scraper.request_prev()
        if items:
            self._log("Prev global aplicado a instancias X")

    def _kill_all_twitter_instances(self) -> None:
        with self.twitter_instances_lock:
            ids = list(self.twitter_instances.keys())
        for instance_id in ids:
            self._kill_instance(int(instance_id))
        self._log("Kill global aplicado a instancias X")

    def _start_loop_worker(self, key: str, title: str, interval: float = 2.0) -> None:
        with self._workers_lock:
            current = self._feed_workers.get(key)
            if current and current.get("thread") and current["thread"].is_alive():
                self._log(f"{title} ya esta activo")
                return
            stop_event = threading.Event()

            def worker() -> None:
                self._log(f"{title} iniciado")
                while not stop_event.is_set() and not self._shutdown:
                    time.sleep(max(0.5, float(interval)))
                self._log(f"{title} detenido")

            thread = threading.Thread(target=worker, daemon=True)
            self._feed_workers[key] = {"thread": thread, "stop_event": stop_event, "title": title}
            thread.start()

    def _stop_loop_worker(self, key: str) -> None:
        with self._workers_lock:
            item = self._feed_workers.get(key)
        if not item:
            return
        stop_event = item.get("stop_event")
        thread = item.get("thread")
        if stop_event:
            stop_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=2)
        with self._workers_lock:
            self._feed_workers.pop(key, None)

    def start_feed(self, platform: str) -> None:
        if FeedScraper is None:
            raise RuntimeError("Falta 'feed_scraper.py' o sus dependencias (playwright).")

        cfg = self._feed_runtime_config()
        platform_name = str(platform or "").strip().lower() or "instagram"

        if platform_name == "twitter":
            primary = next((m for m in self.monitors if bool(m.get("primary", False))), None)
            default_monitor_id = int((primary or self.monitors[0]).get("id", 1))
            self._start_twitter_feed_instance(default_monitor_id)
            return

        if self.scraper and self.scraper.is_running():
            try:
                self.scraper.stop()
            except Exception:
                pass
            self.scraper = None

        self.scraper = FeedScraper(
            self.download_from_feed,
            poll_seconds=cfg["scroll_pause"],
            scroll_px=cfg["scroll_px"],
            cookies_file=str(self.get_var("cookies_file_var", "") or "").strip(),
            image_dwell_seconds=cfg["image_seconds"],
            scroll_pause_seconds=cfg["scroll_pause"],
            wait_video_end=bool(self.get_var("feed_wait_video_end_var", True)),
            max_video_wait_seconds=cfg["max_video_wait"],
            only_visible=True,
            start_maximized=True,
            tiktok_likes_only=bool(self.get_var("feed_tiktok_likes_only_var", False)),
            browser_color_scheme=str(self.get_var("ui_theme_var", "dark") or "dark").strip().lower(),
        )
        self.scraper.set_log_callback(self._log)
        self.scraper.start(platform_name)
        self._update_scraping_state()
        self._log(f"Scraper iniciado para {platform_name}")

        if not self.feed_worker_running:
            self.feed_worker_running = True
            threading.Thread(target=self._feed_download_worker, daemon=True).start()

    def stop_feed(self) -> None:
        stopped_any = False

        if self.scraper:
            try:
                self.scraper.stop()
            finally:
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
            self._drain_feed_download_queue()
            self._log("Scraper detenido")
        else:
            self._log("No hay scraper en ejecucion")

        if not self.x_actions_running:
            self.feed_urls_queued.clear()

    def start_x_actions_monitor(self) -> None:
        if self.x_actions_running:
            self._log("X monitor: ya esta en ejecucion")
            return

        sources = self._x_actions_sources()
        if not sources:
            raise RuntimeError("Activa al menos un checkbox de acciones X.")

        self.x_actions_seen_urls.clear()
        self.x_actions_seen_urls_by_label.clear()
        self.x_actions_seen_status_ids_by_label.clear()
        self.downloaded_status_ids = self._scan_downloaded_status_ids()
        self._log(f"X monitor: indice local de status descargados={len(self.downloaded_status_ids)}")
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
        self.x_actions_running = False

    def open_live_log_window(self) -> None:
        path = self.log_file_path
        if not os.path.isfile(path):
            self._prepare_log_file()
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self._log(f"No se pudo abrir log: {exc}")

    def _on_main_close(self) -> None:
        self.shutdown()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        try:
            self.stop_feed()
        except Exception:
            pass
        try:
            self.stop_x_actions_monitor()
        except Exception:
            pass
        try:
            self._kill_all_twitter_instances()
        except Exception:
            pass
        if bool(self.get_var("auto_save_defaults_var", False)):
            self._save_persisted_settings()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1500, 900)
        self.resize(1760, 1020)

        self.bridge = CoreBridge()

        self._ui_loading = True
        self._is_shutting_down = False
        self._last_log_count = 0
        self._monitor_signature: tuple | None = None
        self._instances_signature: tuple | None = None
        self._combo_signatures: dict[str, tuple] = {}
        self._active_theme = "dark"
        self.instance_rows: dict[int, dict[str, Any]] = {}

        self._setup_styles()
        self._build_ui()
        self._bind_data_to_legacy()
        self._ui_loading = False

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_from_legacy)
        self.refresh_timer.start(120)

        self._refresh_from_legacy()
        self._append_local_log("UI moderna lista. Motor core activo en segundo plano.")

    def _setup_styles(self) -> None:
        theme = str(self.bridge.get_var("ui_theme_var", "dark") or "dark").strip().lower()
        self._apply_theme(theme)

    def _apply_theme(self, theme_code: str) -> None:
        clean = "light" if str(theme_code or "").strip().lower() == "light" else "dark"
        self._active_theme = clean
        self.bridge.set_var("ui_theme_var", clean)

        palette = {
            "dark": {
                "text": "#d5dee8",
                "window_bg": "#070b12",
                "root_0": "#070b12",
                "root_1": "#0d1520",
                "root_2": "#111c2a",
                "panel": "#121a25",
                "panel_border": "#223447",
                "brand": "#2dd4bf",
                "brand_sub": "#8ba4bd",
                "nav_text": "#9db4c9",
                "nav_hover": "#1b2a3a",
                "nav_active": "#143045",
                "nav_active_border": "#2dd4bf",
                "title": "#ecf7ff",
                "subtitle": "#8ba4bd",
                "hint": "#8ba4bd",
                "input_bg": "#0f1722",
                "input_border": "#2a4157",
                "input_focus": "#35b8ff",
                "input_focus_bg": "#101d2a",
                "btn": "#2294f5",
                "btn_border": "#48aefe",
                "btn_hover": "#43a9ff",
                "btn_text": "#f4fbff",
                "soft": "#1f3245",
                "soft_border": "#355169",
                "soft_hover": "#294359",
                "soft_text": "#d5dee8",
                "danger": "#c0392b",
                "danger_border": "#e35d50",
                "danger_hover": "#d9483a",
                "danger_text": "#fff5f4",
                "menu_bg": "#0f1722",
                "menu_border": "#2a4157",
                "menu_hover": "#17354a",
                "instance_card": "#0f1722",
                "instance_card_border": "#2a4157",
                "scroll_bg": "#0c141e",
                "scroll_handle": "#355169",
                "scroll_hover": "#4c7293",
            },
            "light": {
                "text": "#1d2938",
                "window_bg": "#edf3f9",
                "root_0": "#eef5fb",
                "root_1": "#e8f0f8",
                "root_2": "#dce8f4",
                "panel": "#ffffff",
                "panel_border": "#c9d7e6",
                "brand": "#0ea5a4",
                "brand_sub": "#4f6478",
                "nav_text": "#42586f",
                "nav_hover": "#e6eef7",
                "nav_active": "#d8ecff",
                "nav_active_border": "#0ea5a4",
                "title": "#0d2033",
                "subtitle": "#4f6478",
                "hint": "#597087",
                "input_bg": "#f6fbff",
                "input_border": "#b8ccdf",
                "input_focus": "#0284c7",
                "input_focus_bg": "#ffffff",
                "btn": "#0284c7",
                "btn_border": "#0ea5e9",
                "btn_hover": "#0ea5e9",
                "btn_text": "#ffffff",
                "soft": "#e8eff7",
                "soft_border": "#c3d2e2",
                "soft_hover": "#d9e5f1",
                "soft_text": "#243447",
                "danger": "#c24134",
                "danger_border": "#db5b4d",
                "danger_hover": "#d64b3d",
                "danger_text": "#ffffff",
                "menu_bg": "#ffffff",
                "menu_border": "#b8ccdf",
                "menu_hover": "#d9ecff",
                "instance_card": "#f6fbff",
                "instance_card_border": "#c9d7e6",
                "scroll_bg": "#e7eef6",
                "scroll_handle": "#b6c7d8",
                "scroll_hover": "#98afc6",
            },
        }

        p = palette[clean]
        self.setStyleSheet(
            """
            * {
                font-family: 'Segoe UI', sans-serif;
                color: %(text)s;
                font-size: 11pt;
            }
            QMainWindow {
                background-color: %(window_bg)s;
            }
            QWidget#root {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 %(root_0)s,
                    stop:0.5 %(root_1)s,
                    stop:1 %(root_2)s);
            }
            QFrame#sidebar {
                background-color: %(panel)s;
                border-right: 1px solid %(panel_border)s;
            }
            QLabel#brand {
                font-size: 20pt;
                font-weight: 700;
                color: %(brand)s;
            }
            QLabel#brandSub {
                color: %(brand_sub)s;
                font-size: 9pt;
            }
            QPushButton#nav {
                text-align: left;
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 10px 12px;
                font-weight: 600;
                color: %(nav_text)s;
                background-color: transparent;
            }
            QPushButton#nav:hover {
                background-color: %(nav_hover)s;
                border-color: %(panel_border)s;
                color: %(text)s;
            }
            QPushButton#nav:checked {
                background-color: %(nav_active)s;
                border-color: %(nav_active_border)s;
                color: %(title)s;
            }
            QFrame#topBar {
                background-color: %(panel)s;
                border: 1px solid %(panel_border)s;
                border-radius: 14px;
            }
            QLabel#title {
                font-size: 18pt;
                font-weight: 700;
                color: %(title)s;
            }
            QLabel#subtitle {
                color: %(subtitle)s;
                font-size: 10pt;
            }
            QFrame#card {
                background-color: %(panel)s;
                border: 1px solid %(panel_border)s;
                border-radius: 14px;
            }
            QGroupBox {
                font-weight: 700;
                color: %(text)s;
                border: 1px solid %(panel_border)s;
                border-radius: 12px;
                margin-top: 10px;
                padding: 12px 10px 10px 10px;
                background-color: %(panel)s;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
            QLabel#hint {
                color: %(hint)s;
                font-size: 9pt;
            }
            QLineEdit, QComboBox {
                background-color: %(input_bg)s;
                border: 1px solid %(input_border)s;
                border-radius: 8px;
                padding: 9px 11px;
                color: %(text)s;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 2px solid %(input_focus)s;
                background-color: %(input_focus_bg)s;
            }
            QComboBox QAbstractItemView {
                background-color: %(menu_bg)s;
                color: %(text)s;
                border: 1px solid %(menu_border)s;
                selection-background-color: %(menu_hover)s;
            }
            QCheckBox {
                color: %(text)s;
            }
            QPushButton {
                background-color: %(btn)s;
                border: 1px solid %(btn_border)s;
                border-radius: 9px;
                color: %(btn_text)s;
                padding: 10px 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: %(btn_hover)s;
            }
            QPushButton#soft {
                background-color: %(soft)s;
                border: 1px solid %(soft_border)s;
                color: %(soft_text)s;
            }
            QPushButton#soft:hover {
                background-color: %(soft_hover)s;
            }
            QPushButton#danger {
                background-color: %(danger)s;
                border: 1px solid %(danger_border)s;
                color: %(danger_text)s;
            }
            QPushButton#danger:hover {
                background-color: %(danger_hover)s;
            }
            QTextEdit {
                background-color: %(input_bg)s;
                border: 1px solid %(input_border)s;
                border-radius: 10px;
                color: %(text)s;
                padding: 8px;
                font-family: Consolas, 'Cascadia Mono', monospace;
                font-size: 9pt;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QMenu {
                background-color: %(menu_bg)s;
                border: 1px solid %(menu_border)s;
                padding: 4px;
            }
            QMenu::item {
                padding: 7px 14px;
                border-radius: 6px;
                margin: 2px 4px;
            }
            QMenu::item:selected {
                background-color: %(menu_hover)s;
            }
            QFrame#instanceCard {
                background-color: %(instance_card)s;
                border: 1px solid %(instance_card_border)s;
                border-radius: 10px;
            }
            QLabel#instanceTitle {
                color: %(title)s;
                font-weight: 700;
                font-size: 10pt;
            }
            QLabel#instanceMeta {
                color: %(hint)s;
                font-size: 9pt;
            }
            QScrollBar:vertical {
                background: %(scroll_bg)s;
                width: 12px;
                margin: 0px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: %(scroll_handle)s;
                min-height: 25px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: %(scroll_hover)s;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar:horizontal {
                background: %(scroll_bg)s;
                height: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background: %(scroll_handle)s;
                min-width: 25px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal:hover {
                background: %(scroll_hover)s;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            """
            % p
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        shell = QHBoxLayout(root)
        shell.setContentsMargins(14, 14, 14, 14)
        shell.setSpacing(12)

        sidebar = self._build_sidebar()
        content = self._build_content()

        shell.addWidget(sidebar)
        shell.addWidget(content, 1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(270)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 18, 16, 18)
        layout.setSpacing(10)

        brand = QLabel("Downloader")
        brand.setObjectName("brand")
        brand_sub = QLabel("Modern PyQt5 UI + standalone engine")
        brand_sub.setObjectName("brandSub")
        layout.addWidget(brand)
        layout.addWidget(brand_sub)
        layout.addSpacing(12)

        self.nav_buttons: list[QPushButton] = []
        nav_labels = ["Descargas", "Automatizacion", "Instancias X", "Actividad"]
        for idx, text in enumerate(nav_labels):
            btn = QPushButton(text)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, i=idx: self._switch_page(i))
            self.nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch(1)

        check_btn = QPushButton("Verificar dependencias")
        check_btn.setObjectName("soft")
        check_btn.clicked.connect(lambda: self._run_legacy("_check_dependencies"))
        layout.addWidget(check_btn)

        open_backup = QPushButton("Abrir motor downloader.py")
        open_backup.setObjectName("soft")
        open_backup.clicked.connect(lambda: self._open_path(self._engine_file_path()))
        layout.addWidget(open_backup)

        return sidebar

    def _build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        topbar = QFrame()
        topbar.setObjectName("topBar")
        topbar_layout = QVBoxLayout(topbar)
        topbar_layout.setContentsMargins(14, 12, 14, 12)

        title = QLabel("Downloader Control Center")
        title.setObjectName("title")
        subtitle = QLabel("Misma logica del proyecto original, interfaz moderna en PyQt5")
        subtitle.setObjectName("subtitle")

        topbar_layout.addWidget(title)
        topbar_layout.addWidget(subtitle)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Idioma UI:"))
        self.ui_language_combo = QComboBox()
        self.ui_language_combo.addItems(["Español", "English"])
        lang_row.addWidget(self.ui_language_combo)
        lang_row.addSpacing(12)
        lang_row.addWidget(QLabel("Tema:"))
        self.ui_theme_combo = QComboBox()
        self.ui_theme_combo.addItems(["Oscuro", "Claro"])
        lang_row.addWidget(self.ui_theme_combo)
        lang_row.addStretch(1)
        restart_top_btn = QPushButton("Reiniciar app")
        restart_top_btn.setObjectName("danger")
        restart_top_btn.clicked.connect(self._restart_self)
        lang_row.addWidget(restart_top_btn)
        topbar_layout.addLayout(lang_row)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_downloads_page())
        self.pages.addWidget(self._build_automation_page())
        self.pages.addWidget(self._build_instances_page())
        self.pages.addWidget(self._build_activity_page())

        layout.addWidget(topbar)
        layout.addWidget(self.pages, 1)

        self._switch_page(0)
        return content

    def _build_scroll_page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        inner_layout.setSpacing(10)

        scroll.setWidget(inner)
        page_layout.addWidget(scroll)
        return page, inner_layout

    def _build_downloads_page(self) -> QWidget:
        page, layout = self._build_scroll_page()

        source = QGroupBox("Fuente y salida")
        src = QGridLayout(source)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://...")
        src.addWidget(QLabel("URL:"), 0, 0)
        src.addWidget(self.url_edit, 0, 1)
        btn_info = QPushButton("Cargar info")
        btn_info.clicked.connect(lambda: self._run_legacy("load_video_info"))
        src.addWidget(btn_info, 0, 2)
        btn_paste_url = QPushButton("Pegar")
        btn_paste_url.setObjectName("soft")
        btn_paste_url.clicked.connect(lambda: self._paste_into(self.url_edit))
        src.addWidget(btn_paste_url, 0, 3)

        self.output_dir_edit = QLineEdit()
        src.addWidget(QLabel("Salida:"), 1, 0)
        src.addWidget(self.output_dir_edit, 1, 1)
        btn_out = QPushButton("Elegir carpeta")
        btn_out.setObjectName("soft")
        btn_out.clicked.connect(lambda: self._pick_directory_for(self.output_dir_edit, "output_dir_var"))
        src.addWidget(btn_out, 1, 2)

        self.image_output_edit = QLineEdit()
        src.addWidget(QLabel("Salida imagenes:"), 2, 0)
        src.addWidget(self.image_output_edit, 2, 1)
        btn_img_out = QPushButton("Elegir carpeta imagenes")
        btn_img_out.setObjectName("soft")
        btn_img_out.clicked.connect(lambda: self._pick_directory_for(self.image_output_edit, "image_output_dir_var"))
        src.addWidget(btn_img_out, 2, 2)

        self.image_url_edit = QLineEdit()
        self.image_url_edit.setPlaceholderText("https://...jpg / ...png")
        src.addWidget(QLabel("URL imagen:"), 3, 0)
        src.addWidget(self.image_url_edit, 3, 1)
        btn_img_dl = QPushButton("Descargar imagen URL")
        btn_img_dl.clicked.connect(lambda: self._run_legacy("download_image_url"))
        src.addWidget(btn_img_dl, 3, 2)
        btn_paste_img = QPushButton("Pegar")
        btn_paste_img.setObjectName("soft")
        btn_paste_img.clicked.connect(lambda: self._paste_into(self.image_url_edit))
        src.addWidget(btn_paste_img, 3, 3)

        src.setColumnStretch(1, 1)
        layout.addWidget(source)

        social = QGroupBox("Redes sociales")
        soc = QGridLayout(social)

        self.instagram_edit = QLineEdit()
        self.instagram_edit.setPlaceholderText("Instagram URL")
        soc.addWidget(QLabel("Instagram:"), 0, 0)
        soc.addWidget(self.instagram_edit, 0, 1)
        btn_ig_info = QPushButton("Info IG")
        btn_ig_info.setObjectName("soft")
        btn_ig_info.clicked.connect(lambda: self._run_legacy("load_instagram_info"))
        soc.addWidget(btn_ig_info, 0, 2)
        btn_ig_best = QPushButton("Descargar IG BEST")
        btn_ig_best.clicked.connect(lambda: self._run_legacy("download_instagram_best"))
        soc.addWidget(btn_ig_best, 0, 3)
        btn_ig_paste = QPushButton("Pegar")
        btn_ig_paste.setObjectName("soft")
        btn_ig_paste.clicked.connect(lambda: self._paste_into(self.instagram_edit))
        soc.addWidget(btn_ig_paste, 0, 4)

        self.twitter_edit = QLineEdit()
        self.twitter_edit.setPlaceholderText("Twitter/X URL")
        soc.addWidget(QLabel("Twitter/X:"), 1, 0)
        soc.addWidget(self.twitter_edit, 1, 1)
        btn_tw_info = QPushButton("Info TW")
        btn_tw_info.setObjectName("soft")
        btn_tw_info.clicked.connect(lambda: self._run_legacy("load_twitter_info"))
        soc.addWidget(btn_tw_info, 1, 2)
        btn_tw_best = QPushButton("Descargar TW BEST")
        btn_tw_best.clicked.connect(lambda: self._run_legacy("download_twitter_best"))
        soc.addWidget(btn_tw_best, 1, 3)
        btn_tw_paste = QPushButton("Pegar")
        btn_tw_paste.setObjectName("soft")
        btn_tw_paste.clicked.connect(lambda: self._paste_into(self.twitter_edit))
        soc.addWidget(btn_tw_paste, 1, 4)

        soc.setColumnStretch(1, 1)
        layout.addWidget(social)

        options = QGroupBox("Opciones")
        opt = QGridLayout(options)

        self.max_size_edit = QLineEdit()
        opt.addWidget(QLabel("Tamano objetivo (MB):"), 0, 0)
        opt.addWidget(self.max_size_edit, 0, 1)

        self.start_edit = QLineEdit()
        self.end_edit = QLineEdit()
        opt.addWidget(QLabel("Inicio (mm:ss o hh:mm:ss):"), 0, 2)
        opt.addWidget(self.start_edit, 0, 3)
        opt.addWidget(QLabel("Fin (mm:ss o hh:mm:ss):"), 0, 4)
        opt.addWidget(self.end_edit, 0, 5)

        self.duration_label = QLabel("Duracion: -")
        self.duration_label.setObjectName("hint")
        opt.addWidget(self.duration_label, 0, 6)

        self.language_combo = QComboBox()
        self.quality_combo = QComboBox()
        self.audio_quality_combo = QComboBox()
        opt.addWidget(QLabel("Idioma audio:"), 1, 0)
        opt.addWidget(self.language_combo, 1, 1)
        opt.addWidget(QLabel("Calidad video:"), 1, 2)
        opt.addWidget(self.quality_combo, 1, 3)
        opt.addWidget(QLabel("Calidad audio:"), 1, 4)
        opt.addWidget(self.audio_quality_combo, 1, 5)

        self.compression_combo = QComboBox()
        self.compression_combo.addItems(["sin_compresion", "baja", "media", "alta"])
        opt.addWidget(QLabel("Compresion:"), 2, 0)
        opt.addWidget(self.compression_combo, 2, 1)

        self.include_subtitles_chk = QCheckBox("Descargar subtitulos")
        self.embed_subtitles_chk = QCheckBox("Embeber subtitulos en MP4 (toggle en reproductor)")
        opt.addWidget(self.include_subtitles_chk, 2, 2, 1, 2)
        opt.addWidget(self.embed_subtitles_chk, 2, 4, 1, 2)

        self.subtitle_lang_combo = QComboBox()
        opt.addWidget(QLabel("Idioma subtitulos:"), 3, 0)
        opt.addWidget(self.subtitle_lang_combo, 3, 1)
        subtitle_hint = QLabel("Auto, all, idioma detectado o patron es.*/en.*")
        subtitle_hint.setObjectName("hint")
        opt.addWidget(subtitle_hint, 3, 2, 1, 4)

        self.use_cookies_chk = QCheckBox("Usar cookies del navegador")
        self.cookies_browser_combo = QComboBox()
        self.cookies_browser_combo.addItems(["chrome", "edge", "firefox", "brave"])
        opt.addWidget(self.use_cookies_chk, 4, 0, 1, 2)
        opt.addWidget(QLabel("Navegador:"), 4, 2)
        opt.addWidget(self.cookies_browser_combo, 4, 3)

        self.cookies_file_edit = QLineEdit()
        self.cookies_folder_edit = QLineEdit()
        opt.addWidget(QLabel("cookies.txt:"), 5, 0)
        opt.addWidget(self.cookies_file_edit, 5, 1, 1, 3)
        btn_cookie_file = QPushButton("Elegir cookies")
        btn_cookie_file.setObjectName("soft")
        btn_cookie_file.clicked.connect(self._pick_cookie_file)
        opt.addWidget(btn_cookie_file, 5, 4)

        opt.addWidget(QLabel("Carpeta cookies:"), 6, 0)
        opt.addWidget(self.cookies_folder_edit, 6, 1, 1, 3)
        btn_cookie_folder = QPushButton("Elegir carpeta")
        btn_cookie_folder.setObjectName("soft")
        btn_cookie_folder.clicked.connect(self._pick_cookie_folder)
        opt.addWidget(btn_cookie_folder, 6, 4)

        self.auto_save_chk = QCheckBox("Guardar predeterminados automaticamente")
        self.remember_pos_chk = QCheckBox("Recordar ubicacion de la ventana")
        self.start_with_windows_chk = QCheckBox("Iniciar con Windows")
        self.clipboard_monitor_chk = QCheckBox("Monitor portapapeles (auto)")
        opt.addWidget(self.auto_save_chk, 7, 0, 1, 2)
        opt.addWidget(self.remember_pos_chk, 7, 2, 1, 2)
        opt.addWidget(self.start_with_windows_chk, 7, 4)
        opt.addWidget(self.clipboard_monitor_chk, 7, 5)

        opt.setColumnStretch(1, 1)
        opt.setColumnStretch(3, 1)
        opt.setColumnStretch(5, 1)
        layout.addWidget(options)

        actions = QGroupBox("Acciones de descarga")
        act = QHBoxLayout(actions)

        btn_best = QPushButton("BEST")
        btn_best.clicked.connect(lambda: self._run_legacy("download_best"))
        act.addWidget(btn_best)

        btn_audio = QPushButton("Audio")
        btn_audio.setObjectName("soft")
        btn_audio.clicked.connect(lambda: self._run_legacy("download_audio_only"))
        act.addWidget(btn_audio)

        btn_video = QPushButton("Solo video")
        btn_video.setObjectName("soft")
        btn_video.clicked.connect(lambda: self._run_legacy("download_video_only"))
        act.addWidget(btn_video)

        btn_limit = QPushButton("Limitar tamano")
        btn_limit.setObjectName("soft")
        btn_limit.clicked.connect(lambda: self._run_legacy("download_limited_size"))
        act.addWidget(btn_limit)

        btn_trim = QPushButton("Recortar segmento")
        btn_trim.setObjectName("soft")
        btn_trim.clicked.connect(lambda: self._run_legacy("download_trimmed"))
        act.addWidget(btn_trim)

        btn_restart = QPushButton("Reiniciar app")
        btn_restart.setObjectName("danger")
        btn_restart.clicked.connect(self._restart_self)
        act.addWidget(btn_restart)

        layout.addWidget(actions)
        layout.addStretch(1)
        return page

    def _build_automation_page(self) -> QWidget:
        page, layout = self._build_scroll_page()

        feed = QGroupBox("Feed automatico")
        grid = QGridLayout(feed)

        self.feed_image_seconds_edit = QLineEdit()
        self.feed_scroll_pause_edit = QLineEdit()
        self.feed_scroll_px_edit = QLineEdit()
        self.feed_wait_video_end_chk = QCheckBox("Esperar video hasta final")
        self.feed_max_video_wait_edit = QLineEdit()
        self.feed_tiktok_likes_only_chk = QCheckBox("TikTok: solo videos con like")
        self.feed_twitter_creator_folders_chk = QCheckBox("Twitter: guardar por creador")

        grid.addWidget(QLabel("Imagen (s):"), 0, 0)
        grid.addWidget(self.feed_image_seconds_edit, 0, 1)
        grid.addWidget(QLabel("Pausa scroll (s):"), 0, 2)
        grid.addWidget(self.feed_scroll_pause_edit, 0, 3)
        grid.addWidget(QLabel("Pixeles scroll:"), 0, 4)
        grid.addWidget(self.feed_scroll_px_edit, 0, 5)

        grid.addWidget(self.feed_wait_video_end_chk, 1, 0, 1, 2)
        grid.addWidget(QLabel("Max video (s):"), 1, 2)
        grid.addWidget(self.feed_max_video_wait_edit, 1, 3)

        grid.addWidget(self.feed_tiktok_likes_only_chk, 2, 0, 1, 2)
        grid.addWidget(self.feed_twitter_creator_folders_chk, 2, 2, 1, 2)

        btn_ig = QPushButton("Iniciar Feed IG")
        btn_ig.clicked.connect(lambda: self._run_legacy("start_feed", "instagram"))
        btn_tt = QPushButton("Iniciar Feed TikTok")
        btn_tt.clicked.connect(lambda: self._run_legacy("start_feed", "tiktok"))
        btn_tw = QPushButton("Iniciar Feed Twitter/X")
        btn_tw.clicked.connect(lambda: self._run_legacy("start_feed", "twitter"))
        btn_yt = QPushButton("Iniciar Feed YouTube Shorts")
        btn_yt.clicked.connect(lambda: self._run_legacy("start_feed", "youtube"))
        btn_stop = QPushButton("STOP Feed")
        btn_stop.setObjectName("danger")
        btn_stop.clicked.connect(lambda: self._run_legacy("stop_feed"))
        btn_live = QPushButton("Ver log en vivo")
        btn_live.setObjectName("soft")
        btn_live.clicked.connect(lambda: self._run_legacy("open_live_log_window"))

        grid.addWidget(btn_ig, 3, 0)
        grid.addWidget(btn_tt, 3, 1)
        grid.addWidget(btn_tw, 3, 2)
        grid.addWidget(btn_yt, 3, 3)
        grid.addWidget(btn_stop, 3, 4)
        grid.addWidget(btn_live, 3, 5)

        feed_hint_1 = QLabel("Monitor X: invisible, sin abrir navegador.")
        feed_hint_1.setObjectName("hint")
        grid.addWidget(feed_hint_1, 4, 0, 1, 3)
        feed_hint_2 = QLabel("Ruta descarga: usa campo 'Salida' arriba")
        feed_hint_2.setObjectName("hint")
        grid.addWidget(feed_hint_2, 4, 3, 1, 3)

        for col in range(6):
            grid.setColumnStretch(col, 1)

        layout.addWidget(feed)

        xmon = QGroupBox("Monitor X (likes, retweets, perfil, guardados)")
        xg = QGridLayout(xmon)

        self.x_actions_user_edit = QLineEdit()
        self.x_actions_poll_seconds_edit = QLineEdit()
        self.x_actions_bookmarks_chk = QCheckBox("X Guardados")
        self.x_actions_likes_chk = QCheckBox("X Likes")
        self.x_actions_retweets_chk = QCheckBox("X Retweets")
        self.x_actions_profile_chk = QCheckBox("X Perfil")

        xg.addWidget(QLabel("Usuario X (likes/retweets/perfil):"), 0, 0)
        xg.addWidget(self.x_actions_user_edit, 0, 1)
        xg.addWidget(QLabel("Chequeo (s):"), 0, 2)
        xg.addWidget(self.x_actions_poll_seconds_edit, 0, 3)

        xg.addWidget(self.x_actions_bookmarks_chk, 1, 0)
        xg.addWidget(self.x_actions_likes_chk, 1, 1)
        xg.addWidget(self.x_actions_retweets_chk, 1, 2)
        xg.addWidget(self.x_actions_profile_chk, 1, 3)

        btn_start_x = QPushButton("Iniciar Monitor X")
        btn_start_x.clicked.connect(lambda: self._run_legacy("start_x_actions_monitor"))
        btn_stop_x = QPushButton("Detener Monitor X")
        btn_stop_x.setObjectName("danger")
        btn_stop_x.clicked.connect(lambda: self._run_legacy("stop_x_actions_monitor"))

        xg.addWidget(btn_start_x, 2, 0, 1, 2)
        xg.addWidget(btn_stop_x, 2, 2, 1, 2)
        layout.addWidget(xmon)

        layout.addStretch(1)
        return page

    def _build_instances_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top = QGroupBox("Instancias Feed X")
        top_layout = QVBoxLayout(top)

        monitor_row = QHBoxLayout()
        self.monitor_combo = QComboBox()
        self.monitor_cookie_label = QLabel("Cookie monitor: -")
        self.monitor_cookie_label.setObjectName("hint")

        self.monitor_cookie_menu_btn = QPushButton("☰")
        self.monitor_cookie_menu_btn.setObjectName("soft")
        self.monitor_cookie_menu_btn.setFixedWidth(44)
        self.monitor_cookie_menu_btn.clicked.connect(self._show_monitor_cookie_menu)

        self.start_monitor_instance_btn = QPushButton("Start monitor")
        self.start_monitor_instance_btn.clicked.connect(self._start_selected_monitor_instance)

        monitor_row.addWidget(QLabel("Monitor:"))
        monitor_row.addWidget(self.monitor_combo, 1)
        monitor_row.addWidget(self.monitor_cookie_label, 2)
        monitor_row.addWidget(self.monitor_cookie_menu_btn)
        monitor_row.addWidget(self.start_monitor_instance_btn)
        top_layout.addLayout(monitor_row)

        global_row = QHBoxLayout()
        self.global_cookie_label = QLabel("Cookie global: -")
        self.global_cookie_label.setObjectName("hint")

        self.global_cookie_menu_btn = QPushButton("☰")
        self.global_cookie_menu_btn.setObjectName("soft")
        self.global_cookie_menu_btn.setFixedWidth(44)
        self.global_cookie_menu_btn.clicked.connect(self._show_global_cookie_menu)

        apply_global_btn = QPushButton("Aplicar global a monitores")
        apply_global_btn.setObjectName("soft")
        apply_global_btn.clicked.connect(lambda: self._run_legacy("_apply_global_cookie_to_all_monitors"))

        global_row.addWidget(self.global_cookie_label, 1)
        global_row.addWidget(self.global_cookie_menu_btn)
        global_row.addWidget(apply_global_btn)
        top_layout.addLayout(global_row)

        global_controls = QHBoxLayout()
        btn_prev_all = QPushButton("PREV global")
        btn_prev_all.setObjectName("soft")
        btn_prev_all.clicked.connect(lambda: self._run_legacy("_prev_all_twitter_instances"))
        btn_skip_all = QPushButton("Skip global")
        btn_skip_all.setObjectName("soft")
        btn_skip_all.clicked.connect(lambda: self._run_legacy("_skip_all_twitter_instances"))
        btn_f11_all = QPushButton("F11 global")
        btn_f11_all.setObjectName("soft")
        btn_f11_all.clicked.connect(lambda: self._run_legacy("_set_all_instances_fullscreen", True))
        btn_exit_f11 = QPushButton("Salir F11 global")
        btn_exit_f11.setObjectName("soft")
        btn_exit_f11.clicked.connect(lambda: self._run_legacy("_set_all_instances_fullscreen", False))
        btn_kill_all = QPushButton("Kill global")
        btn_kill_all.setObjectName("danger")
        btn_kill_all.clicked.connect(lambda: self._run_legacy("_kill_all_twitter_instances"))

        global_controls.addWidget(btn_prev_all)
        global_controls.addWidget(btn_skip_all)
        global_controls.addWidget(btn_f11_all)
        global_controls.addWidget(btn_exit_f11)
        global_controls.addWidget(btn_kill_all)
        top_layout.addLayout(global_controls)

        layout.addWidget(top)

        list_card = QGroupBox("Instancias activas (controles directos)")
        list_layout = QVBoxLayout(list_card)

        self.instances_scroll = QScrollArea()
        self.instances_scroll.setWidgetResizable(True)
        self.instances_scroll.setFrameShape(QFrame.NoFrame)

        self.instances_rows_host = QWidget()
        self.instances_rows_layout = QVBoxLayout(self.instances_rows_host)
        self.instances_rows_layout.setContentsMargins(2, 2, 2, 2)
        self.instances_rows_layout.setSpacing(8)
        self.instances_rows_layout.addStretch(1)

        self.instances_scroll.setWidget(self.instances_rows_host)
        list_layout.addWidget(self.instances_scroll)

        self.instance_hint_label = QLabel("No hay instancias activas. Usa Start monitor para crear una.")
        self.instance_hint_label.setObjectName("hint")
        list_layout.addWidget(self.instance_hint_label)
        layout.addWidget(list_card, 1)

        return page

    def _create_instance_row(self, instance_id: int) -> dict[str, Any]:
        card = QFrame()
        card.setObjectName("instanceCard")

        outer = QVBoxLayout(card)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        title = QLabel(f"Instancia #{int(instance_id)}")
        title.setObjectName("instanceTitle")
        meta = QLabel("Inicializando...")
        meta.setObjectName("instanceMeta")
        meta.setWordWrap(True)

        outer.addWidget(title)
        outer.addWidget(meta)

        config_row = QHBoxLayout()
        config_row.setSpacing(6)

        theme_label = QLabel("Tema: -")
        theme_label.setObjectName("instanceMeta")
        video_dir_label = QLabel("Videos: -")
        video_dir_label.setObjectName("instanceMeta")
        image_dir_label = QLabel("Imagenes: -")
        image_dir_label.setObjectName("instanceMeta")
        for label in (theme_label, video_dir_label, image_dir_label):
            label.setWordWrap(True)

        config_row.addWidget(theme_label, 1)
        config_row.addWidget(video_dir_label, 2)
        config_row.addWidget(image_dir_label, 2)

        outer.addLayout(config_row)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(6)

        def make_setting_btn(text: str, callback) -> QPushButton:
            btn = QPushButton(text)
            btn.setObjectName("soft")
            btn.clicked.connect(callback)
            settings_row.addWidget(btn)
            return btn

        btn_theme = make_setting_btn("Cambiar tema", lambda _checked=False, iid=int(instance_id): self._toggle_instance_theme(iid))
        btn_video_dir = make_setting_btn("Ruta videos", lambda _checked=False, iid=int(instance_id): self._pick_instance_video_output_dir(iid))
        btn_image_dir = make_setting_btn("Ruta imagenes", lambda _checked=False, iid=int(instance_id): self._pick_instance_image_output_dir(iid))

        settings_row.addStretch(1)
        outer.addLayout(settings_row)

        actions = QHBoxLayout()
        actions.setSpacing(6)

        def make_btn(text: str, method_name: str, danger: bool = False) -> QPushButton:
            btn = QPushButton(text)
            btn.setObjectName("danger" if danger else "soft")
            btn.clicked.connect(lambda _checked=False, iid=int(instance_id), m=method_name: self._run_legacy(m, iid))
            actions.addWidget(btn)
            return btn

        btn_pause = make_btn("Pausar", "_pause_resume_instance")
        btn_mute = make_btn("Mute", "_toggle_mute_instance")
        btn_f11 = make_btn("F11", "_set_instance_fullscreen")
        btn_like = make_btn("Like", "_like_instance_current_post")
        btn_rt = make_btn("Retweet", "_retweet_instance_current_post")
        btn_prev = make_btn("PREV", "_prev_instance")
        btn_skip = make_btn("Skip", "_skip_instance")
        btn_stop = make_btn("Detener", "_stop_instance", danger=True)
        btn_kill = make_btn("Kill", "_kill_instance", danger=True)

        actions.addStretch(1)
        outer.addLayout(actions)

        return {
            "card": card,
            "title": title,
            "meta": meta,
            "theme_label": theme_label,
            "video_dir_label": video_dir_label,
            "image_dir_label": image_dir_label,
            "btn_theme": btn_theme,
            "btn_video_dir": btn_video_dir,
            "btn_image_dir": btn_image_dir,
            "btn_pause": btn_pause,
            "btn_mute": btn_mute,
            "btn_f11": btn_f11,
            "btn_like": btn_like,
            "btn_rt": btn_rt,
            "btn_prev": btn_prev,
            "btn_skip": btn_skip,
            "btn_stop": btn_stop,
            "btn_kill": btn_kill,
        }

    def _update_instance_row(self, row: dict[str, Any], instance_id: int, item: dict[str, Any], cookie_text: str) -> None:
        scraper = item.get("scraper")
        running = bool(scraper and scraper.is_running())
        paused = bool(scraper and scraper.is_paused())
        muted = bool(scraper and scraper.is_muted())
        fullscreen = bool(scraper and scraper.is_window_fullscreen())
        theme_mode = str(item.get("theme_mode") or "dark").strip().lower() or "dark"
        video_dir = str(item.get("video_output_dir") or "").strip()
        image_dir = str(item.get("image_output_dir") or "").strip()

        status = "running" if running else "stopped"
        row["title"].setText(f"#{int(instance_id)} {item.get('name', 'X')} - {item.get('monitor_label', '-')}")
        row["meta"].setText(
            f"estado={status} | pausa={'si' if paused else 'no'} | mute={'si' if muted else 'no'} | "
            f"vista={'F11' if fullscreen else 'max'} | cookie={cookie_text}"
        )
        row["theme_label"].setText(f"Tema: {theme_mode}")
        row["video_dir_label"].setText(f"Videos: {video_dir or 'global'}")
        row["image_dir_label"].setText(f"Imagenes: {image_dir or 'global'}")
        row["btn_theme"].setText("Tema claro" if theme_mode == "dark" else "Tema oscuro")

        row["btn_pause"].setText("Reanudar" if paused else "Pausar")
        row["btn_mute"].setText("Unmute" if muted else "Mute")
        row["btn_f11"].setText("Salir F11" if fullscreen else "F11")

        action_keys = ["btn_pause", "btn_mute", "btn_f11", "btn_like", "btn_rt", "btn_prev", "btn_skip", "btn_stop"]
        for key in action_keys:
            row[key].setEnabled(running)
        row["btn_kill"].setEnabled(True)

    def _instance_current_theme(self, instance_id: int) -> str:
        item = self._get_instance_item(int(instance_id))
        if not item:
            return str(self.bridge.get_var("ui_theme_var", "dark") or "dark").strip().lower()
        theme_mode = str(item.get("theme_mode") or "dark").strip().lower()
        return theme_mode if theme_mode in {"dark", "light", "no-preference"} else "dark"

    def _toggle_instance_theme(self, instance_id: int) -> None:
        item = self._get_instance_item(int(instance_id))
        if not item:
            return
        current = str(item.get("theme_mode") or "dark").strip().lower()
        next_theme = "light" if current == "dark" else "dark"
        item["theme_mode"] = next_theme
        scraper = item.get("scraper")
        if scraper:
            try:
                scraper.set_browser_color_scheme(next_theme)
            except Exception as exc:
                self._log(f"Aviso tema instancia {item.get('name')}: {exc}")
        self._refresh_instances_list()

    def _pick_instance_video_output_dir(self, instance_id: int) -> None:
        self._pick_instance_directory(int(instance_id), is_image=False)

    def _pick_instance_image_output_dir(self, instance_id: int) -> None:
        self._pick_instance_directory(int(instance_id), is_image=True)

    def _pick_instance_directory(self, instance_id: int, is_image: bool) -> None:
        item = self._get_instance_item(int(instance_id))
        if not item:
            return

        current = str(item.get("image_output_dir" if is_image else "video_output_dir") or "").strip()
        initial = current or (self.cookies_folder_edit.text().strip() or os.path.dirname(os.path.abspath(__file__)))
        selected = QFileDialog.getExistingDirectory(
            self,
            "Selecciona carpeta de imagenes" if is_image else "Selecciona carpeta de videos",
            initial,
        )
        if not selected:
            return

        key = "image_output_dir" if is_image else "video_output_dir"
        item[key] = selected
        self._log(f"Ruta {key} para {item.get('name')}: {selected}")
        self._refresh_instances_list()

    def _build_activity_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        log_box = QGroupBox("Log de actividad")
        log_layout = QVBoxLayout(log_box)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_box, 1)

        actions = QHBoxLayout()
        clear_btn = QPushButton("Limpiar")
        clear_btn.setObjectName("soft")
        clear_btn.clicked.connect(self.log_text.clear)
        actions.addWidget(clear_btn)

        open_activity = QPushButton("Abrir activity.log")
        open_activity.setObjectName("soft")
        open_activity.clicked.connect(lambda: self._open_path(getattr(self.bridge.app, "log_file_path", "")))
        actions.addWidget(open_activity)

        open_downloader = QPushButton("Abrir carpeta downloader")
        open_downloader.setObjectName("soft")
        open_downloader.clicked.connect(lambda: self._open_path(os.path.dirname(os.path.abspath(__file__))))
        actions.addWidget(open_downloader)

        open_live = QPushButton("Log en vivo")
        open_live.setObjectName("soft")
        open_live.clicked.connect(lambda: self._run_legacy("open_live_log_window"))
        actions.addWidget(open_live)

        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _bind_data_to_legacy(self) -> None:
        self._bind_line_edit(self.url_edit, "url_var")
        self._bind_line_edit(self.output_dir_edit, "output_dir_var")
        self._bind_line_edit(self.image_output_edit, "image_output_dir_var")
        self._bind_line_edit(self.image_url_edit, "image_url_var")
        self._bind_line_edit(self.instagram_edit, "instagram_url_var")
        self._bind_line_edit(self.twitter_edit, "twitter_url_var")
        self._bind_line_edit(self.max_size_edit, "max_size_var")
        self._bind_line_edit(self.start_edit, "start_var")
        self._bind_line_edit(self.end_edit, "end_var")
        self._bind_line_edit(self.cookies_file_edit, "cookies_file_var")
        self._bind_line_edit(self.cookies_folder_edit, "cookies_folder_var")
        self._bind_line_edit(self.feed_image_seconds_edit, "feed_image_seconds_var")
        self._bind_line_edit(self.feed_scroll_pause_edit, "feed_scroll_pause_var")
        self._bind_line_edit(self.feed_scroll_px_edit, "feed_scroll_px_var")
        self._bind_line_edit(self.feed_max_video_wait_edit, "feed_max_video_wait_var")
        self._bind_line_edit(self.x_actions_user_edit, "x_actions_user_var")
        self._bind_line_edit(self.x_actions_poll_seconds_edit, "x_actions_poll_seconds_var")

        self._bind_checkbox(self.include_subtitles_chk, "include_subtitles_var")
        self._bind_checkbox(self.embed_subtitles_chk, "embed_subtitles_var")
        self._bind_checkbox(self.use_cookies_chk, "use_cookies_var")
        self._bind_checkbox(self.auto_save_chk, "auto_save_defaults_var")
        self._bind_checkbox(self.remember_pos_chk, "remember_window_position_var")
        self._bind_checkbox(self.start_with_windows_chk, "start_with_windows_var")
        self._bind_checkbox(self.clipboard_monitor_chk, "clipboard_monitor_var")
        self._bind_checkbox(self.feed_wait_video_end_chk, "feed_wait_video_end_var")
        self._bind_checkbox(self.feed_tiktok_likes_only_chk, "feed_tiktok_likes_only_var")
        self._bind_checkbox(self.feed_twitter_creator_folders_chk, "feed_twitter_creator_folders_var")
        self._bind_checkbox(self.x_actions_bookmarks_chk, "x_actions_bookmarks_var")
        self._bind_checkbox(self.x_actions_likes_chk, "x_actions_likes_var")
        self._bind_checkbox(self.x_actions_retweets_chk, "x_actions_retweets_var")
        self._bind_checkbox(self.x_actions_profile_chk, "x_actions_profile_var")

        self._bind_combo(self.compression_combo, "compression_var")
        self._bind_combo(self.cookies_browser_combo, "cookies_browser_var")
        self._bind_combo(self.language_combo, "selected_language_var")
        self._bind_combo(self.quality_combo, "selected_quality_var")
        self._bind_combo(self.audio_quality_combo, "selected_audio_quality_var")
        self._bind_combo(self.subtitle_lang_combo, "subtitle_lang_var")

        lang_code = str(self.bridge.get_var("ui_language_var", "es")).strip().lower()
        self.ui_language_combo.setCurrentIndex(0 if lang_code == "es" else 1)
        self.ui_language_combo.currentIndexChanged.connect(self._on_ui_language_combo_changed)

        theme_code = str(self.bridge.get_var("ui_theme_var", "dark") or "dark").strip().lower()
        self.ui_theme_combo.setCurrentIndex(1 if theme_code == "light" else 0)
        self.ui_theme_combo.currentIndexChanged.connect(self._on_ui_theme_combo_changed)

        self.auto_save_chk.toggled.connect(lambda _v: self._legacy_hook("_on_auto_save_defaults_toggle"))
        self.remember_pos_chk.toggled.connect(lambda _v: self._legacy_hook("_schedule_window_geometry_save"))
        self.start_with_windows_chk.toggled.connect(lambda _v: self._legacy_hook("_on_start_with_windows_toggle"))
        self.monitor_combo.currentIndexChanged.connect(lambda _idx: self._refresh_cookie_labels())

    def _bind_line_edit(self, widget: QLineEdit, var_name: str) -> None:
        widget.setText(str(self.bridge.get_var(var_name, "")))
        widget.textChanged.connect(lambda text, name=var_name: self.bridge.set_var(name, text))

    def _bind_checkbox(self, widget: QCheckBox, var_name: str) -> None:
        widget.setChecked(bool(self.bridge.get_var(var_name, False)))
        widget.toggled.connect(lambda checked, name=var_name: self.bridge.set_var(name, bool(checked)))

    def _bind_combo(self, widget: QComboBox, var_name: str) -> None:
        current = str(self.bridge.get_var(var_name, ""))
        if current and widget.findText(current) < 0:
            widget.addItem(current)
        if current:
            widget.setCurrentText(current)
        widget.currentTextChanged.connect(lambda text, name=var_name: self.bridge.set_var(name, text))

    def _legacy_hook(self, method_name: str) -> None:
        if self._ui_loading:
            return
        try:
            self.bridge.call(method_name)
        except Exception as exc:
            self._append_local_log(f"WARN hook {method_name}: {exc}")

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    def _on_ui_language_combo_changed(self, _index: int) -> None:
        lang_code = "es" if self.ui_language_combo.currentIndex() == 0 else "en"
        self.bridge.set_var("ui_language_var", lang_code)
        if not self._ui_loading:
            self._run_legacy("_on_ui_language_change")

    def _on_ui_theme_combo_changed(self, _index: int) -> None:
        theme_code = "light" if self.ui_theme_combo.currentIndex() == 1 else "dark"
        self._apply_theme(theme_code)

    def _paste_into(self, target: QLineEdit) -> None:
        target.setText(QApplication.clipboard().text().strip())

    def _pick_directory_for(self, target: QLineEdit, var_name: str) -> None:
        initial = target.text().strip() or os.path.dirname(os.path.abspath(__file__))
        selected = QFileDialog.getExistingDirectory(self, "Selecciona carpeta", initial)
        if selected:
            target.setText(selected)
            self.bridge.set_var(var_name, selected)

    def _pick_cookie_file(self) -> None:
        initial_dir = self.cookies_folder_edit.text().strip() or os.path.dirname(os.path.abspath(__file__))
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Selecciona cookies",
            initial_dir,
            "Cookies (*.txt *.json);;Todos (*.*)",
        )
        if selected:
            self.cookies_file_edit.setText(selected)
            self.bridge.set_var("cookies_file_var", selected)

    def _pick_cookie_folder(self) -> None:
        self._pick_directory_for(self.cookies_folder_edit, "cookies_folder_var")

    def _run_legacy(self, method_name: str, *args) -> None:
        try:
            self.bridge.call(method_name, *args)
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Error en {method_name}:\n{exc}")
            self._append_local_log(f"ERROR {method_name}: {exc}")

    def _current_monitor_id(self) -> int | None:
        data = self.monitor_combo.currentData()
        if data is None:
            return None
        return int(data)

    def _start_selected_monitor_instance(self) -> None:
        monitor_id = self._current_monitor_id()
        if monitor_id is None:
            QMessageBox.warning(self, APP_TITLE, "No hay monitores detectados")
            return
        self._run_legacy("_start_twitter_feed_instance", int(monitor_id), None)

    def _show_monitor_cookie_menu(self) -> None:
        monitor_id = self._current_monitor_id()
        if monitor_id is None:
            QMessageBox.warning(self, APP_TITLE, "No hay monitor seleccionado")
            return

        menu = QMenu(self)

        use_global = menu.addAction("Seleccionar: usar cookie global")
        use_global.triggered.connect(lambda: self._run_legacy("_clear_monitor_cookie_choice", int(monitor_id)))

        no_cookie = menu.addAction("Seleccionar: sin cookies")
        no_cookie.triggered.connect(lambda: self._run_legacy("_set_monitor_cookie_choice", int(monitor_id), ""))

        pool = list(self.bridge.call("_cookie_pool_files") or [])
        if pool:
            menu.addSeparator()
            for path in pool:
                label = str(self.bridge.call("_cookie_label", path))
                action = menu.addAction(label)
                action.triggered.connect(lambda _checked=False, p=path: self._run_legacy("_set_monitor_cookie_choice", int(monitor_id), p))

        menu.addSeparator()
        manual = menu.addAction("Elegir cookies manualmente...")
        manual.triggered.connect(lambda: self._choose_cookie_for_monitor(int(monitor_id)))

        menu.exec_(self.monitor_cookie_menu_btn.mapToGlobal(QPoint(0, self.monitor_cookie_menu_btn.height())))

    def _show_global_cookie_menu(self) -> None:
        menu = QMenu(self)

        no_cookie = menu.addAction("Seleccionar global: sin cookies")
        no_cookie.triggered.connect(lambda: self._run_legacy("_set_global_cookie_choice", ""))

        pool = list(self.bridge.call("_cookie_pool_files") or [])
        if pool:
            menu.addSeparator()
            for path in pool:
                label = str(self.bridge.call("_cookie_label", path))
                action = menu.addAction(label)
                action.triggered.connect(lambda _checked=False, p=path: self._run_legacy("_set_global_cookie_choice", p))

        menu.addSeparator()
        manual = menu.addAction("Elegir cookie global manualmente...")
        manual.triggered.connect(self._choose_global_cookie)

        menu.exec_(self.global_cookie_menu_btn.mapToGlobal(QPoint(0, self.global_cookie_menu_btn.height())))

    def _choose_cookie_for_monitor(self, monitor_id: int) -> None:
        initial_dir = self.cookies_folder_edit.text().strip() or os.path.dirname(os.path.abspath(__file__))
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Selecciona cookies para monitor",
            initial_dir,
            "Cookies (*.txt *.json);;Todos (*.*)",
        )
        if selected:
            self._run_legacy("_set_monitor_cookie_choice", int(monitor_id), selected)

    def _choose_global_cookie(self) -> None:
        initial_dir = self.cookies_folder_edit.text().strip() or os.path.dirname(os.path.abspath(__file__))
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Selecciona cookie global",
            initial_dir,
            "Cookies (*.txt *.json);;Todos (*.*)",
        )
        if selected:
            self._run_legacy("_set_global_cookie_choice", selected)

    def _refresh_from_legacy(self) -> None:
        self.bridge.process_due_callbacks()
        self._append_legacy_logs()
        self._refresh_dynamic_combos()
        self._refresh_monitor_combo()
        self._refresh_cookie_labels()
        self._refresh_instances_list()

    def _append_legacy_logs(self) -> None:
        history = list(getattr(self.bridge.app, "log_history", []))
        if self._last_log_count > len(history):
            self._last_log_count = 0

        if self._last_log_count < len(history):
            for line in history[self._last_log_count:]:
                self.log_text.moveCursor(QTextCursor.End)
                self.log_text.insertPlainText(str(line) + "\n")
            self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
            self._last_log_count = len(history)

    def _refresh_dynamic_combos(self) -> None:
        self.duration_label.setText(str(self.bridge.get_var("duration_var", "Duracion: -")))

        self._set_combo_values(
            "language",
            self.language_combo,
            list(getattr(self.bridge.app, "available_languages", ["auto"])),
            str(self.bridge.get_var("selected_language_var", "auto")),
        )
        self._set_combo_values(
            "quality",
            self.quality_combo,
            list(getattr(self.bridge.app, "available_qualities", ["best"])),
            str(self.bridge.get_var("selected_quality_var", "best")),
        )
        self._set_combo_values(
            "audio_quality",
            self.audio_quality_combo,
            list(getattr(self.bridge.app, "available_audio_qualities", ["best audio"])),
            str(self.bridge.get_var("selected_audio_quality_var", "best audio")),
        )
        self._set_combo_values(
            "subtitle_lang",
            self.subtitle_lang_combo,
            list(getattr(self.bridge.app, "available_subtitle_languages", ["auto", "all"])),
            str(self.bridge.get_var("subtitle_lang_var", "auto")),
        )

    def _set_combo_values(self, key: str, combo: QComboBox, values: list[str], selected: str) -> None:
        normalized = tuple(str(v) for v in values if str(v).strip())
        signature = (normalized, str(selected))
        if self._combo_signatures.get(key) == signature:
            return

        self._combo_signatures[key] = signature
        combo.blockSignals(True)
        current_before = combo.currentText()
        combo.clear()
        combo.addItems(list(normalized) if normalized else [selected or ""])

        target = selected or current_before
        if target:
            idx = combo.findText(target)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.addItem(target)
                combo.setCurrentIndex(combo.findText(target))
        combo.blockSignals(False)

        if target:
            self.bridge.set_var(
                {
                    self.language_combo: "selected_language_var",
                    self.quality_combo: "selected_quality_var",
                    self.audio_quality_combo: "selected_audio_quality_var",
                    self.subtitle_lang_combo: "subtitle_lang_var",
                }[combo],
                target,
            )

    def _refresh_monitor_combo(self) -> None:
        monitors = list(getattr(self.bridge.app, "monitors", []))
        signature = tuple((int(m.get("id", 0)), str(m.get("label", ""))) for m in monitors)
        if signature == self._monitor_signature:
            return

        self._monitor_signature = signature
        selected_id = self._current_monitor_id()

        self.monitor_combo.blockSignals(True)
        self.monitor_combo.clear()
        for monitor in monitors:
            monitor_id = int(monitor.get("id", 0))
            label = str(monitor.get("label") or f"Monitor {monitor_id}")
            self.monitor_combo.addItem(label, monitor_id)
        self.monitor_combo.blockSignals(False)

        if selected_id is not None:
            idx = self.monitor_combo.findData(selected_id)
            if idx >= 0:
                self.monitor_combo.setCurrentIndex(idx)

    def _refresh_cookie_labels(self) -> None:
        monitor_id = self._current_monitor_id()
        if monitor_id is not None:
            text = str(self.bridge.call("_monitor_cookie_display", int(monitor_id)))
            self.monitor_cookie_label.setText(f"Cookie monitor: {text}")
        else:
            self.monitor_cookie_label.setText("Cookie monitor: -")

        global_cookie = str(self.bridge.call("_selected_global_cookie") or "")
        global_text = str(self.bridge.call("_cookie_label", global_cookie))
        self.global_cookie_label.setText(f"Cookie global: {global_text}")

    def _refresh_instances_list(self) -> None:
        app = self.bridge.app
        with app.twitter_instances_lock:
            items = sorted(app.twitter_instances.items(), key=lambda entry: int(entry[0]))

        signature_parts = []
        snapshot: list[tuple[int, dict[str, Any], str]] = []
        for instance_id, item in items:
            scraper = item.get("scraper")
            running = bool(scraper and scraper.is_running())
            paused = bool(scraper and scraper.is_paused())
            muted = bool(scraper and scraper.is_muted())
            fullscreen = bool(scraper and scraper.is_window_fullscreen())
            cookie_file = str(item.get("cookie_file") or "")
            cookie_text = str(self.bridge.call("_cookie_label", cookie_file))
            status = "running" if running else "stopped"
            signature_parts.append((int(instance_id), status, paused, muted, fullscreen, cookie_text))
            snapshot.append((int(instance_id), item, cookie_text))

        signature = tuple(signature_parts)
        if signature == self._instances_signature:
            return

        self._instances_signature = signature
        live_ids: set[int] = set()

        for instance_id, item, cookie_text in snapshot:
            live_ids.add(int(instance_id))
            row = self.instance_rows.get(int(instance_id))
            if row is None:
                row = self._create_instance_row(int(instance_id))
                self.instance_rows[int(instance_id)] = row
                insert_index = max(0, self.instances_rows_layout.count() - 1)
                self.instances_rows_layout.insertWidget(insert_index, row["card"])
            self._update_instance_row(row, int(instance_id), item, cookie_text)

        stale_ids = [iid for iid in self.instance_rows.keys() if iid not in live_ids]
        for stale_id in stale_ids:
            row = self.instance_rows.pop(int(stale_id), None)
            card = row.get("card") if row else None
            if card is not None:
                self.instances_rows_layout.removeWidget(card)
                card.deleteLater()

        total_active = len(snapshot)
        if total_active:
            self.instance_hint_label.setText(f"Instancias activas: {total_active}. Usa los botones en cada fila.")
        else:
            self.instance_hint_label.setText("No hay instancias activas. Usa Start monitor para crear una.")

    def _append_local_log(self, text: str) -> None:
        self.log_text.moveCursor(QTextCursor.End)
        self.log_text.insertPlainText(str(text) + "\n")
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def _engine_file_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloader.py")

    def _open_path(self, path: str) -> None:
        if not path:
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            QMessageBox.warning(self, APP_TITLE, f"No se pudo abrir:\n{path}\n\n{exc}")

    def _restart_self(self) -> None:
        try:
            script = os.path.abspath(__file__)
            subprocess.Popen([sys.executable, script], cwd=os.path.dirname(script))
            self.close()
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"No se pudo reiniciar: {exc}")

    def _prepare_shutdown(self) -> None:
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        try:
            self.refresh_timer.stop()
        except Exception:
            pass
        self.bridge.shutdown()

    def closeEvent(self, event) -> None:
        self._prepare_shutdown()
        super().closeEvent(event)


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 12))

    window = MainWindow()
    app.aboutToQuit.connect(window._prepare_shutdown)
    window.showMaximized()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()