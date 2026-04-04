import os
import threading
import time
import json
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

    self._log_callback: Callable[[str], None] | None = None
    self._stop_event = threading.Event()
    self._thread: threading.Thread | None = None
    self._seen_urls: set[str] = set()
    self._deps_verified = False

  def set_log_callback(self, callback: Callable[[str], None]) -> None:
    self._log_callback = callback

  def _log(self, message: str) -> None:
    if self._log_callback:
      self._log_callback(message)

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
    selected = Platform.parse(platform)
    self._thread = threading.Thread(target=self._run, args=(selected,), daemon=True)
    self._thread.start()

  def stop(self) -> None:
    self._stop_event.set()
    if self._thread and self._thread.is_alive():
      self._thread.join(timeout=5)

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
    preferred = os.path.join(root_dir, "browser_profile")
    if os.path.isdir(preferred):
      return preferred
    return os.path.join(root_dir, "downloader", "browser_profile")

  def _existing_cookie_files(self) -> list[str]:
    import random
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
      while not self._stop_event.is_set():
        context = None
        page = None
        try:
          self._log(f"Abriendo feed {platform.value}: {start_url}")
          browser_args = ["--disable-infobars"]
          if self.start_maximized:
            browser_args.append("--start-maximized")

          context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            no_viewport=True,
            args=browser_args,
          )
          self._apply_context_cookies(context)
          page = self._prepare_page(context)
          page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
          self._maximize_window(page)
          self._log("Navegador listo. Desplazando y detectando URL visible...")

          self._scrape_loop(page, platform)
        except Exception as exc:
          if self._stop_event.is_set():
            break
          self._log(f"Aviso scraper: {exc}")
          self._log("Reintentando inicializar navegador del feed en 3 segundos...")
          time.sleep(3)
        finally:
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

    while not self._stop_event.is_set():
      current = self._twitter_top_visible_media_item(page, min_abs_top=last_processed_abs_top)
      if not current:
        self._safe_scroll_down(page, min(320, max(140, int(self.scroll_px * 0.35))))
        time.sleep(self.scroll_pause_seconds)
        continue

      current_url = str(current.get("url") or "").strip()
      if not current_url:
        self._scroll_by(page, min(260, max(120, int(self.scroll_px * 0.25))))
        time.sleep(self.scroll_pause_seconds)
        continue

      self._center_twitter_item(page, current_url)

      if current_url not in self._seen_urls:
        self._seen_urls.add(current_url)
        self._log(f"Detectado: {current_url}")
        try:
          self.on_url_detected(current_url)
        except Exception as callback_exc:
          self._log(f"Aviso callback descarga: {callback_exc}")

      media_count = int(current.get("media_count") or 1)
      is_video = bool(current.get("has_video", False))
      current_abs_top = float(current.get("abs_top") or 0.0)

      if current_abs_top > last_processed_abs_top:
        last_processed_abs_top = current_abs_top

      if is_video:
        self._play_and_unmute_primary_video(page, current_url)
        self._try_fullscreen_current_video(page, current_url, enter=True)
        self._wait_video_or_timeout(page, current_url)
        self._try_fullscreen_current_video(page, current_url, enter=False)
      else:
        self._process_twitter_carousel(page, current_url, media_count)

      moved = self._move_to_next_twitter_item(page, current_url, last_processed_abs_top)
      if not moved:
        self._safe_scroll_down(page, min(320, max(140, int(self.scroll_px * 0.35))))
      time.sleep(self.scroll_pause_seconds)

  def _twitter_top_visible_media_item(self, page, min_abs_top: float = -1.0) -> dict | None:
    return page.evaluate(
      """
      (minAbsTop) => {
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
          const media = Array.from(article.querySelectorAll('img, video'));
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

        const out = [];
        for (const article of articles) {
          const rect = article.getBoundingClientRect();
          const visible = rect.bottom > 0 && rect.top < vh;
          if (!visible) continue;

          const link = article.querySelector('a[href*="/status/"]');
          if (!link) continue;

          const mediaCount = visibleMediaCount(article);
          const hasVideo = Boolean(article.querySelector('video'));
          if (mediaCount <= 0 && !hasVideo) continue;

          const absTop = (window.scrollY || window.pageYOffset || 0) + rect.top;
          if (absTop <= Number(minAbsTop || -1) + 40) continue;

          out.push({
            url: canonical(link.href),
            raw_url: link.href,
            top: rect.top,
            abs_top: absTop,
            center: rect.top + rect.height / 2,
            has_video: hasVideo,
            media_count: Math.max(1, mediaCount),
          });
        }

        out.sort((a, b) => a.top - b.top);
        return out.length ? out[0] : null;
      }
      """
      ,
      min_abs_top,
    )

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

      step = max(-220.0, min(220.0, delta * 0.55))
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

          try {
            best.muted = false;
            best.volume = 1.0;
            best.play();
          } catch {}

          const duration = Number(best.duration || 0);
          const currentTime = Number(best.currentTime || 0);
          const ended = Boolean(best.ended) || (duration > 0 && currentTime >= duration - 0.35);
          return {
            has_video: true,
            ended,
            paused: Boolean(best.paused),
            current_time: currentTime,
            duration,
          };
        }

        return { has_video: false, ended: false, paused: true, current_time: 0, duration: 0 };
      }
      """,
      target_url,
    )

  def _play_and_unmute_primary_video(self, page, target_url: str) -> None:
    _ = self._video_state_for_url(page, target_url)

  def _wait_video_or_timeout(self, page, target_url: str) -> None:
    if not self.wait_video_end:
      time.sleep(self.image_dwell_seconds)
      return

    start = time.time()
    while not self._stop_event.is_set():
      state = self._video_state_for_url(page, target_url)
      if not state.get("has_video", False):
        time.sleep(self.image_dwell_seconds)
        return

      if bool(state.get("paused", False)):
        # Fuerza play si se pausa por autoplay policy.
        self._play_and_unmute_primary_video(page, target_url)

      if bool(state.get("ended", False)):
        return

      if (time.time() - start) >= self.max_video_wait_seconds:
        self._log(
          f"Aviso feed: maximo de espera de video alcanzado ({int(self.max_video_wait_seconds)}s), avanzando."
        )
        return

      time.sleep(max(0.2, self.poll_seconds))

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
      }
      """,
      {"targetUrl": target_url, "enter": enter},
    )

  def _process_twitter_carousel(self, page, target_url: str, media_count: int) -> None:
    # Si no hay carrusel, espera tiempo de imagen normal.
    if media_count <= 1:
      time.sleep(self.image_dwell_seconds)
      return

    slides = min(max(1, media_count), 8)
    for idx in range(slides):
      if self._stop_event.is_set():
        return
      time.sleep(self.image_dwell_seconds)
      if idx >= slides - 1:
        break
      moved = self._twitter_click_next_media(page, target_url)
      if not moved:
        break
      time.sleep(max(0.15, self.poll_seconds * 0.5))

  def _twitter_click_next_media(self, page, target_url: str) -> bool:
    return bool(
      page.evaluate(
        """
        (targetUrl) => {
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

            const nextBtn = article.querySelector('button[data-testid="carouselControl-right"], button[aria-label*="Next" i], div[role="button"][aria-label*="Next" i]');
            if (!nextBtn) return false;
            nextBtn.click();
            return true;
          }
          return false;
        }
        """,
        target_url,
      )
    )

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
          window.scrollBy({ top: 260, left: 0, behavior: 'smooth' });
          return false;
        }

        const delta = next.center - (vh / 2);
        const step = Math.max(-40, Math.min(280, delta));
        window.scrollBy({ top: step, left: 0, behavior: 'smooth' });
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

  def _maximize_window(self, page) -> None:
    if not self.start_maximized:
      return
    try:
      cdp = page.context.new_cdp_session(page)
      info = cdp.send("Browser.getWindowForTarget")
      cdp.send("Browser.setWindowBounds", {
        "windowId": info["windowId"],
        "bounds": {"windowState": "maximized"},
      })
    except Exception:
      pass

  def _advance(self, page) -> None:
    page.evaluate(
      """
      (distance) => {
        window.scrollBy({ top: Number(distance) || 900, left: 0, behavior: 'smooth' });
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
