/*
 * ESP32 Cooling System Firmware
 * =============================
 * For HAM Lab Cooling Controller
 *
 * Hardware
 * --------
 *  - ESP32
 *  - DallasTemperature DS18B20 (2x) on ONE_WIRE_BUS
 *      inlet  = sensor index 0
 *      outlet = sensor index 1
 *  - L298N motor driver for the pump (variable speed via PWM)
 *      ENA -> PWM_PIN (PWM output, LEDC)
 *      IN1 -> IN1_PIN
 *      IN2 -> IN2_PIN
 *      (remove the ENA jumper, DO NOT use +5V from the driver to the ESP32)
 *  - YF-S201 flow sensor
 *      Red -> 5V, Black -> GND, Yellow -> FLOW_PIN (interrupt input)
 *  - Active buzzer on BUZZER_PIN
 *
 * Serial protocol (every ~1 s)
 * ----------------------------
 *   TEMP,<inlet>,<outlet>,FLOW,<flowLmin>,PWM,<pwmValue>,PUMP,<ON/OFF>
 * Example:
 *   TEMP,27.4,36.8,FLOW,3.12,PWM,182,PUMP,ON
 *
 * Manual commands (newline terminated)
 * ------------------------------------
 *   ON                       -> pump ON at current auto/manual PWM (manual mode)
 *   OFF                      -> pump OFF (manual mode)
 *   AUTO                     -> enable automatic outlet-temperature control
 *   STATUS                   -> print a human readable status block
 *   PWM=<value>              -> set manual pump speed, value 0..255
 *   FLOW?                    -> print current flow rate
 *   TEMP?                    -> print current inlet/outlet temperatures
 *   CALIBRATE                -> run the 60 s flow-sensor calibration routine
 *
 * Calibration
 * -----------
 *  - Run pump at a fixed PWM for 60 seconds.
 *  - Collect the discharged water and measure its volume (litres).
 *  - The new calibration factor is computed as:
 *        calibrationFactor = totalPulses / measuredVolumeLiters
 *  - Persisted in Preferences (NVS) so it survives power loss.
 *
 * The calibration factor is exposed as a top-of-file variable so it can be
 * easily tweaked manually if desired.
 */

#include <Preferences.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ---------------------------------------------------------------------------
// Easy-to-modify constants
// ---------------------------------------------------------------------------
// Pins
#define ONE_WIRE_BUS 4
#define FLOW_PIN     34
#define PWM_PIN      25   // L298N ENA
#define IN1_PIN      26   // L298N IN1
#define IN2_PIN      27   // L298N IN2
#define BUZZER_PIN   18

// Fixed manual PWM used during a calibration run (0..255)
#define CALIBRATION_PWM 180
// Duration of the calibration run (seconds)
#define CALIBRATION_SECONDS 60

// Safety thresholds
#define HIGH_TEMP_ALARM_C   45.0f   // outlet above this -> pump 100% + buzzer
#define ZERO_FLOW_PWM_LIMIT 51      // approx 20% of 255 -> no-flow safety check
#define FLOW_ZERO_THRESHOLD 0.05f   // L/min below this is treated as "zero flow"

// Set to 0 to DISABLE the zero-flow safety interlock (pump stays on even with
// no flow pulses). Useful for bench testing without water flowing. Set back to
// 1 for production so the pump is protected against dry-running.
#define ENABLE_ZERO_FLOW_GUARD 0

// Set to 0 to DISABLE the DS18B20 sensor-fault pump shutdown (pump keeps
// running even if a temperature sensor isn't connected / reads -127 C).
// Useful for bench testing with just one sensor. Set back to 1 for production.
#define ENABLE_SENSOR_GUARD 0

// Automatic pump speed breakpoints (outlet temperature -> desired PWM %)
// T <= LOW_1                  -> 30%
// LOW_1 < T <= LOW_2          -> 40%
// LOW_2 < T <= MID_1          -> 55%
// MID_1 < T <= MID_2          -> 70%
// MID_2 < T <= HIGH_1         -> 85%
// T > HIGH_1                  -> 100%
#define OUTLET_T_LOW_1   30.0f
#define OUTLET_T_LOW_2   36.0f
#define OUTLET_T_MID_1   39.0f
#define OUTLET_T_MID_2   44.0f
#define OUTLET_T_HIGH_1  50.0f

// Sensor error debounce (consecutive bad reads before declaring a fault)
#define SENSOR_ERROR_CONFIRMATIONS 3

// PWM resolution (8-bit -> 0..255)
#define PWM_RESOLUTION_BITS 8
#define PWM_FREQUENCY_HZ    500   // MUST match the working reference (500 Hz).
                                 // Many DC water pumps won't spin at 20 kHz.

// ---------------------------------------------------------------------------
// Flow calibration factor (pulses per litre). Tune with CALIBRATE or edit here.
// ---------------------------------------------------------------------------
float calibrationFactor = 7.5f;

// NVS namespace + key for the persisted calibration factor
static const char* NVS_NAMESPACE = "cooling";
static const char* NVS_CAL_KEY = "cal_factor";

// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------
OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

Preferences prefs;

volatile unsigned long flowPulseCount = 0;   // incremented by the ISR
volatile unsigned long lastFlowPulseUs = 0;  // for frequency estimation

bool autoMode = true;
bool pumpState = false;      // is the pump commanded ON
bool buzzerState = false;
bool calibrating = false;    // calibration routine active

int pumpPwm = 0;             // current commanded PWM (0..255)
int targetPwm = 0;           // desired PWM (used for smooth ramping)

int sensorErrorCount = 0;

unsigned long lastSerialSendMs = 0;
const unsigned long SERIAL_INTERVAL_MS = 1000;

unsigned long lastFlowUpdateMs = 0;
const unsigned long FLOW_UPDATE_INTERVAL_MS = 1000;

// Rolling instantaneous flow rate (L/min)
float flowRate = 0.0f;

float inletTemp = 0.0f;
float outletTemp = 0.0f;

String lastError = "";       // current active error message (empty when none)

// ---------------------------------------------------------------------------
// Flow sensor ISR
// ---------------------------------------------------------------------------
void IRAM_ATTR onFlowPulse()
{
    flowPulseCount++;
    lastFlowPulseUs = micros();
}

// ---------------------------------------------------------------------------
// Buzzer
// ---------------------------------------------------------------------------
void buzzerON()
{
    digitalWrite(BUZZER_PIN, HIGH);
    buzzerState = true;
}

void buzzerOFF()
{
    digitalWrite(BUZZER_PIN, LOW);
    buzzerState = false;
}

// ---------------------------------------------------------------------------
// Pump control (L298N)
// ---------------------------------------------------------------------------
void setPumpPwm(int value)
{
    if (value < 0) value = 0;
    if (value > 255) value = 255;
    pumpPwm = value;
    ledcWrite(PWM_PIN, (uint32_t)pumpPwm);
}

void pumpON(int pwm = -1)
{
    // Direction: forward (IN1 HIGH, IN2 LOW)
    digitalWrite(IN1_PIN, HIGH);
    digitalWrite(IN2_PIN, LOW);
    if (pwm >= 0)
        setPumpPwm(pwm);
    pumpState = true;
}

void pumpOFF()
{
    ledcWrite(PWM_PIN, 0);
    digitalWrite(IN1_PIN, LOW);
    digitalWrite(IN2_PIN, LOW);
    pumpState = false;
    pumpPwm = 0;
    targetPwm = 0;
}

// ---------------------------------------------------------------------------
// Automatic pump speed selection based on outlet temperature (% -> PWM)
// ---------------------------------------------------------------------------
int autoSpeeds[] = { 30, 40, 55, 70, 85, 100 }; // percentages
const int AUTO_SPEED_COUNT = 6;

int autoTargetPwmForTemp(float t)
{
    float pct = 30.0f;
    if (t <= OUTLET_T_LOW_1)          pct = 30.0f;
    else if (t <= OUTLET_T_LOW_2)     pct = 40.0f;
    else if (t <= OUTLET_T_MID_1)     pct = 55.0f;
    else if (t <= OUTLET_T_MID_2)     pct = 70.0f;
    else if (t <= OUTLET_T_HIGH_1)    pct = 85.0f;
    else                              pct = 100.0f;
    return (int)(pct / 100.0f * 255.0f);
}

// Smoothly ramp the current PWM toward the target (no sudden jumps).
void rampPwm()
{
    if (pumpPwm == targetPwm) return;
    // Step of 2 per loop iteration gives a fast-but-smooth transition.
    int step = 2;
    if (targetPwm > pumpPwm)
    {
        pumpPwm += step;
        if (pumpPwm > targetPwm) pumpPwm = targetPwm;
    }
else
    {
        pumpPwm -= step;
        if (pumpPwm < targetPwm) pumpPwm = targetPwm;
    }
    ledcWrite(PWM_PIN, (uint32_t)pumpPwm);
}

// ===========================================================================
// Flow rate calculation
// ===========================================================================
// Flow rate (L/min) = pulse frequency / calibrationFactor
//   instantaneous frequency (Hz) = pulses per second
//   -> L/min = (pulsesPerSecond * 60) / (pulsesPerLitre * 60)
//   Simpler: L/min = pulsesPerSecond / calibrationFactor
//   (because calibrationFactor has units pulses per litre, and 1 pulse/s
//    corresponds to 60 pulses/min -> L/min = freq / factor)
void updateFlowRate()
{
    unsigned long now = micros();
    if (now - lastFlowPulseUs < 1000000UL) // we saw a pulse within the last second
    {
        // Estimate instantaneous frequency from the last two pulses.
        static unsigned long lastUs = 0;
        if (lastUs != 0 && lastFlowPulseUs > lastUs)
        {
            unsigned long deltaUs = lastFlowPulseUs - lastUs;
            if (deltaUs > 0)
            {
                float freqHz = 1000000.0f / (float)deltaUs;
                flowRate = freqHz / calibrationFactor;
            }
        }
        lastUs = lastFlowPulseUs;
    }
    else
    {
        flowRate = 0.0f;
    }
}

// ===========================================================================
// Safety checks
// ===========================================================================
void runSafetyChecks()
{
// 1) DS18B20 disconnected (only when the guard is enabled)
#if ENABLE_SENSOR_GUARD
    if (inletTemp == DEVICE_DISCONNECTED_C || outletTemp == DEVICE_DISCONNECTED_C)
    {
        sensorErrorCount++;
        if (sensorErrorCount >= SENSOR_ERROR_CONFIRMATIONS)
        {
            lastError = "ERROR : SENSOR NOT FOUND";
            pumpOFF();
            buzzerON();
            Serial.println(lastError);
        }
        return;
    }
    else
    {
        sensorErrorCount = 0;
    }
#endif

// 2) Zero flow while pump PWM > 20% (only when the guard is enabled)
#if ENABLE_ZERO_FLOW_GUARD
    if (pumpState && pumpPwm > ZERO_FLOW_PWM_LIMIT && flowRate < FLOW_ZERO_THRESHOLD)
    {
        lastError = "ERROR : NO WATER FLOW";
        pumpOFF();
        buzzerON();
        Serial.println(lastError);
        return;
    }
#endif

    // 3) Outlet temperature exceeds 45 C -> pump 100% + buzzer
    if (outletTemp > HIGH_TEMP_ALARM_C)
    {
        lastError = "HIGH TEMPERATURE WARNING";
        if (pumpState)
        {
            targetPwm = 255;       // force 100%
            setPumpPwm(255);
        }
        buzzerON();
        Serial.println(lastError);
        return;
    }

    // No fault -> clear
    if (lastError.length() > 0)
    {
        lastError = "";
        // Only clear the buzzer if the temperature is no longer critical.
        if (outletTemp <= (HIGH_TEMP_ALARM_C - 2.0f))
            buzzerOFF();
    }
}

// ===========================================================================
// Automatic pump control
// ===========================================================================
void autoControl()
{
    if (!autoMode || !pumpState) return;

    // High-temperature override handled in safety checks.
    targetPwm = autoTargetPwmForTemp(outletTemp);
    rampPwm();
}

// ===========================================================================
// Calibration routine
// ===========================================================================
void startCalibration()
{
    if (calibrating) return;
    calibrating = true;
    lastError = "";

    Serial.println("CALIBRATION : Collect water for 60 seconds.");
    Serial.println("CALIBRATION : Pump running at fixed PWM=" + String(CALIBRATION_PWM));

    // Reset counters and run the pump at a fixed speed.
    flowPulseCount = 0;
    pumpON(CALIBRATION_PWM);
    buzzerOFF();
}

void finishCalibration()
{
    calibrating = false;
    pumpOFF();

    // Measured volume is entered over serial at the end (litres).
    Serial.println("CALIBRATION : Enter measured volume in litres (e.g. 1.5):");
    // Blocking read is acceptable here because it is a user-driven one-off.
    String line = Serial.readStringUntil('\n');
    line.trim();
    float volumeLitres = line.toFloat();
    if (volumeLitres <= 0.0f)
    {
        Serial.println("CALIBRATION : Aborted (invalid volume).");
        return;
    }

    if (flowPulseCount > 0)
    {
        calibrationFactor = (float)flowPulseCount / volumeLitres;
        prefs.putFloat(NVS_CAL_KEY, calibrationFactor);
        Serial.println("CALIBRATION : New calibration factor = " + String(calibrationFactor, 3));
        Serial.println("CALIBRATION : Saved to NVS (persists across power loss).");
    }
    else
    {
        Serial.println("CALIBRATION : No pulses detected - check flow sensor.");
    }
}

// ===========================================================================
// Serial command handling
// ===========================================================================
void handleSerial()
{
    if (!Serial.available()) return;

    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();

    // Non-blocking calibration flow: if we are mid-calibration, poll for the
    // completion trigger instead. We handle the timing in loop().
    if (cmd == "ON")
    {
        autoMode = false;
        if (targetPwm <= 0) targetPwm = 255; // default to full speed in manual
        pumpON(targetPwm);
        buzzerOFF();
    }
    else if (cmd == "OFF")
    {
        autoMode = false;
        pumpOFF();
    }
    else if (cmd == "AUTO")
    {
        autoMode = true;
        if (!pumpState) pumpON();
    }
    else if (cmd.startsWith("PWM="))
    {
        autoMode = false;
        int value = cmd.substring(4).toInt();
        if (value >= 0 && value <= 255)
        {
            targetPwm = value;
            if (pumpState) setPumpPwm(value);
        }
        else
        {
            Serial.println("ERROR : PWM must be 0..255");
        }
    }
    else if (cmd == "FLOW?")
    {
        Serial.println("FLOW=" + String(flowRate, 2) + " L/min");
    }
    else if (cmd == "TEMP?")
    {
        Serial.println("INLET=" + String(inletTemp, 2));
        Serial.println("OUTLET=" + String(outletTemp, 2));
    }
    else if (cmd == "STATUS")
    {
        Serial.println("--------------------------");
        Serial.println("Status:");
        Serial.print("  Inlet  : "); Serial.print(inletTemp); Serial.println(" C");
        Serial.print("  Outlet : "); Serial.print(outletTemp); Serial.println(" C");
        Serial.print("  Flow   : "); Serial.print(flowRate); Serial.println(" L/min");
        Serial.print("  PWM    : "); Serial.println(pumpPwm);
        Serial.print("  Pump   : "); Serial.println(pumpState ? "ON" : "OFF");
        Serial.print("  Mode   : "); Serial.println(autoMode ? "AUTO" : "MANUAL");
        Serial.print("  Buzzer : "); Serial.println(buzzerState ? "ON" : "OFF");
        Serial.print("  CalFac : "); Serial.println(calibrationFactor, 3);
        Serial.println("--------------------------");
    }
    else if (cmd == "CALIBRATE")
    {
        startCalibration();
    }
}

// ===========================================================================
// Periodic serial output (Tkinter parsing)
// ===========================================================================
void sendSerialData()
{
    Serial.print("TEMP,");
    Serial.print(inletTemp, 2);
    Serial.print(",");
    Serial.print(outletTemp, 2);
    Serial.print(",FLOW,");
    Serial.print(flowRate, 2);
    Serial.print(",PWM,");
    Serial.print(pumpPwm);
    Serial.print(",PUMP,");
    Serial.println(pumpState ? "ON" : "OFF");
}

// ===========================================================================
// Setup
// ===========================================================================
void setup()
{
    Serial.begin(115200);

    pinMode(IN1_PIN, OUTPUT);
    pinMode(IN2_PIN, OUTPUT);
    pinMode(BUZZER_PIN, OUTPUT);
    pinMode(FLOW_PIN, INPUT_PULLUP);

// PWM output on ENA using the Arduino Core 3.x LEDC API.
    ledcAttach(PWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
    ledcWrite(PWM_PIN, 0);

    // Flow sensor interrupt (rising edge).
    attachInterrupt(digitalPinToInterrupt(FLOW_PIN), onFlowPulse, RISING);

    buzzerOFF();
    pumpOFF();

    sensors.begin();

    // Load persisted calibration factor.
    prefs.begin(NVS_NAMESPACE, false);
    calibrationFactor = prefs.getFloat(NVS_CAL_KEY, calibrationFactor);

    Serial.println("=====================================");
    Serial.println("ESP32 Cooling System Started");
    Serial.println("=====================================");
    Serial.println("Commands:");
    Serial.println("ON | OFF | AUTO | STATUS | PWM=<0..255> | FLOW? | TEMP? | CALIBRATE");
    Serial.println("=====================================");
}

// ===========================================================================
// Main loop (non-blocking, millis()-driven)
// ===========================================================================
void loop()
{
    // 1) Read temperatures.
    sensors.requestTemperatures();
    inletTemp = sensors.getTempCByIndex(0);
    outletTemp = sensors.getTempCByIndex(1);

    // 2) Update flow rate once per second.
    unsigned long nowMs = millis();
    if (nowMs - lastFlowUpdateMs >= FLOW_UPDATE_INTERVAL_MS)
    {
        lastFlowUpdateMs = nowMs;
        updateFlowRate();
    }

    // 3) Safety checks (may force pump off / buzzer / 100%).
    runSafetyChecks();

    // 4) Automatic control (smooth PWM ramping).
    autoControl();

    // 5) Handle incoming commands.
    handleSerial();

    // 6) Calibration timing (non-blocking).
    if (calibrating)
    {
        static unsigned long calStartMs = 0;
        if (calStartMs == 0) calStartMs = millis();
        if (millis() - calStartMs >= (unsigned long)CALIBRATION_SECONDS * 1000UL)
        {
            calStartMs = 0;
            finishCalibration();
        }
    }

    // 7) Periodic serial output.
    if (millis() - lastSerialSendMs >= SERIAL_INTERVAL_MS)
    {
        lastSerialSendMs = millis();
        sendSerialData();
    }
}
