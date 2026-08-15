"""
TTS-сервис с конвейерной обработкой.
Generator Thread генерирует аудио, Player Thread воспроизводит.
"""
import os
import sys
import shutil
import requests
import requests.adapters
import threading
import queue
import io
from pathlib import Path
import numpy as np
import time

from core.error_logger import log_debug, log_message

try:
    import sounddevice as sd
    _HAS_SD = True
except ImportError:
    _HAS_SD = False

try:
    import soundfile as sf
    _HAS_SF = True
except ImportError:
    _HAS_SF = False

try:
    import soxr
    _HAS_SOXR = True
except ImportError:
    _HAS_SOXR = False

try:
    import pyrubberband as pyrb
    _HAS_RUBBERBAND = True
except ImportError:
    _HAS_RUBBERBAND = False

def _setup_rubberband_path():
    """
    pyrubberband вызывает исполняемый файл rubberband через subprocess,
    поэтому он должен быть в PATH. Добавляем локальную папку rubberband/
    проекта (а в exe-сборке — папку рядом с exe).
    """
    if not _HAS_RUBBERBAND:
        return

    roots = [Path(__file__).resolve().parent.parent]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)

    exe_name = "rubberband.exe" if os.name == "nt" else "rubberband"
    for root in roots:
        rb_dir = root / "rubberband"
        if (rb_dir / exe_name).exists():
            if str(rb_dir) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = str(rb_dir) + os.pathsep + os.environ.get("PATH", "")
            break

_setup_rubberband_path()

if _HAS_RUBBERBAND:
    _rb_exe = shutil.which("rubberband")
    if _rb_exe:
        print(f"[TTS] ✓ pyrubberband доступен (exe: {_rb_exe})")
    else:
        print("[TTS] ⚠ pyrubberband импортирован, но rubberband.exe не найден в PATH — будет soxr")
        _HAS_RUBBERBAND = False
else:
    print("[TTS] ⚠ pyrubberband не установлен — будет soxr")

def get_output_audio_devices():
    """
    Вернуть список названий доступных устройств вывода.
    Используется настройками интерфейса.
    """
    if not _HAS_SD:
        return []

    try:
        devices = sd.query_devices()
    except Exception:
        return []

    names = []

    for device in devices:
        try:
            max_output_channels = int(device.get("max_output_channels", 0) or 0)
        except Exception:
            max_output_channels = 0

        if max_output_channels <= 0:
            continue

        name = str(device.get("name", "")).strip()

        if name and name not in names:
            names.append(name)

    return sorted(names)

class TTSService:
    """Конвейер: генерация аудио через XTTS API + воспроизведение."""

    GENERATION_TIMEOUT = 60

    def __init__(self, api_url="http://localhost:8020/tts_to_audio/"):
        self.api_url = api_url
        self.gen_queue = queue.SimpleQueue()
        self.ready_queue = queue.SimpleQueue()
        self.volume = 0.8
        self.speed = 1.0
        self.preserve_pitch = True
        self.running = False
        self.enabled = True
        self.is_generating = False
        self.is_playing = False
        self.on_speech_start = None
        self.on_speech_end = None
        self.items_map = {}
        self.items_lock = threading.Lock()
        self._stop_event = threading.Event()
        self.output_device_query = ""
        self._last_output_device_index = None
        self._device_warning_query = None

        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=4,
            max_retries=requests.adapters.Retry(
                total=1, backoff_factor=0.1,
                status_forcelist=[500, 502, 503, 504]
            )
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({
            'User-Agent': 'TwitchTTSReader/1.0',
            'Accept': 'audio/wav, application/json'
        })
        self._start_workers()
        log_debug(
            "TTS",
            "init",
            api_url=self.api_url,
            sounddevice=_HAS_SD,
            soundfile=_HAS_SF,
            soxr=_HAS_SOXR,
            rubberband=_HAS_RUBBERBAND,
            volume=self.volume,
            speed=self.speed,
        )

    def _start_workers(self):
        self.running = True
        self._stop_event.clear()

        threading.Thread(
            target=self._generator_loop,
            daemon=True,
            name="TTS-Generator"
        ).start()

        threading.Thread(
            target=self._player_loop,
            daemon=True,
            name="TTS-Player"
        ).start()

        log_debug("TTS", "workers started")

    def register_item(self, item_id, item):
        with self.items_lock:
            self.items_map[item_id] = item

    def unregister_item(self, item_id):
        with self.items_lock:
            self.items_map.pop(item_id, None)

    def get_item(self, item_id):
        with self.items_lock:
            return self.items_map.get(item_id)

    def speak(self, text, voice, item_id=None):
        if not self.running:
            log_debug("TTS", "speak rejected not running", item_id=item_id)
            return

        if not self.enabled:
            log_debug("TTS", "speak rejected disabled", item_id=item_id)
            return

        if not text or not text.strip():
            log_debug("TTS", "speak rejected empty text", item_id=item_id)
            return

        log_debug(
            "TTS",
            "enqueue",
            item_id=item_id,
            voice=voice,
            text_len=len(text),
            enabled=self.enabled,
        )

        self.gen_queue.put((text, voice, item_id))

    def set_volume(self, volume):
        old_volume = self.volume
        self.volume = max(0.0, min(1.0, volume))

        if (old_volume <= 0.0 and self.volume > 0.0) or (old_volume > 0.0 and self.volume <= 0.0):
            log_debug(
                "TTS",
                "volume changed",
                old_volume=old_volume,
                new_volume=self.volume,
            )

    def set_speed(self, speed):
        self.speed = max(0.5, min(2.0, speed))

    def _apply_speed(self, audio_data, sample_rate, item_id=None):
        if abs(self.speed - 1.0) < 0.01:
            return audio_data, sample_rate

        audio_float = audio_data.astype(np.float32, copy=False)

        if self.preserve_pitch and _HAS_RUBBERBAND:
            try:
                stretched = pyrb.time_stretch(audio_float, sample_rate, self.speed)
                return stretched, sample_rate
            except Exception as e:
                log_debug(
                    "TTS",
                    "speed rubberband failed, fallback",
                    item_id=item_id,
                    speed=self.speed,
                    error=f"{type(e).__name__}: {e}"[:200],
                )

        if _HAS_SOXR:
            try:
                target_rate = int(sample_rate / self.speed)

                if target_rate <= 0:
                    log_debug(
                        "TTS",
                        "speed soxr invalid target rate",
                        item_id=item_id,
                        sample_rate=sample_rate,
                        speed=self.speed,
                    )
                else:
                    resampled = soxr.resample(
                        audio_float,
                        sample_rate,
                        target_rate,
                        quality="MQ"
                    )
                    return resampled, sample_rate
            except Exception as e:
                log_debug(
                    "TTS",
                    "speed soxr failed, fallback",
                    item_id=item_id,
                    speed=self.speed,
                    error=f"{type(e).__name__}: {e}"[:200],
                )

        new_length = int(len(audio_float) / self.speed)

        if new_length <= 0:
            log_debug(
                "TTS",
                "speed linear invalid length",
                item_id=item_id,
                speed=self.speed,
                frames=len(audio_float),
            )
            return audio_float, sample_rate

        indices = np.linspace(0, len(audio_float) - 1, new_length, dtype=np.float32)
        linear = np.interp(
            indices,
            np.arange(len(audio_float), dtype=np.float32),
            audio_float
        ).astype(np.float32)

        return linear, sample_rate

    def set_output_device(self, device_query):
        """
        Установить устройство вывода по имени/подстроке.

        Пример:
        set_output_device("HyperX")

        Пустая строка означает устройство по умолчанию.
        """
        self.output_device_query = str(device_query or "").strip()
        self._last_output_device_index = None
        self._device_warning_query = None

    def _safe_sd_stop(self):
        """Безопасно остановить текущий звук."""
        if not _HAS_SD:
            return

        try:
            sd.stop()
        except Exception:
            return

    def _safe_callback(self, callback, item_id):
        """
        Безопасный вызов UI-колбэка.
        Ошибка подсветки не должна ломать воспроизведение.
        """
        if callback is None or item_id is None:
            return

        try:
            callback(item_id)
        except Exception as e:
            log_message(
                f"TTS callback error: {type(e).__name__}: {e}",
                level="WARNING"
            )

    def _resolve_output_device(self):
        """
        Найти индекс устройства вывода по self.output_device_query.

        Возвращает:
        - индекс устройства, если найдено;
        - None, если нужно использовать устройство по умолчанию.
        """
        if not _HAS_SD:
            return None

        query = self.output_device_query.strip()

        if not query:
            self._last_output_device_index = None
            self._device_warning_query = None
            return None

        try:
            devices = sd.query_devices()
        except Exception as e:
            if self._device_warning_query != query:
                log_message(
                    f"Не удалось получить список аудиоустройств: {type(e).__name__}",
                    level="WARNING"
                )
                self._device_warning_query = query

            self._last_output_device_index = None
            return None

        query_lower = query.lower()

        for index, device in enumerate(devices):
            try:
                max_output_channels = int(device.get("max_output_channels", 0) or 0)
            except Exception:
                max_output_channels = 0

            if max_output_channels <= 0:
                continue

            name = str(device.get("name", "")).strip()

            if not name:
                continue

            if query_lower in name.lower():
                self._last_output_device_index = index
                self._device_warning_query = None
                return index

        if self._device_warning_query != query:
            log_message(
                f"Аудиоустройство '{query}' не найдено. "
                f"Используется устройство по умолчанию.",
                level="WARNING"
            )
            self._device_warning_query = query

        self._last_output_device_index = None
        return None

    def _generator_loop(self):
        if not _HAS_SF:
            log_debug("TTS", "generator disabled no soundfile")
            return

        log_debug("TTS", "generator started")

        while self.running:
            item_id = None

            try:
                item = self.gen_queue.get()

                if item is None:
                    log_debug("TTS", "generator stop signal")
                    break

                text, voice, item_id = item
            except Exception as e:
                log_debug(
                    "TTS",
                    "generator queue error",
                    error=f"{type(e).__name__}: {e}"[:200],
                )

                if not self.running:
                    break

                continue

            if not self.running or self._stop_event.is_set():
                log_debug("TTS", "generator exit stop", item_id=item_id)
                break

            if not self.enabled:
                log_debug("TTS", "gen skip disabled", item_id=item_id)
                continue

            if not text or not text.strip():
                log_debug("TTS", "gen skip empty text", item_id=item_id)
                continue

            self.is_generating = True
            started = time.perf_counter()

            try:
                log_debug(
                    "TTS",
                    "gen start",
                    item_id=item_id,
                    voice=voice,
                    text_len=len(text),
                )

                response = self.session.post(
                    self.api_url,
                    json={"text": text, "language": "ru", "speaker_wav": voice},
                    timeout=self.GENERATION_TIMEOUT
                )

                elapsed_ms = int((time.perf_counter() - started) * 1000)

                if not self.running or self._stop_event.is_set():
                    log_debug(
                        "TTS",
                        "gen skip after response stop",
                        item_id=item_id,
                        status=response.status_code,
                        ms=elapsed_ms,
                    )
                    break

                if response.status_code != 200:
                    log_debug(
                        "TTS",
                        "gen bad status",
                        item_id=item_id,
                        status=response.status_code,
                        ms=elapsed_ms,
                        bytes=len(response.content or b""),
                    )
                    continue

                audio_data, sample_rate = sf.read(io.BytesIO(response.content))

                if audio_data is None or len(audio_data) == 0:
                    log_debug(
                        "TTS",
                        "gen empty audio",
                        item_id=item_id,
                        status=200,
                        ms=elapsed_ms,
                    )
                    continue

                if not sample_rate or sample_rate <= 0:
                    log_debug(
                        "TTS",
                        "gen invalid sample rate",
                        item_id=item_id,
                        sr=sample_rate,
                        ms=elapsed_ms,
                    )
                    continue

                audio_data, sample_rate = self._apply_speed(
                    audio_data,
                    sample_rate,
                    item_id
                )

                if not self.running or self._stop_event.is_set():
                    log_debug(
                        "TTS",
                        "gen skip ready stop",
                        item_id=item_id,
                        ms=elapsed_ms,
                    )
                    break

                if not self.enabled:
                    log_debug(
                        "TTS",
                        "gen skip ready disabled",
                        item_id=item_id,
                        ms=elapsed_ms,
                    )
                    continue

                try:
                    duration = round(len(audio_data) / float(sample_rate), 2)
                except Exception:
                    duration = -1

                self.ready_queue.put((audio_data, sample_rate, item_id))

                log_debug(
                    "TTS",
                    "gen ready",
                    item_id=item_id,
                    status=200,
                    ms=elapsed_ms,
                    bytes=len(response.content or b""),
                    sr=sample_rate,
                    dur=duration,
                )

            except requests.exceptions.Timeout as e:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_debug(
                    "TTS",
                    "gen timeout",
                    item_id=item_id,
                    ms=elapsed_ms,
                    error=f"{type(e).__name__}: {e}"[:200],
                )

            except requests.exceptions.ConnectionError as e:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_debug(
                    "TTS",
                    "gen connection error",
                    item_id=item_id,
                    ms=elapsed_ms,
                    error=f"{type(e).__name__}: {e}"[:200],
                )

                # Небольшая задержка, чтобы не долбить сервер,
                # если он временно недоступен, а в очереди много сообщений.
                time.sleep(0.2)

            except requests.exceptions.RequestException as e:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_debug(
                    "TTS",
                    "gen request error",
                    item_id=item_id,
                    ms=elapsed_ms,
                    error=f"{type(e).__name__}: {e}"[:200],
                )

            except Exception as e:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_debug(
                    "TTS",
                    "gen error",
                    item_id=item_id,
                    ms=elapsed_ms,
                    error=f"{type(e).__name__}: {e}"[:300],
                )

            finally:
                self.is_generating = False

        log_debug(
            "TTS",
            "generator exited",
            running=self.running,
            stop_event=self._stop_event.is_set(),
        )

    def _player_loop(self):
        if not _HAS_SD:
            return

        while self.running:
            try:
                item = self.ready_queue.get()

                if item is None:
                    break

                audio_data, sample_rate, item_id = item
            except Exception as e:
                log_message(
                    f"TTS player queue error: {type(e).__name__}: {e}",
                    level="WARNING"
                )

                if not self.running:
                    break

                continue

            if not self.running or self._stop_event.is_set():
                break

            if not self.enabled:
                continue

            if audio_data is None or len(audio_data) == 0:
                log_message(
                    f"TTS player skipped empty audio, item_id={item_id}",
                    level="WARNING"
                )
                continue

            if not sample_rate or sample_rate <= 0:
                log_message(
                    f"TTS player skipped invalid sample_rate={sample_rate}, item_id={item_id}",
                    level="WARNING"
                )
                continue

            self.is_playing = True
            self._safe_callback(self.on_speech_start, item_id)

            try:
                audio_play = audio_data * self.volume

                if audio_play.dtype != np.float32:
                    audio_play = audio_play.astype(np.float32, copy=False)

                pad = int(sample_rate * 0.15)

                if audio_play.ndim == 1:
                    tail = np.zeros(pad, dtype=np.float32)
                else:
                    tail = np.zeros((pad, audio_play.shape[1]), dtype=np.float32)

                audio_play = np.concatenate([audio_play, tail])

                device_index = self._resolve_output_device()
                last_error = None

                # До 3 попыток воспроизведения.
                # 1 попытка: выбранное устройство.
                # 2 попытка: перевыбор устройства.
                # 3 попытка: fallback на устройство по умолчанию.
                for attempt in range(1, 4):
                    if not self.running or self._stop_event.is_set() or not self.enabled:
                        break

                    try:
                        if device_index is not None:
                            sd.play(audio_play, sample_rate, device=device_index)
                        else:
                            sd.play(audio_play, sample_rate)

                        sd.wait()

                        last_error = None
                        break

                    except Exception as e:
                        last_error = e

                        self._safe_sd_stop()

                        if not self.running or self._stop_event.is_set() or not self.enabled:
                            break

                        if attempt == 1:
                            time.sleep(0.2)
                            device_index = self._resolve_output_device()
                        elif attempt == 2:
                            if device_index is not None:
                                device_index = None

                            time.sleep(0.3)
                        else:
                            break

                if last_error is not None:
                    log_message(
                        f"TTS playback failed after retries: "
                        f"{type(last_error).__name__}: {last_error}",
                        level="ERROR"
                    )

            except Exception as e:
                log_message(
                    f"TTS player error: {type(e).__name__}: {e}",
                    level="ERROR"
                )
                self._safe_sd_stop()

            finally:
                self.is_playing = False
                self._safe_callback(self.on_speech_end, item_id)

    def clear_queue(self):
        gen_cleared = 0
        ready_cleared = 0

        while True:
            try:
                self.gen_queue.get_nowait()
                gen_cleared += 1
            except queue.Empty:
                break

        while True:
            try:
                self.ready_queue.get_nowait()
                ready_cleared += 1
            except queue.Empty:
                break

        if _HAS_SD:
            try:
                sd.stop()
            except Exception as e:
                log_debug(
                    "TTS",
                    "clear_queue sd.stop error",
                    error=f"{type(e).__name__}: {e}"[:200],
                )

        log_debug(
            "TTS",
            "clear_queue",
            gen_cleared=gen_cleared,
            ready_cleared=ready_cleared,
        )

    def is_busy(self):
        return self.is_generating or self.is_playing

    def set_enabled(self, enabled):
        """Включить/выключить озвучку, не убивая рабочие потоки."""
        self.enabled = enabled

        log_debug("TTS", "set_enabled", enabled=enabled)

        if not enabled:
            self.flush()

    def flush(self):
        """Очистить очереди и прервать текущее воспроизведение (потоки остаются живы)."""
        gen_cleared = 0
        ready_cleared = 0

        while True:
            try:
                self.gen_queue.get_nowait()
                gen_cleared += 1
            except queue.Empty:
                break

        while True:
            try:
                self.ready_queue.get_nowait()
                ready_cleared += 1
            except queue.Empty:
                break

        if _HAS_SD:
            try:
                sd.stop()
            except Exception as e:
                log_debug(
                    "TTS",
                    "flush sd.stop error",
                    error=f"{type(e).__name__}: {e}"[:200],
                )

        self.is_generating = False
        self.is_playing = False

        log_debug(
            "TTS",
            "flush",
            gen_cleared=gen_cleared,
            ready_cleared=ready_cleared,
            enabled=self.enabled,
        )

    def clear_and_stop(self):
        """Очистить очереди и прервать воспроизведение."""
        self._stop_event.set()

        gen_cleared = 0
        ready_cleared = 0

        while True:
            try:
                self.gen_queue.get_nowait()
                gen_cleared += 1
            except queue.Empty:
                break

        while True:
            try:
                self.ready_queue.get_nowait()
                ready_cleared += 1
            except queue.Empty:
                break

        if _HAS_SD:
            try:
                sd.stop()
            except Exception as e:
                log_debug(
                    "TTS",
                    "clear_and_stop sd.stop error",
                    error=f"{type(e).__name__}: {e}"[:200],
                )

        self.is_generating = False
        self.is_playing = False

        log_debug(
            "TTS",
            "clear_and_stop",
            gen_cleared=gen_cleared,
            ready_cleared=ready_cleared,
        )

    def stop(self):
        """Полная остановка сервиса."""
        log_debug("TTS", "stop", running=self.running)

        self.running = False
        self._stop_event.set()
        self.clear_and_stop()

        try:
            self.gen_queue.put(None)
            self.ready_queue.put(None)
        except Exception as e:
            log_debug(
                "TTS",
                "stop signal put error",
                error=f"{type(e).__name__}: {e}"[:200],
            )

        try:
            self.session.close()
        except Exception as e:
            log_debug(
                "TTS",
                "session close error",
                error=f"{type(e).__name__}: {e}"[:200],
            )

    def _log_output_device(self, reason=""):
        """Записать текущее устройство вывода, если доступен sounddevice."""
        if not _HAS_SD:
            return

        try:
            device = sd.query_devices(kind="output")
            log_debug(
                "TTS",
                "output device",
                reason=reason,
                name=str(device.get("name"))[:120],
                sr=device.get("default_samplerate"),
            )
        except Exception as e:
            log_debug(
                "TTS",
                "output device error",
                reason=reason,
                error=f"{type(e).__name__}: {e}"[:200],
            )

    def _invoke_callback(self, callback, callback_name, item_id):
        """
        Безопасный вызов UI-колбэка.
        Ошибка колбэка не должна останавливать воспроизведение.
        """
        if callback is None or item_id is None:
            return

        try:
            callback(item_id)
        except Exception as e:
            log_debug(
                "TTS",
                "callback error",
                callback=callback_name,
                item_id=item_id,
                error=f"{type(e).__name__}: {e}"[:200],
            )