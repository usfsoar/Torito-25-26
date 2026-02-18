#ifndef LORA_MODULE_H
#define LORA_MODULE_H

#include <Arduino.h>
#include <HardwareSerial.h>

class LoRaModule {
public:
    LoRaModule(uint8_t rxPin, uint8_t txPin, uint8_t address);
    
    bool begin();
    bool configure(uint8_t address, unsigned long band, uint8_t networkId);
    String sendATCommand(const char* command, unsigned long timeout = 1000);
    bool sendData(uint8_t destAddress, String hexData);
    bool receiveData(String& hexData);
    
private:
    HardwareSerial* loraSerial;
    uint8_t _rxPin;
    uint8_t _txPin;
    uint8_t _address;
};

#endif
