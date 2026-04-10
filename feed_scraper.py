import os
import threading
import time
import json
import re
from enum import Enum
from typing import Callable


class Platform(str, Enum):
  INSTAGRAM = "instagram"
  TIKTOK = "tiktok"
  TWITTER = "twitter"
  YOUTUBE = "youtube"

  @classmethod
  def parse(cls, value: str | "Platform") -> "Platform":
    if isinstance(value, cls):
      return value
    clean = (value or "").strip().lower()
    for item in cls:
      if item.value == clean:
        return item
    return cls.INSTAGRAM


class FeedScraper:
  def __init__(
    self,
    on_url_detected: Callable[[str], None],
    poll_seconds: float = 1.5,
    scroll_px: int = 900,
    cookies_file: str = "",
    image_dwell_seconds: float = 10.0,
    scroll_pause_seconds: float = 1.5,
    wait_video_end: bool = True,
    max_video_wait_seconds: float = 300.0,
    only_visible: bool = True,
    start_maximized: bool = True,
    tiktok_likes_only: bool = False,
    monitor_bounds: dict | None = None,
    instance_name: str = "",
    cookie_candidates: list[str] | None = None,
    browser_color_scheme: str = "dark",
  ):
    self.on_url_detected = on_url_detected
    self.poll_seconds = max(0.2, float(poll_seconds or 1.5))
    self.scroll_px = max(100, int(scroll_px or 900))
    self.cookies_file = (cookies_file or "").strip()
    self.image_dwell_seconds = max(1.0, float(image_dwell_seconds or 10.0))
    self.scroll_pause_seconds = max(0.2, float(scroll_pause_seconds or 1.5))
    self.wait_video_end = bool(wait_video_end)
    self.max_video_wait_seconds = max(5.0, float(max_video_wait_seconds or 300.0))
    self.only_visible = bool(only_visible)
    self.start_maximized = bool(start_maximized)
    self.tiktok_likes_only = bool(tiktok_likes_only)
    self.monitor_bounds = monitor_bounds if isinstance(monitor_bounds, dict) else None
    self.instance_name = (instance_name or "").strip()
    self.cookie_candidates = [str(item).strip() for item in (cookie_candidates or []) if str(item).strip()]
    self._browser_color_scheme = self._normalize_color_scheme(browser_color_scheme)

    self._log_callback: Callable[[str], None] | None = None
    self._stop_event = threading.Event()
    self._thread: threading.Thread | None = None
    self._seen_urls: set[str] = set()
    self._deps_verified = False
    self._pause_event = threading.Event()
    self._skip_event = threading.Event()
    self._prev_event = threading.Event()
    self._prev_requests = 0
    self._nav_queue: list[str] = []
    self._pending_nav_from_wait: str | None = None
    self._kill_event = threading.Event()
    self._action_like_event = threading.Event()
    self._action_retweet_event = threading.Event()
    self._state_lock = threading.Lock()
    self._muted = False
    self._window_fullscreen = False
    self._pending_window_state: bool | None = None
    self._prev_nav_cooldown_until = 0.0
    self._last_home_guard_log_ts = 0.0
    self._twitter_focus_status_id = ""
    self._runtime_context = None
    self._runtime_page = None
    self._last_error_summary = ""
    self._last_error_log_ts = 0.0
    self._blocked_popup_count = 0
    self._last_popup_block_log_ts = 0.0

  def _normalize_color_scheme(self, value: str) -> str:
    clean = str(value or "").strip().lower()
    if clean not in {"dark", "light", "no-preference"}:
      return "dark"
    return clean

  def set_browser_color_scheme(self, value: str) -> None:
    with self._state_lock:
      self._browser_color_scheme = self._normalize_color_scheme(value)
      page = self._runtime_page
    if page is not None:
      try:
        page.emulate_media(color_scheme=None if self._browser_color_scheme == "no-preference" else self._browser_color_scheme)
      except Exception:
        pass

  def _apply_browser_color_scheme(self, page) -> None:
    scheme = self._normalize_color_scheme(self._browser_color_scheme)
    try:
      page.emulate_media(color_scheme=None if scheme == "no-preference" else scheme)
    except Exception:
      pass

  def set_log_callback(self, callback: Callable[[str], None]) -> None:
    self._log_callback = callback

  def _log(self, message: str) -> None:
    if self._log_callback:
      prefix = f"[{self.instance_name}] " if self.instance_name else ""
      self._log_callback(prefix + message)

  def is_running(self) -> bool:
    return bool(self._thread and self._thread.is_alive())

  def is_paused(self) -> bool:
    return self._pause_event.is_set()

  def pause(self) -> None:
    self._pause_event.set()
    self._log("Scraper en pausa")

  def resume(self) -> None:
    self._pause_event.clear()
    self._log("Scraper reanudado")

  def toggle_pause(self) -> bool:
    if self.is_paused():
      self.resume()
    else:
      self.pause()
    return self.is_paused()

  def set_muted(self, muted: bool) -> None:
    with self._state_lock:
      self._muted = bool(muted)
    self._log("Audio: mute" if muted else "Audio: unmute")

  def is_muted(self) -> bool:
    with self._state_lock:
      return bool(self._muted)

  def is_window_fullscreen(self) -> bool:
    with self._state_lock:
      return bool(self._window_fullscreen)

  def set_window_fullscreen(self, enabled: bool) -> None:
    target = bool(enabled)
    with self._state_lock:
      if self._window_fullscreen == target and self._pending_window_state == target:
        return
      self._window_fullscreen = target
      self._pending_window_state = target

    state = "fullscreen" if target else "maximizada"
    self._log(f"Ventana: {state}")

  def toggle_window_fullscreen(self) -> bool:
    with self._state_lock:
      self._window_fullscreen = not self._window_fullscreen
      target = self._window_fullscreen
      self._pending_window_state = target

    state = "fullscreen" if target else "maximizada"
    self._log(f"Ventana: {state}")
    return target

  def toggle_muted(self) -> bool:
    with self._state_lock:
      self._muted = not self._muted
      muted = self._muted
    self._log("Audio: mute" if muted else "Audio: unmute")
    return muted

  def update_runtime_settings(
    self,
    poll_seconds: float | None = None,
    scroll_pause_seconds: float | None = None,
    scroll_px: int | None = None,
    image_dwell_seconds: float | None = None,
    wait_video_end: bool | None = None,
    max_video_wait_seconds: float | None = None,
    tiktok_likes_only: bool | None = None,
  ) -> bool:
    changed = False
    with self._state_lock:
      if poll_seconds is not None:
        new_poll = max(0.2, float(poll_seconds))
        if abs(new_poll - self.poll_seconds) > 0.0001:
          self.poll_seconds = new_poll
          changed = True

      if scroll_pause_seconds is not None:
        new_pause = max(0.2, float(scroll_pause_seconds))
        if abs(new_pause - self.scroll_pause_seconds) > 0.0001:
          self.scroll_pause_seconds = new_pause
          changed = True

      if scroll_px is not None:
        new_scroll = max(100, int(scroll_px))
        if new_scroll != self.scroll_px:
          self.scroll_px = new_scroll
          changed = True

      if image_dwell_seconds is not None:
        new_dwell = max(1.0, float(image_dwell_seconds))
        if abs(new_dwell - self.image_dwell_seconds) > 0.0001:
          self.image_dwell_seconds = new_dwell
          changed = True

      if wait_video_end is not None:
        new_wait_end = bool(wait_video_end)
        if new_wait_end != self.wait_video_end:
          self.wait_video_end = new_wait_end
          changed = True

      if max_video_wait_seconds is not None:
        new_max_wait = max(5.0, float(max_video_wait_seconds))
        if abs(new_max_wait - self.max_video_wait_seconds) > 0.0001:
          self.max_video_wait_seconds = new_max_wait
          changed = True

      if tiktok_likes_only is not None:
        new_tiktok_mode = bool(tiktok_likes_only)
        if new_tiktok_mode != self.tiktok_likes_only:
          self.tiktok_likes_only = new_tiktok_mode
          changed = True

    return changed

  def request_skip(self) -> None:
    with self._state_lock:
      self._nav_queue.append("skip")
      self._sync_nav_flags_locked()

  def request_prev(self) -> None:
    with self._state_lock:
      self._nav_queue.append("prev")
      self._prev_requests += 1
      self._sync_nav_flags_locked()

  def request_like_current_twitter_post(self) -> bool:
    if not self.is_running():
      return False
    self._action_like_event.set()
    self._log("Accion solicitada: Like al post actual")
    return True

  def request_retweet_current_twitter_post(self) -> bool:
    if not self.is_running():
      return False
    self._action_retweet_event.set()
    self._log("Accion solicitada: Retweet al post actual")
    return True

  def _arm_prev_nav_cooldown(self, seconds: float = 0.8) -> None:
    self._prev_nav_cooldown_until = max(
      self._prev_nav_cooldown_until,
      time.time() + max(0.2, float(seconds or 0.0)),
    )

  def kill(self) -> None:
    self._kill_event.set()
    self._stop_event.set()
    self._action_like_event.clear()
    self._action_retweet_event.clear()
    self._twitter_focus_status_id = ""
    try:
      if self._runtime_context is not None:
        self._runtime_context.close()
    except Exception:
      pass
    if self._thread and self._thread.is_alive():
      self._thread.join(timeout=2)

  def _is_module_available(self, import_name: str) -> bool:
    try:
      __import__(import_name)
      return True
    except Exception:
      return False

  def _verify_runtime_dependencies(self) -> bool:
    if self._deps_verified:
      return True

    self._log("Scraper: verificando dependencias runtime...")
    if not self._is_module_available("playwright"):
      self._log("Scraper: falta playwright en el entorno actual.")
      self._log("Instala dependencias con: python -m pip install -r requirements.txt")
      return False

    self._deps_verified = True
    return True

  def start(self, platform: str | Platform) -> None:
    if self._thread and self._thread.is_alive():
      self._log("Scraper ya esta en ejecucion")
      return

    if not self._verify_runtime_dependencies():
      return

    self._stop_event.clear()
    self._kill_event.clear()
    with self._state_lock:
      self._nav_queue.clear()
      self._pending_nav_from_wait = None
      self._prev_requests = 0
      self._sync_nav_flags_locked()
    self._pause_event.clear()
    self._action_like_event.clear()
    self._action_retweet_event.clear()
    self._twitter_focus_status_id = ""
    selected = Platform.parse(platform)
    self._thread = threading.Thread(target=self._run, args=(selected,), daemon=True)
    self._thread.start()

  def stop(self) -> None:
    self._stop_event.set()
    with self._state_lock:
      self._nav_queue.append("skip")
      self._prev_requests = max(1, self._prev_requests)
      self._nav_queue.append("prev")
      self._sync_nav_flags_locked()
    self._action_like_event.clear()
    self._action_retweet_event.clear()
    self._twitter_focus_status_id = ""
    if self._thread and self._thread.is_alive():
      self._thread.join(timeout=5)

  def _wait_if_paused(self, page=None) -> bool:
    while self._pause_event.is_set() and not self._stop_event.is_set() and not self._kill_event.is_set():
      if page is not None:
        try:
          self._apply_pending_window_state(page)
        except Exception:
          pass
        try:
          self._sync_page_mute_state(page)
        except Exception:
          pass
      time.sleep(0.15)
    return bool(self._stop_event.is_set() or self._kill_event.is_set())

  def _wait_with_interrupt(self, seconds: float, page=None) -> str:
    end_at = time.time() + max(0.0, float(seconds or 0.0))
    while time.time() < end_at:
      if self._stop_event.is_set() or self._kill_event.is_set():
        return "stop"
      if self._wait_if_paused(page):
        return "stop"
      next_action = self._dequeue_nav_action_for_wait()
      if next_action == "skip":
        self._consume_skip_request()
        return "skip"
      if next_action == "prev":
        return next_action
      if page is not None:
        try:
          self._close_runtime_extra_pages(page, quiet=True)
        except Exception:
          pass
        try:
          self._apply_pending_window_state(page)
        except Exception:
          pass
        try:
          self._sync_page_mute_state(page)
        except Exception:
          pass
        try:
          self._dismiss_translation_popups(page)
        except Exception:
          pass
        try:
          self._drain_pending_user_actions(page)
        except Exception:
          pass
      time.sleep(0.12)
    return "done"

  def _consume_prev_request(self) -> bool:
    with self._state_lock:
      if self._pending_nav_from_wait == "prev":
        self._pending_nav_from_wait = None
        pending = self._prev_requests
        self._sync_nav_flags_locked()
        self._log(f"PREV consumido (pendientes={pending})")
        return True
      if not self._nav_queue or self._nav_queue[0] != "prev":
        self._sync_nav_flags_locked()
        return False

      self._nav_queue.pop(0)
      self._prev_requests = max(0, self._prev_requests - 1)
      pending = self._prev_requests
      self._sync_nav_flags_locked()
    self._log(f"PREV consumido (pendientes={pending})")
    return True

  def _consume_skip_request(self) -> bool:
    with self._state_lock:
      if self._pending_nav_from_wait == "skip":
        self._pending_nav_from_wait = None
        self._sync_nav_flags_locked()
        return True
      if not self._nav_queue or self._nav_queue[0] != "skip":
        self._sync_nav_flags_locked()
        return False
      self._nav_queue.pop(0)
      self._sync_nav_flags_locked()
      return True

  def _sync_nav_flags_locked(self) -> None:
    has_skip = any(action == "skip" for action in self._nav_queue) or self._pending_nav_from_wait == "skip"
    has_prev = (self._prev_requests > 0) or any(action == "prev" for action in self._nav_queue) or self._pending_nav_from_wait == "prev"
    if has_skip:
      self._skip_event.set()
    else:
      self._skip_event.clear()
    if has_prev:
      self._prev_event.set()
    else:
      self._prev_event.clear()

  def _dequeue_nav_action_for_wait(self) -> str | None:
    with self._state_lock:
      if self._pending_nav_from_wait in {"skip", "prev"}:
        return self._pending_nav_from_wait
      if not self._nav_queue:
        self._sync_nav_flags_locked()
        return None

      action = self._nav_queue.pop(0)
      if action == "prev":
        self._prev_requests = max(0, self._prev_requests - 1)
      self._pending_nav_from_wait = action
      self._sync_nav_flags_locked()
      return action

  def _dismiss_translation_popups(self, page) -> None:
    _ = page.evaluate(
      """
      () => {
        const roots = Array.from(document.querySelectorAll('button, div[role="button"], span, a'));
        const normalize = (value) => String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
        const actionHints = [
          'not now',
          'no thanks',
          'ahora no',
          'no traducir',
          'mostrar original',
          'show original',
          'keep original',
          'never translate',
          'nunca traducir',
        ];
        const translateHints = [
          'translate',
          'translation',
          'traducir',
          'traduccion',
          'idioma',
          'language',
        ];

        for (const node of roots) {
          const txt = normalize(node.innerText || node.textContent || node.getAttribute('aria-label') || '');
          if (!txt) continue;
          if (!actionHints.some((hint) => txt.includes(hint))) continue;

          // Avoid closing unrelated overlays (like image viewers).
          const container = node.closest('[role="dialog"], [aria-modal="true"], div');
          const containerText = normalize(container ? (container.innerText || container.textContent || '') : txt);
          if (!translateHints.some((hint) => containerText.includes(hint) || txt.includes(hint))) continue;

          try {
            node.click();
            return true;
          } catch {}
        }
        return false;
      }
      """
    )

  def _drain_pending_user_actions(self, page) -> None:
    if self._action_like_event.is_set():
      self._action_like_event.clear()
      self._like_current_twitter_post(page)

    if self._action_retweet_event.is_set():
      self._action_retweet_event.clear()
      self._retweet_current_twitter_post(page)

  def _status_id_from_url(self, value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
      return ""
    match = re.search(r"/status/(\d+)", clean, flags=re.IGNORECASE)
    if not match:
      return ""
    return str(match.group(1) or "").strip()

  def _resolve_action_target_status_id(self, page) -> str:
    page_url = ""
    try:
      page_url = str(page.url or "").strip()
    except Exception:
      page_url = ""

    from_page = self._status_id_from_url(page_url)
    if from_page:
      return from_page

    from_focus = self._status_id_from_url(self._twitter_focus_status_id)
    if from_focus:
      return from_focus

    return ""

  def _like_current_twitter_post(self, page) -> None:
    target_status_id = self._resolve_action_target_status_id(page)
    if not target_status_id:
      self._log("Accion Like: no se pudo resolver tweet objetivo")
      return

    result = page.evaluate(
      """
      (targetStatusId) => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const normalizedTarget = String(targetStatusId || '').trim();
        if (!normalizedTarget) return { ok: false, reason: 'no-target-status-id' };

        const statusIdFrom = (href) => {
          const m = String(href || '').match(/\/status\/(\d+)/i);
          return m ? String(m[1]) : '';
        };

        const articles = Array.from(document.querySelectorAll('article'));
        let target = null;
        let best = Number.POSITIVE_INFINITY;

        for (const article of articles) {
          const links = Array.from(article.querySelectorAll('a[href*="/status/"]'));
          if (!links.length) continue;
          const matchesTarget = links.some((a) => statusIdFrom(a.href || a.getAttribute('href') || '') === normalizedTarget);
          if (!matchesTarget) continue;

          const hasActionBar = Boolean(
            article.querySelector('[data-testid="like"], [data-testid="unlike"], [data-testid="retweet"], [data-testid="unretweet"]')
          );
          if (!hasActionBar) continue;

          const rect = article.getBoundingClientRect();
          if (rect.bottom <= 0 || rect.top >= vh) continue;
          const centerDelta = Math.abs((rect.top + rect.height / 2) - (vh / 2));
          if (centerDelta < best) {
            best = centerDelta;
            target = article;
          }
        }

        if (!target) return { ok: false, reason: 'no-target' };

        const alreadyLiked = target.querySelector('[data-testid="unlike"]');
        if (alreadyLiked) return { ok: true, state: 'already-liked' };

        const likeBtn = target.querySelector('[data-testid="like"]');
        if (!likeBtn) return { ok: false, reason: 'like-button-missing' };

        try {
          likeBtn.click();
        } catch {
          try {
            likeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
          } catch {
            return { ok: false, reason: 'like-click-failed' };
          }
        }
        return { ok: true, state: 'liked' };
      }
      """,
      target_status_id,
    )
    if not isinstance(result, dict):
      self._log("Accion Like: respuesta inesperada")
      return
    if bool(result.get("ok", False)):
      state = str(result.get("state") or "ok")
      if state == "already-liked":
        self._log("Accion Like: ya tenia like")
      else:
        self._log("Accion Like: aplicada")
      return
    self._log(f"Accion Like: no se pudo ({result.get('reason') or 'sin-detalle'})")

  def _retweet_current_twitter_post(self, page) -> None:
    target_status_id = self._resolve_action_target_status_id(page)
    if not target_status_id:
      self._log("Accion Retweet: no se pudo resolver tweet objetivo")
      return

    first = page.evaluate(
      """
      (targetStatusId) => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const normalizedTarget = String(targetStatusId || '').trim();
        if (!normalizedTarget) return { ok: false, reason: 'no-target-status-id' };

        const statusIdFrom = (href) => {
          const m = String(href || '').match(/\/status\/(\d+)/i);
          return m ? String(m[1]) : '';
        };

        const articles = Array.from(document.querySelectorAll('article'));
        let target = null;
        let best = Number.POSITIVE_INFINITY;

        for (const article of articles) {
          const links = Array.from(article.querySelectorAll('a[href*="/status/"]'));
          if (!links.length) continue;
          const matchesTarget = links.some((a) => statusIdFrom(a.href || a.getAttribute('href') || '') === normalizedTarget);
          if (!matchesTarget) continue;

          const hasActionBar = Boolean(
            article.querySelector('[data-testid="retweet"], [data-testid="unretweet"]')
          );
          if (!hasActionBar) continue;

          const rect = article.getBoundingClientRect();
          if (rect.bottom <= 0 || rect.top >= vh) continue;
          const centerDelta = Math.abs((rect.top + rect.height / 2) - (vh / 2));
          if (centerDelta < best) {
            best = centerDelta;
            target = article;
          }
        }

        if (!target) return { ok: false, reason: 'no-target' };

        const alreadyRetweeted = target.querySelector('[data-testid="unretweet"]');
        if (alreadyRetweeted) return { ok: true, state: 'already-retweeted' };

        const rtBtn = target.querySelector('[data-testid="retweet"]');
        if (!rtBtn) return { ok: false, reason: 'retweet-button-missing' };

        try {
          rtBtn.click();
        } catch {
          try {
            rtBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
          } catch {
            return { ok: false, reason: 'retweet-click-failed' };
          }
        }

        return { ok: true, state: 'menu-opened' };
      }
      """,
      target_status_id,
    )

    if not isinstance(first, dict):
      self._log("Accion Retweet: respuesta inesperada")
      return
    if not bool(first.get("ok", False)):
      self._log(f"Accion Retweet: no se pudo ({first.get('reason') or 'sin-detalle'})")
      return
    if str(first.get("state") or "") == "already-retweeted":
      self._log("Accion Retweet: ya estaba retweeteado")
      return

    confirmed = False
    for _ in range(12):
      try:
        confirmed = bool(
          page.evaluate(
            """
            () => {
              const retweetOnly = document.querySelector('[data-testid="retweetConfirm"]');
              if (!retweetOnly) return false;
              try {
                retweetOnly.click();
                return true;
              } catch {
                try {
                  retweetOnly.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                  return true;
                } catch {
                  return false;
                }
              }
            }
            """
          )
        )
      except Exception:
        confirmed = False
      if confirmed:
        break
      time.sleep(0.08)

    if confirmed:
      self._log("Accion Retweet: aplicada (retweet normal)")
      return
    self._log("Accion Retweet: no aparecio opcion de retweet")

  def _platform_url(self, platform: Platform) -> str:
    match platform:
      case Platform.INSTAGRAM:
        return "https://www.instagram.com/"
      case Platform.TIKTOK:
        return "https://www.tiktok.com/foryou"
      case Platform.TWITTER:
        return "https://x.com/home"
      case Platform.YOUTUBE:
        return "https://www.youtube.com/shorts"

  def _profile_dir(self) -> str:
    root_dir = os.path.dirname(os.path.dirname(__file__))
    if self.instance_name:
      safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.instance_name).strip("_") or "instance"
      profiles_root = os.path.join(root_dir, "downloader", "browser_profiles")
      os.makedirs(profiles_root, exist_ok=True)
      profile = os.path.join(profiles_root, safe_name)
      os.makedirs(profile, exist_ok=True)
      return profile

    preferred = os.path.join(root_dir, "browser_profile")
    if os.path.isdir(preferred):
      return preferred
    fallback = os.path.join(root_dir, "downloader", "browser_profile")
    os.makedirs(fallback, exist_ok=True)
    return fallback

  def _error_summary(self, exc: Exception) -> str:
    raw = str(exc or "").strip()
    if not raw:
      return "error desconocido"

    if "Browser logs:" in raw:
      raw = raw.split("Browser logs:", 1)[0].strip()
    if "Call log:" in raw:
      raw = raw.split("Call log:", 1)[0].strip()

    first = raw.splitlines()[0].strip() if raw.splitlines() else raw
    if "launch_persistent_context" in first and "Target page, context or browser has been closed" in first:
      return "No se pudo abrir Chromium para esta instancia (perfil en uso o cerrado por conflicto)."

    return first[:260]

  def _log_scraper_error(self, exc: Exception) -> None:
    summary = self._error_summary(exc)
    now = time.time()
    if summary == self._last_error_summary and (now - self._last_error_log_ts) < 12.0:
      return
    self._last_error_summary = summary
    self._last_error_log_ts = now
    self._log(f"Aviso scraper: {summary}")

  def _is_closed_target_error(self, exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "target page, context or browser has been closed" in text or "has been closed" in text

  def _existing_cookie_files(self) -> list[str]:
    import random
    if self.cookie_candidates:
      unique_selected: list[str] = []
      seen_selected: set[str] = set()
      for item in self.cookie_candidates:
        clean = (item or "").strip()
        if not clean or not os.path.isfile(clean):
          continue
        key = os.path.normcase(os.path.abspath(clean))
        if key in seen_selected:
          continue
        seen_selected.add(key)
        unique_selected.append(clean)
      return unique_selected

    root_dir = os.path.dirname(os.path.dirname(__file__))
    manual = (self.cookies_file or "").strip()

    dirs_to_scan = [
      os.path.join(root_dir, "downloader", "cookies"),
      os.path.join(root_dir, "cookies"),
      os.path.join(root_dir, "downloader"),
      root_dir
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
      if not os.path.isdir(d):
        continue
      try:
        for f in os.listdir(d):
          if f.lower().endswith(".txt") or f.lower().endswith(".json"):
            if "cookie" in f.lower():
              add_file(os.path.join(d, f))
      except Exception:
        pass

    if out:
      first = out[0]
      tail = out[1:]
      random.shuffle(tail)

      final_list = [first] + tail
      return final_list

    return []

  def _load_netscape_cookies(self, file_path: str) -> list[dict]:
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
          if not name:
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
      self._log(f"Aviso cookies: no se pudo leer {file_path}: {exc}")
    return cookies

  def _load_json_cookies(self, file_path: str) -> list[dict]:
    cookies: list[dict] = []
    try:
      with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        parsed = json.load(f)
    except Exception as exc:
      self._log(f"Aviso cookies: no se pudo leer JSON {file_path}: {exc}")
      return cookies

    raw_items = []
    if isinstance(parsed, list):
      raw_items = parsed
    elif isinstance(parsed, dict):
      maybe = parsed.get("cookies")
      if isinstance(maybe, list):
        raw_items = maybe

    for item in raw_items:
      if not isinstance(item, dict):
        continue
      name = str(item.get("name") or "").strip()
      value = str(item.get("value") or "").strip()
      domain = str(item.get("domain") or "").strip()
      if not name or not value or not domain:
        continue

      cookie = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": str(item.get("path") or "/"),
        "secure": bool(item.get("secure", False)),
      }

      same_site = item.get("sameSite")
      if isinstance(same_site, str) and same_site.strip():
        cookie["sameSite"] = same_site.strip()

      expires = item.get("expires")
      try:
        exp = int(float(expires))
        if exp > 0:
          cookie["expires"] = exp
      except Exception:
        pass

      cookies.append(cookie)

    return cookies

  def _apply_context_cookies(self, context) -> None:
    candidates = self._existing_cookie_files()
    if not candidates:
      self._log("Aviso cookies: no hay cookies.txt/cookies2.txt para cargar en navegador")
      return

    loaded_total = 0
    by_key: dict[tuple[str, str, str], dict] = {}
    for cookie_file in candidates:
      if cookie_file.lower().endswith(".json"):
        parsed = self._load_json_cookies(cookie_file)
      else:
        parsed = self._load_netscape_cookies(cookie_file)
      if parsed:
        self._log(f"Cookies cargadas desde: {cookie_file} ({len(parsed)})")
      for cookie in parsed:
        key = (cookie.get("name", ""), cookie.get("domain", ""), cookie.get("path", "/"))
        by_key[key] = cookie

    if not by_key:
      self._log("Aviso cookies: archivos detectados, pero no contienen cookies validas")
      return

    merged = list(by_key.values())
    try:
      context.add_cookies(merged)
      loaded_total = len(merged)
    except Exception as exc:
      self._log(f"Aviso cookies: no se pudieron aplicar al navegador: {exc}")
      return

    self._log(f"Cookies aplicadas al navegador: {loaded_total}")

  def _prepare_page(self, context):
    pages = list(context.pages)
    page = pages[0] if pages else context.new_page()

    for other in list(context.pages):
      if other == page:
        continue
      try:
        url = (other.url or "").strip().lower()
      except Exception:
        url = ""
      if url in {"", "about:blank", "chrome://newtab", "chrome://newtab/"}:
        try:
          other.close()
        except Exception:
          pass

    return page

  def _log_popup_block(self, closed_count: int) -> None:
    if closed_count <= 0:
      return
    self._blocked_popup_count += int(closed_count)
    now = time.time()
    if (now - self._last_popup_block_log_ts) < 2.5:
      return
    self._last_popup_block_log_ts = now
    self._log(f"Proteccion anti-popup: cerrada(s) {self._blocked_popup_count} pestana(s) nueva(s)")

  def _close_extra_context_pages(self, context, keep_page=None, quiet: bool = False) -> int:
    if context is None:
      return 0

    closed = 0
    for candidate in list(context.pages):
      if keep_page is not None and candidate == keep_page:
        continue
      try:
        candidate.close()
        closed += 1
      except Exception:
        pass

    if closed and not quiet:
      self._log_popup_block(closed)
    return closed

  def _close_runtime_extra_pages(self, keep_page=None, quiet: bool = False) -> int:
    return self._close_extra_context_pages(self._runtime_context, keep_page=keep_page, quiet=quiet)

  def _install_popup_guards(self, context, keep_page) -> None:
    def on_new_page(new_page) -> None:
      if self._stop_event.is_set() or self._kill_event.is_set():
        return
      if keep_page is not None and new_page == keep_page:
        return
      try:
        new_page.close()
        self._log_popup_block(1)
      except Exception:
        pass

    try:
      context.on("page", on_new_page)
    except Exception:
      pass

    try:
      if keep_page is not None:
        keep_page.on("popup", on_new_page)
    except Exception:
      pass

  def _run(self, platform: Platform) -> None:
    try:
      self._run_playwright(platform)
    except Exception as exc:
      self._log(f"ERROR scraper: {exc}")

  def _run_playwright(self, platform: Platform) -> None:
    try:
      from playwright.sync_api import sync_playwright
    except Exception as exc:
      self._log(f"Playwright no disponible: {exc}")
      return

    start_url = self._platform_url(platform)
    profile_dir = self._profile_dir()

    with sync_playwright() as p:
      while not self._stop_event.is_set() and not self._kill_event.is_set():
        context = None
        page = None
        try:
          self._log(f"Abriendo feed {platform.value}: {start_url}")
          browser_args = [
            "--disable-infobars",
            "--disable-translate",
            "--disable-features=Translate,TranslateUI,TranslateBubble",
          ]
          bounds = self._normalized_monitor_bounds()
          if bounds:
            browser_args += [
              f"--window-position={bounds['left']},{bounds['top']}",
              f"--window-size={bounds['width']},{bounds['height']}",
            ]
          elif self.start_maximized:
            browser_args.append("--start-maximized")

          context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            no_viewport=True,
            args=browser_args,
          )
          self._apply_context_cookies(context)
          page = self._prepare_page(context)
          self._install_popup_guards(context, page)
          self._close_extra_context_pages(context, keep_page=page, quiet=True)
          self._runtime_context = context
          self._runtime_page = page
          self._apply_browser_color_scheme(page)
          page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
          self._dismiss_translation_popups(page)
          self._apply_browser_color_scheme(page)
          self._apply_window_placement(page)
          self._apply_pending_window_state(page, force=True)
          self._log("Navegador listo. Desplazando y detectando URL visible...")

          self._scrape_loop(page, platform)
        except Exception as exc:
          if self._stop_event.is_set() or self._kill_event.is_set():
            break
          self._log_scraper_error(exc)
          self._log("Reintentando inicializar navegador del feed en 3 segundos...")
          time.sleep(3)
        finally:
          self._runtime_page = None
          self._runtime_context = None
          if context is not None:
            try:
              context.close()
            except Exception:
              pass

  def _scrape_loop(self, page, platform: Platform) -> None:
    if platform == Platform.TWITTER:
      self._scrape_loop_twitter(page)
      return

    # Fallback estable para otras plataformas.
    while not self._stop_event.is_set():
      try:
        detected = self._detect_visible_url(page, platform)
      except Exception as exc:
        text = str(exc)
        if "has been closed" in text or "Target page" in text:
          raise RuntimeError("Page/context cerrado por el navegador")
        raise

      if detected and detected not in self._seen_urls:
        self._seen_urls.add(detected)
        self._log(f"Detectado: {detected}")
        try:
          self.on_url_detected(detected)
        except Exception as callback_exc:
          self._log(f"Aviso callback descarga: {callback_exc}")

      try:
        if self._consume_prev_request():
          self._retreat(page)
        else:
          self._advance(page)
      except Exception as exc:
        text = str(exc)
        if "has been closed" in text or "Target page" in text:
          raise RuntimeError("Page/context cerrado durante scroll")
        raise

      time.sleep(self.poll_seconds)

  def _scrape_loop_twitter(self, page) -> None:
    self._log("Feed Twitter: modo item-por-item activo")
    last_processed_abs_top = -1.0
    bootstrap_wait_done = False

    while not self._stop_event.is_set():
      self._close_runtime_extra_pages(page, quiet=True)
      self._apply_browser_color_scheme(page)
      self._ensure_twitter_home(page)
      self._dismiss_translation_popups(page)
      self._drain_pending_user_actions(page)
      if self._wait_if_paused(page):
        return

      if not bootstrap_wait_done:
        if not self._twitter_has_visible_media(page):
          wait_result = self._wait_with_interrupt(max(0.4, min(1.0, self.scroll_pause_seconds)), page)
          if wait_result == "stop":
            return
          continue
        bootstrap_wait_done = True

      if time.time() < self._prev_nav_cooldown_until and not self._prev_event.is_set():
        time.sleep(0.12)
        continue

      force_prev_pick = False
      current = None
      if self._consume_prev_request():
        force_prev_pick = True
        current = self._navigate_prev_to_previous_item(page)
        if current is None:
          continue

      if current is None:
        current = self._twitter_top_visible_media_item(
          page,
          min_abs_top=last_processed_abs_top,
        )
      if not current:
        if force_prev_pick:
          self._safe_scroll_up(page, min(320, max(140, int(self.scroll_px * 0.35))))
        elif self._consume_skip_request():
          pass
        else:
          self._safe_scroll_down(page, min(320, max(140, int(self.scroll_px * 0.35))))
        wait_result = self._wait_with_interrupt(self.scroll_pause_seconds, page)
        if wait_result == "stop":
          return
        continue

      current_url = str(current.get("url") or "").strip()
      if not current_url:
        self._scroll_by(page, min(260, max(120, int(self.scroll_px * 0.25))))
        wait_result = self._wait_with_interrupt(self.scroll_pause_seconds, page)
        if wait_result == "stop":
          return
        continue

      self._twitter_focus_status_id = current_url

      current_payload = {
        "url": current_url,
        "creator_hint": current.get("creator_hint"),
        "prefer_image_output": bool(current.get("prefer_image_output", False)),
        "media_kind": current.get("media_kind"),
        "media_urls": list(current.get("media_urls") or []),
      }

      if force_prev_pick:
        self._center_twitter_item_upward_only(page, current_url)
      else:
        self._center_twitter_item(page, current_url)

      if current_url not in self._seen_urls:
        self._seen_urls.add(current_url)
        self._log(f"Detectado: {current_url}")
        try:
          self.on_url_detected(current_payload)
        except Exception as callback_exc:
          self._log(f"Aviso callback descarga: {callback_exc}")

      related_items = list(current.get("related_items") or [])
      if not related_items:
        related_items = [{"url": item} for item in list(current.get("related_urls") or [])]

      for related in related_items:
        related_clean = str((related or {}).get("url") or "").strip()
        if not related_clean or related_clean == current_url or related_clean in self._seen_urls:
          continue
        self._seen_urls.add(related_clean)
        self._log(f"Detectado relacionado: {related_clean}")
        try:
          rel_has_photo = bool((related or {}).get("has_photo", False))
          self.on_url_detected(
            {
              "url": related_clean,
              "creator_hint": current.get("creator_hint"),
              "prefer_image_output": rel_has_photo,
              "media_kind": "image" if rel_has_photo else "",
            }
          )
        except Exception as callback_exc:
          self._log(f"Aviso callback descarga relacionada: {callback_exc}")

      media_count = int(current.get("media_count") or 1)
      carousel_count = int(current.get("carousel_count") or media_count or 1)
      is_video = bool(current.get("has_video", False))
      is_carousel = bool(current.get("is_carousel", False)) or carousel_count > 1
      current_abs_top = float(current.get("abs_top") or 0.0)

      if force_prev_pick:
        # Rebase forward anchor to the item reached by PREV.
        # Without this, the loop keeps an old "far ahead" anchor and jumps forward again.
        last_processed_abs_top = current_abs_top
      elif current_abs_top > last_processed_abs_top:
        last_processed_abs_top = current_abs_top

      if is_carousel:
        carousel_result = self._process_twitter_image_item(page, current_url, carousel_count)
        if carousel_result == "prev_post":
          self._wait_with_interrupt(max(0.15, min(0.5, self.scroll_pause_seconds * 0.25)), page)
          prev_item = self._navigate_prev_to_previous_item(page)
          if prev_item:
            prev_url = str(prev_item.get("url") or "").strip()
            try:
              last_processed_abs_top = float(prev_item.get("abs_top") or 0.0) - 120.0
            except Exception:
              last_processed_abs_top = -1.0
            if prev_url:
              self._twitter_focus_status_id = prev_url
          self._arm_prev_nav_cooldown()
          continue
        if carousel_result == "skip_post":
          moved = self._move_to_next_twitter_item(page, current_url, last_processed_abs_top)
          if not moved:
            self._safe_scroll_down(page, min(320, max(140, int(self.scroll_px * 0.35))))
          continue
      elif is_video:
        self._play_and_unmute_primary_video(page, current_url)
        self._try_fullscreen_current_video(page, current_url, enter=True)
        video_result = self._wait_video_or_timeout(page, current_url)
        self._try_fullscreen_current_video(page, current_url, enter=False)
        if video_result == "stop":
          return
        if video_result == "prev":
          if self._consume_prev_request():
            self._wait_with_interrupt(max(0.15, min(0.5, self.scroll_pause_seconds * 0.25)), page)
            prev_item = self._navigate_prev_to_previous_item(page)
            if prev_item:
              prev_url = str(prev_item.get("url") or "").strip()
              try:
                last_processed_abs_top = float(prev_item.get("abs_top") or 0.0) - 120.0
              except Exception:
                last_processed_abs_top = -1.0
              if prev_url:
                self._twitter_focus_status_id = prev_url
              self._arm_prev_nav_cooldown()
          continue

        if video_result == "skip":
          settle_result = "skip"
        else:
          settle_result = self._wait_with_interrupt(max(0.45, min(0.9, self.scroll_pause_seconds * 0.6)), page)
        if settle_result == "stop":
          return
        if settle_result == "prev":
          if self._consume_prev_request():
            self._wait_with_interrupt(max(0.15, min(0.5, self.scroll_pause_seconds * 0.25)), page)
            prev_item = self._navigate_prev_to_previous_item(page)
            if prev_item:
              prev_url = str(prev_item.get("url") or "").strip()
              try:
                last_processed_abs_top = float(prev_item.get("abs_top") or 0.0) - 120.0
              except Exception:
                last_processed_abs_top = -1.0
              if prev_url:
                self._twitter_focus_status_id = prev_url
              self._arm_prev_nav_cooldown()
          continue
      else:
        image_result = self._process_twitter_image_item(page, current_url, carousel_count if is_carousel else media_count)
        if image_result == "prev_post":
          self._wait_with_interrupt(max(0.15, min(0.5, self.scroll_pause_seconds * 0.25)), page)
          prev_item = self._navigate_prev_to_previous_item(page)
          if prev_item:
            prev_url = str(prev_item.get("url") or "").strip()
            try:
              last_processed_abs_top = float(prev_item.get("abs_top") or 0.0) - 120.0
            except Exception:
              last_processed_abs_top = -1.0
            if prev_url:
              self._twitter_focus_status_id = prev_url
            self._arm_prev_nav_cooldown()
          continue
        if image_result == "skip_post":
          moved = self._move_to_next_twitter_item(page, current_url, last_processed_abs_top)
          if not moved:
            self._safe_scroll_down(page, min(320, max(140, int(self.scroll_px * 0.35))))
          continue

      # After PREV, do not auto-advance forward in the same cycle.
      if force_prev_pick:
        wait_result = self._wait_with_interrupt(self.scroll_pause_seconds, page)
        if wait_result == "stop":
          return
        continue

      if self._consume_prev_request():
        continue

      moved = self._move_to_next_twitter_item(page, current_url, last_processed_abs_top)
      if not moved:
        self._safe_scroll_down(page, min(320, max(140, int(self.scroll_px * 0.35))))
      wait_result = self._wait_with_interrupt(self.scroll_pause_seconds, page)
      if wait_result == "stop":
        return

  def _ensure_twitter_home(self, page) -> None:
    try:
      state = page.evaluate(
        """
        () => {
          const href = String(window.location.href || '');
          const host = String(window.location.hostname || '').toLowerCase();
          const path = String(window.location.pathname || '').toLowerCase();
          const isXHost = host === 'x.com' || host === 'www.x.com' || host === 'twitter.com' || host === 'www.twitter.com';
          const isHome = path === '/home' || path === '/home/' || path.startsWith('/home?');
          return { href, isXHost, isHome };
        }
        """
      )
    except Exception:
      return

    if not isinstance(state, dict):
      return
    is_x_host = bool(state.get("isXHost", False))
    is_home = bool(state.get("isHome", False))
    if is_x_host and is_home:
      return

    now = time.time()
    if (now - self._last_home_guard_log_ts) >= 8.0:
      current = str(state.get("href") or "").strip() or "(desconocido)"
      if not is_x_host:
        self._log(f"Home guard: salida de dominio X ({current}), regresando a /home")
      else:
        self._log(f"Home guard: fuera de Home ({current}), regresando a /home")
      self._last_home_guard_log_ts = now

    try:
      page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45000)
      self._apply_window_placement(page)
    except Exception:
      return

  def _twitter_top_visible_media_item(
    self,
    page,
    min_abs_top: float = -1.0,
    exclude_url: str = "",
    anchor_abs_top: float | None = None,
    prefer_before_anchor: bool = False,
  ) -> dict | None:
    return page.evaluate(
      """
      ({ minAbsTop, excludeUrl, anchorAbsTop, preferBeforeAnchor }) => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const vw = window.innerWidth || document.documentElement.clientWidth;
        const articles = Array.from(document.querySelectorAll('article'));

        const canonical = (href) => {
          if (!href) return null;
          const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
          if (!m) return href;
          return `https://x.com/${m[1]}/status/${m[2]}`;
        };

        const visibleMediaCount = (article) => {
          const media = Array.from(
            article.querySelectorAll(
              'a[href*="/status/"][href*="/photo/"] img, img[src*="twimg.com/media"], img[src*="pbs.twimg.com/media"], a[href*="/status/"][href*="/video/"] video, video'
            )
          );
          let count = 0;
          for (const el of media) {
            const r = el.getBoundingClientRect();
            const visible = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
            if (!visible) continue;
            const area = Math.max(0, r.width) * Math.max(0, r.height);
            if (area < 12000) continue;
            count += 1;
          }
          return count;
        };

        const visibleVideoCount = (article) => {
          const videos = Array.from(article.querySelectorAll('video'));
          let count = 0;
          for (const v of videos) {
            const r = v.getBoundingClientRect();
            const visible = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
            if (!visible) continue;
            const area = Math.max(0, r.width) * Math.max(0, r.height);
            if (area < 12000) continue;
            count += 1;
          }
          return count;
        };

        const normalizeImageUrl = (value) => {
          if (!value) return null;
          try {
            const parsed = new URL(String(value), window.location.href);
            const host = parsed.hostname.toLowerCase();
            if (!host.includes('twimg.com')) return null;
            if (!/\/media\//i.test(parsed.pathname)) return null;
            parsed.searchParams.set('name', 'orig');
            return parsed.toString();
          } catch {
            return null;
          }
        };

        const out = [];
        for (const article of articles) {
          const rect = article.getBoundingClientRect();
          const visible = rect.bottom > 0 && rect.top < vh;
          if (!visible) continue;

          const statusLinks = Array.from(article.querySelectorAll('a[href*="/status/"]'))
            .map((anchor, index) => {
              const rawHref = anchor.href || anchor.getAttribute('href') || '';
              const canonicalHref = canonical(rawHref);
              return {
                url: canonicalHref,
                raw_url: rawHref,
                has_photo: /\/photo\//i.test(rawHref),
                has_video_link: /\/video\//i.test(rawHref),
                has_time: !!anchor.querySelector('time'),
                index,
              };
            })
            .filter((item) => !!item.url);

          if (!statusLinks.length) continue;

          const mediaCount = visibleMediaCount(article);
          const imageUrls = Array.from(article.querySelectorAll('img'))
            .map((img) => normalizeImageUrl(img.currentSrc || img.src || img.getAttribute('src') || ''))
            .filter((value, index, array) => !!value && array.indexOf(value) === index);
          const imageCount = imageUrls.length;
          const mediaKeyFromRaw = (rawHref) => {
            const raw = String(rawHref || '');
            const m = raw.match(/\/status\/\d+\/(photo|video)\/(\d+)/i);
            if (m) return `${m[1].toLowerCase()}/${m[2]}`;
            if (/\/photo\//i.test(raw)) return `photo/${raw}`;
            if (/\/video\//i.test(raw)) return `video/${raw}`;
            return null;
          };

          const mediaLinkKeys = new Set();
          const photoLinkKeys = new Set();
          for (const item of statusLinks) {
            if (!item.has_photo && !item.has_video_link) continue;
            const key = mediaKeyFromRaw(item.raw_url);
            if (!key) continue;
            mediaLinkKeys.add(key);
            if (item.has_photo) photoLinkKeys.add(key);
          }

          const hasPhotoLinks = photoLinkKeys.size > 0;
          const photoLinkCount = photoLinkKeys.size;
          const mediaLinkCount = mediaLinkKeys.size;
          const hasVideoLink = statusLinks.some((item) => item.has_video_link);
          const hasInlineVideo = Boolean(
            article.querySelector(
              'video, [data-testid="videoPlayer"], [data-testid="videoComponent"], [data-testid="playButton"], div[role="button"][aria-label*="Play" i], div[role="button"][aria-label*="Reproducir" i], div[aria-label*="video" i]'
            )
          );
          const hasVideo = visibleVideoCount(article) > 0 || hasVideoLink || hasInlineVideo;
          const hasCarouselControls = Boolean(
            article.querySelector('button[data-testid="carouselControl-right"], button[aria-label*="Next" i], div[role="button"][aria-label*="Next" i]')
          );
          const hasImage = imageCount > 0 || hasPhotoLinks;
          if (mediaCount <= 0 && !hasVideo && !hasImage && !hasCarouselControls) continue;

          const inferredSlideCount = Math.max(0, mediaLinkCount, imageCount, photoLinkCount);
          const effectiveSlides = inferredSlideCount > 0
            ? inferredSlideCount
            : (hasCarouselControls ? Math.max(2, mediaCount) : 1);

          const absTop = (window.scrollY || window.pageYOffset || 0) + rect.top;
          if (absTop <= Number(minAbsTop || -1) + 40) continue;

          let primary = statusLinks[0].url;
          const preferredLink = statusLinks.find((item) => item.has_photo || item.has_video_link);
          if (preferredLink) {
            primary = preferredLink.url;
          } else {
            const timeLink = statusLinks.find((item) => item.has_time);
            if (timeLink) {
              primary = timeLink.url;
            }
          }

          if (excludeUrl && primary === excludeUrl) continue;

          const relatedUrls = [];
          const relatedItems = [];
          const relatedSeen = new Set([primary]);
          for (const link of statusLinks) {
            if (relatedSeen.has(link.url)) continue;
            relatedSeen.add(link.url);
            relatedUrls.push(link.url);
            relatedItems.push({
              url: link.url,
              has_photo: !!link.has_photo,
              has_video_link: !!link.has_video_link,
            });
          }

          out.push({
            url: primary,
            related_urls: relatedUrls.slice(0, 4),
            related_items: relatedItems.slice(0, 4),
            raw_urls: statusLinks.map((item) => item.raw_url),
            creator_hint: null,
            top: rect.top,
            abs_top: absTop,
            center: rect.top + rect.height / 2,
            has_video: hasVideo,
            has_image: hasImage,
            media_count: Math.max(1, effectiveSlides),
            image_count: imageCount,
            carousel_count: hasCarouselControls ? Math.max(2, effectiveSlides) : Math.max(1, effectiveSlides),
            is_carousel: hasCarouselControls || effectiveSlides > 1,
            prefer_image_output: hasImage && !hasVideo,
            media_kind: hasVideo ? ((hasCarouselControls || effectiveSlides > 1) ? 'carousel_video' : 'video') : (effectiveSlides > 1 || hasCarouselControls ? 'carousel' : (hasImage ? 'image' : 'unknown')),
            media_urls: imageUrls.slice(0, 6),
          });
        }

        if (preferBeforeAnchor && Number.isFinite(Number(anchorAbsTop))) {
          const anchor = Number(anchorAbsTop);
          const prevCandidates = out
            .filter((item) => Number(item.abs_top || 0) < (anchor - 20))
            .sort((a, b) => Number(b.abs_top || 0) - Number(a.abs_top || 0));
          if (prevCandidates.length) {
            return prevCandidates[0];
          }
          // In PREV mode never fall back to top-visible item,
          // otherwise navigation may jump forward.
          return null;
        }

        out.sort((a, b) => a.top - b.top);
        return out.length ? out[0] : null;
      }
      """
      ,
      {
        "minAbsTop": min_abs_top,
        "excludeUrl": (exclude_url or ""),
        "anchorAbsTop": (anchor_abs_top if anchor_abs_top is not None else None),
        "preferBeforeAnchor": bool(prefer_before_anchor),
      },
    )

  def _twitter_has_visible_media(self, page) -> bool:
    return bool(
      page.evaluate(
        """
        () => {
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const vw = window.innerWidth || document.documentElement.clientWidth;
          const articles = Array.from(document.querySelectorAll('article'));
          for (const article of articles) {
            const r = article.getBoundingClientRect();
            if (!(r.bottom > 0 && r.top < vh)) continue;
            const media = Array.from(article.querySelectorAll('img, video'));
            for (const el of media) {
              const mr = el.getBoundingClientRect();
              const visible = mr.bottom > 0 && mr.top < vh && mr.right > 0 && mr.left < vw;
              if (!visible) continue;
              const area = Math.max(0, mr.width) * Math.max(0, mr.height);
              if (area >= 12000) return true;
            }
          }
          return false;
        }
        """
      )
    )

  def _navigate_prev_to_previous_item(self, page) -> dict | None:
    focus_url = str(self._twitter_focus_status_id or "").strip()
    anchor_abs_top = self._twitter_item_abs_top(page, focus_url) if focus_url else None
    step_scroll = min(520, max(220, int(self.scroll_px * 0.6)))

    if not focus_url:
      self._log("PREV: sin foco previo, buscando anterior por scroll incremental")
      baseline = self._twitter_top_visible_media_item(page, min_abs_top=-1.0)
      if baseline:
        focus_url = str(baseline.get("url") or "").strip()
        try:
          anchor_abs_top = float(baseline.get("abs_top") or 0.0)
        except Exception:
          anchor_abs_top = None
        if focus_url:
          self._twitter_focus_status_id = focus_url
          self._log(f"PREV: foco baseline={focus_url}")

    for attempt in range(1, 7):
      moved_prev = False
      scroll_before = page.evaluate("() => Number(window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0)")
      if focus_url and anchor_abs_top is not None:
        moved_prev = self._move_to_previous_twitter_item(page, focus_url, anchor_abs_top)

      self._log(
        f"PREV intento {attempt}/6: foco={focus_url or '-'} "
        f"anchor={anchor_abs_top if anchor_abs_top is not None else 'n/a'} moved_prev={'yes' if moved_prev else 'no'}"
      )

      wait_result = self._wait_with_interrupt(max(0.18, self.scroll_pause_seconds * 0.45), page)
      if wait_result == "stop":
        return None

      candidate = self._twitter_top_visible_media_item(
        page,
        min_abs_top=-1.0,
        exclude_url=focus_url,
        anchor_abs_top=anchor_abs_top,
        prefer_before_anchor=True,
      )
      if candidate:
        c_url = str(candidate.get("url") or "").strip()
        c_abs = float(candidate.get("abs_top") or 0.0)
        self._log(f"PREV objetivo encontrado: {c_url}")
        return candidate

      self._safe_scroll_up(page, step_scroll)
      scroll_after = page.evaluate("() => Number(window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0)")
      self._log(f"PREV scroll: before={int(scroll_before)} after={int(scroll_after)} delta={int(scroll_after - scroll_before)}")
      wait_result = self._wait_with_interrupt(max(0.18, self.scroll_pause_seconds * 0.45), page)
      if wait_result == "stop":
        return None

      if focus_url:
        refreshed = self._twitter_item_abs_top(page, focus_url)
        if refreshed is not None:
          anchor_abs_top = refreshed

    self._log("PREV: no se encontro anterior tras 6 intentos")
    return None

  def _center_twitter_item(self, page, target_url: str) -> None:
    for _ in range(12):
      result = page.evaluate(
        """
        (targetUrl) => {
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const articles = Array.from(document.querySelectorAll('article'));

          const canonical = (href) => {
            if (!href) return null;
            const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
            if (!m) return href;
            return `https://x.com/${m[1]}/status/${m[2]}`;
          };
          for (const article of articles) {
            const link = article.querySelector('a[href*="/status/"]');
            if (!link) continue;
            const url = canonical(link.href);
            if (url !== targetUrl) continue;

            const r = article.getBoundingClientRect();
            const delta = (r.top + r.height / 2) - (vh / 2);
            return { found: true, delta };
          }
          return { found: false, delta: 0 };
        }
        """,
        target_url,
      )
      if not result or not result.get("found"):
        return

      delta = float(result.get("delta") or 0.0)
      if abs(delta) <= 20:
        return

      step = max(-220.0, min(0.0, delta * 0.55))
      self._scroll_by(page, step)
      time.sleep(0.12)

  def _center_twitter_item_upward_only(self, page, target_url: str) -> None:
    for _ in range(12):
      result = page.evaluate(
        """
        (targetUrl) => {
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const articles = Array.from(document.querySelectorAll('article'));

          const canonical = (href) => {
            if (!href) return null;
            const m = String(href).match(/https?:\/\/(?:www\.)?(?:x|twitter)\.com\/([^\/?#]+)\/status\/(\d+)/i);
            if (!m) return href;
            return `https://x.com/${m[1]}/status/${m[2]}`;
          };
          for (const article of articles) {
            const link = article.querySelector('a[href*="/status/"]');
            if (!link) continue;
            const url = canonical(link.href);
            if (url !== targetUrl) continue;

            const r = article.getBoundingClientRect();
            const delta = (r.top + r.height / 2) - (vh / 2);
            return { found: true, delta };
          }
          return { found: false, delta: 0 };
        }
        """,
        target_url,
      )
      if not result or not result.get("found"):
        return

      delta = float(result.get("delta") or 0.0)
      if abs(delta) <= 20:
        return
      if delta >= 0:
        return

      step = max(-220.0, min(0.0, delta * 0.55))
      self._scroll_by(page, step)
      time.sleep(0.12)

  def _scroll_by(self, page, distance: float) -> None:
    page.evaluate(
      """
      (distance) => {
        window.scrollBy({ top: Number(distance) || 0, left: 0, behavior: 'smooth' });
      }
      """,
      distance,
    )

  def _safe_scroll_down(self, page, distance: float) -> None:
    amount = max(100.0, float(distance or 0.0))
    before = page.evaluate(
      """
      () => Number(window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0)
      """
    )

    self._scroll_by(page, amount)
    time.sleep(0.12)

    after = page.evaluate(
      """
      () => Number(window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0)
      """
    )

    if after <= before + 1:
      page.evaluate(
        """
        ({ base, extra }) => {
          const target = Number(base || 0) + Number(extra || 0);
          window.scrollTo({ top: target, left: 0, behavior: 'auto' });
        }
        """,
        {"base": before, "extra": amount},
      )

  def _safe_scroll_up(self, page, distance: float) -> None:
    amount = max(100.0, float(distance or 0.0))
    before = page.evaluate(
      """
      () => Number(window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0)
      """
    )

    page.evaluate(
      """
      (distance) => {
        const current = Number(window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0);
        const target = Math.max(0, current - (Number(distance) || 0));
        window.scrollTo({ top: target, left: 0, behavior: 'auto' });
      }
      """,
      amount,
    )
    time.sleep(0.08)

    after = page.evaluate(
      """
      () => Number(window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0)
      """
    )

    if after >= before - 1:
      page.evaluate(
        """
        ({ base, extra }) => {
          const target = Number(base || 0) - Number(extra || 0);
          window.scrollTo({ top: Math.max(0, target), left: 0, behavior: 'auto' });
        }
        """,
        {"base": before, "extra": amount},
      )

  def _video_state_for_url(self, page, target_url: str) -> dict:
    return page.evaluate(
      """
      (targetUrl) => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const vw = window.innerWidth || document.documentElement.clientWidth;
        const articles = Array.from(document.querySelectorAll('article'));

        const canonical = (href) => {
          if (!href) return null;
          const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
          if (!m) return href;
          return `https://x.com/${m[1]}/status/${m[2]}`;
        };

        for (const article of articles) {
          const link = article.querySelector('a[href*="/status/"]');
          if (!link || canonical(link.href) !== targetUrl) continue;

          const videos = Array.from(article.querySelectorAll('video'));
          let best = null;
          let bestArea = 0;
          for (const v of videos) {
            const r = v.getBoundingClientRect();
            const visible = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
            if (!visible) continue;
            const area = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0))
              * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
            if (area <= bestArea) continue;
            best = v;
            bestArea = area;
          }

          if (!best) return { has_video: false, ended: false, paused: true, current_time: 0, duration: 0 };

          const trackerFor = (video) => {
            if (video.__feedTracker) {
              return video.__feedTracker;
            }

            const tracker = {
              play_ms: 0,
              started_at: null,
              last_current_time: 0,
              last_duration: 0,
              loop_count: 0,
            };

            const accrue = () => {
              if (tracker.started_at !== null) {
                tracker.play_ms += performance.now() - tracker.started_at;
                tracker.started_at = null;
              }
            };

            const markPlaying = () => {
              tracker.last_duration = Number(video.duration || 0) || tracker.last_duration || 0;
              if (!video.paused && !video.ended && tracker.started_at === null) {
                tracker.started_at = performance.now();
              }
            };

            try {
              video.addEventListener('play', markPlaying, true);
              video.addEventListener('playing', markPlaying, true);
              video.addEventListener('pause', accrue, true);
              video.addEventListener('ended', accrue, true);
              video.addEventListener('loadedmetadata', markPlaying, true);
              video.addEventListener('timeupdate', () => {
                const currentTime = Number(video.currentTime || 0);
                if (tracker.started_at !== null && currentTime + 0.5 < tracker.last_current_time) {
                  tracker.loop_count += 1;
                }
                tracker.last_current_time = currentTime;
                tracker.last_duration = Number(video.duration || 0) || tracker.last_duration || 0;
                if (!video.paused && !video.ended && tracker.started_at === null) {
                  tracker.started_at = performance.now();
                }
              }, true);
            } catch {}

            video.__feedTracker = tracker;
            return tracker;
          };

          const playedSecondsFor = (tracker) => {
            if (!tracker) return 0;
            if (tracker.started_at !== null) {
              return (tracker.play_ms + (performance.now() - tracker.started_at)) / 1000;
            }
            return tracker.play_ms / 1000;
          };

          const record = trackerFor(best);

          try {
            best.play();
          } catch {}

          const duration = Number(best.duration || 0) || Number(record.last_duration || 0) || 0;
          const currentTime = Number(best.currentTime || 0);
          const playedSeconds = playedSecondsFor(record);
          const ended = Boolean(best.ended) || (duration > 0 && currentTime >= duration - 0.35) || (duration > 0 && playedSeconds >= duration - 0.35);
          return {
            has_video: true,
            ended,
            paused: Boolean(best.paused),
            playing: !Boolean(best.paused) && !Boolean(best.ended),
            current_time: currentTime,
            duration,
            play_time: playedSeconds,
            loop_count: Number(record.loop_count || 0),
            duration_known: duration > 0,
          };
        }

        return { has_video: false, ended: false, paused: true, current_time: 0, duration: 0 };
      }
      """,
      target_url,
    )

  def _viewer_video_state(self, page) -> dict:
    return page.evaluate(
      """
      () => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const vw = window.innerWidth || document.documentElement.clientWidth;

        const isVisibleNode = (el) => {
          if (!el) return false;
          const rect = el.getBoundingClientRect();
          if (!(rect.width > 10 && rect.height > 10)) return false;
          if (!(rect.bottom > 0 && rect.top < vh && rect.right > 0 && rect.left < vw)) return false;
          const style = window.getComputedStyle(el);
          return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.01;
        };

        const modalRoots = Array.from(
          document.querySelectorAll('[role="dialog"], [aria-modal="true"], [data-testid="swipe-to-dismiss-container"], [data-testid="cellInnerDiv"]')
        ).filter((node) => isVisibleNode(node));

        const pickBestVideo = (root) => {
          const videos = Array.from(root.querySelectorAll('video'));
          let best = null;
          let bestArea = 0;
          for (const video of videos) {
            if (!isVisibleNode(video)) continue;
            const rect = video.getBoundingClientRect();
            const area = Math.max(0, Math.min(rect.right, vw) - Math.max(rect.left, 0))
              * Math.max(0, Math.min(rect.bottom, vh) - Math.max(rect.top, 0));
            if (area <= bestArea) continue;
            best = video;
            bestArea = area;
          }
          return { video: best, area: bestArea };
        };

        let bestVideo = null;
        let bestArea = 0;
        const roots = modalRoots.length ? modalRoots : [document];
        for (const root of roots) {
          const picked = pickBestVideo(root);
          if (picked.video && picked.area > bestArea) {
            bestVideo = picked.video;
            bestArea = picked.area;
          }
        }

        if (!bestVideo) {
          return { has_video: false, ended: false, paused: true, current_time: 0, duration: 0, play_time: 0, loop_count: 0 };
        }

        const trackerFor = (video) => {
          if (video.__feedTracker) {
            return video.__feedTracker;
          }

          const tracker = {
            play_ms: 0,
            started_at: null,
            last_current_time: 0,
            last_duration: 0,
            loop_count: 0,
          };

          const accrue = () => {
            if (tracker.started_at !== null) {
              tracker.play_ms += performance.now() - tracker.started_at;
              tracker.started_at = null;
            }
          };

          const markPlaying = () => {
            tracker.last_duration = Number(video.duration || 0) || tracker.last_duration || 0;
            if (!video.paused && !video.ended && tracker.started_at === null) {
              tracker.started_at = performance.now();
            }
          };

          try {
            video.addEventListener('play', markPlaying, true);
            video.addEventListener('playing', markPlaying, true);
            video.addEventListener('pause', accrue, true);
            video.addEventListener('ended', accrue, true);
            video.addEventListener('loadedmetadata', markPlaying, true);
            video.addEventListener('timeupdate', () => {
              const currentTime = Number(video.currentTime || 0);
              if (tracker.started_at !== null && currentTime + 0.5 < tracker.last_current_time) {
                tracker.loop_count += 1;
              }
              tracker.last_current_time = currentTime;
              tracker.last_duration = Number(video.duration || 0) || tracker.last_duration || 0;
              if (!video.paused && !video.ended && tracker.started_at === null) {
                tracker.started_at = performance.now();
              }
            }, true);
          } catch {}

          video.__feedTracker = tracker;
          return tracker;
        };

        const playedSecondsFor = (tracker) => {
          if (!tracker) return 0;
          if (tracker.started_at !== null) {
            return (tracker.play_ms + (performance.now() - tracker.started_at)) / 1000;
          }
          return tracker.play_ms / 1000;
        };

        const record = trackerFor(bestVideo);
        try {
          const playResult = bestVideo.play();
          if (playResult && typeof playResult.catch === 'function') {
            playResult.catch(() => {});
          }
        } catch {}

        const duration = Number(bestVideo.duration || 0) || Number(record.last_duration || 0) || 0;
        const currentTime = Number(bestVideo.currentTime || 0);
        const playedSeconds = playedSecondsFor(record);
        const ended = Boolean(bestVideo.ended)
          || (duration > 0 && currentTime >= duration - 0.35)
          || (duration > 0 && playedSeconds >= duration - 0.35);

        return {
          has_video: true,
          ended,
          paused: Boolean(bestVideo.paused),
          playing: !Boolean(bestVideo.paused) && !Boolean(bestVideo.ended),
          current_time: currentTime,
          duration,
          play_time: playedSeconds,
          loop_count: Number(record.loop_count || 0),
          duration_known: duration > 0,
        };
      }
      """
    )

  def _play_visible_viewer_video(self, page) -> None:
    muted = self.is_muted()
    page.evaluate(
      """
      ({ muted }) => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const vw = window.innerWidth || document.documentElement.clientWidth;
        const videos = Array.from(document.querySelectorAll('video'));
        for (const video of videos) {
          const rect = video.getBoundingClientRect();
          const visible = rect.bottom > 0 && rect.top < vh && rect.right > 0 && rect.left < vw;
          if (!visible) continue;
          const area = Math.max(0, Math.min(rect.right, vw) - Math.max(rect.left, 0))
            * Math.max(0, Math.min(rect.bottom, vh) - Math.max(rect.top, 0));
          if (area < 12000) continue;

          try {
            video.muted = Boolean(muted);
            if (!Boolean(muted)) {
              video.volume = 1.0;
            }
          } catch {}

          try {
            const playResult = video.play();
            if (playResult && typeof playResult.catch === 'function') {
              playResult.catch(() => {});
            }
          } catch {}
        }
      }
      """,
      {"muted": bool(muted)},
    )

  def _play_and_unmute_primary_video(self, page, target_url: str) -> None:
    muted = self.is_muted()
    page.evaluate(
      """
      ({ targetUrl, muted }) => {
        const canonical = (href) => {
          if (!href) return null;
          const m = String(href).match(/https?:\/\/(?:www\.)?(?:x|twitter)\.com\/([^\/?#]+)\/status\/(\d+)/i);
          if (!m) return href;
          return `https://x.com/${m[1]}/status/${m[2]}`;
        };

        const videos = [];
        const articles = Array.from(document.querySelectorAll('article'));
        for (const article of articles) {
          const link = article.querySelector('a[href*="/status/"]');
          if (link && canonical(link.href) === targetUrl) {
            videos.push(...Array.from(article.querySelectorAll('video')));
          }
        }

        if (!videos.length) {
          videos.push(...Array.from(document.querySelectorAll('video')));
        }

        for (const v of videos) {
          try {
            v.muted = Boolean(muted);
            if (!Boolean(muted)) {
              v.volume = 1.0;
            }
          } catch {}

          try {
            const playResult = v.play();
            if (playResult && typeof playResult.catch === 'function') {
              playResult.catch(() => {});
            }
          } catch {}
        }
      }
      """,
      {"targetUrl": target_url, "muted": bool(muted)},
    )

  def _wait_video_or_timeout(self, page, target_url: str) -> str:
    if not self.wait_video_end:
      wait_result = self._wait_with_interrupt(self.image_dwell_seconds, page)
      if wait_result in {"skip", "stop", "prev"}:
        return wait_result
      return "done"

    start = time.time()
    while not self._stop_event.is_set():
      if self._wait_if_paused(page):
        return "stop"
      if self._consume_skip_request():
        return "skip"
      state = self._video_state_for_url(page, target_url)
      if not state.get("has_video", False):
        wait_result = self._wait_with_interrupt(self.image_dwell_seconds, page)
        if wait_result in {"skip", "stop", "prev"}:
          return wait_result
        return "done"

      play_time = float(state.get("play_time") or 0.0)
      duration = float(state.get("duration") or 0.0)
      loop_count = int(state.get("loop_count") or 0)

      if duration > 0 and play_time >= max(0.0, duration - 0.35):
        return "done"

      # GIFs / videos sin metadata confiable pueden quedarse en espera demasiado tiempo.
      # Si no hay duracion, usamos dwell normal como limite razonable.
      if duration <= 0 and play_time >= max(2.0, float(self.image_dwell_seconds)):
        return "done"

      # Si el clip ya ha hecho loop, no seguir esperando indefinidamente.
      if duration > 0 and loop_count >= 1 and play_time >= max(duration, 2.0):
        return "done"

      if bool(state.get("paused", False)):
        # Fuerza play si se pausa por autoplay policy.
        self._play_and_unmute_primary_video(page, target_url)

      if bool(state.get("ended", False)):
        return "done"

      if (time.time() - start) >= self.max_video_wait_seconds:
        self._log(
          f"Aviso feed: maximo de espera de video alcanzado ({int(self.max_video_wait_seconds)}s), avanzando."
        )
        return "done"

      wait_result = self._wait_with_interrupt(max(0.2, self.poll_seconds), page)
      if wait_result in {"skip", "stop", "prev"}:
        return wait_result

    return "done"

  def _wait_viewer_video_or_timeout(self, page) -> str:
    if not self.wait_video_end:
      wait_result = self._wait_with_interrupt(self.image_dwell_seconds, page)
      if wait_result in {"skip", "stop", "prev"}:
        return wait_result
      return "done"

    start = time.time()
    while not self._stop_event.is_set():
      if self._wait_if_paused(page):
        return "stop"
      if self._consume_skip_request():
        return "skip"

      state = self._viewer_video_state(page)
      if not state.get("has_video", False):
        wait_result = self._wait_with_interrupt(self.image_dwell_seconds, page)
        if wait_result in {"skip", "stop", "prev"}:
          return wait_result
        return "done"

      play_time = float(state.get("play_time") or 0.0)
      duration = float(state.get("duration") or 0.0)
      loop_count = int(state.get("loop_count") or 0)

      if duration > 0 and play_time >= max(0.0, duration - 0.35):
        return "done"

      if duration <= 0 and play_time >= max(2.0, float(self.image_dwell_seconds)):
        return "done"

      if duration > 0 and loop_count >= 1 and play_time >= max(duration, 2.0):
        return "done"

      if bool(state.get("paused", False)):
        self._play_visible_viewer_video(page)

      if bool(state.get("ended", False)):
        return "done"

      if (time.time() - start) >= self.max_video_wait_seconds:
        self._log(
          f"Aviso feed: maximo de espera de video alcanzado ({int(self.max_video_wait_seconds)}s), avanzando."
        )
        return "done"

      wait_result = self._wait_with_interrupt(max(0.2, self.poll_seconds), page)
      if wait_result in {"skip", "stop", "prev"}:
        return wait_result

    return "done"

  def _try_fullscreen_current_video(self, page, target_url: str, enter: bool) -> None:
    page.evaluate(
      """
      ({ targetUrl, enter }) => {
        const articles = Array.from(document.querySelectorAll('article'));
        const canonical = (href) => {
          if (!href) return null;
          const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
          if (!m) return href;
          return `https://x.com/${m[1]}/status/${m[2]}`;
        };

        let targetVideo = null;
        for (const article of articles) {
          const link = article.querySelector('a[href*="/status/"]');
          if (!link || canonical(link.href) !== targetUrl) continue;
          targetVideo = article.querySelector('video');
          if (targetVideo) break;
        }
        if (!targetVideo) {
          const visibleVideos = Array.from(document.querySelectorAll('video')).filter((v) => {
            const r = v.getBoundingClientRect();
            return r.width > 120 && r.height > 120 && r.bottom > 0 && r.top < (window.innerHeight || document.documentElement.clientHeight);
          });
          targetVideo = visibleVideos.length ? visibleVideos[0] : null;
        }
        if (!targetVideo) return;

        try {
          if (enter) {
            if (targetVideo.requestFullscreen) {
              targetVideo.requestFullscreen();
            }
          } else {
            if (document.fullscreenElement && document.exitFullscreen) {
              document.exitFullscreen();
            }
          }
        } catch {}

        if (enter) {
          try {
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }));
          } catch {}
        }
      }
      """,
      {"targetUrl": target_url, "enter": enter},
    )

  def _fallback_open_twitter_carousel_index(self, page, target_url: str, index: int, in_viewer: bool) -> bool:
    # Some video slides open in a focused state where Next/Prev controls are unavailable.
    # Exit fullscreen/viewer first, then open the requested slide by index.
    try:
      self._try_fullscreen_current_video(page, target_url, enter=False)
    except Exception:
      pass

    if in_viewer:
      try:
        self._close_twitter_image_viewer(page)
      except Exception:
        pass

    try:
      return self._open_twitter_media_at_index(page, target_url, index)
    except Exception:
      return False

  def _process_twitter_carousel(self, page, target_url: str, media_count: int, in_viewer: bool = False) -> str | None:
    slides = min(max(1, int(media_count or 1)), 8)

    if slides <= 1:
      wait_result = self._wait_with_interrupt(self.image_dwell_seconds, page)
      if wait_result == "skip":
        return "skip_post"
      if wait_result == "prev":
        self._consume_prev_request()
        return "prev_post"
      if wait_result == "stop":
        return None
      return None

    for slide_index in range(slides):
      if self._stop_event.is_set():
        return None

      if self._consume_prev_request():
        return "prev_post"

      opened = False
      if in_viewer and slide_index == 0:
        opened = True
      else:
        opened = self._open_twitter_media_at_index(page, target_url, slide_index)
        if not opened and slide_index == 0:
          opened = self._open_twitter_image_viewer(page, target_url)

      if not opened:
        if slide_index == 0:
          fallback_wait = self._wait_with_interrupt(self.image_dwell_seconds, page)
          if fallback_wait == "skip":
            return "skip_post"
          if fallback_wait == "prev":
            self._consume_prev_request()
            return "prev_post"
          return None
        self._log(
          f"Carrusel: no pude abrir media {slide_index + 1}/{slides} en {target_url}. Se continua al siguiente post."
        )
        return "skip_post"

      settle_result = self._wait_with_interrupt(max(0.2, min(0.7, self.scroll_pause_seconds)), page)
      if settle_result == "stop":
        return None
      if settle_result == "prev":
        self._consume_prev_request()
        return "prev_post"
      if settle_result == "skip":
        if slide_index >= slides - 1:
          return "skip_post"
        try:
          self._close_twitter_image_viewer(page)
        except Exception:
          pass
        continue

      media_result = self._wait_viewer_video_or_timeout(page)
      if media_result == "stop":
        return None
      if media_result == "prev":
        self._consume_prev_request()
        return "prev_post"
      if media_result == "skip" and slide_index >= slides - 1:
        try:
          self._close_twitter_image_viewer(page)
        except Exception:
          pass
        return "skip_post"

      try:
        self._close_twitter_image_viewer(page)
      except Exception:
        pass

      post_close = self._wait_with_interrupt(max(0.12, self.poll_seconds * 0.35), page)
      if post_close == "stop":
        return None
      if post_close == "prev":
        self._consume_prev_request()
        return "prev_post"
      if post_close == "skip" and slide_index >= slides - 1:
        return "skip_post"

    return None

  def _process_twitter_image_item(self, page, target_url: str, media_count: int) -> str | None:
    total_media = max(1, int(media_count or 1))

    if total_media > 1:
      return self._process_twitter_carousel(page, target_url, total_media, in_viewer=False)

    try:
      opened = self._open_twitter_image_viewer(page, target_url)
    except Exception as exc:
      self._log(f"Error abriendo viewer: {exc}")
      opened = False

    if not opened:
      wait_result = self._wait_with_interrupt(self.image_dwell_seconds, page)
      if wait_result == "skip":
        return "skip_post"
      if wait_result == "prev":
        self._consume_prev_request()
        return "prev_post"
      return None

    try:
      wait_result = self._wait_viewer_video_or_timeout(page)
      if wait_result == "stop":
        return None
      if wait_result == "prev":
        self._consume_prev_request()
        return "prev_post"
      if wait_result == "skip":
        self._consume_skip_request()
        return "skip_post"
      return None
    finally:
      try:
        self._close_twitter_image_viewer(page)
      except Exception:
        pass

  def _open_twitter_image_viewer(self, page, target_url: str) -> bool:
    # Abrir siempre desde el primer media del carrusel para mantener indice consistente.
    if self._open_twitter_media_at_index(page, target_url, 0):
      return True

    try:
      result = page.evaluate(
        """
        (targetUrl) => {
          const canonical = (href) => {
            if (!href) return null;
            const m = String(href).match(/https?:\/\/(?:www\.)?(?:x|twitter)\.com\/([^\/?#]+)\/status\/(\d+)/i);
            if (!m) return href;
            return `https://x.com/${m[1]}/status/${m[2]}`;
          };

          const articles = Array.from(document.querySelectorAll('article'));
          for (const article of articles) {
            const statusAnchor = article.querySelector('a[href*="/status/"]');
            if (!statusAnchor || canonical(statusAnchor.href) !== targetUrl) continue;

            const preferred = article.querySelector('a[href*="/status/"][href*="/video/"]')
              || article.querySelector('a[href*="/status/"][href*="/video/"] video')
              || article.querySelector('video')
              || article.querySelector('[data-testid="videoPlayer"]')
              || article.querySelector('[data-testid="videoComponent"]')
              || article.querySelector('a[href*="/status/"][href*="/photo/"]')
              || article.querySelector('a[href*="/status/"][href*="/photo/"] img')
              || article.querySelector('img[src*="twimg.com/media"], img[src*="pbs.twimg.com/media"]');
            if (!preferred) return false;

            const clickable = preferred.closest('a[href*="/photo/"], a[href*="/video/"], div[role="button"], button') || preferred;
            try {
              clickable.scrollIntoView({ behavior: 'instant', block: 'center' });
            } catch {}
            try {
              clickable.click();
              return true;
            } catch {
              try {
                preferred.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                return true;
              } catch {
                return false;
              }
            }
          }
          return false;
        }
        """,
        target_url,
      )
      return bool(result)
    except Exception as exc:
      self._log(f"Error en evaluate abriendo viewer: {exc}")
      return False

  def _open_twitter_media_at_index(self, page, target_url: str, index: int) -> bool:
    try:
      idx = max(0, int(index))
    except Exception:
      idx = 0

    try:
      result = page.evaluate(
        """
        ({ targetUrl, index }) => {
          const canonical = (href) => {
            if (!href) return null;
            const m = String(href).match(/https?:\/\/(?:www\.)?(?:x|twitter)\.com\/([^\/?#]+)\/status\/(\d+)/i);
            if (!m) return href;
            return `https://x.com/${m[1]}/status/${m[2]}`;
          };

          const articles = Array.from(document.querySelectorAll('article'));
          for (const article of articles) {
            const statusAnchor = article.querySelector('a[href*="/status/"]');
            if (!statusAnchor || canonical(statusAnchor.href) !== targetUrl) continue;

            const ordered = [];
            const seen = new Set();

            const pushCandidate = (el) => {
              if (!el) return;
              const clickable = el.closest('a[href*="/photo/"], a[href*="/video/"], div[role="button"], button') || el;
              if (!clickable) return;
              const sourceKey = el.getAttribute('href')
                || el.getAttribute('src')
                || el.getAttribute('data-testid')
                || '';
              const key = `${clickable.tagName}|${clickable.getAttribute('href') || ''}|${clickable.getAttribute('aria-label') || ''}|${sourceKey}`;
              if (seen.has(key)) return;
              seen.add(key);
              ordered.push({ clickable, source: el });
            };

            const mediaAnchors = Array.from(article.querySelectorAll('a[href*="/status/"][href*="/photo/"], a[href*="/status/"][href*="/video/"]'));
            for (const anchor of mediaAnchors) pushCandidate(anchor);

            if (!ordered.length) {
              const mediaNodes = Array.from(article.querySelectorAll('video, img[src*="twimg.com/media"], img[src*="pbs.twimg.com/media"]'));
              for (const node of mediaNodes) pushCandidate(node);
            }

            if (!ordered.length) return false;
            const pick = ordered[Math.min(Math.max(0, Number(index) || 0), ordered.length - 1)];
            const clickable = pick.clickable;
            const source = pick.source || clickable;
            try {
              source.scrollIntoView({ behavior: 'instant', block: 'center' });
            } catch {}

            try {
              clickable.click();
              return true;
            } catch {
              try {
                source.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                return true;
              } catch {
                return false;
              }
            }
          }
          return false;
        }
        """,
        {"targetUrl": target_url, "index": idx},
      )
      return bool(result)
    except Exception as exc:
      self._log(f"Error en open media por indice ({idx}): {exc}")
      return False

  def _close_twitter_image_viewer(self, page) -> None:
    try:
      closed = page.evaluate(
        """
        () => {
          const closeBtn = document.querySelector('button[aria-label*="Close" i], button[aria-label*="Cerrar" i], div[role="button"][aria-label*="Close" i], div[role="button"][aria-label*="Cerrar" i]');
          if (closeBtn) {
            try {
              closeBtn.click();
              return true;
            } catch {}
          }
          return false;
        }
        """
      )
    except Exception as exc:
      if self._stop_event.is_set() or self._kill_event.is_set() or self._is_closed_target_error(exc):
        return
      self._log(f"Error en evaluate cerrar viewer: {exc}")
      closed = False

    if not closed:
      try:
        page.keyboard.press("Escape")
      except Exception as exc:
        if self._stop_event.is_set() or self._kill_event.is_set() or self._is_closed_target_error(exc):
          return
        self._log(f"Error en Escape cerrar viewer: {exc}")

    try:
      wait_result = self._wait_with_interrupt(max(0.15, self.poll_seconds * 0.4), page)
      if wait_result in {"skip", "stop", "prev"}:
        return
    except Exception as exc:
      if self._stop_event.is_set() or self._kill_event.is_set() or self._is_closed_target_error(exc):
        return
      self._log(f"Error durante wait cerrar viewer: {exc}")
      return

  def _twitter_click_next_media(self, page, target_url: str, in_viewer: bool = False) -> bool:
    try:
      clicked = bool(
        page.evaluate(
          """
          ({ targetUrl, inViewer }) => {
            const pickButton = (root) => root.querySelector('button[data-testid="carouselControl-right"], button[aria-label*="Next" i], button[aria-label*="Siguiente" i], div[role="button"][aria-label*="Next" i], div[role="button"][aria-label*="Siguiente" i]');
            if (inViewer) {
              const nextViewer = pickButton(document);
              if (!nextViewer) return false;
              try {
                nextViewer.click();
                return true;
              } catch {
                return false;
              }
            }

            const articles = Array.from(document.querySelectorAll('article'));
            const canonical = (href) => {
              if (!href) return null;
              const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
              if (!m) return href;
              return `https://x.com/${m[1]}/status/${m[2]}`;
            };

            for (const article of articles) {
              const link = article.querySelector('a[href*="/status/"]');
              if (!link || canonical(link.href) !== targetUrl) continue;

              const nextBtn = pickButton(article);
              if (!nextBtn) return false;
              nextBtn.click();
              return true;
            }
            return false;
          }
          """,
          {"targetUrl": target_url, "inViewer": bool(in_viewer)},
        )
      )
    except Exception as exc:
      self._log(f"Error en click next media: {exc}")
      clicked = False

    if clicked:
      return True
    if in_viewer:
      return False
    try:
      page.keyboard.press("ArrowRight")
      return True
    except Exception as exc:
      self._log(f"Error en ArrowRight: {exc}")
      return False

  def _twitter_click_previous_media(self, page, target_url: str, in_viewer: bool = False) -> bool:
    try:
      clicked = bool(
        page.evaluate(
          """
          ({ targetUrl, inViewer }) => {
            const pickButton = (root) => root.querySelector('button[data-testid="carouselControl-left"], button[aria-label*="Previous" i], button[aria-label*="Back" i], button[aria-label*="Anterior" i], button[aria-label*="Atrás" i], div[role="button"][aria-label*="Previous" i], div[role="button"][aria-label*="Back" i], div[role="button"][aria-label*="Anterior" i], div[role="button"][aria-label*="Atrás" i]');
            if (inViewer) {
              const prevViewer = pickButton(document);
              if (!prevViewer) return false;
              try {
                prevViewer.click();
                return true;
              } catch {
                return false;
              }
            }

            const articles = Array.from(document.querySelectorAll('article'));
            const canonical = (href) => {
              if (!href) return null;
              const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
              if (!m) return href;
              return `https://x.com/${m[1]}/status/${m[2]}`;
            };

            for (const article of articles) {
              const link = article.querySelector('a[href*="/status/"]');
              if (!link || canonical(link.href) !== targetUrl) continue;

              const prevBtn = pickButton(article);
              if (!prevBtn) return false;
              prevBtn.click();
              return true;
            }
            return false;
          }
          """,
          {"targetUrl": target_url, "inViewer": bool(in_viewer)},
        )
      )
    except Exception as exc:
      self._log(f"Error en click previous media: {exc}")
      clicked = False

    if clicked:
      return True
    if in_viewer:
      return False
    try:
      page.keyboard.press("ArrowLeft")
      return True
    except Exception as exc:
      self._log(f"Error en ArrowLeft: {exc}")
      return False

  def _twitter_item_abs_top(self, page, target_url: str) -> float | None:
    clean_url = str(target_url or "").strip()
    if not clean_url:
      return None

    value = page.evaluate(
      """
      (targetUrl) => {
        const canonical = (href) => {
          if (!href) return null;
          const m = String(href).match(/https?:\/\/(?:www\.)?(?:x|twitter)\.com\/([^\/?#]+)\/status\/(\d+)/i);
          if (!m) return href;
          return `https://x.com/${m[1]}/status/${m[2]}`;
        };

        const articles = Array.from(document.querySelectorAll('article'));
        for (const article of articles) {
          const links = Array.from(article.querySelectorAll('a[href*="/status/"]'));
          if (!links.length) continue;
          const hasTarget = links.some((link) => canonical(link.href || link.getAttribute('href') || '') === targetUrl);
          if (!hasTarget) continue;
          const r = article.getBoundingClientRect();
          const absTop = (window.scrollY || window.pageYOffset || 0) + r.top;
          return Number(absTop);
        }
        return null;
      }
      """,
      clean_url,
    )

    try:
      if value is None:
        return None
      return float(value)
    except Exception:
      return None

  def _move_to_next_twitter_item(self, page, current_url: str, current_abs_top: float) -> bool:
    moved = page.evaluate(
      """
      ({ currentUrl, currentAbsTop }) => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const articles = Array.from(document.querySelectorAll('article'));
        const canonical = (href) => {
          if (!href) return null;
          const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
          if (!m) return href;
          return `https://x.com/${m[1]}/status/${m[2]}`;
        };

        const candidates = [];
        for (const article of articles) {
          const link = article.querySelector('a[href*="/status/"]');
          if (!link) continue;
          const url = canonical(link.href);
          const r = article.getBoundingClientRect();
          const absTop = (window.scrollY || window.pageYOffset || 0) + r.top;
          const hasMedia = Boolean(article.querySelector('video, img'));
          if (!hasMedia) continue;

          if (url === currentUrl) continue;
          if (absTop <= Number(currentAbsTop || 0) + 40) continue;

          candidates.push({ top: r.top, center: r.top + r.height / 2, absTop });
        }

        candidates.sort((a, b) => a.absTop - b.absTop);
        const next = candidates.length ? candidates[0] : null;
        if (!next) {
          window.scrollBy({ top: 260, left: 0, behavior: 'auto' });
          return false;
        }

        const delta = next.center - (vh / 2);
        const step = Math.max(-40, Math.min(280, delta));
        window.scrollBy({ top: step, left: 0, behavior: 'auto' });
        return true;
      }
      """,
      {"currentUrl": current_url, "currentAbsTop": float(current_abs_top)},
    )
    return bool(moved)

  def _move_to_previous_twitter_item(self, page, current_url: str, current_abs_top: float) -> bool:
    moved = page.evaluate(
      """
      ({ currentUrl, currentAbsTop }) => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const articles = Array.from(document.querySelectorAll('article'));
        const canonical = (href) => {
          if (!href) return null;
          const m = String(href).match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
          if (!m) return href;
          return `https://x.com/${m[1]}/status/${m[2]}`;
        };

        const candidates = [];
        for (const article of articles) {
          const link = article.querySelector('a[href*="/status/"]');
          if (!link) continue;
          const url = canonical(link.href);
          const r = article.getBoundingClientRect();
          const absTop = (window.scrollY || window.pageYOffset || 0) + r.top;
          const hasMedia = Boolean(article.querySelector('video, img'));
          if (!hasMedia) continue;

          if (url === currentUrl) continue;
          if (absTop >= Number(currentAbsTop || 0) - 40) continue;

          candidates.push({ top: r.top, center: r.top + r.height / 2, absTop });
        }

        candidates.sort((a, b) => b.absTop - a.absTop);
        const prev = candidates.length ? candidates[0] : null;
        if (!prev) {
          window.scrollBy({ top: -260, left: 0, behavior: 'auto' });
          return false;
        }

        const delta = prev.center - (vh / 2);
        const step = Math.max(-280, Math.min(0, delta));
        window.scrollBy({ top: step, left: 0, behavior: 'auto' });
        return true;
      }
      """,
      {"currentUrl": current_url, "currentAbsTop": float(current_abs_top)},
    )
    return bool(moved)

  def _primary_visible_video_state(self, page) -> dict:
    return page.evaluate(
      """
      () => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const vw = window.innerWidth || document.documentElement.clientWidth;
        const videos = Array.from(document.querySelectorAll('video'));
        let best = null;
        let bestArea = 0;

        for (const v of videos) {
          const r = v.getBoundingClientRect();
          const visible = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
          if (!visible) continue;

          const width = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
          const height = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
          const area = width * height;
          if (area <= bestArea) continue;

          bestArea = area;
          best = v;
        }

        if (!best) {
          return { has_video: false, ended: false, current_time: 0, duration: 0 };
        }

        const duration = Number(best.duration || 0);
        const currentTime = Number(best.currentTime || 0);
        const ended = Boolean(best.ended) || (duration > 0 && currentTime >= duration - 0.35);

        return {
          has_video: true,
          ended,
          current_time: currentTime,
          duration,
        };
      }
      """
    )

  def _has_primary_visible_video(self, page) -> bool:
    state = self._primary_visible_video_state(page)
    return bool(state.get("has_video", False))

  def _sync_page_mute_state(self, page) -> None:
    muted = self.is_muted()
    try:
      page.evaluate(
        """
        (muted) => {
          const media = Array.from(document.querySelectorAll('video, audio'));
          const vh = window.innerHeight || document.documentElement.clientHeight || 0;
          const vw = window.innerWidth || document.documentElement.clientWidth || 0;

          let primary = null;
          let primaryArea = 0;
          for (const node of media) {
            try {
              const rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
              const visible = rect && rect.bottom > 0 && rect.top < vh && rect.right > 0 && rect.left < vw;
              if (visible) {
                const area = Math.max(0, Math.min(rect.right, vw) - Math.max(rect.left, 0))
                  * Math.max(0, Math.min(rect.bottom, vh) - Math.max(rect.top, 0));
                if (area > primaryArea) {
                  primaryArea = area;
                  primary = node;
                }
              }

              node.muted = Boolean(muted);
              node.defaultMuted = Boolean(muted);
              if (muted) {
                node.volume = 0;
              } else if (Number(node.volume || 0) === 0) {
                node.volume = 1;
              }
            } catch (_) {
              // Ignore individual node failures and continue syncing the rest.
            }
          }

          // Fallback for custom players that ignore plain media.muted updates.
          try {
            const normalize = (value) => String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
            const isVisible = (el) => {
              if (!el || !el.getBoundingClientRect) return false;
              const r = el.getBoundingClientRect();
              if (r.width < 8 || r.height < 8) return false;
              return r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
            };

            const root = primary
              ? (primary.closest('[role="dialog"], article, [data-testid="videoPlayer"], [data-testid="videoComponent"]') || document)
              : document;

            const controls = Array.from(root.querySelectorAll('button, div[role="button"]')).filter(isVisible);
            const muteHints = ['mute', 'silenciar', 'quitar sonido'];
            const unmuteHints = ['unmute', 'activar sonido', 'con sonido', 'sonido'];

            let candidate = null;
            for (const btn of controls) {
              const label = normalize(btn.getAttribute('aria-label') || btn.innerText || btn.textContent || '');
              if (!label) continue;
              if (muted && muteHints.some((hint) => label.includes(hint))) {
                candidate = btn;
                break;
              }
              if (!muted && unmuteHints.some((hint) => label.includes(hint))) {
                candidate = btn;
                break;
              }
            }

            if (candidate) {
              candidate.click();
            }
          } catch (_) {
            // Ignore fallback-control errors.
          }

          window.__feed_scraper_muted = Boolean(muted);
        }
        """,
        muted,
      )
    except Exception:
      pass

  def _apply_window_placement(self, page) -> None:
    try:
      cdp = page.context.new_cdp_session(page)
      info = cdp.send("Browser.getWindowForTarget")
      window_id = info["windowId"]
      bounds = self._normalized_monitor_bounds()
      if bounds:
        # First move/resize the native window to the selected monitor.
        cdp.send(
          "Browser.setWindowBounds",
          {
            "windowId": window_id,
            "bounds": {
              "windowState": "normal",
              "left": bounds["left"],
              "top": bounds["top"],
              "width": bounds["width"],
              "height": bounds["height"],
            },
          },
        )

        # Then maximize while it is already located on the target monitor.
        if self.start_maximized:
          cdp.send(
            "Browser.setWindowBounds",
            {
              "windowId": window_id,
              "bounds": {"windowState": "maximized"},
            },
          )
      elif self.start_maximized:
        cdp.send("Browser.setWindowBounds", {
          "windowId": window_id,
          "bounds": {"windowState": "maximized"},
        })
    except Exception:
      pass

  def _apply_pending_window_state(self, page, force: bool = False) -> None:
    pending: bool | None = None
    with self._state_lock:
      pending = self._pending_window_state
      if force and pending is None:
        pending = self._window_fullscreen
      self._pending_window_state = None

    if pending is None:
      return

    self._apply_window_state(page, bool(pending))

  def _apply_window_state(self, page, fullscreen: bool) -> None:
    try:
      cdp = page.context.new_cdp_session(page)
      info = cdp.send("Browser.getWindowForTarget")
      window_id = info["windowId"]
      if fullscreen:
        try:
          cdp.send(
            "Browser.setWindowBounds",
            {
              "windowId": window_id,
              "bounds": {"windowState": "fullscreen"},
            },
          )
        except Exception:
          cdp.send(
            "Browser.setWindowBounds",
            {
              "windowId": window_id,
              "bounds": {"windowState": "maximized"},
            },
          )
      else:
        bounds = self._normalized_monitor_bounds()
        if bounds:
          cdp.send(
            "Browser.setWindowBounds",
            {
              "windowId": window_id,
              "bounds": {
                "windowState": "normal",
                "left": bounds["left"],
                "top": bounds["top"],
                "width": bounds["width"],
                "height": bounds["height"],
              },
            },
          )
        cdp.send(
          "Browser.setWindowBounds",
          {
            "windowId": window_id,
            "bounds": {"windowState": "maximized"},
          },
        )
    except Exception:
      pass

  def _normalized_monitor_bounds(self) -> dict | None:
    raw = self.monitor_bounds
    if not isinstance(raw, dict):
      return None
    try:
      left = int(raw.get("left", 0))
      top = int(raw.get("top", 0))
      width = max(640, int(raw.get("width", 0)))
      height = max(480, int(raw.get("height", 0)))
      return {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
      }
    except Exception:
      return None

  def _advance(self, page) -> None:
    page.evaluate(
      """
      (distance) => {
        window.scrollBy({ top: Number(distance) || 900, left: 0, behavior: 'smooth' });
      }
      """,
      self.scroll_px,
    )

  def _retreat(self, page) -> None:
    page.evaluate(
      """
      (distance) => {
        window.scrollBy({ top: -(Number(distance) || 900), left: 0, behavior: 'smooth' });
      }
      """,
      self.scroll_px,
    )

  def _detect_visible_url(self, page, platform: Platform) -> str | None:
    match platform:
      case Platform.TWITTER:
        return page.evaluate(
        """
        () => {
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const center = vh / 2;
          const articles = Array.from(document.querySelectorAll('article'));
          let best = null;
          let bestScore = Infinity;

          for (const article of articles) {
            const rect = article.getBoundingClientRect();
            const visible = rect.bottom > 0 && rect.top < vh;
            if (!visible) continue;

            let link = article.querySelector('a[href*="/status/"][href*="/photo/"]')
              || article.querySelector('a[href*="/status/"][href*="/video/"]')
              || article.querySelector('a[href*="/status/"]');
            if (!link) continue;

            const articleCenter = rect.top + rect.height / 2;
            const score = Math.abs(center - articleCenter);
            if (score < bestScore) {
              bestScore = score;
              best = link.href;
            }
          }

          if (!best) return null;
          const m = best.match(/https?:\\/\\/(?:www\\.)?(?:x|twitter)\\.com\\/([^\\/?#]+)\\/status\\/(\\d+)/i);
          if (!m) return best;
          return `https://x.com/${m[1]}/status/${m[2]}`;
        }
        """
        )

      case Platform.INSTAGRAM:
        return page.evaluate(
        """
        () => {
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const links = Array.from(document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]'));
          let best = null;
          let bestScore = Infinity;

          for (const link of links) {
            const rect = link.getBoundingClientRect();
            const visible = rect.bottom > 0 && rect.top < vh;
            if (!visible) continue;

            const center = Math.abs((vh / 2) - (rect.top + rect.height / 2));
            if (center < bestScore) {
              bestScore = center;
              best = link.href;
            }
          }

          return best;
        }
        """
        )

      case Platform.TIKTOK:
        return page.evaluate(
        """
        () => {
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const links = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/photo/"]'));
          let best = null;
          let bestScore = Infinity;

          for (const link of links) {
            const rect = link.getBoundingClientRect();
            const visible = rect.bottom > 0 && rect.top < vh;
            if (!visible) continue;

            const center = Math.abs((vh / 2) - (rect.top + rect.height / 2));
            if (center < bestScore) {
              bestScore = center;
              best = link.href;
            }
          }

          return best;
        }
        """
        )

      case Platform.YOUTUBE:
        return page.evaluate(
        """
        () => {
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const links = Array.from(document.querySelectorAll('a[href*="/shorts/"]'));
          let best = null;
          let bestScore = Infinity;

          for (const link of links) {
            const rect = link.getBoundingClientRect();
            const visible = rect.bottom > 0 && rect.top < vh;
            if (!visible) continue;

            const center = Math.abs((vh / 2) - (rect.top + rect.height / 2));
            if (center < bestScore) {
              bestScore = center;
              best = link.href;
            }
          }

          return best;
        }
        """
        )

    return None
