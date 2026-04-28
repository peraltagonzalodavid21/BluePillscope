# PillScope Pro 🔬

Un osciloscopio digital de alto rendimiento basado en el microcontrolador **STM32F103 (Blue Pill)** y una interfaz gráfica profesional en Python. Desarrollado como proyecto de ingeniería para visualización, análisis y medición de señales eléctricas en tiempo real.

## 🚀 Características Principales

*   **Hardware Robusto:** Basado en la arquitectura ARM Cortex-M3 (STM32F103C8T6 a 72MHz).
*   **Interfaz Hantek-Style:** GUI desarrollada en Python (`pyqtgraph` y `PyQt5`) con estética profesional, modo oscuro y alta fluidez.
*   **Adquisición de Datos:** Base de tiempo fija y ultra-estable de 100µs/muestra mediante DMA y Timer 3.
*   **Comunicación USB CDC:** Transmisión de datos crudos ininterrumpida y sin parpadeos ("doble pulso" eliminado por hardware-holdoff).
*   **Herramientas de Medición:**
    *   **Cursores de Tiempo y Voltaje** (Medición manual de ΔT, 1/ΔT y ΔV).
    *   Cálculos matemáticos en vivo: V-Max, V-Min, V-PP, Frecuencia y Duty Cycle.
*   **Analizador de Espectro:** Implementación de Fast Fourier Transform (FFT) para visualizar frecuencias armónicas en tiempo real.
*   **Deep Memory (Memoria Cosida):** Historial ajustable de 1x hasta 32x (4096 puntos en pantalla).

## 🛠️ Stack Tecnológico

*   **Firmware:** C (STM32CubeIDE, HAL Drivers, USB Device Library).
*   **Software PC:** Python 3 (PySerial, NumPy, PyQtGraph, PyQt5).
*   **Frontend Analógico (AFE):** Op-Amp LM358 configurado para sumar un offset DC de 1.65V, permitiendo al ADC leer señales de Corriente Alterna (AC) como audio y senoidales puras.

## ⚙️ Uso y Controles

1. Conectar la Blue Pill al puerto USB de la PC.
2. Ejecutar `pillscope_app.py`.
3. Seleccionar el puerto COM y presionar **RUN**.
4. Utilizar la rueda del ratón para hacer *Zoom In/Out* (Eje X) y arrastrar para mover la gráfica.
5. El nivel de *Trigger* se ajusta arrastrando la línea horizontal roja.

---
*Proyecto de Electrónica - 2026*
