"""
PillScope Viewer v3.0 - Hantek-Style Interface
Diseñado especificamente para el firmware STM32F103 Blue Pill de PERA.
Protocolo: FRAME_START\n seguido de 128 lineas float (en Voltios).
"""
import sys
import serial
import serial.tools.list_ports
import pyqtgraph as pg
import numpy as np
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import QSizePolicy

# ─── Paleta de Colores (Hantek DSO Inspired) ─────────────────────────────────
BG_APP      = "#1a1a2e"      # Fondo de la aplicacion
BG_SCREEN   = "#0a0a0a"      # Pantalla del osciloscopio (negro tubo)
BG_PANEL    = "#16213e"      # Paneles laterales/inferior
GRID_COLOR  = "#2a4060"      # Cuadricula suave azulada
CH1_COLOR   = "#FFE066"      # Canal 1: Amarillo Hantek
TRIG_COLOR  = "#FF6B6B"      # Linea de Trigger: Rojo
ACCENT_BLUE = "#0F3460"      # Botones
ACCENT_CYAN = "#00D4FF"      # Highlight activo
TEXT_LIGHT  = "#E0E0E0"      # Texto claro
TEXT_DIM    = "#606080"      # Texto apagado
BTN_NORMAL  = "#1e3a5f"
BTN_ACTIVE  = "#0F3460"
BTN_GREEN   = "#1a472a"
BTN_RED     = "#5c1a1a"

# Tiempo FIJO del firmware (PSC=71, ARR=99 en TIM3 a 72MHz) = 100µs/muestra
# Este valor nunca cambia. El Time/Div del UI es solo zoom visual.
FIXED_SAMPLE_US = 100

# Cuantos "divs" horizontales tiene la pantalla (estandar osciloscopio = 10)
H_DIVS = 10

# ── Mapeo de Time/Div y V/Div ───────────────────────────────────────────────
TIME_DIV_MAP = {
    "10 µs/div":   10,
    "20 µs/div":   20,
    "50 µs/div":   50,
    "100 µs/div":  100,
    "200 µs/div":  200,
    "500 µs/div":  500,
    "1 ms/div":    1000,
    "2 ms/div":    2000,
    "5 ms/div":    5000,
    "10 ms/div":   10000,
    "20 ms/div":   20000,
}

V_DIV_MAP = {
    "500 mV/div":  (0.5,  -0.5, 4.0),
    "1 V/div":     (1.0,  -1.0, 5.0),
    "2 V/div":     (2.0,  -2.5, 5.0),
    "5 V/div":     (5.0,  -5.5, 5.5),
    "50 mV/div":   (0.05, -0.1, 0.5),
    "100 mV/div":  (0.1,  -0.2, 1.0),
}


# ── Clases Auxiliares ────────────────────────────────────────────────────────

class FFTWindow(QtWidgets.QWidget):
    """Ventana independiente para el Analizador de Espectro (Transformada de Fourier)"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Analizador de Espectro (FFT)")
        self.resize(800, 400)
        self.setStyleSheet(f"background-color: {BG_APP};")
        layout = QtWidgets.QVBoxLayout(self)
        
        self.plot = pg.PlotWidget()
        self.plot.setBackground(BG_SCREEN)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel('bottom', 'Frecuencia', units='Hz', color=TEXT_DIM, size='10pt')
        self.plot.setLabel('left', 'Amplitud Relativa', color=TEXT_DIM, size='10pt')
        
        # Color púrpura eléctrico para la FFT
        self.curve = self.plot.plot([], [], pen=pg.mkPen(color='#DDA0DD', width=2))
        layout.addWidget(self.plot)
        
    def update_fft(self, display_data, fixed_sample_us):
        if len(display_data) < 128: return
        n = len(display_data)
        
        # Calcular Frecuencias en el Eje X (d es el periodo en segundos)
        freq = np.fft.rfftfreq(n, d=fixed_sample_us * 1e-6)
        
        # Calcular Amplitudes en el Eje Y
        fft_vals = np.abs(np.fft.rfft(display_data))
        
        # Remover el componente DC (Frecuencia 0Hz) para que no aplaste visualmente a los demás picos
        if len(fft_vals) > 0:
            fft_vals[0] = 0 
            
        self.curve.setData(freq, fft_vals)

def make_btn(text, color=BTN_NORMAL, min_w=None, checkable=False):
    btn = QtWidgets.QPushButton(text)
    btn.setCheckable(checkable)
    btn.setMinimumHeight(28)
    if min_w: btn.setMinimumWidth(min_w)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {color}; color: {TEXT_LIGHT};
            border: 1px solid #2a4060; border-radius: 4px;
            font-size: 12px; font-weight: bold; padding: 3px 8px;
        }}
        QPushButton:hover {{ background-color: #234980; border-color: {ACCENT_CYAN}; }}
        QPushButton:checked {{ background-color: #0e5c8a; border-color: {ACCENT_CYAN}; color: {ACCENT_CYAN}; }}
    """)
    return btn


def make_label(text, size=12, color=TEXT_LIGHT, bold=False):
    lbl = QtWidgets.QLabel(text)
    weight = "bold" if bold else "normal"
    lbl.setStyleSheet(f"color: {color}; font-size: {size}px; font-weight: {weight};")
    return lbl


class PillScopeViewer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PillScope Pro  |  STM32F103 Blue Pill")
        self.resize(1280, 760)
        self.setMinimumSize(1024, 640)
        self.setStyleSheet(f"background-color: {BG_APP}; color: {TEXT_LIGHT};")

        # ── Estado ────────────────────────────────────────────────────────────
        self.ser = None
        self.running = False
        self.buffer_str = ""
        self.reading_frame = False
        self.current_frame = []
        self.history_frames = []
        self.MAX_HISTORY = 8          # Default 8×128 = 1024 puntos (máximo 32×128 = 4096)
        self.display_data = np.array([])
        self.trig_level = 1.65        # Voltios. Linea visual del trigger
        self.current_time_us = 100   # us_per_div inicial
        self.zoom_x_min = 0          # Pan horizontal

        # ── Layout Maestro ────────────────────────────────────────────────────
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        root_vbox = QtWidgets.QVBoxLayout(root)
        root_vbox.setSpacing(4)
        root_vbox.setContentsMargins(6, 4, 6, 4)

        # Barra Superior
        root_vbox.addLayout(self._build_topbar())

        # Cuerpo: Pantalla + Panel Derecho
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(4)
        body.addLayout(self._build_screen(), stretch=5)
        body.addLayout(self._build_right_panel(), stretch=0)
        root_vbox.addLayout(body, stretch=1)

        # Barra Inferior de Métricas
        root_vbox.addLayout(self._build_bottombar())

        # ── Timer ─────────────────────────────────────────────────────────────
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._read_serial)

    # ──────────────────────────────────────────────────────────────────────────
    # UI BUILDERS
    # ──────────────────────────────────────────────────────────────────────────

    def _build_topbar(self):
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)

        # Logo / título
        logo = make_label("⚡ PillScope", size=16, color=ACCENT_CYAN, bold=True)
        bar.addWidget(logo)

        sep = QtWidgets.QFrame(); sep.setFrameShape(QtWidgets.QFrame.VLine)
        sep.setStyleSheet(f"color: {GRID_COLOR};"); bar.addWidget(sep)

        # Puerto COM
        bar.addWidget(make_label("Puerto:", size=12))
        self.combo_ports = QtWidgets.QComboBox()
        self.combo_ports.setFixedWidth(100)
        self.combo_ports.setStyleSheet(f"""
            QComboBox {{ background-color: {BTN_NORMAL}; color: {TEXT_LIGHT};
                border: 1px solid {GRID_COLOR}; border-radius: 3px; padding: 2px 6px; font-size: 12px; }}
            QComboBox::drop-down {{ border: none; }}
        """)
        self._refresh_ports()
        bar.addWidget(self.combo_ports)

        btn_ref = make_btn("🔄", min_w=30)
        btn_ref.setToolTip("Refrescar Puertos COM")
        btn_ref.clicked.connect(self._refresh_ports)
        bar.addWidget(btn_ref)

        self.btn_run = make_btn("▶ RUN", color=BTN_GREEN, min_w=90)
        self.btn_run.clicked.connect(self._toggle_run)
        bar.addWidget(self.btn_run)

        bar.addStretch()

        # Indicadores de estado
        self.lbl_status = make_label("● DESCONECTADO", color="#FF6B6B", bold=True)
        bar.addWidget(self.lbl_status)

        return bar

    def _build_screen(self):
        vbox = QtWidgets.QVBoxLayout()
        vbox.setSpacing(0)

        # Etiqueta CH1
        ch1_lbl = make_label("  CH1 (100µs/div)  |  1V/div  |  DC", size=11, color=CH1_COLOR, bold=True)
        self.lbl_ch1_info = ch1_lbl
        ch1_lbl.setStyleSheet(f"background-color: #0d1b2a; color: {CH1_COLOR}; font-size:11px; font-weight:bold; padding: 2px 8px; border-bottom: 2px solid {CH1_COLOR};")
        vbox.addWidget(ch1_lbl)

        # PlotWidget principal
        self.plot = pg.PlotWidget()
        self.plot.setBackground(BG_SCREEN)
        # Habilitar mouse: wheel=zoom X, drag=pan X
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setMenuEnabled(False)
        # Conectar wheel event propio para zoom centrado
        self.plot.scene().sigMouseClicked.connect(lambda e: None)  # placeholder

        # Estilo de los ejes
        for axis in ['left', 'bottom', 'top', 'right']:
            self.plot.showAxis(axis)
            ax = self.plot.getAxis(axis)
            ax.setPen(pg.mkPen(GRID_COLOR))
            ax.setTextPen(pg.mkPen(TEXT_DIM))

        self.plot.getAxis('left').setLabel('Voltaje', units='V', color=TEXT_DIM, size='10pt')
        self.plot.getAxis('bottom').setLabel('', color=TEXT_DIM, size='9pt')
        self.plot.setYRange(-1.0, 5.0, padding=0)

        # Traza del canal 1
        self.curve = self.plot.plot([], [], pen=pg.mkPen(color=CH1_COLOR, width=2))

        # Linea de Trigger
        self.trig_line = pg.InfiniteLine(
            pos=self.trig_level, angle=0,
            pen=pg.mkPen(TRIG_COLOR, width=1, style=QtCore.Qt.DashLine),
            movable=True, label='T', labelOpts={'color': TRIG_COLOR, 'position': 0.95}
        )
        self.trig_line.sigPositionChanged.connect(self._on_trig_moved)
        self.plot.addItem(self.trig_line)

        # ── Cursores (Ocultos por defecto) ──
        cursor_pen_t = pg.mkPen("#E0E0E0", width=1, style=QtCore.Qt.DotLine)
        cursor_pen_v = pg.mkPen("#00D4FF", width=1, style=QtCore.Qt.DotLine)
        
        self.cur_t1 = pg.InfiniteLine(pos=10, angle=90, pen=cursor_pen_t, movable=True, label='T1', labelOpts={'color': '#E0E0E0', 'position': 0.1})
        self.cur_t2 = pg.InfiniteLine(pos=100, angle=90, pen=cursor_pen_t, movable=True, label='T2', labelOpts={'color': '#E0E0E0', 'position': 0.15})
        self.cur_v1 = pg.InfiniteLine(pos=1.0, angle=0, pen=cursor_pen_v, movable=True, label='V1', labelOpts={'color': '#00D4FF', 'position': 0.1})
        self.cur_v2 = pg.InfiniteLine(pos=2.0, angle=0, pen=cursor_pen_v, movable=True, label='V2', labelOpts={'color': '#00D4FF', 'position': 0.15})
        
        for cur in [self.cur_t1, self.cur_t2, self.cur_v1, self.cur_v2]:
            self.plot.addItem(cur)
            cur.hide()
            cur.sigPositionChanged.connect(self._update_cursors)

        vbox.addWidget(self.plot)
        return vbox

    def _build_right_panel(self):
        panel = QtWidgets.QVBoxLayout()
        panel.setSpacing(6)

        # ── SECCIÓN: Time/Div ─────────────────────────────────────────────────
        panel.addWidget(self._section_title("⏱ TIEMPO (Zoom Visual)"))
        self.combo_time = self._build_combo(list(TIME_DIV_MAP.keys()), "100 µs/div")
        self.combo_time.currentTextChanged.connect(self._on_time_changed)
        panel.addWidget(self.combo_time)
        panel.addLayout(self._build_zoom_controls())
        lbl_hint = make_label("Rueda mouse = zoom\nArrastre = pan", size=9, color=TEXT_DIM)
        panel.addWidget(lbl_hint)

        # ── SECCIÓN: V/Div ────────────────────────────────────────────────────
        panel.addWidget(self._section_title("📶 VERTICAL CH1"))
        self.combo_vdiv = self._build_combo(list(V_DIV_MAP.keys()), "1 V/div")
        self.combo_vdiv.currentTextChanged.connect(self._on_vdiv_changed)
        panel.addWidget(self.combo_vdiv)

        # ── SECCIÓN: Trigger Visual ────────────────────────────────────────────
        panel.addWidget(self._section_title("🎯 TRIGGER"))
        trig_row = QtWidgets.QHBoxLayout()
        trig_row.addWidget(make_label("Nivel:", size=11))
        self.lbl_trig_val = make_label(f"{self.trig_level:.2f} V", size=11, color=TRIG_COLOR)
        trig_row.addWidget(self.lbl_trig_val)
        panel.addLayout(trig_row)
        trig_note = make_label("(Arrastrá la línea roja)", size=9, color=TEXT_DIM)
        panel.addWidget(trig_note)

        # ── SECCIÓN: Memoria ──────────────────────────────────────────────────
        panel.addWidget(self._section_title("💾 MEMORIA (frames cosidos)"))
        mem_row1 = QtWidgets.QHBoxLayout()
        mem_row2 = QtWidgets.QHBoxLayout()
        for n, lbl in [(1,"1x"),(2,"2x"),(4,"4x"),(8,"8x")]:
            b = make_btn(lbl, min_w=35)
            b.clicked.connect(lambda checked, frames=n: self._set_history(frames))
            mem_row1.addWidget(b)
        for n, lbl in [(12,"12x"),(16,"16x"),(24,"24x"),(32,"32x")]:
            b = make_btn(lbl, min_w=35)
            b.clicked.connect(lambda checked, frames=n: self._set_history(frames))
            mem_row2.addWidget(b)
        panel.addLayout(mem_row1)
        panel.addLayout(mem_row2)
        self.lbl_mem_pts = make_label("8x = 1024 pts", size=9, color=TEXT_DIM)
        panel.addWidget(self.lbl_mem_pts)

        # ── SECCIÓN: Cursores Profesionales ──────────────────────────────────
        panel.addWidget(self._section_title("📐 CURSORES"))
        
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_cur_t = make_btn("T1/T2", checkable=True, min_w=60)
        self.btn_cur_v = make_btn("V1/V2", checkable=True, min_w=60)
        self.btn_cur_t.clicked.connect(self._toggle_cursors)
        self.btn_cur_v.clicked.connect(self._toggle_cursors)
        btn_row.addWidget(self.btn_cur_t)
        btn_row.addWidget(self.btn_cur_v)
        panel.addLayout(btn_row)

        self.lbl_cur_dt = make_label("ΔT: --", size=11, color="#E0E0E0")
        self.lbl_cur_f  = make_label("1/ΔT: --", size=11, color="#E0E0E0", bold=True)
        self.lbl_cur_dv = make_label("ΔV: --", size=11, color="#00D4FF", bold=True)
        
        panel.addWidget(self.lbl_cur_dt)
        panel.addWidget(self.lbl_cur_f)
        panel.addWidget(self.lbl_cur_dv)

        panel.addStretch()

        # ── MEDICIONES ────────────────────────────────────────────────────────
        panel.addWidget(self._section_title("📊 MEDICIONES"))

        for attr, name, color in [
            ("lbl_freq",   "Freq:",    ACCENT_CYAN),
            ("lbl_vmax",   "V-Max:",   "#98FB98"),
            ("lbl_vmin",   "V-Min:",   "#FFA07A"),
            ("lbl_vpp",    "V-PP:",    CH1_COLOR),
            ("lbl_duty",   "Duty %:",  "#DDA0DD"),
        ]:
            row = QtWidgets.QHBoxLayout()
            row.addWidget(make_label(name, size=11, color=TEXT_DIM))
            lbl = make_label("---", size=11, color=color, bold=True)
            setattr(self, attr, lbl)
            row.addWidget(lbl)
            panel.addLayout(row)

        panel.addSpacing(10)
        self.btn_fft = make_btn("📊 Analizador Espectro (FFT)", color="#4B0082")
        self.btn_fft.clicked.connect(self._show_fft)
        panel.addWidget(self.btn_fft)

        return panel

    def _build_bottombar(self):
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(16)

        bg = f"background-color: {BG_PANEL}; border: 1px solid {GRID_COLOR}; border-radius:4px; padding: 4px 8px;"

        fields = [
            ("lbl_b_time",   "Time/Div",  "100 µs"),
            ("lbl_b_sr",     "Sample Δ",  "100 µs"),
            ("lbl_b_vdiv",   "V/Div CH1", "1 V"),
            ("lbl_b_trig",   "Trigger",   "1.65 V"),
            ("lbl_b_pts",    "Puntos",    "512"),
        ]
        for attr, name, val in fields:
            box = QtWidgets.QWidget()
            bv = QtWidgets.QVBoxLayout(box)
            bv.setContentsMargins(0,0,0,0); bv.setSpacing(0)
            bv.addWidget(make_label(name, size=9, color=TEXT_DIM))
            lbl = make_label(val, size=12, color=TEXT_LIGHT, bold=True)
            setattr(self, attr, lbl)
            bv.addWidget(lbl)
            box.setStyleSheet(bg)
            bar.addWidget(box)

        bar.addStretch()

        # Boton guardar CSV
        btn_csv = make_btn("💾 Guardar CSV", color=ACCENT_BLUE)
        btn_csv.clicked.connect(self._save_csv)
        bar.addWidget(btn_csv)

        return bar

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _section_title(self, text):
        lbl = make_label(text, size=10, color=TEXT_DIM)
        lbl.setStyleSheet(f"color:{TEXT_DIM}; font-size:10px; border-bottom:1px solid {GRID_COLOR}; padding-bottom:2px; margin-top:6px;")
        return lbl

    def _build_combo(self, items, default):
        c = QtWidgets.QComboBox()
        c.addItems(items)
        c.setCurrentText(default)
        c.setFixedWidth(155)
        c.setStyleSheet(f"""
            QComboBox {{ background-color:{BTN_NORMAL}; color:{TEXT_LIGHT}; border:1px solid {GRID_COLOR};
                         border-radius:3px; padding:3px 6px; font-size:12px; }}
            QComboBox::drop-down {{ border:none; }}
            QComboBox QAbstractItemView {{ background-color:#1a2a40; color:{TEXT_LIGHT}; selection-background-color:{ACCENT_BLUE}; }}
        """)
        return c

    def _build_zoom_controls(self):
        """Botones de zoom rapido horizontal a agregar al panel de tiempo."""
        row = QtWidgets.QHBoxLayout()
        btn_zoom_in  = make_btn("🔍+", min_w=45)
        btn_zoom_out = make_btn("🔍-", min_w=45)
        btn_zoom_fit = make_btn("FIT", min_w=45)
        btn_zoom_in.setToolTip("Zoom In Horizontal (o rueda del mouse)")
        btn_zoom_out.setToolTip("Zoom Out Horizontal (o rueda del mouse)")
        btn_zoom_fit.setToolTip("Ver todos los puntos")
        btn_zoom_in.clicked.connect(lambda: self._do_zoom(0.5))
        btn_zoom_out.clicked.connect(lambda: self._do_zoom(2.0))
        btn_zoom_fit.clicked.connect(self._zoom_fit)
        row.addWidget(btn_zoom_in)
        row.addWidget(btn_zoom_out)
        row.addWidget(btn_zoom_fit)
        return row

    # ── SLOTS ─────────────────────────────────────────────────────────────────

    def _refresh_ports(self):
        prev = self.combo_ports.currentText() if hasattr(self, 'combo_ports') else ""
        if hasattr(self, 'combo_ports'):
            self.combo_ports.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.combo_ports.addItem(p.device)
        if prev:
            idx = self.combo_ports.findText(prev)
            if idx >= 0: self.combo_ports.setCurrentIndex(idx)

    def _toggle_run(self):
        if self.ser and self.ser.is_open:
            # Detener
            self.timer.stop()
            self.ser.close()
            self.ser = None
            self.running = False
            self.btn_run.setText("▶ RUN")
            self.btn_run.setStyleSheet(self.btn_run.styleSheet().replace("#8b0000","").replace("background-color: #1a472a","background-color: #5c1a1a"))
            self.lbl_status.setText("● DESCONECTADO")
            self.lbl_status.setStyleSheet(f"color: #FF6B6B; font-size:12px; font-weight:bold;")
        else:
            port = self.combo_ports.currentText()
            if not port:
                QtWidgets.QMessageBox.warning(self, "Error", "Seleccioná un puerto COM primero.")
                return
            try:
                self.ser = serial.Serial(port, 115200, timeout=0)
                self.buffer_str = ""
                self.reading_frame = False
                self.current_frame = []
                self.history_frames = []
                self.running = True
                self.btn_run.setText("■ STOP")
                self.lbl_status.setText(f"● CONECTADO  {port}")
                self.lbl_status.setStyleSheet(f"color: #98FB98; font-size:12px; font-weight:bold;")
                self.timer.start(1)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error Puerto", str(e))

    def _on_time_changed(self, text):
        us_per_div = TIME_DIV_MAP.get(text, 100)
        # Samples en pantalla = (us_per_div * H_DIVS) / us_por_muestra_fijo
        samples_visible = max(4, int((us_per_div * H_DIVS) / FIXED_SAMPLE_US))
        # Centrar la vista en el medio de los datos disponibles
        total = len(self.display_data) if len(self.display_data) > 0 else 512
        mid = total // 2
        half = samples_visible // 2
        xmin = max(0, mid - half)
        xmax = min(total, mid + half)
        self.plot.setXRange(xmin, xmax, padding=0)
        self.lbl_b_time.setText(text.split("/")[0].strip())
        self.lbl_b_sr.setText(f"{FIXED_SAMPLE_US} µs/smp")
        self._update_ch1_label()

    def _do_zoom(self, factor):
        """Zoom horizontal centrado en la vista actual."""
        vr = self.plot.viewRange()
        x0, x1 = vr[0]
        cx = (x0 + x1) / 2
        half = (x1 - x0) / 2 * factor
        total = len(self.display_data) if len(self.display_data) > 0 else 512
        new_x0 = max(0, cx - half)
        new_x1 = min(total, cx + half)
        if new_x1 - new_x0 >= 2:  # Minimo 2 muestras visibles
            self.plot.setXRange(new_x0, new_x1, padding=0)

    def _zoom_fit(self):
        """Ver todos los puntos disponibles."""
        total = len(self.display_data) if len(self.display_data) > 0 else 512
        self.plot.setXRange(0, total, padding=0.02)

    def _on_vdiv_changed(self, text):
        vdiv, ymin, ymax = V_DIV_MAP.get(text, (1.0, -1.0, 5.0))
        self.current_vdiv = vdiv
        self.plot.setYRange(ymin, ymax, padding=0)
        self.lbl_b_vdiv.setText(text.split("/")[0].strip())
        self._update_ch1_label()

    def _on_trig_moved(self):
        pos = self.trig_line.value()
        self.trig_level = pos
        self.lbl_trig_val.setText(f"{pos:.2f} V")
        self.lbl_b_trig.setText(f"{pos:.2f} V")

    def _show_fft(self):
        if not hasattr(self, 'fft_window'):
            self.fft_window = FFTWindow()
        self.fft_window.show()

    def _toggle_cursors(self):
        show_t = self.btn_cur_t.isChecked()
        show_v = self.btn_cur_v.isChecked()
        
        self.cur_t1.setVisible(show_t)
        self.cur_t2.setVisible(show_t)
        self.cur_v1.setVisible(show_v)
        self.cur_v2.setVisible(show_v)
        
        self._update_cursors()

    def _update_cursors(self):
        # Time cursors (X axis is in samples)
        t1 = self.cur_t1.value()
        t2 = self.cur_t2.value()
        dt_samples = abs(t2 - t1)
        dt_us = dt_samples * FIXED_SAMPLE_US
        
        if dt_us > 0:
            if dt_us >= 1000:
                self.lbl_cur_dt.setText(f"ΔT: {dt_us/1000:.2f} ms")
            else:
                self.lbl_cur_dt.setText(f"ΔT: {dt_us:.0f} µs")
            
            freq_hz = 1_000_000.0 / dt_us
            if freq_hz >= 1000:
                self.lbl_cur_f.setText(f"1/ΔT: {freq_hz/1000:.2f} kHz")
            else:
                self.lbl_cur_f.setText(f"1/ΔT: {freq_hz:.1f} Hz")
        else:
            self.lbl_cur_dt.setText("ΔT: --")
            self.lbl_cur_f.setText("1/ΔT: --")
            
        # Voltage cursors (Y axis)
        v1 = self.cur_v1.value()
        v2 = self.cur_v2.value()
        dv = abs(v2 - v1)
        self.lbl_cur_dv.setText(f"ΔV: {dv:.3f} V")

    def _set_history(self, n):
        self.MAX_HISTORY = n
        self.history_frames = self.history_frames[-n:]
        pts = n * 128
        self.lbl_mem_pts.setText(f"{n}x = {pts} pts  ({pts*FIXED_SAMPLE_US/1000:.1f} ms total)")

    def _update_ch1_label(self):
        tkey = self.combo_time.currentText().replace("/div","")
        vkey = self.combo_vdiv.currentText().replace("/div","")
        self.lbl_ch1_info.setText(f"  CH1  {tkey}/div  |  {vkey}/div  |  DC")

    # ── SERIAL ────────────────────────────────────────────────────────────────

    def _read_serial(self):
        if not self.ser or not self.ser.is_open:
            return
        try:
            waiting = self.ser.in_waiting
            if waiting == 0: return

            chunk = self.ser.read(waiting).decode('utf-8', errors='ignore')
            self.buffer_str += chunk

            if '\n' not in self.buffer_str:
                return

            lines = self.buffer_str.split('\n')
            self.buffer_str = lines.pop()

            for line in lines:
                line = line.strip()
                if not line: continue

                if line == "FRAME_START":
                    self.reading_frame = True
                    self.current_frame = []
                    continue

                if not self.reading_frame:
                    continue

                try:
                    self.current_frame.append(float(line))
                except ValueError:
                    pass

                if len(self.current_frame) == 128:
                    self.history_frames.append(list(self.current_frame))
                    if len(self.history_frames) > self.MAX_HISTORY:
                        self.history_frames.pop(0)

                    flat = []
                    for f in self.history_frames:
                        flat.extend(f)

                    if flat:
                        arr = np.array(flat, dtype=np.float32)
                        self.display_data = arr
                        self.curve.setData(arr)
                        self._compute_metrics(arr)
                        
                        # Actualizar FFT si la ventana está abierta
                        if hasattr(self, 'fft_window') and self.fft_window.isVisible():
                            self.fft_window.update_fft(arr, FIXED_SAMPLE_US)

                    self.reading_frame = False

        except Exception as e:
            print("Serial error:", e)

    # ── MATEMÁTICAS ───────────────────────────────────────────────────────────

    def _compute_metrics(self, arr):
        if len(arr) < 4: return

        v_max = float(np.max(arr))
        v_min = float(np.min(arr))
        v_pp  = v_max - v_min
        mid   = (v_max + v_min) / 2.0

        self.lbl_vmax.setText(f"{v_max:.3f} V")
        self.lbl_vmin.setText(f"{v_min:.3f} V")
        self.lbl_vpp.setText(f"{v_pp:.3f} V")
        self.lbl_b_pts.setText(str(len(arr)))

        # Frecuencia: SIEMPRE usa FIXED_SAMPLE_US (100µs real del firmware)
        # El selector Time/Div es solo zoom visual, no cambia la velocidad del chip.
        crossings = []
        for i in range(1, len(arr)):
            if arr[i-1] < mid <= arr[i]:
                crossings.append(i)

        if len(crossings) >= 2:
            periods_us = [(crossings[k+1] - crossings[k]) * FIXED_SAMPLE_US
                         for k in range(len(crossings)-1)]
            avg_period_us = np.mean(periods_us)
            if avg_period_us > 0:
                freq_hz = 1_000_000.0 / avg_period_us
                if freq_hz >= 1000:
                    self.lbl_freq.setText(f"{freq_hz/1000:.2f} kHz")
                else:
                    self.lbl_freq.setText(f"{freq_hz:.1f} Hz")

                # Duty Cycle
                high_samples = int(np.sum(arr > mid))
                duty = 100.0 * high_samples / len(arr)
                self.lbl_duty.setText(f"{duty:.1f} %")
        else:
            self.lbl_freq.setText("---")
            self.lbl_duty.setText("---")

    # ── GUARDAR CSV ───────────────────────────────────────────────────────────

    def _save_csv(self):
        if self.display_data is None or len(self.display_data) == 0:
            QtWidgets.QMessageBox.information(self, "Sin Datos", "Conectá el osciloscopio primero.")
            return
        fname, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Guardar Captura", "", "CSV Files (*.csv)")
        if not fname: return
        try:
            with open(fname, 'w') as f:
                f.write("muestra,voltaje_V\n")
                for i, v in enumerate(self.display_data):
                    f.write(f"{i},{v:.4f}\n")
            QtWidgets.QMessageBox.information(self, "Guardado", f"Captura guardada en:\n{fname}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def closeEvent(self, event):
        if self.ser and self.ser.is_open:
            self.ser.close()
        event.accept()


if __name__ == '__main__':
    pg.setConfigOptions(antialias=True)
    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Segoe UI", 10))
    w = PillScopeViewer()
    w.show()
    sys.exit(app.exec_())
