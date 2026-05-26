/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "usb_device.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
#define ADC_BUF_SIZE 2048   // Aumentado para 1.71 MSPS Uniformes (8KB RAM)
#define CHUNK_SIZE 1024     // Trama contigua de 597 µs
#define TRIGGER_LEVEL 2048 // ~1.65V

// Firmware States
#define MODE_CONTINUOUS 0
#define MODE_SINGLE 1
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
ADC_HandleTypeDef hadc1;
ADC_HandleTypeDef hadc2;
DMA_HandleTypeDef hdma_adc1;

TIM_HandleTypeDef htim3;
TIM_HandleTypeDef htim4;

/* USER CODE BEGIN PV */
uint32_t adc_buffer[ADC_BUF_SIZE]; // Buffer de 32 bits para Modo Dual
char tx_buffer[CHUNK_SIZE * 2 + 16]; // 2064 bytes para transporte binario seguro

uint32_t last_idx = 0;
uint16_t last_val = 0;
uint16_t trigger_level = 2048; // Umbral dinámico (inicial 1.65V)
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_DMA_Init(void);
static void MX_ADC1_Init(void);
static void MX_ADC2_Init(void);
static void MX_TIM3_Init(void);
static void MX_TIM4_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
#include "usbd_cdc_if.h"
#include <stdio.h>
#include <string.h>
#include <stdbool.h>

/* Helper para verificar si el puerto USB está listo (Evita cuelgues) */
extern USBD_HandleTypeDef hUsbDeviceFS;
bool is_usb_ready(void) {
    USBD_CDC_HandleTypeDef *hcdc = (USBD_CDC_HandleTypeDef*)hUsbDeviceFS.pClassData;
    if (hcdc == NULL) return false;
    return (hcdc->TxState == 0); // 0 significa IDLE
}

/* Potenciómetro eliminado: Timebase fijo en 100͘s/muestra (PSC=71, ARR=99) */
/* USER CODE END 0 */


/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_ADC1_Init();
  MX_USB_DEVICE_Init();
  MX_ADC2_Init();
  MX_TIM3_Init();
  MX_TIM4_Init();
  /* USER CODE BEGIN 2 */
  
  // Calibración y arranque de ADCs en Modo Dual
  HAL_ADCEx_Calibration_Start(&hadc1);
  HAL_ADCEx_Calibration_Start(&hadc2);
  
  // ¡CRÍTICO! El ADC2 (Slave) debe encenderse explícitamente en el HAL de F1
  HAL_ADC_Start(&hadc2);
  
  // Botones de Comando Profesionales
  bool trigger_falling = false;
  bool normal_mode     = false;
  bool filter_averaging= false;
  bool is_paused       = false;
  bool single_shot_req = false;
  bool probe_x10_mode  = false;
  bool trigger_armed   = false;
  
  uint32_t last_btn_time = 0;
  uint16_t btn_last_state = 0xFFFF;
  
  // 1. Iniciar Onda Cuadrada Fija Test a 1kHz en PB6
  HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_1); 
  
  // 2. Iniciar Timer Rítmico de captura
  HAL_TIM_Base_Start(&htim3);               
  
  // 3. Iniciar ADCs en modo DMA Dual Intercalado
  HAL_ADCEx_MultiModeStart_DMA(&hadc1, (uint32_t*)adc_buffer, ADC_BUF_SIZE);
  
  last_idx = ADC_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_adc1);
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    // Lectura de Estado Mantenido
    probe_x10_mode = (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_9) == GPIO_PIN_RESET);

    // ==========================================
    // LECTURA DE BOTONES (ANTI-REBOTE SOFTWARE)
    // ==========================================
    uint32_t current_time = HAL_GetTick();
    if (current_time - last_btn_time > 50) {
        uint16_t state = GPIOB->IDR;
        if ((state & GPIO_PIN_10) == 0 && (btn_last_state & GPIO_PIN_10) != 0) trigger_falling = !trigger_falling;
        if ((state & GPIO_PIN_11) == 0 && (btn_last_state & GPIO_PIN_11) != 0) normal_mode = !normal_mode;
        
        // BOTÓN PB12: AUTO-SET (Ajuste automático de Trigger)
        if ((state & GPIO_PIN_12) == 0 && (btn_last_state & GPIO_PIN_12) != 0) {
            uint16_t min_v = 4095, max_v = 0;
            for (int i = 0; i < CHUNK_SIZE/2; i++) {
                uint16_t v = (uint16_t)(adc_buffer[i] & 0xFFF);
                if (v < min_v) min_v = v;
                if (v > max_v) max_v = v;
            }
            trigger_level = (min_v + max_v) / 2;
            
            // Avisar a la PC el nuevo nivel (L<valor>\n)
            if (is_usb_ready()) {
                char msg[16];
                int len = sprintf(msg, "L%d\n", trigger_level);
                CDC_Transmit_FS((uint8_t*)msg, len);
            }
        }

        if ((state & GPIO_PIN_14) == 0 && (btn_last_state & GPIO_PIN_14) != 0) is_paused = !is_paused;
        if ((state & GPIO_PIN_15) == 0 && (btn_last_state & GPIO_PIN_15) != 0) {
             if (is_paused) { single_shot_req = true; is_paused = false; }
        }
        btn_last_state = state;
        last_btn_time = current_time;
    }
    
    if (is_paused && !single_shot_req) {
        HAL_Delay(10);
        continue;
    }

    uint32_t current_idx = ADC_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_adc1);
    int triggered = 0;
    uint32_t trigger_idx = 0;
    int hysteresis = 150;

    // Buscar el cruce lógico (Trigger)
    while (last_idx != current_idx && !triggered) {
        uint32_t dual_val = adc_buffer[last_idx];
        uint16_t val = (uint16_t)(dual_val & 0xFFF); // Muestra del ADC1
        
        if (!trigger_falling) {
            if (val < (trigger_level - hysteresis)) trigger_armed = true;
            if (trigger_armed && val >= (trigger_level + hysteresis)) {
                triggered = 1;
                trigger_idx = last_idx;
                trigger_armed = false;
            }
        } else {
            if (val > (trigger_level + hysteresis)) trigger_armed = true;
            if (trigger_armed && val <= (trigger_level - hysteresis)) {
                triggered = 1;
                trigger_idx = last_idx;
                trigger_armed = false;
            }
        }
        last_val = val;
        last_idx = (last_idx + 1) % ADC_BUF_SIZE;
    }
    
    static uint32_t last_trigger_time = 0;
    if (!triggered && !normal_mode && (HAL_GetTick() - last_trigger_time > 50)) {
        triggered = 1;
        trigger_idx = current_idx;
    }
    
    if (triggered) {
        last_trigger_time = HAL_GetTick();
        
        // Esperar a que el buffer se llene (necesitamos CHUNK_SIZE muestras totales)
        // Como cada entrada de 32 bits tiene 2 muestras, necesitamos CHUNK_SIZE/2 entradas de buffer
        uint32_t required_buffer_samples = CHUNK_SIZE / 2;
        while (1) {
            uint32_t wait_idx = ADC_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_adc1);
            int distance = (wait_idx >= trigger_idx) ? (wait_idx - trigger_idx) : (ADC_BUF_SIZE - trigger_idx + wait_idx);
            if (distance >= required_buffer_samples) break;
        }
        
        int ac_mode = (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_13) == GPIO_PIN_RESET);
        uint32_t tmp_ptr = trigger_idx;
        int bin_idx = 0;

        // Encabezado
        tx_buffer[bin_idx++] = 0xAA;
        tx_buffer[bin_idx++] = 0xBB;
        tx_buffer[bin_idx++] = 0xCC;
        tx_buffer[bin_idx++] = 0xDD;

        for (int i = 0; i < required_buffer_samples; i++) {
            uint32_t dual_val = adc_buffer[tmp_ptr];
            
            // Extraer las dos muestras (ADC1 y ADC2)
            uint16_t samples[2];
            samples[0] = (uint16_t)(dual_val & 0xFFF);
            samples[1] = (uint16_t)((dual_val >> 16) & 0xFFF);

            for (int s = 0; s < 2; s++) {
                int32_t adc_val = samples[s];
                int32_t pin_mv = (adc_val * 3300) / 4095;
                int16_t real_mv;
                
                // Reconstrucción Analógica AFE (Divisor x2 e inyección de offset 1.65V)
                if (ac_mode) {
                    real_mv = (int16_t)((pin_mv - 1650) * 2);
                } else {
                    real_mv = (int16_t)((pin_mv * 2) - 1650);
                }
                
                if (probe_x10_mode) real_mv *= 10;

                tx_buffer[bin_idx++] = (uint8_t)(real_mv & 0xFF);
                tx_buffer[bin_idx++] = (uint8_t)((real_mv >> 8) & 0xFF);
            }
            
            tmp_ptr = (tmp_ptr + 1) % ADC_BUF_SIZE;
        }
        
        if (is_usb_ready()) {
            CDC_Transmit_FS((uint8_t*)tx_buffer, bin_idx);
        }
        
        if (single_shot_req) {
            single_shot_req = false;
            is_paused = true;
        }
        
        HAL_Delay(1); 
        last_idx = (ADC_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_adc1)) % ADC_BUF_SIZE;
    }
    /* USER CODE END WHILE */


    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};
  RCC_PeriphCLKInitTypeDef PeriphClkInit = {0};

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL9;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
  PeriphClkInit.PeriphClockSelection = RCC_PERIPHCLK_ADC|RCC_PERIPHCLK_USB;
  PeriphClkInit.AdcClockSelection = RCC_ADCPCLK2_DIV6;
  PeriphClkInit.UsbClockSelection = RCC_USBCLKSOURCE_PLL_DIV1_5;
  if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInit) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief ADC1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC1_Init(void)
{

  /* USER CODE BEGIN ADC1_Init 0 */

  /* USER CODE END ADC1_Init 0 */

  ADC_MultiModeTypeDef multimode = {0};
  ADC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN ADC1_Init 1 */

  /* USER CODE END ADC1_Init 1 */

  /** Common config
  */
  hadc1.Instance = ADC1;
  hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.ExternalTrigConv = ADC_EXTERNALTRIGCONV_T3_TRGO;
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc1.Init.NbrOfConversion = 1;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure the ADC multi-mode
  */
  multimode.Mode = ADC_DUALMODE_INTERLFAST;
  if (HAL_ADCEx_MultiModeConfigChannel(&hadc1, &multimode) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Regular Channel
  */
  sConfig.Channel = ADC_CHANNEL_0;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
  if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC1_Init 2 */

  /* USER CODE END ADC1_Init 2 */

}

/**
  * @brief ADC2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC2_Init(void)
{

  /* USER CODE BEGIN ADC2_Init 0 */

  /* USER CODE END ADC2_Init 0 */

  ADC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN ADC2_Init 1 */

  /* USER CODE END ADC2_Init 1 */

  /** Common config
  */
  hadc2.Instance = ADC2;
  hadc2.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc2.Init.ContinuousConvMode = DISABLE;
  hadc2.Init.DiscontinuousConvMode = DISABLE;
  hadc2.Init.ExternalTrigConv = ADC_SOFTWARE_START;
  hadc2.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc2.Init.NbrOfConversion = 1;
  if (HAL_ADC_Init(&hadc2) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Regular Channel
  */
  sConfig.Channel = ADC_CHANNEL_0;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
  if (HAL_ADC_ConfigChannel(&hadc2, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC2_Init 2 */

  /* USER CODE END ADC2_Init 2 */

}

/**
  * @brief TIM3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM3_Init(void)
{

  /* USER CODE BEGIN TIM3_Init 0 */

  /* USER CODE END TIM3_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};

  /* USER CODE BEGIN TIM3_Init 1 */

  /* USER CODE END TIM3_Init 1 */
  htim3.Instance = TIM3;
  htim3.Init.Prescaler = 0;
  htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim3.Init.Period = 83;
  htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim3) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim3, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim3, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM3_Init 2 */

  /* USER CODE END TIM3_Init 2 */

}

/**
  * @brief TIM4 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM4_Init(void)
{

  /* USER CODE BEGIN TIM4_Init 0 */

  /* USER CODE END TIM4_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};

  /* USER CODE BEGIN TIM4_Init 1 */

  /* USER CODE END TIM4_Init 1 */
  htim4.Instance = TIM4;
  htim4.Init.Prescaler = 71;
  htim4.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim4.Init.Period = 999;
  htim4.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim4.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim4) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim4, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim4) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim4, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 500;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim4, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM4_Init 2 */

  /* USER CODE END TIM4_Init 2 */
  HAL_TIM_MspPostInit(&htim4);

}

/**
  * Enable DMA controller clock
  */
static void MX_DMA_Init(void)
{

  /* DMA controller clock enable */
  __HAL_RCC_DMA1_CLK_ENABLE();

  /* DMA interrupt init */
  /* DMA1_Channel1_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA1_Channel1_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA1_Channel1_IRQn);

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pins : PB10 PB11 PB12 PB13
                           PB14 PB15 */
  GPIO_InitStruct.Pin = GPIO_PIN_9|GPIO_PIN_10|GPIO_PIN_11|GPIO_PIN_12|GPIO_PIN_13
                          |GPIO_PIN_14|GPIO_PIN_15;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
void Update_Trigger_Level(uint16_t level) {
    if (level > 4095) level = 4095;
    trigger_level = level;
}

/**
  * @brief Cambia la velocidad de muestreo del hardware dinámicamente.
  * @param speed_idx: 0=Turbo(1.7MSPS), 1=Fast(171kSPS), 2=Mid(17kSPS), 3=Slow(1.7kSPS)
  */
void Update_Sampling_Rate(uint8_t speed_idx) {
    // Detener timer para evitar glitches
    HAL_TIM_Base_Stop(&htim3);
    
    switch(speed_idx) {
        case 0: // 1.714 MSPS (Uniforme)
            TIM3->PSC = 0;
            TIM3->ARR = 83;
            break;
        case 1: // 171.4 kSPS
            TIM3->PSC = 0;
            TIM3->ARR = 839;
            break;
        case 2: // 17.14 kSPS
            TIM3->PSC = 49;
            TIM3->ARR = 83;
            break;
        case 3: // 1.714 kSPS
            TIM3->PSC = 499;
            TIM3->ARR = 83;
            break;
        default:
            TIM3->PSC = 0;
            TIM3->ARR = 83;
            break;
    }
    
    // Resetear contador y arrancar
    TIM3->CNT = 0;
    HAL_TIM_Base_Start(&htim3);
}
/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
