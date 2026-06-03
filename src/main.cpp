#include <Arduino.h>
#include <EEPROM.h>
#include <string.h>
#include <stdlib.h>

#define MOTOR_PIN     3
#define MOTOR2_PIN    5
#define DEV_TRIGGER_OUT_PIN 4
#define DEV_PULSE_IN_PIN    2
#define IR_LED_PIN    6
#define IR_SENSOR   A0
#define LDR_LED_PIN   7
#define LDR_SENSOR   A1
#define THRESHOLD   100   // diferencia mínima ADC para considerar barrera libre
#define DISPENSE_TIMEOUT_MS_DEFAULT 10000
#define DISPENSE_TIMEOUT_MS_MIN 100
#define DISPENSE_TIMEOUT_MS_MAX 60000
#define LDR_STREAM_INTERVAL_MS 100
#define EEPROM_ADDR_DEV_MODE 0
#define EEPROM_ADDR_DEV_PULSE_MS 1
#define EEPROM_ADDR_TIMEOUT_MS 3
#define DEV_PULSE_MS_DEFAULT 10
#define DEV_PULSE_MS_MIN 1
#define DEV_PULSE_MS_MAX 1000

#define CMD_BUF_SIZE  32

uint8_t motorSpeed = 255;   // 0-255, configurable via $SVEL
uint8_t motor2Speed = 255;  // 0-255, configurable via $SVEL2
unsigned long motor1StopDelayMs = 1000;
unsigned long motor2StopDelayMs = 1500;

char cmdBuf[CMD_BUF_SIZE];
uint8_t cmdLen = 0;
bool ldrContinuousMode = false;
unsigned long lastLdrStreamMs = 0;
uint8_t devMode = 0;
unsigned int devPulseMs = DEV_PULSE_MS_DEFAULT;
unsigned long dispenseTimeoutMs = DISPENSE_TIMEOUT_MS_DEFAULT;

uint8_t sanitizeDevMode(uint8_t mode) {
  return mode <= 1 ? mode : 0;
}

unsigned int sanitizeDevPulseMs(unsigned int pulseMs) {
  if (pulseMs < DEV_PULSE_MS_MIN || pulseMs > DEV_PULSE_MS_MAX) {
    return DEV_PULSE_MS_DEFAULT;
  }
  return pulseMs;
}

unsigned long sanitizeDispenseTimeoutMs(unsigned long timeoutMs) {
  if (timeoutMs < DISPENSE_TIMEOUT_MS_MIN || timeoutMs > DISPENSE_TIMEOUT_MS_MAX) {
    return DISPENSE_TIMEOUT_MS_DEFAULT;
  }
  return timeoutMs;
}

void saveDevModeToEeprom(uint8_t mode) {
  EEPROM.update(EEPROM_ADDR_DEV_MODE, sanitizeDevMode(mode));
}

void saveDevPulseMsToEeprom(unsigned int pulseMs) {
  unsigned int sanitizedPulse = sanitizeDevPulseMs(pulseMs);
  EEPROM.put(EEPROM_ADDR_DEV_PULSE_MS, sanitizedPulse);
}

void saveDispenseTimeoutToEeprom(unsigned long timeoutMs) {
  unsigned long sanitizedTimeout = sanitizeDispenseTimeoutMs(timeoutMs);
  EEPROM.put(EEPROM_ADDR_TIMEOUT_MS, sanitizedTimeout);
}

void loadDevModeFromEeprom() {
  uint8_t storedMode = EEPROM.read(EEPROM_ADDR_DEV_MODE);
  devMode = sanitizeDevMode(storedMode);
  if (storedMode != devMode) {
    saveDevModeToEeprom(devMode);
  }
}

void loadDevPulseMsFromEeprom() {
  unsigned int storedPulseMs = 0;
  EEPROM.get(EEPROM_ADDR_DEV_PULSE_MS, storedPulseMs);
  devPulseMs = sanitizeDevPulseMs(storedPulseMs);
  if (storedPulseMs != devPulseMs) {
    saveDevPulseMsToEeprom(devPulseMs);
  }
}

void loadDispenseTimeoutFromEeprom() {
  unsigned long storedTimeoutMs = 0;
  EEPROM.get(EEPROM_ADDR_TIMEOUT_MS, storedTimeoutMs);
  dispenseTimeoutMs = sanitizeDispenseTimeoutMs(storedTimeoutMs);
  if (storedTimeoutMs != dispenseTimeoutMs) {
    saveDispenseTimeoutToEeprom(dispenseTimeoutMs);
  }
}

// Modula el LED IR y compara lecturas con/sin luz.
// Retorna true si la barrera está bloqueada (tarjeta presente).
bool isBeamBlocked() {
  digitalWrite(IR_LED_PIN, HIGH);
  delay(1);
  int readOn = analogRead(IR_SENSOR);   // V bajo = luz encendida
  digitalWrite(IR_LED_PIN, LOW);
  delay(1);
  int readOff = analogRead(IR_SENSOR);  // V alto = luz apagada

  // Serial.print("ON="); Serial.print(readOn);
  // Serial.print(" OFF="); Serial.prinAt(readOff);
  // Serial.print(" DIFF="); Serial.println(readOff - readOn);

  // Barrera libre:  readOff >> readOn  → diferencia > THRESHOLD
  // Barrera cortada: readOff ≈ readOn  → diferencia < THRESHOLD
  int diff = readOff - readOn;  
  if(diff < THRESHOLD) {
    digitalWrite(LED_BUILTIN, HIGH); // DEBUG: enciende LED si barrera bloqueada
  } else {
    digitalWrite(LED_BUILTIN, LOW);  // DEBUG: apaga LED si barrera libre
  }
  return diff < THRESHOLD;
}

int readLdrWithLed() {
  digitalWrite(LDR_LED_PIN, LOW);
  delay(10);
  int adcValue = analogRead(LDR_SENSOR);
  digitalWrite(LDR_LED_PIN, HIGH);
  return adcValue;
}

int readLdrRaw() {
  return analogRead(LDR_SENSOR);
}

bool waitForBeamState(bool targetBlocked, unsigned long timeoutMs) {
  unsigned long startTime = millis();
  while (millis() - startTime < timeoutMs) {
    if (isBeamBlocked() == targetBlocked) {
      return true;
    }
  }
  return false;
}

bool waitForPulseOnDevInput(unsigned long timeoutMs) {
  unsigned long startTime = millis();
  int prevState = digitalRead(DEV_PULSE_IN_PIN);

  while (millis() - startTime < timeoutMs) {
    int currentState = digitalRead(DEV_PULSE_IN_PIN);
    if (prevState == LOW && currentState == HIGH) {
      return true;
    }
    prevState = currentState;
  }

  return false;
}

void dispense() {
  // STATE 0 - PRECHECK: no iniciar si la barrera ya está tapada.
  if (isBeamBlocked()) {
    Serial.println("ERR:2");
    return;
  }

  // STATE 1 - START: arranca ambos motores y confirma recepción del comando.
  analogWrite(MOTOR_PIN, motorSpeed);
  analogWrite(MOTOR2_PIN, motor2Speed);
  Serial.println("OK");

  // STATE 2 - CLASSIFY: mide LDR con LED auxiliar para clasificar tarjeta.
  int ldrValue = readLdrWithLed();
 // Serial.println(ldrValue); // DEBUG: muestra valor LDR para diagnóstico
  if (ldrValue < 800) {
    Serial.println("GOLD");
  }

  // STATE 3 - WAIT_CLEAR: espera barrera libre para confirmar movimiento.
  bool beamClear = waitForBeamState(false, dispenseTimeoutMs);

  if (!beamClear) {
    analogWrite(MOTOR_PIN, 0);
    Serial.println("TIMEOUT");
    return;
  }

  // STATE 4 - WAIT_BLOCK: espera que la tarjeta vuelva a tapar la barrera.
  bool cardDetected = waitForBeamState(true, dispenseTimeoutMs);

  if (!cardDetected) {
    analogWrite(MOTOR_PIN, 0);
    analogWrite(MOTOR2_PIN, 0);
    Serial.println("ERR:1");
    return;
  }

  // STATE 5 - STOP_PICKER: mantiene un retardo y detiene el motor 2.
  if (motor2StopDelayMs > 0) {
    delay(motor2StopDelayMs);
  }
  analogWrite(MOTOR2_PIN, 0);

  // STATE 6 - WAIT_CLEAR_END: espera salida completa para poder frenar motor 1.
  bool beamClearAgain = waitForBeamState(false, dispenseTimeoutMs);

  // STATE 7 - FINISH: detiene motor 1 y reporta resultado final.
  if (beamClearAgain && motor1StopDelayMs > 0) {
    delay(motor1StopDelayMs);
  }
  analogWrite(MOTOR_PIN, 0);
  Serial.println(beamClearAgain ? "ERR:0" : "ERR:1");
}

void dispenseAltDev1() {
  digitalWrite(DEV_TRIGGER_OUT_PIN, HIGH);
  delay(devPulseMs);
  digitalWrite(DEV_TRIGGER_OUT_PIN, LOW);

  bool pulseDetected = waitForPulseOnDevInput(dispenseTimeoutMs);
  Serial.println(pulseDetected ? "ERR:0" : "ERR:1");
}

void processCommand(const char *cmd) {
  if (strcmp(cmd, "$D") == 0) {
    if (devMode == 0) {
      dispense();
    } else {
      dispenseAltDev1();
    }
  } else if (strcmp(cmd, "$RM") == 0) {
    analogWrite(MOTOR_PIN, motorSpeed);
    Serial.println("MOTOR:ON");
  } else if (strcmp(cmd, "$SM") == 0) {
    analogWrite(MOTOR_PIN, 0);
    Serial.println("MOTOR:OFF");
  } else if (strncmp(cmd, "$SVEL ", 6) == 0) {
    int val = atoi(cmd + 6);
    if (val >= 0 && val <= 255) {
      motorSpeed = (uint8_t)val;
      Serial.print("SPEED=");
      Serial.println(motorSpeed);
    } else {
      Serial.println("ERR:RANGE");
    }
  } else if (strncmp(cmd, "$SVEL2 ", 7) == 0) {
    int val = atoi(cmd + 7);
    if (val >= 0 && val <= 255) {
      motor2Speed = (uint8_t)val;
      Serial.print("SPEED2=");
      Serial.println(motor2Speed);
    } else {
      Serial.println("ERR:OUT-RANGE");
    }
  } else if (strncmp(cmd, "$DM1 ", 5) == 0) {
    long val = atol(cmd + 5);
    if (val >= 0) {
      motor1StopDelayMs = (unsigned long)val;
      Serial.print("DM1=");
      Serial.println(motor1StopDelayMs);
    } else {
      Serial.println("ERR:RANGE");
    }
  } else if (strncmp(cmd, "$DM2 ", 5) == 0) {
    long val = atol(cmd + 5);
    if (val >= 0) {
      motor2StopDelayMs = (unsigned long)val;
      Serial.print("DM2=");
      Serial.println(motor2StopDelayMs);
    } else {
      Serial.println("ERR:RANGE");
    }
  } else if (strcmp(cmd, "$RP") == 0) {
    analogWrite(MOTOR2_PIN, motor2Speed);
    Serial.println("MOTOR2:ON");
  } else if (strcmp(cmd, "$SP") == 0) {
    analogWrite(MOTOR2_PIN, 0);
    Serial.println("MOTOR2:OFF");
  } else if (strcmp(cmd, "$LDR") == 0) {
    int adcValue = readLdrWithLed();
    Serial.print("LDR=");
    Serial.println(adcValue);
  } else if (strcmp(cmd, "$LDRC") == 0) {
    ldrContinuousMode = true;
    lastLdrStreamMs = 0;
    digitalWrite(LDR_LED_PIN, LOW);
    Serial.println("LDRC:ON");
  } else if (strcmp(cmd, "$LDRS") == 0) {
    ldrContinuousMode = false;
    digitalWrite(LDR_LED_PIN, HIGH);
    Serial.println("LDRC:OFF");
  } else if (strncmp(cmd, "$DEV ", 5) == 0) {
    int val = atoi(cmd + 5);
    if (val == 0 || val == 1) {
      devMode = (uint8_t)val;
      saveDevModeToEeprom(devMode);
      Serial.print("DEV=");
      Serial.println(devMode);
    } else {
      Serial.println("ERR:RANGE");
    }
  } else if (strncmp(cmd, "$D2P ", 5) == 0) {
    long val = atol(cmd + 5);
    if (val >= DEV_PULSE_MS_MIN && val <= DEV_PULSE_MS_MAX) {
      devPulseMs = (unsigned int)val;
      saveDevPulseMsToEeprom(devPulseMs);
      Serial.print("D2P=");
      Serial.println(devPulseMs);
    } else {
      Serial.println("ERR:RANGE");
    }
  } else if (strcmp(cmd, "$TOUT") == 0) {
    Serial.print("TOUT=");
    Serial.println(dispenseTimeoutMs);
  } else if (strncmp(cmd, "$TOUT ", 6) == 0) {
    long val = atol(cmd + 6);
    if (val >= DISPENSE_TIMEOUT_MS_MIN && val <= DISPENSE_TIMEOUT_MS_MAX) {
      dispenseTimeoutMs = (unsigned long)val;
      saveDispenseTimeoutToEeprom(dispenseTimeoutMs);
      Serial.print("TOUT=");
      Serial.println(dispenseTimeoutMs);
    } else {
      Serial.println("ERR:RANGE");
    }
  } else {
    Serial.println("ERR:BAD-CMD");
  }
}

void setup() {
  Serial.begin(115200);
  loadDevModeFromEeprom();
  loadDevPulseMsFromEeprom();
  loadDispenseTimeoutFromEeprom();

  pinMode(MOTOR_PIN, OUTPUT);
  digitalWrite(MOTOR_PIN, LOW);
  pinMode(MOTOR2_PIN, OUTPUT);
  digitalWrite(MOTOR2_PIN, LOW);
  pinMode(DEV_TRIGGER_OUT_PIN, OUTPUT);
  digitalWrite(DEV_TRIGGER_OUT_PIN, LOW);
  pinMode(DEV_PULSE_IN_PIN, INPUT);
  pinMode(IR_LED_PIN, OUTPUT);
  digitalWrite(IR_LED_PIN, LOW);
  pinMode(LDR_LED_PIN, OUTPUT);
  digitalWrite(LDR_LED_PIN, HIGH);
  pinMode(LED_BUILTIN, OUTPUT); // DEBUG: LED integrado para indicar estado de la barrera
}

void loop() {
  if (ldrContinuousMode) {
    unsigned long now = millis();
    if (now - lastLdrStreamMs >= LDR_STREAM_INTERVAL_MS) {
      lastLdrStreamMs = now;
      int adcValue = readLdrRaw();
      Serial.print("LDR=");
      Serial.println(adcValue);
    }
  }

  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen > 0) {
        cmdBuf[cmdLen] = '\0';
        processCommand(cmdBuf);
        cmdLen = 0;
      }
    } else {
      if (cmdLen < CMD_BUF_SIZE - 1) {
        cmdBuf[cmdLen++] = c;
      }
    }
  }
}