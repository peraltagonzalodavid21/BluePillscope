# BluePillScope v4.0: High-Performance DSO Architecture
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: STM32](https://img.shields.io/badge/Platform-STM32F103-blue.svg)](https://www.st.com/en/microcontrollers-microprocessors/stm32f103.html)

**BluePillScope** es un osciloscopio digital de almacenamiento (DSO) de alto rendimiento basado en el microcontrolador **STM32F103C8T6 (Blue Pill)** y una estación base desarrollada en **Python**. 

Este proyecto representa una evolución desde la instrumentación educativa de 8 bits hacia una arquitectura determinística de nivel ingeniería, logrando una tasa de muestreo uniforme de **1.714 MSPS** mediante técnicas avanzadas de hardware-interleaving y gestión de datos por DMA.

---

## Características Principales

*   **Arquitectura Dual ADC Interleaved:** Uso coordinado de ADC1 (Master) y ADC2 (Slave) para duplicar la resolución temporal [1, 2].
*   **Adquisición Determinística (1.71 MSPS):** Sincronización precisa mediante Timer 3 (ARR=83) y tiempos de muestreo de 1.5 ciclos, eliminando errores de *overrun* [3, 4].
*   **Protocolo Binario Eficiente:** Reducción del ancho de banda USB de 68.4 Mbps (ASCII) a solo **27.36 Mbps** mediante ráfagas binarias de 16 bits [5].
*   **Interfaz Híbrida (Control Físico):** Mapeo completo del Puerto B (PB9-PB15) para control instantáneo de disparo, acoplamiento y atenuación [6].
*   **DSP en Tiempo Real:** Reconstrucción de señal mediante interpolación de **Whittaker-Shannon (Sinc)** y filtrado de media móvil [7, 8].
*   **Instrumentación Avanzada:** Analizador de Espectro (FFT) y cursores manuales para mediciones de $\Delta T$ y $\Delta V$ [9, 10].

---

## Especificaciones Técnicas (The "Datasheet" View)

### Unidad de Adquisición (Firmware C)
La estabilidad del sistema no depende de latencias de software. El hardware está configurado para una captura 100% uniforme [11]:
- **Reloj ADC:** 12 MHz.
- **Ciclos de Conversión:** 14 ciclos ($1.167\,\mu s$).
- **Sample Rate Real:** 1 sample cada 7 ciclos de reloj ADC ($583.33\,ns$) -> **1.714 MSPS** [3, 4].
- **Deep Memory:** Buffer contiguo de 1024 muestras (ventana de ~600 µs sin interrupciones) [3, 12].

### Front-End Analógico (AFE)
Basado en un amplificador operacional **LM358** con inyección de offset de 1.65V para señales AC [13].
- **Limitación Física:** El *Slew Rate* de $0.5\,V/\mu s$ impone una rampa de ~11 muestras en flancos cuadrados de 3.3V ($T_{rise} = 6.6\,\mu s$). Este comportamiento es una frontera analógica caracterizada, no un error de código [14].

---

## Interfaz de Usuario e Interacción
El BluePillScope permite una interacción híbrida única entre controles físicos y digitales [15]:

| Pin | Función | Descripción |
| :--- | :--- | :--- |
| **PB13** | **Coupling AC/DC** | Resta de offset de 1.65V en firmware para centrar señales AC [6]. |
| **PB10** | **Trigger Slope** | Alterna entre flanco ascendente y descendente [6]. |
| **PB14** | **Run / Stop** | Congela la trama actual (Modo HOLD) [6]. |
| **GUI** | **Instrumentación** | FFT Spectrum, Cursores manuales y Exportación a CSV [7, 9]. |

---

## Estructura del Repositorio
- `/Core`: Firmware desarrollado en C (STM32CubeIDE + HAL) [16].
- `/App Python`: Aplicación Host (Python 3.x, PyQt5, PyQtGraph, NumPy) [16].
- `/Docs`: Reportes técnicos detallados y bitácoras de investigación [17].

---

## Hoja de Ruta (Roadmap)
Para escalar este proyecto a niveles de instrumentación profesional se proponen [18-20]:
1.  Sustitución del LM358 por un **TL072** para mejorar la respuesta transitoria a flancos rápidos.
3.  Migración a arquitecturas **RP2040** para aprovechar bloques PIO y mayor RAM.

---

## Autor
**Gonzalo David Peralta**
