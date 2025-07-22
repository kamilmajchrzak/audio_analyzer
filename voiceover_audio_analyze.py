import sys
import torch
import torchaudio
from scipy.io import wavfile
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget,
    QFileDialog, QDoubleSpinBox, QLabel, QHBoxLayout
)
from PyQt5.QtCore import Qt
from PyQt5.QtMultimedia import QSoundEffect
from PyQt5.QtCore import QUrl
import tempfile
import os
import wave
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
import webrtcvad

class AudioPlotCanvas(FigureCanvas):
    def __init__(self, parent=None):
        fig = Figure()
        self.ax = fig.add_subplot(111)
        super().__init__(fig)
        self.setParent(parent)
        self.markers = []
        self.marker_labels = []
        self.audio_data = None
        self.sample_rate = None
        self.duration = None
        self.cid_press = self.mpl_connect("button_press_event", self.on_press)
        self.cid_motion = self.mpl_connect("motion_notify_event", self.on_motion)
        self.cid_release = self.mpl_connect("button_release_event", self.on_release)
        self.selected_marker = None
        self.active_marker = None
        self.playback_patch = None
        self.zoom = 1.0
        self.offset = 0.0
        self.mpl_connect("scroll_event", self.on_scroll)

    def plot_waveform(self, data, sample_rate):
        self.audio_data = data
        self.sample_rate = sample_rate
        self.duration = len(data) / sample_rate
        self.ax.clear()

        max_points = 3000
        if len(data) > max_points:
            factor = len(data) // max_points
            data_ds = data[::factor]
            times = np.linspace(0, self.duration, num=len(data_ds))
        else:
            data_ds = data
            times = np.linspace(0, self.duration, num=len(data_ds))

        self.ax.plot(times, data_ds, color='blue')
        self.ax.set_xlim(0, self.duration / self.zoom)
        self.draw()

    def on_scroll(self, event):
        center = event.xdata if event.xdata else self.duration / 2
        if event.button == 'up':
            self.zoom *= 1.2
        elif event.button == 'down':
            self.zoom /= 1.2

        new_width = self.duration / self.zoom
        left = max(0, center - new_width / 2)
        right = min(self.duration, center + new_width / 2)
        self.ax.set_xlim(left, right)
        self.draw()

    def add_marker(self, time_pos):
        line = Line2D([time_pos, time_pos], [min(self.audio_data), max(self.audio_data)],
                      color='red', linewidth=1.5, picker=True)
        self.ax.add_line(line)
        index = len(self.markers) + 1
        triangle = self.ax.plot(time_pos, max(self.audio_data), marker='v', color='black')[0]
        label = self.ax.text(time_pos, max(self.audio_data), str(index), color='black', fontsize=8, ha='center', va='bottom')
        self.markers.append(line)
        self.marker_labels.append((triangle, label))
        self.draw()

    def clear_markers(self):
        for m in self.markers:
            m.remove()
        for triangle, label in self.marker_labels:
            triangle.remove()
            label.remove()
        self.markers = []
        self.marker_labels = []
        self.draw()

    def set_active_marker(self, marker):
        if hasattr(self, 'active_marker') and self.active_marker:
            self.active_marker.set_color('red')
        self.active_marker = marker
        if self.active_marker:
            self.active_marker.set_color('green')
        self.draw()

    def get_active_marker_time(self):
        if hasattr(self, 'active_marker') and self.active_marker:
            return self.active_marker.get_xdata()[0]
        return None

    def get_next_marker_time(self, start_time):
        future_markers = sorted([m.get_xdata()[0] for m in self.markers if m.get_xdata()[0] > start_time])
        return future_markers[0] if future_markers else self.duration

    def on_press(self, event):
        if event.button != 1 or event.xdata is None:
            return
        for m in self.markers:
            if abs(m.get_xdata()[0] - event.xdata) < 0.02:
                self.set_active_marker(m)
                self.selected_marker = m
                break

    def on_motion(self, event):
        if self.selected_marker and event.xdata:
            x = max(0, min(event.xdata, self.duration))
            self.selected_marker.set_xdata([x, x])
            if self.selected_marker == self.active_marker:
                self.set_active_marker(self.selected_marker)
            try:
                idx = self.markers.index(self.selected_marker)
                triangle, label = self.marker_labels[idx]
                triangle.set_xdata([x])
                label.set_position((x, max(self.audio_data)))
            except Exception as e:
                print("Błąd przesuwania wskaźników:", e)
            self.draw()

    def on_release(self, event):
        self.selected_marker = None

    def highlight_playback_segment(self, start, end):
        if self.playback_patch:
            self.playback_patch.remove()
        self.playback_patch = self.ax.axvspan(start, end, color='gray', alpha=0.3, zorder=-1)
        self.draw()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Porównanie dwóch plików WAV")
        self.canvas1 = AudioPlotCanvas(self)
        self.canvas2 = AudioPlotCanvas(self)

        # Kontrolki dla canvas1
        open_button1 = QPushButton("Otwórz WAV 1")
        open_button1.clicked.connect(lambda: self.open_wave_file(self.canvas1))

        marker_button1 = QPushButton("Postaw markery 1")
        marker_button1.clicked.connect(lambda: self.place_markers(self.canvas1))

        play_button1 = QPushButton("Play 1")
        play_button1.clicked.connect(lambda: self.play_audio_segment(self.canvas1))

        save_button1 = QPushButton("Zapisz fragment 1")
        save_button1.clicked.connect(lambda: self.save_audio_segment(self.canvas1))

        # Kontrolki dla canvas2
        open_button2 = QPushButton("Otwórz WAV 2")
        open_button2.clicked.connect(lambda: self.open_wave_file(self.canvas2))

        marker_button2 = QPushButton("Postaw markery 2")
        marker_button2.clicked.connect(lambda: self.place_markers(self.canvas2))

        play_button2 = QPushButton("Play 2")
        play_button2.clicked.connect(lambda: self.play_audio_segment(self.canvas2))

        save_button2 = QPushButton("Zapisz fragment 2")
        save_button2.clicked.connect(lambda: self.save_audio_segment(self.canvas2))

        self.silence_spinbox = QDoubleSpinBox()
        self.silence_spinbox.setValue(0.6)
        self.silence_spinbox.setSingleStep(0.1)
        self.silence_spinbox.setSuffix(" s")

        self.vad_spacing_spinbox = QDoubleSpinBox()
        self.vad_spacing_spinbox.setValue(0.5)
        self.vad_spacing_spinbox.setSingleStep(0.1)
        self.vad_spacing_spinbox.setSuffix(" s")

        self.vad_aggr_spinbox = QDoubleSpinBox()
        self.vad_aggr_spinbox.setRange(0, 3)
        self.vad_aggr_spinbox.setValue(2)
        self.vad_aggr_spinbox.setSingleStep(1)

        self.vad_frame_spinbox = QDoubleSpinBox()
        self.vad_frame_spinbox.setValue(30)
        self.vad_frame_spinbox.setSingleStep(10)
        self.vad_frame_spinbox.setSuffix(" ms")

        self.vad_overlap_spinbox = QDoubleSpinBox()
        self.vad_overlap_spinbox.setValue(0.0)
        self.vad_overlap_spinbox.setSingleStep(0.1)
        self.vad_overlap_spinbox.setSuffix(" x")

        silence_label = QLabel("Minimalna długość ciszy:")

        # Layouty
        top_layout = QHBoxLayout()
        top_layout.addWidget(open_button1)
        top_layout.addWidget(marker_button1)
        top_layout.addWidget(play_button1)
        top_layout.addWidget(save_button1)

        top2_layout = QHBoxLayout()
        top2_layout.addWidget(open_button2)
        top2_layout.addWidget(marker_button2)
        top2_layout.addWidget(play_button2)
        top2_layout.addWidget(save_button2)

        silence_layout = QHBoxLayout()
        silence_layout.addWidget(silence_label)
        silence_layout.addWidget(self.silence_spinbox)
        silence_layout.addWidget(QLabel("Min odstęp VAD:"))
        silence_layout.addWidget(self.vad_spacing_spinbox)
        silence_layout.addWidget(QLabel("Agresywność VAD:"))
        silence_layout.addWidget(self.vad_aggr_spinbox)
        silence_layout.addWidget(QLabel("Okno VAD:"))
        silence_layout.addWidget(self.vad_frame_spinbox)
        silence_layout.addWidget(QLabel("Nakładka:"))
        silence_layout.addWidget(self.vad_overlap_spinbox)

        layout = QVBoxLayout()
        layout.addLayout(top_layout)
        layout.addWidget(self.canvas1)
        layout.addLayout(top2_layout)
        layout.addWidget(self.canvas2)
        layout.addLayout(silence_layout)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.silero_model, self.utils = torch.hub.load(repo_or_dir='/Users/kamil/.cache/torch/hub/snakers4_silero-vad_master', model='silero_vad', source='local')
        (self.get_speech_timestamps,
         self.save_audio,
         self.read_audio,
         self.VADIterator,
         self.collect_chunks) = self.utils

    def open_wave_file(self, canvas):
        path, _ = QFileDialog.getOpenFileName(self, "Otwórz plik WAV", "", "Wave files (*.wav)")
        if path:
            sample_rate, data = wavfile.read(path)
            if len(data.shape) > 1:
                data = data[:, 0]
            data = data.astype(np.float32)
            max_val = np.max(np.abs(data))
            if max_val > 0:
                data /= max_val
            canvas.plot_waveform(data, sample_rate)

    def place_markers(self, canvas):
        if canvas.audio_data is None:
            return
        canvas.clear_markers()
        sample_rate = canvas.sample_rate

        if canvas == self.canvas2:
            silence_thresh = 0.02
            silence_duration = self.silence_spinbox.value()
            min_samples = int(silence_duration * sample_rate)

            audio_abs = np.abs(canvas.audio_data)
            silent = audio_abs < silence_thresh
            silence_start = None
            for i, is_silent in enumerate(silent):
                if is_silent and silence_start is None:
                    silence_start = i
                elif not is_silent and silence_start is not None:
                    if i - silence_start >= min_samples:
                        marker_pos = i / sample_rate
                        canvas.add_marker(marker_pos)
                    silence_start = None
        else:
            # canvas1 — wykrywanie mowy
            temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            path = temp_wav.name
            from scipy.io.wavfile import write
            write(path, canvas.sample_rate, (canvas.audio_data * 32767).astype(np.int16))

            wav_tensor = self.read_audio(path, sampling_rate=16000)
            speech_timestamps = self.get_speech_timestamps(wav_tensor, self.silero_model, sampling_rate=16000)

            for segment in speech_timestamps:
                start_sec = segment['start'] / 16000
                canvas.add_marker(start_sec)

    def play_audio_segment(self, canvas):
        start = canvas.get_active_marker_time()
        if start is None or canvas.audio_data is None:
            return
        end = canvas.get_next_marker_time(start)
        canvas.highlight_playback_segment(start, end)

        sample_rate = canvas.sample_rate
        start_sample = int(start * sample_rate)
        end_sample = int(end * sample_rate)
        segment = canvas.audio_data[start_sample:end_sample]
        segment = (segment * 32767).astype(np.int16).tobytes()

        from PyQt5.QtMultimedia import QAudioFormat, QAudioOutput
        from PyQt5.QtCore import QBuffer, QByteArray

        format = QAudioFormat()
        format.setChannelCount(1)
        format.setSampleRate(sample_rate)
        format.setSampleSize(16)
        format.setCodec("audio/pcm")
        format.setByteOrder(QAudioFormat.LittleEndian)
        format.setSampleType(QAudioFormat.SignedInt)

        self.audio_buffer = QBuffer()
        self.audio_data = QByteArray(segment)
        self.audio_buffer.setData(self.audio_data)
        self.audio_buffer.open(QBuffer.ReadOnly)

        self.audio_output = QAudioOutput(format)
        self.audio_output.start(self.audio_buffer)

    def save_audio_segment(self, canvas):
        start = canvas.get_active_marker_time()
        if start is None or canvas.audio_data is None:
            return
        end = canvas.get_next_marker_time(start)

        sample_rate = canvas.sample_rate
        start_sample = int(start * sample_rate)
        end_sample = int(end * sample_rate)
        segment = canvas.audio_data[start_sample:end_sample]
        segment = (segment * 32767).astype(np.int16).tobytes()

        path, _ = QFileDialog.getSaveFileName(self, "Zapisz fragment jako", "fragment.wav", "WAV files (*.wav)")
        if path:
            with wave.open(path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(segment)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1000, 600)
    win.show()
    sys.exit(app.exec_())
