# Test App (CLI)

Herramienta en Python para probar el firmware del dispenser por puerto serie.

## Requisitos

- Python 3.9+
- pyserial

Instalacion:

```bash
pip install pyserial
```

## Ejecutar

Desde la raiz del proyecto:

```bash
python3 testapp/dispenser_test_cli.py
```

## Funciones

- Menu interactivo para:
  - Listar puertos serie
  - Configurar puerto y baudrate
  - Conectar/desconectar
  - Configurar test por lotes
  - Mandar un `$D` directo
  - Enviar comandos manuales
- Test por lotes:
  - Envia `$D` una cantidad de veces
  - Espera respuesta final de cada intento (`ERR:*` o `TIMEOUT`) antes de enviar el siguiente
  - Espera un intervalo configurable entre envios (despues de la respuesta final)
  - Timeout configurable para esperar esa respuesta final
  - Si recibe `ERR:1` o `ERR:2`, pregunta si deseas continuar con el lote
- Logging completo de comunicacion serie:
  - `TX` comandos enviados
  - `RX` respuestas recibidas
  - Archivos en `testapp/logs/`

## Compilar ejecutable para Windows (.exe)

Requiere **Python 3.9+** instalado en Windows y acceso a internet para bajar PyInstaller la primera vez.

1. Copiar la carpeta `testapp/` a una máquina Windows (o clonar el repositorio)
2. Hacer doble click en `testapp/build_windows.bat`
3. El ejecutable queda en `testapp/dist/dispenser_tester.exe`

El `.exe` es autónomo (un solo archivo), no requiere Python instalado para ejecutarse.
La carpeta `logs/` se crea automáticamente junto al `.exe` al primer uso.
