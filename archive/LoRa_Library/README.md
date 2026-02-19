# LoRa Library

Custom library for E22 LoRa module communication on ESP32.

> Note: This library is intentionally restricted to ESP platforms (ESP32 and ESP8266). It will produce a compile-time error on non-ESP targets.

## Files

- **LoRaModule.h** - Header file with class definition
- **LoRaModule.cpp** - Implementation of LoRa communication methods
- **lora_config.h** - Configuration constants for LoRa network settings

## Features
- AT command interface for E22 modules
- Send and receive hex data
- Automatic module configuration (address, band, network ID)
- Hardware serial communication (UART)

## Installation
Copy this folder to your PlatformIO `lib/` directory.
