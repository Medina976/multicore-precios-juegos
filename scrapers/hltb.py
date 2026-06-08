"""
scrapers/hltb.py
Dueño: Brack

Extrae los tiempos de juego de HowLongToBeat.com.

ESTRATEGIA (3 capas, de más rápida a más robusta):
    1. POST directo con hash cacheado (sin navegador).
       → Si responde 200, se retorna inmediatamente.
    2. Si el hash expiró (404/400): re-extracción del hash con requests
       escaneando los chunks de Next.js.
       → Si se obtiene un hash nuevo, se reintenta el POST.
    3. Si requests también falla (HLTB bloquea la descarga de chunks):
       se lanza Selenium headless para cargar la página completa con JS
       y extraer el hash del bundle renderizado.
       → Se retorna el hash fresco y se reintenta el POST.

Por qué Selenium como último recurso y no como primera opción:
    - Selenium es ~10x más lento que requests.
    - En el orquestador se lanzan 10 juegos en paralelo; si cada uno
      abriera un navegador la RAM se agotaría.
    - Con el hash cacheado en memoria, el 99% de las llamadas pasan
      por la capa 1 (POST directo, sin navegador).
    - Solo se lanza Selenium cuando el hash realmente expiró y requests
      no pudo obtener uno nuevo. Eso ocurre una vez por sesión, no por juego.

Firma requerida por orquestador_brack.py:
    scraper.obtener_datos(juego: dict) -> dict

Retorna:
    {
        "main":           float | None,   # historia principal (horas)
        "main_extra":     float | None,   # historia + extras
        "completionist":  float | None,   # 100% completionista
    }

Dependencias adicionales (agregar a requirements.txt):
    selenium>=4.20.0
    webdriver-manager>=4.0.1
    fake-useragent>=1.5.1     # ya estaba
"""

import re
import time
import logging
import threading
import requests
from fake_useragent import UserAgent

log = logging.getLogger(__name__)

_ua = UserAgent()
_MAX_REINTENTOS = 3
_ESPERA_BASE    = 2

_HLTB_HOME        = "https://howlongtobeat.com/"
_HLTB_SEARCH_BASE = "https://howlongtobeat.com/api/search"
_HLTB_REFERER     = "https://howlongtobeat.com/"

# ─── Caché de hash (compartido entre threads, protegido con lock) ────────────
_hash_cache: str | None = None
_hash_lock  = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 2 — extracción de hash con requests (sin navegador)
# ═══════════════════════════════════════════════════════════════════════════

def _extraer_hash_via_requests() -> str | None:
    """
    Descarga la página principal de HLTB y sus chunks de Next.js con
    requests (sin JS) e intenta extraer el hash del endpoint de búsqueda.

    Funciona cuando HLTB no bloquea el User-Agent y el hash está visible
    en el HTML o en los primeros chunks JS.
    """
    try:
        hdrs = {"User-Agent": _ua.random, "Accept": "text/html,*/*"}
        resp = requests.get(_HLTB_HOME, headers=hdrs, timeout=15)
        resp.raise_for_status()
        html = resp.text

        # Patrón directo en HTML/JS inlined
        m = re.search(r'/api/search/([a-zA-Z0-9]{10,35})["\'/]', html)
        if m:
            log.debug(f"[HLTB] Hash vía requests (HTML): {m.group(1)}")
            return m.group(1)

        # Buscar en chunks de Next.js — máx 8 para no tardar demasiado
        chunk_urls = re.findall(r'"(/_next/static/chunks/[^"]+\.js)"', html)
        for cu in chunk_urls[:8]:
            try:
                cs = requests.get(
                    f"https://howlongtobeat.com{cu}",
                    headers=hdrs,
                    timeout=10,
                )
                m = re.search(r'/api/search/([a-zA-Z0-9]{10,35})["\'/]', cs.text)
                if m:
                    log.debug(f"[HLTB] Hash vía requests (chunk JS): {m.group(1)}")
                    return m.group(1)
            except requests.RequestException:
                continue

    except requests.RequestException as e:
        log.debug(f"[HLTB] requests falló al extraer hash: {e}")

    return None


# ═══════════════════════════════════════════════════════════════════════════
# CAPA 3 — extracción de hash con Selenium headless (último recurso)
# ═══════════════════════════════════════════════════════════════════════════

def _extraer_hash_via_selenium() -> str | None:
    """
    Abre HLTB con un Chrome headless real. El navegador ejecuta todo el
    JavaScript de Next.js, por lo que el bundle completo queda disponible
    en el DOM y podemos extraer el hash desde el fuente renderizado.

    Se usa webdriver-manager para descargar/mantener chromedriver
    automáticamente (sin instalación manual).

    Solo se llama cuando la capa 2 falla — típicamente una vez por sesión.
    """
    try:
        # Importaciones aquí para no cargar Selenium si nunca se necesita
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
        from webdriver_manager.chrome import ChromeDriverManager

        log.info("[HLTB] Iniciando Selenium headless para extraer hash...")

        opts = Options()
        opts.add_argument("--headless=new")          # Chrome ≥112
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,800")
        opts.add_argument(f"user-agent={_ua.random}")
        # Silenciar logs de Chrome en consola
        opts.add_experimental_option("excludeSwitches", ["enable-logging"])

        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=opts,
        )
        try:
            driver.get(_HLTB_HOME)

            # Esperar hasta que el body tenga contenido real (página cargada)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "main"))
            )

            # Dar tiempo extra para que los chunks JS asíncronos terminen
            time.sleep(2)

            page_source = driver.page_source

            # 1. Buscar en el HTML renderizado
            m = re.search(r'/api/search/([a-zA-Z0-9]{10,35})["\'/]', page_source)
            if m:
                log.info(f"[HLTB] Hash extraído con Selenium (page_source): {m.group(1)}")
                return m.group(1)

            # 2. Buscar en los scripts cargados por el navegador
            scripts = driver.find_elements(By.TAG_NAME, "script")
            for script in scripts:
                src = script.get_attribute("src") or ""
                if "/_next/static/chunks" not in src:
                    continue
                try:
                    # Hacer fetch del chunk desde el navegador (evita CORS/bloqueos)
                    contenido = driver.execute_script(
                        """const r = await fetch(arguments[0]);
                           return await r.text();""",
                        src,
                    )
                    m2 = re.search(r'/api/search/([a-zA-Z0-9]{10,35})["\'/]', contenido or "")
                    if m2:
                        log.info(f"[HLTB] Hash extraído con Selenium (chunk): {m2.group(1)}")
                        return m2.group(1)
                except Exception:
                    continue

        finally:
            driver.quit()

    except Exception as e:
        log.error(f"[HLTB] Selenium falló al extraer hash: {e}")

    return None


# ═══════════════════════════════════════════════════════════════════════════
# Gestión del caché del hash
# ═══════════════════════════════════════════════════════════════════════════

def _obtener_hash(forzar_refresh: bool = False) -> str | None:
    """
    Retorna el hash del endpoint de búsqueda de HLTB.
    Usa caché en memoria (thread-safe).
    Si forzar_refresh=True, ignora el caché y lo extrae de nuevo.
    """
    global _hash_cache

    with _hash_lock:
        if _hash_cache and not forzar_refresh:
            return _hash_cache

        log.info("[HLTB] Extrayendo hash dinámico (capa 2: requests)...")
        h = _extraer_hash_via_requests()

        if not h:
            log.warning("[HLTB] Capa 2 falló. Usando Selenium (capa 3)...")
            h = _extraer_hash_via_selenium()

        if h:
            _hash_cache = h
            log.info(f"[HLTB] Hash cacheado: {h}")
        else:
            log.error("[HLTB] No se pudo obtener el hash por ningún método.")

        return _hash_cache


# ═══════════════════════════════════════════════════════════════════════════
# ScraperHLTB — clase pública consumida por el orquestador
# ═══════════════════════════════════════════════════════════════════════════

class ScraperHLTB:
    """Scraper para tiempos de juego de HowLongToBeat."""

    def obtener_datos(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'titulo'.

        Retorna
        -------
        dict con claves 'main', 'main_extra', 'completionist' (float o None).
        """
        titulo = juego.get("titulo", "")
        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} no tiene título")

        payload = {
            "searchType": "games",
            "searchTerms": titulo.split(),
            "searchPage": 1,
            "size": 5,
            "searchOptions": {
                "games": {
                    "userId": 0,
                    "platform": "",
                    "sortCategory": "popular",
                    "rangeCategory": "main",
                    "rangeTime": {"min": None, "max": None},
                    "gameplay": {"perspective": "", "flow": "", "genre": ""},
                    "rangeYear": {"min": "", "max": ""},
                    "modifier": "",
                },
                "users": {"sortCategory": "postcount"},
                "lists": {"sortCategory": "follows"},
                "filter": "",
                "sort": 0,
                "randomizer": 0,
            },
            "useCache": True,
        }

        espera        = _ESPERA_BASE
        hash_invalido = False   # bandera para forzar refresh en el 2.º intento

        for intento in range(1, _MAX_REINTENTOS + 1):

            # Si el intento anterior detectó hash inválido, forzar re-extracción
            h = _obtener_hash(forzar_refresh=hash_invalido)
            hash_invalido = False

            if not h:
                log.error(f"[HLTB] Sin hash disponible para '{titulo}'. Abortando.")
                return {"main": None, "main_extra": None, "completionist": None}

            search_url = f"{_HLTB_SEARCH_BASE}/{h}"
            hdrs = {
                "User-Agent": _ua.random,
                "Referer":    _HLTB_REFERER,
                "Origin":     "https://howlongtobeat.com",
                "Content-Type": "application/json",
                "Accept":     "application/json, text/plain, */*",
            }

            try:
                resp = requests.post(search_url, json=payload, headers=hdrs, timeout=15)

                # Hash expirado: HLTB devuelve 400 o 404
                if resp.status_code in (400, 404):
                    log.warning(
                        f"[HLTB] Hash '{h}' expiró (HTTP {resp.status_code}). "
                        f"Forzando re-extracción en intento {intento+1}..."
                    )
                    hash_invalido = True
                    if intento < _MAX_REINTENTOS:
                        time.sleep(espera)
                        espera *= 2
                    continue

                # Rate limit
                if resp.status_code == 429:
                    log.warning(f"[HLTB] Rate limit (429). Esperando {espera}s...")
                    time.sleep(espera)
                    espera *= 2
                    continue

                resp.raise_for_status()
                data = resp.json()

                resultados = data.get("data", [])
                if not resultados:
                    log.warning(f"[HLTB] Sin resultados para: '{titulo}'")
                    return {"main": None, "main_extra": None, "completionist": None}

                # Seleccionar el resultado con título más parecido al buscado
                titulo_lower = titulo.lower()
                mejor = resultados[0]
                for res in resultados:
                    nombre_res = (res.get("game_name") or "").lower()
                    if nombre_res == titulo_lower:
                        mejor = res
                        break

                def _seg_a_horas(segundos) -> float | None:
                    """HLTB almacena los tiempos en segundos; convertimos a horas."""
                    if not segundos:
                        return None
                    return round(int(segundos) / 3600, 1)

                resultado = {
                    "main":          _seg_a_horas(mejor.get("comp_main")),
                    "main_extra":    _seg_a_horas(mejor.get("comp_plus")),
                    "completionist": _seg_a_horas(mejor.get("comp_100")),
                }

                log.info(f"[HLTB] {titulo} → {resultado}")
                return resultado

            except requests.RequestException as e:
                log.warning(
                    f"[HLTB] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2

        log.error(f"[HLTB] Agotados {_MAX_REINTENTOS} intentos para '{titulo}'")
        return {"main": None, "main_extra": None, "completionist": None}
