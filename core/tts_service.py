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

    def _start_workers(self):
        self.running = True
        self._stop_event.clear()
        threading.Thread(target=self._generator_loop, daemon=True, name="TTS-Generator").start()
        threading.Thread(target=self._player_loop, daemon=True, name="TTS-Player").start()

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
            return
        self.gen_queue.put((text, voice, item_id))

    def set_volume(self, volume):
        self.volume = max(0.0, min(1.0, volume))

    def set_speed(self, speed):
        self.speed = max(0.5, min(2.0, speed))

    def _apply_speed(self, audio_data, sample_rate):
        if abs(self.speed - 1.0) < 0.01:
            return audio_data, sample_rate
        audio_float = audio_data.astype(np.float32, copy=False)

        if self.preserve_pitch and _HAS_RUBBERBAND:
            try:
                return pyrb.time_stretch(audio_float, sample_rate, self.speed), sample_rate
            except Exception:
                pass

        if _HAS_SOXR:
            try:
                return soxr.resample(
                    audio_float, sample_rate,
                    int(sample_rate / self.speed), quality='MQ'
                ), sample_rate
            except Exception:
                pass

        new_length = int(len(audio_float) / self.speed)
        if new_length <= 0:
            return audio_float, sample_rate
        indices = np.linspace(0, len(audio_float) - 1, new_length, dtype=np.float32)
        return np.interp(indices, np.arange(len(audio_float), dtype=np.float32), audio_float).astype(np.float32), sample_rate

    def _generator_loop(self):
        if not _HAS_SF:
            return
        while self.running:
            try:
                item = self.gen_queue.get()
                if item is None:
                    break
                text, voice, item_id = item
            except Exception:
                if not self.running:
                    break
                continue

            if not self.running or self._stop_event.is_set():
                break

            # Озвучка выключена — молча выбрасываем задачу
            if not self.enabled:
                continue

            # ВАЖНО: здесь только is_generating, без is_playing
            self.is_generating = True
            try:
                response = self.session.post(
                    self.api_url,
                    json={"text": text, "language": "ru", "speaker_wav": voice},
                    timeout=self.GENERATION_TIMEOUT
                )
                if not self.running or self._stop_event.is_set():
                    break
                if response.status_code == 200:
                    audio_data, sample_rate = sf.read(io.BytesIO(response.content))
                    audio_data, sample_rate = self._apply_speed(audio_data, sample_rate)
                    # ВОТ НУЖНАЯ СТРОКА: добавлено and self.enabled
                    if self.running and not self._stop_event.is_set() and self.enabled:
                        self.ready_queue.put((audio_data, sample_rate, item_id))
            except requests.exceptions.ConnectionError:
                break
            except Exception:
                pass
            finally:
                self.is_generating = False

    def _player_loop(self):
        if not _HAS_SD:
            return
        while self.running:
            try:
                item = self.ready_queue.get()
                if item is None:
                    break
                audio_data, sample_rate, item_id = item
            except Exception:
                if not self.running:
                    break
                continue

            if not self.running or self._stop_event.is_set():
                break

            if not self.enabled:
                continue

            self.is_playing = True
            if self.on_speech_start and item_id is not None:
                try:
                    self.on_speech_start(item_id)
                except Exception:
                    pass
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
                sd.play(audio_play, sample_rate)
                sd.wait()
            except Exception:
                pass
            finally:
                self.is_playing = False
                if self.on_speech_end and item_id is not None:
                    try:
                        self.on_speech_end(item_id)
                    except Exception:
                        pass

    def clear_queue(self):
        while True:
            try:
                self.gen_queue.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                self.ready_queue.get_nowait()
            except queue.Empty:
                break
        if _HAS_SD:
            try:
                sd.stop()
            except Exception:
                pass

    def is_busy(self):
        return self.is_generating or self.is_playing

    def set_enabled(self, enabled):
        """Включить/выключить озвучку, не убивая рабочие потоки."""
        self.enabled = enabled
        if not enabled:
            self.flush()

    def flush(self):
        """Очистить очереди и прервать текущее воспроизведение (потоки остаются живы)."""
        while True:
            try:
                self.gen_queue.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                self.ready_queue.get_nowait()
            except queue.Empty:
                break
        if _HAS_SD:
            try:
                sd.stop()
            except Exception:
                pass
        self.is_generating = False
        self.is_playing = False

    def clear_and_stop(self):
        """Очистить очереди и прервать воспроизведение."""
        self._stop_event.set()
        while True:
            try:
                self.gen_queue.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                self.ready_queue.get_nowait()
            except queue.Empty:
                break
        if _HAS_SD:
            try:
                sd.stop()
            except Exception:
                pass
        self.is_generating = False
        self.is_playing = False

    def stop(self):
        """Полная остановка сервиса."""
        self.running = False
        self._stop_event.set()
        self.clear_and_stop()
        try:
            self.gen_queue.put(None)
            self.ready_queue.put(None)
        except Exception:
            pass
        try:
            self.session.close()
        except Exception:
            pass