#ifndef LORA_MODULE_H
#define LORA_MODULE_H

// Library intentionally restricted to ESP platforms only
#if !defined(ESP32) && !defined(ESP8266)
#error "LoRa_Library supports only ESP32 and ESP8266"
#endif

#include <Arduino.h>
#include <HardwareSerial.h>

class LoRaModule {
public:
    // `serial` is required — pass a HardwareSerial reference (e.g. `Serial1`).
    LoRaModule(uint8_t rxPin, uint8_t txPin, uint8_t address, HardwareSerial &serial);

    bool begin();
    bool configure(uint8_t address, unsigned long band, uint8_t networkId);
    String sendATCommand(const char* command, unsigned long timeout = 1000);
    bool sendData(uint8_t destAddress, String hexData);
    bool receiveData(String& hexData);
    bool set_parameter(uint8_t sf, uint8_t bw, uint8_t cr, uint8_t preamble);

private:
    HardwareSerial &loraSerial; // reference — must be supplied at construction
    uint8_t _rxPin;
    uint8_t _txPin;
    uint8_t _address;
};

#endif
