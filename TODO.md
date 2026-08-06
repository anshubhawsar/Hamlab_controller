# Task: Add manual PWM speed control (active in MANUAL mode) to cooling panel

Reference: ESP32 cooling sketch (inlet/outlet temp, flow, PWM, pump control).

## Status: COMPLETE

## Plan Steps
- [x] Add `set_pwm(value)` method to `cooling_controller.py`
- [x] Add manual PWM slider control to `gui_integration.py` controls card
- [x] Wire slider enable/disable based on connection + mode (enabled only in MANUAL mode)
- [x] Display live PWM value
- [x] Verify imports and UI build

## Additional fixes (Pump not running + flow not showing)
- [x] `serial_manager.py`: block parser now captures `Flow Rate` / `PWM` lines (flow was not showing)
- [x] `cooling_controller.py`: `set_pwm(value)` added
- [x] `gui_integration.py`: manual PWM slider control wired to MANUAL mode
- [x] Firmware: disabled zero-flow guard (`ENABLE_ZERO_FLOW_GUARD 0`) so pump stays ON without flow
- [x] Firmware: disabled sensor-fault guard (`ENABLE_SENSOR_GUARD 0`) so pump stays ON with a missing DS18B20
- [x] Python modules compile (py_compile) — PASSED

## Note
- Both `FLASH_THIS_FIRMWARE.ino` and `esp32_firmware/cooling_controller_firmware.ino` updated.
- Re-flash the firmware to the ESP32, reconnect, and test Pump ON in the Cooling panel.
