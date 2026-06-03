"""
BluePillScope Pro Viewer v4.0 - Professional Interface
Diseñado para el firmware STM32F103 Blue Pill en Modo ADC Dual Intercalado Uniforme.
Protocolo: 0xAA 0xBB 0xCC 0xDD + 1024 muestras int16_t (Little Endian) en milivoltios.
"""
import sys
import serial
import serial.tools.list_ports
import pyqtgraph as pg
import numpy as np
import struct
import csv
from PyQt5 import QtWidgets, QtCore, QtGui
from scipy import signal

# ─── Temas Profesionales ─────────────────────────────────────────────────────
THEMES = {
    "Hantek Dark": {
        "BG_APP": "#1a1a2e",
        "BG_SCREEN": "#0a0a0a",
        "BG_PANEL": "#16213e",
        "GRID_COLOR": "#2a4060",
        "CH1_COLOR": "#FFE066",
        "TRIG_COLOR": "#FF6B6B",
        "TEXT_LIGHT": "#E0E0E0",
        "TEXT_DIM": "#606080",
        "ACCENT": "#4ecca3",
        "FFT_CURVE": "#DDA0DD"
    },
    "Keysight White": {
        "BG_APP": "#f0f0f0",
        "BG_SCREEN": "#ffffff",
        "BG_PANEL": "#e0e0e0",
        "GRID_COLOR": "#d0d0d0",
        "CH1_COLOR": "#0055ff",
        "TRIG_COLOR": "#ff0000",
        "TEXT_LIGHT": "#202020",
        "TEXT_DIM": "#606060",
        "ACCENT": "#0055ff",
        "FFT_CURVE": "#800080"
    }
}

# Tiempo REAL del firmware en modo Dual Intercalado Uniforme (1.714 MSPS base)
# (us_per_div, hardware_speed_idx, sample_interval_us)
TIME_DIV_MAP = {
    "10 µs/div":  (10, 0, 0.5833333333333334),
    "20 µs/div":  (20, 0, 0.5833333333333334),
    "50 µs/div":  (50, 0, 0.5833333333333334),
    "100 µs/div": (100, 1, 5.833333333333334),
    "200 µs/div": (200, 1, 5.833333333333334),
    "500 µs/div": (500, 1, 5.833333333333334),
    "1 ms/div":   (1000, 2, 58.333333333333336),
    "2 ms/div":   (2000, 2, 58.333333333333336),
    "5 ms/div":   (5000, 2, 58.333333333333336),
    "10 ms/div":  (10000, 3, 583.3333333333334),
    "20 ms/div":  (20000, 3, 583.3333333333334),
    "50 ms/div":  (50000, 3, 583.3333333333334),
}

H_DIVS = 10
CHUNK_SIZE = 1024 # Coincide con el firmware

V_DIV_MAP = {
    "500 mV/div": (0.5, -1.0, 4.0), "1 V/div": (1.0, -2.0, 5.0),
    "2 V/div": (2.0, -4.0, 6.0), "5 V/div": (5.0, -10.0, 10.0)
}

class FFTWindow(QtWidgets.QWidget):
    """Ventana independiente para el Analizador de Espectro (Transformada de Fourier)"""
    def __init__(self, parent_theme):
        super().__init__()
        self.setWindowTitle("Analizador de Espectro (FFT)")
        self.resize(800, 450)
        
        layout = QtWidgets.QVBoxLayout(self)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel('bottom', 'Frecuencia', units='Hz')
        self.plot.setLabel('left', 'Amplitud Relativa')
        self.curve = self.plot.plot([], [], pen=pg.mkPen(width=2))
        layout.addWidget(self.plot)
        
        self.apply_theme(parent_theme)
        
    def apply_theme(self, t):
        self.setStyleSheet(f"background-color: {t['BG_APP']};")
        self.plot.setBackground(t['BG_SCREEN'])
        self.curve.setPen(pg.mkPen(t['FFT_CURVE'], width=2))
        self.plot.getAxis('left').setPen(pg.mkPen(t['GRID_COLOR']))
        self.plot.getAxis('bottom').setPen(pg.mkPen(t['GRID_COLOR']))
        self.plot.getAxis('left').setTextPen(pg.mkPen(t['TEXT_LIGHT']))
        self.plot.getAxis('bottom').setTextPen(pg.mkPen(t['TEXT_LIGHT']))

    def update_fft(self, display_data, fixed_sample_us):
        if len(display_data) < 128: return
        n = len(display_data)
        
        # Calcular Frecuencias en el Eje X (d es el periodo de muestreo en segundos)
        freq = np.fft.rfftfreq(n, d=fixed_sample_us * 1e-6)
        
        # Calcular Amplitudes en el Eje Y
        fft_vals = np.abs(np.fft.rfft(display_data))
        
        # Remover el componente DC (Frecuencia 0Hz)
        if len(fft_vals) > 0:
            fft_vals[0] = 0 
            
        self.curve.setData(freq, fft_vals)

class BluePillScopeViewer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_theme = THEMES["Hantek Dark"]
        self.setWindowTitle("BluePillScope Pro v4.0 | 1.71 MSPS Engineering Edition")
        self.resize(1300, 850)
        
        # Estado
        self.ser = None
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._read_serial)
        self.binary_buffer = bytearray()
        self.history_frames = []
        self.MAX_HISTORY = 4
        self.display_data = np.array([])
        self.use_sinc = False
        self.use_filter = False
        self.stitch_count = 1 # Por defecto 1x (sin costuras)
        self.fixed_sample_us = 0.5833 # Valor inicial (Turbo)
        self.trig_level = 1.65
        self.vertical_offset = 0.0
        
        self._init_ui()
        self._apply_theme("Hantek Dark")

    def _init_ui(self):
        main_widget = QtWidgets.QWidget()
        self.setCentralWidget(main_widget)
        self.main_layout = QtWidgets.QVBoxLayout(main_widget)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(8)

        # ─── BARRA SUPERIOR (TOOLBAR) ──────────────────────────────────────────
        top_bar = QtWidgets.QFrame()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(50)
        top_layout = QtWidgets.QHBoxLayout(top_bar)
        top_layout.setContentsMargins(10, 0, 10, 0)
        
        top_layout.addWidget(QtWidgets.QLabel("PORT:"))
        self.combo_ports = QtWidgets.QComboBox()
        self.combo_ports.setMinimumWidth(120)
        self._refresh_ports()
        top_layout.addWidget(self.combo_ports)
        
        self.btn_refresh = QtWidgets.QPushButton("🔄")
        self.btn_refresh.setFixedSize(30, 30)
        self.btn_refresh.clicked.connect(self._refresh_ports)
        top_layout.addWidget(self.btn_refresh)
        
        self.btn_run = QtWidgets.QPushButton("▶ RUN")
        self.btn_run.setObjectName("btnRun")
        self.btn_run.setFixedSize(80, 30)
        self.btn_run.clicked.connect(self._toggle_run)
        top_layout.addWidget(self.btn_run)
        
        top_layout.addSpacing(20)
        
        # Botones de Herramientas
        self.btn_fft = QtWidgets.QPushButton("📊 FFT")
        self.btn_fft.setFixedSize(60, 30)
        self.btn_fft.clicked.connect(self._toggle_fft_window)
        top_layout.addWidget(self.btn_fft)
        
        self.btn_csv = QtWidgets.QPushButton("💾 SAVE CSV")
        self.btn_csv.setFixedSize(80, 30)
        self.btn_csv.clicked.connect(self._save_csv)
        top_layout.addWidget(self.btn_csv)
        
        top_layout.addStretch()
        
        # Título / Logo Central
        title = QtWidgets.QLabel("BLUEPILLSCOPE PRO v4.0")
        title.setStyleSheet("font-weight: bold; font-size: 16px; color: #4ecca3;")
        top_layout.addWidget(title)
        
        top_layout.addStretch()
        
        top_layout.addWidget(QtWidgets.QLabel("THEME:"))
        self.combo_theme = QtWidgets.QComboBox()
        self.combo_theme.addItems(list(THEMES.keys()))
        self.combo_theme.currentTextChanged.connect(self._apply_theme)
        top_layout.addWidget(self.combo_theme)
        
        self.main_layout.addWidget(top_bar)

        # ─── CUERPO PRINCIPAL (SCOPE + PANEL) ──────────────────────────────────
        body_layout = QtWidgets.QHBoxLayout()
        
        # 1. PANTALLA (PyQtGraph)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel('left', "Tensión", units='V')
        self.plot.setLabel('bottom', "Tiempo", units='s')
        self.curve = self.plot.plot([], [], pen=pg.mkPen(width=2))

        # Línea de Trigger (Visual + Hardware)
        self.trig_line = pg.InfiniteLine(pos=1.65, angle=0, movable=True, 
                                        pen=pg.mkPen("#ff6b6b", width=1, style=QtCore.Qt.DashLine))
        self.trig_line.sigPositionChanged.connect(self._on_trig_line_dragging)
        self.trig_line.sigPositionChangeFinished.connect(self._on_trig_line_moved)
        self.plot.addItem(self.trig_line)
        
        # Cursores T1 / T2 (Manuales)
        self.cursor_t1 = pg.InfiniteLine(pos=0, angle=90, movable=True, pen=pg.mkPen('#00FF00', width=1, style=QtCore.Qt.DashLine), label="T1")
        self.cursor_t2 = pg.InfiniteLine(pos=0.0001, angle=90, movable=True, pen=pg.mkPen('#00FF00', width=1, style=QtCore.Qt.DashLine), label="T2")
        self.plot.addItem(self.cursor_t1)
        self.plot.addItem(self.cursor_t2)
        
        # Cursores V1 / V2 (Manuales)
        self.cursor_v1 = pg.InfiniteLine(pos=1.0, angle=0, movable=True, pen=pg.mkPen('#FF00FF', width=1, style=QtCore.Qt.DashLine), label="V1")
        self.cursor_v2 = pg.InfiniteLine(pos=2.0, angle=0, movable=True, pen=pg.mkPen('#FF00FF', width=1, style=QtCore.Qt.DashLine), label="V2")
        self.plot.addItem(self.cursor_v1)
        self.plot.addItem(self.cursor_v2)

        body_layout.addWidget(self.plot, stretch=4)
        
        # 2. PANEL DE CONTROL LATERAL (SCROLLABLE)
        self.control_panel = QtWidgets.QScrollArea()
        self.control_panel.setWidgetResizable(True)
        self.control_panel.setFixedWidth(240)
        self.control_panel.setObjectName("controlPanel")
        
        control_widget = QtWidgets.QWidget()
        control_widget.setObjectName("controlWidget")
        control_v_layout = QtWidgets.QVBoxLayout(control_widget)
        control_v_layout.setContentsMargins(5, 5, 5, 5)
        
        # Grupo: Base de Tiempo
        group_time = QtWidgets.QGroupBox("⏱ TIMEBASE")
        gt_layout = QtWidgets.QVBoxLayout(group_time)
        self.combo_time = QtWidgets.QComboBox()
        self.combo_time.addItems(list(TIME_DIV_MAP.keys()))
        self.combo_time.setCurrentText("100 µs/div")
        self.combo_time.currentTextChanged.connect(self._on_time_changed)
        gt_layout.addWidget(self.combo_time)
        control_v_layout.addWidget(group_time)
        
        # Grupo: Escala Vertical
        group_vert = QtWidgets.QGroupBox("📶 VERTICAL")
        gv_layout = QtWidgets.QVBoxLayout(group_vert)
        
        gv_layout.addWidget(QtWidgets.QLabel("Volts/Div:"))
        self.combo_vdiv = QtWidgets.QComboBox()
        self.combo_vdiv.addItems(list(V_DIV_MAP.keys()))
        self.combo_vdiv.setCurrentText("1 V/div")
        self.combo_vdiv.currentTextChanged.connect(self._on_vdiv_changed)
        gv_layout.addWidget(self.combo_vdiv)
        
        gv_layout.addWidget(QtWidgets.QLabel("Offset (V):"))
        self.spin_offset = QtWidgets.QDoubleSpinBox()
        self.spin_offset.setRange(-5.0, 5.0)
        self.spin_offset.setSingleStep(0.1)
        self.spin_offset.setValue(0.0)
        self.spin_offset.setDecimals(2)
        self.spin_offset.valueChanged.connect(self._on_offset_changed)
        gv_layout.addWidget(self.spin_offset)
        
        control_v_layout.addWidget(group_vert)

        # Grupo: Trigger
        group_trig = QtWidgets.QGroupBox("🎯 TRIGGER")
        gt_layout = QtWidgets.QVBoxLayout(group_trig)
        
        trig_row = QtWidgets.QHBoxLayout()
        trig_row.addWidget(QtWidgets.QLabel("Nivel:"))
        self.lbl_trig_val = QtWidgets.QLabel(f"{self.trig_level:.2f} V")
        self.lbl_trig_val.setStyleSheet(f"font-weight: bold; color: {self.current_theme['TRIG_COLOR']};")
        trig_row.addWidget(self.lbl_trig_val)
        gt_layout.addLayout(trig_row)
        
        gt_layout.addWidget(QtWidgets.QLabel("(Arrastre la línea roja)"))
        control_v_layout.addWidget(group_trig)
        
        # Grupo: DSP & Memoria
        group_dsp = QtWidgets.QGroupBox("🧪 DSP / MEMORY")
        gd_layout = QtWidgets.QVBoxLayout(group_dsp)
        
        self.cb_sinc = QtWidgets.QCheckBox("Sinc Interpolation")
        self.cb_sinc.stateChanged.connect(self._toggle_sinc)
        gd_layout.addWidget(self.cb_sinc)
        
        self.cb_filter = QtWidgets.QCheckBox("Noise Filter (Avg)")
        self.cb_filter.stateChanged.connect(self._toggle_filter)
        gd_layout.addWidget(self.cb_filter)
        
        gd_layout.addWidget(QtWidgets.QLabel("History Stitch:"))
        self.combo_stitch = QtWidgets.QComboBox()
        self.combo_stitch.addItems(["1x (Clean)", "2x", "4x", "8x", "16x"])
        self.combo_stitch.currentTextChanged.connect(self._on_stitch_changed)
        gd_layout.addWidget(self.combo_stitch)
        
        control_v_layout.addWidget(group_dsp)
        
        # Grupo: Información de Cursores
        group_curs = QtWidgets.QGroupBox("📐 CURSORS")
        gc_layout = QtWidgets.QVBoxLayout(group_curs)
        self.lbl_delta_t = QtWidgets.QLabel("ΔT: ---")
        self.lbl_delta_v = QtWidgets.QLabel("ΔV: ---")
        gc_layout.addWidget(self.lbl_delta_t)
        gc_layout.addWidget(self.lbl_delta_v)
        control_v_layout.addWidget(group_curs)
        
        control_v_layout.addStretch()
        
        self.lbl_status = QtWidgets.QLabel("DISCONNECTED")
        self.lbl_status.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: #ff6b6b; font-weight: bold; border: 1px solid #ff6b6b; padding: 5px;")
        control_v_layout.addWidget(self.lbl_status)
        
        self.control_panel.setWidget(control_widget)
        body_layout.addWidget(self.control_panel)
        self.main_layout.addLayout(body_layout, stretch=1)

        # ─── BARRA INFERIOR DE MÉTRICAS (PRO) ──────────────────────────────────
        metrics_bar = QtWidgets.QHBoxLayout()
        metrics_bar.setSpacing(10)
        
        self.metric_cards = []
        def create_metric_card(title, value_init):
            card = QtWidgets.QFrame()
            card.setObjectName("metricCard")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(6, 4, 6, 4)
            card_layout.setSpacing(2)
            
            lbl_title = QtWidgets.QLabel(title)
            lbl_val = QtWidgets.QLabel(value_init)
            
            card_layout.addWidget(lbl_title)
            card_layout.addWidget(lbl_val)
            self.metric_cards.append((card, lbl_title, lbl_val))
            return card, lbl_val
            
        self.card_vmax, self.lbl_vmax = create_metric_card("V-MAXIMA", "---")
        self.card_vmin, self.lbl_vmin = create_metric_card("V-MINIMA", "---")
        self.card_vpp, self.lbl_vpp = create_metric_card("V-PICO-PICO", "---")
        self.card_vrms, self.lbl_vrms = create_metric_card("V-RMS", "---")
        self.card_freq, self.lbl_freq = create_metric_card("FRECUENCIA", "---")
        self.card_duty, self.lbl_duty = create_metric_card("DUTY CYCLE", "---")
        
        metrics_bar.addWidget(self.card_vmax)
        metrics_bar.addWidget(self.card_vmin)
        metrics_bar.addWidget(self.card_vpp)
        metrics_bar.addWidget(self.card_vrms)
        metrics_bar.addWidget(self.card_freq)
        metrics_bar.addWidget(self.card_duty)
        
        self.main_layout.addLayout(metrics_bar)
        
        # Timer para cursores
        self.cursor_timer = QtCore.QTimer()
        self.cursor_timer.timeout.connect(self._update_cursor_info)
        self.cursor_timer.start(100)
        
        # StatusBar
        self.statusBar().showMessage("Ready | 1.71 MSPS Uniform Mode Enabled")
        self.fft_window = None

    def _update_metric_colors(self):
        for card, lbl_title, lbl_val in self.metric_cards:
            lbl_title.setStyleSheet(f"font-size: 9px; text-transform: uppercase; color: {self.current_theme['TEXT_DIM']}; font-weight: bold;")
            lbl_val.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {self.current_theme['ACCENT']};")

    def _apply_theme(self, name):
        t = THEMES[name]
        self.current_theme = t
        
        # Estilo Global (QSS)
        qss = f"""
            QMainWindow {{ background-color: {t['BG_APP']}; }}
            QFrame#topBar {{ 
                background-color: {t['BG_PANEL']}; 
                border-bottom: 2px solid {t['GRID_COLOR']}; 
                border-radius: 5px;
            }}
            QScrollArea#controlPanel {{ background-color: {t['BG_PANEL']}; border: none; }}
            QWidget#controlWidget {{ background-color: {t['BG_PANEL']}; }}
            QGroupBox {{
                color: {t['ACCENT']};
                font-weight: bold;
                border: 1px solid {t['GRID_COLOR']};
                margin-top: 6px;
                padding-top: 10px;
                border-radius: 5px;
            }}
            QComboBox, QPushButton, QDoubleSpinBox {{
                background-color: {t['BG_PANEL']};
                color: {t['TEXT_LIGHT']};
                border: 1px solid {t['GRID_COLOR']};
                padding: 4px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton#btnRun {{
                background-color: {t['ACCENT']};
                color: #000000;
                font-weight: bold;
            }}
            QLabel {{ color: {t['TEXT_LIGHT']}; font-size: 11px; }}
            QCheckBox {{ color: {t['TEXT_LIGHT']}; font-size: 11px; }}
            QWidget#metricCard {{ background: rgba(0,0,0,0.15); border-radius: 4px; border: 1px solid {t['GRID_COLOR']}; }}
        """
        self.setStyleSheet(qss)
        if hasattr(self, 'lbl_trig_val'):
            self.lbl_trig_val.setStyleSheet(f"font-weight: bold; color: {t['TRIG_COLOR']};")
        self._update_metric_colors()
        
        # Plot styling
        self.plot.setBackground(t['BG_SCREEN'])
        self.curve.setPen(pg.mkPen(t['CH1_COLOR'], width=2))
        self.plot.getAxis('left').setPen(pg.mkPen(t['GRID_COLOR']))
        self.plot.getAxis('bottom').setPen(pg.mkPen(t['GRID_COLOR']))
        self.plot.getAxis('left').setTextPen(pg.mkPen(t['TEXT_LIGHT']))
        self.plot.getAxis('bottom').setTextPen(pg.mkPen(t['TEXT_LIGHT']))
        
        if self.fft_window:
            self.fft_window.apply_theme(t)

    def _refresh_ports(self):
        self.combo_ports.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if not ports:
            self.combo_ports.addItem("No ports found")
        else:
            self.combo_ports.addItems(ports)

    def _toggle_run(self):
        if self.ser and self.ser.is_open:
            self.timer.stop()
            self.ser.close()
            self.ser = None
            self.btn_run.setText("▶ RUN")
            self.lbl_status.setText("DISCONNECTED")
            self.lbl_status.setStyleSheet("color: #ff6b6b; font-weight: bold; border: 1px solid #ff6b6b; padding: 5px;")
        else:
            port = self.combo_ports.currentText()
            if port and port != "No ports found":
                try:
                    self.ser = serial.Serial(port, 115200, timeout=0)
                    self.binary_buffer = bytearray()
                    self.timer.start(1)
                    self.btn_run.setText("■ STOP")
                    self.lbl_status.setText("CONNECTED")
                    self.lbl_status.setStyleSheet("color: #4ecca3; font-weight: bold; border: 1px solid #4ecca3; padding: 5px;")
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def _toggle_fft_window(self):
        if not self.fft_window:
            self.fft_window = FFTWindow(self.current_theme)
        self.fft_window.show()

    def _save_csv(self):
        if len(self.display_data) == 0: return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Guardar Datos CSV", "", "CSV Files (*.csv)")
        if path:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Tiempo (us)", "Voltaje (V)"])
                for i, v in enumerate(self.display_data):
                    writer.writerow([i * self.fixed_sample_us, v])
            QtWidgets.QMessageBox.information(self, "Guardado", "Archivo guardado exitosamente.")

    def _toggle_sinc(self, state): self.use_sinc = (state == QtCore.Qt.Checked)
    def _toggle_filter(self, state): self.use_filter = (state == QtCore.Qt.Checked)
    def _on_stitch_changed(self, text): self.stitch_count = int(text.split('x')[0])

    def _on_time_changed(self, text):
        us_per_div, hardware_idx, sample_us = TIME_DIV_MAP[text]
        self.fixed_sample_us = sample_us
        
        # Enviar comando de velocidad al hardware
        if self.ser and self.ser.is_open:
            try:
                cmd = f"S{hardware_idx}".encode()
                self.ser.write(cmd)
                print(f"Sent speed command: {cmd}")
            except Exception as e:
                print(f"Error sending speed command: {e}")

        total_us = us_per_div * H_DIVS
        self.plot.setXRange(0, total_us * 1e-6, padding=0)

    def _on_vdiv_changed(self, text):
        _, ymin, ymax = V_DIV_MAP[text]
        self.plot.setYRange(ymin, ymax, padding=0)

    def _on_offset_changed(self, val):
        self.vertical_offset = val
        self._on_trig_line_moved()

    def _on_trig_line_dragging(self):
        val_v = self.trig_line.value()
        self.lbl_trig_val.setText(f"{val_v:.2f} V")

    def _on_trig_line_moved(self):
        val_v = self.trig_line.value()
        self.lbl_trig_val.setText(f"{val_v:.2f} V")
        
        # Compensar offset para calcular el voltaje real de entrada
        real_v = val_v - self.vertical_offset
            
        # Convertir a voltaje de pin y luego a valor digital de ADC (12 bits: 0 a 4095)
        # Ecuación del AFE: Vpin = (Vreal + 1.60) / 2
        pin_v = (real_v + 1.60) / 2.0
        adc_val = int((pin_v / 3.3) * 4095)
        
        if adc_val < 0: adc_val = 0
        if adc_val > 4095: adc_val = 4095
        
        if self.ser and self.ser.is_open:
            try:
                cmd = f"L{adc_val}\n".encode()
                self.ser.write(cmd)
                print(f"Sent Trigger Level: {cmd} (adc_val={adc_val}, real_v={real_v:.3f}V)")
            except Exception as e:
                print(f"Error sending trigger: {e}")

    def _update_cursor_info(self):
        dt = abs(self.cursor_t2.value() - self.cursor_t1.value())
        dv = abs(self.cursor_v2.value() - self.cursor_v1.value())
        freq = 1.0 / dt if dt > 0 else 0
        
        if dt < 0.001: self.lbl_delta_t.setText(f"ΔT: {dt*1e6:.1f} µs | {freq/1e3:.1f} kHz")
        else: self.lbl_delta_t.setText(f"ΔT: {dt*1e3:.2f} ms | {freq:.1f} Hz")
        self.lbl_delta_v.setText(f"ΔV: {dv:.2f} V")

    def _read_serial(self):
        if not self.ser or not self.ser.is_open: return
        try:
            waiting = self.ser.in_waiting
            if waiting < 4: return
            
            chunk = self.ser.read(waiting)
            
            # Detectar si el micro nos mandó un nivel de trigger (Auto-Set)
            if b'L' in chunk:
                try:
                    parts = chunk.split(b'L')
                    if len(parts) > 1:
                        val_str = parts[1].split(b'\n')[0]
                        adc_val = int(val_str)
                        pin_v = (adc_val / 4095.0) * 3.3
                        real_v = (pin_v * 2.0) - 1.60
                        
                        # Convertir a coordenadas de pantalla (con offset)
                        screen_v = real_v + self.vertical_offset
                        
                        self.trig_line.setValue(screen_v)
                        self.lbl_trig_val.setText(f"{screen_v:.2f} V")
                except: pass

            self.binary_buffer.extend(chunk)
            header = b'\xaa\xbb\xcc\xdd'
            frame_size = 2052 # 4 header + 1024*2
            
            while True:
                idx = self.binary_buffer.find(header)
                if idx == -1:
                    if len(self.binary_buffer) > 16384: self.binary_buffer = self.binary_buffer[-4:]
                    break
                if len(self.binary_buffer) < idx + frame_size:
                    self.binary_buffer = self.binary_buffer[idx:]
                    break
                
                raw_data = self.binary_buffer[idx+4 : idx+frame_size]
                samples_mv = struct.unpack('<' + 'h' * CHUNK_SIZE, raw_data)
                frame = [v / 1000.0 for v in samples_mv]
                
                self.history_frames.append(frame)
                while len(self.history_frames) > self.stitch_count: self.history_frames.pop(0)
                
                flat = []
                for f in self.history_frames: flat.extend(f)
                raw_display = np.array(flat)
                
                # Aplicar offset de software
                processed_data = raw_display + self.vertical_offset
                self.display_data = processed_data
                
                # DSP
                plot_data = self.display_data
                if self.use_filter and len(plot_data) > 5:
                    plot_data = np.convolve(plot_data, np.ones(5)/5, mode='same')
                if self.use_sinc and len(plot_data) > 10:
                    plot_data = signal.resample(plot_data, len(plot_data) * 4)
                
                # Eje X en SEGUNDOS
                time_axis = np.linspace(0, len(plot_data) * self.fixed_sample_us * 1e-6, len(plot_data))
                self.curve.setData(time_axis, plot_data)
                
                self._update_metrics(self.display_data)
                if self.fft_window and self.fft_window.isVisible():
                    self.fft_window.update_fft(self.display_data, self.fixed_sample_us)
                
                self.binary_buffer = self.binary_buffer[idx+frame_size:]

        except Exception as e:
            print(f"Serial Error: {e}")
            self.binary_buffer = bytearray()

    def _update_metrics(self, data):
        if len(data) < 10: return
        try:
            vmax, vmin = np.max(data), np.min(data)
            vrms = np.sqrt(np.mean(data**2))
            self.lbl_vmax.setText(f"{vmax:.2f} V")
            self.lbl_vmin.setText(f"{vmin:.2f} V")
            self.lbl_vpp.setText(f"{vmax-vmin:.2f} V")
            self.lbl_vrms.setText(f"{vrms:.2f} V")
            
            # Frecuencia y Duty
            mid = (vmax + vmin) / 2
            if (vmax - vmin) > 0.1:
                crossings = np.where(np.diff(data > mid))[0]
                if len(crossings) >= 2:
                    period = np.mean(np.diff(crossings)) * 2 * self.fixed_sample_us
                    freq = 1_000_000 / period if period > 0 else 0
                    self.lbl_freq.setText(f"{freq/1e3:.2f} kHz" if freq > 1000 else f"{freq:.1f} Hz")
                    
                    high_samples = np.sum(data > mid)
                    duty = (high_samples / len(data)) * 100
                    self.lbl_duty.setText(f"{duty:.1f} %")
        except: pass

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    ex = BluePillScopeViewer()
    ex.show()
    sys.exit(app.exec_())
