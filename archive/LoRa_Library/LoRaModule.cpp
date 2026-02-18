#include "LoRaModule.h"
#include <HardwareSerial.h>

LoRaModule::LoRaModule(uint8_t rxPin, uint8_t txPin, uint8_t address)
    : _rxPin(rxPin), _txPin(txPin), _address(address) {
    //loraSerial = new HardwareSerial(1);  // Use UART1
}

bool LoRaModule::begin() {

    delay(100);
    
    Serial.println("Initializing LoRa Module...");
    
    // Verify module is responding
    String response = sendATCommand("AT");
    if (response.indexOf("OK") != -1) {
        Serial.println("LoRa module responding");
        return true;
    } else {
        Serial.println("WARNING: LoRa module not responding!");
        return false;
    }
}

bool LoRaModule::configure(uint8_t address, unsigned long band, uint8_t networkId) {
    char cmd[50];
    
    // Set address
    sprintf(cmd, "AT+ADDRESS=%d", address);
    sendATCommand(cmd);
    delay(100);
    
    // Set band
    sprintf(cmd, "AT+BAND=%lu", band);
    sendATCommand(cmd);
    delay(100);
    
    // Set network ID
    sprintf(cmd, "AT+NETWORKID=%d", networkId);
    sendATCommand(cmd);
    delay(100);
    
    Serial.println("LoRa configured: Address=" + String(address) + 
                   ", Band=" + String(band) + 
                   ", NetworkID=" + String(networkId));
    return true;
}

String LoRaModule::sendATCommand(const char* command, unsigned long timeout) {
    String result = "";
    
    Serial.print("Sending: ");
    Serial.println(command);
    
    // Clear buffer
    while (loraSerial->available()) {
        loraSerial->read();
        Serial.println("Clearing");
    }
    
    loraSerial->println(command);
    
    unsigned long startTime = millis();
    while (millis() - startTime < timeout) {
        if (loraSerial->available()) {
            char c = loraSerial->read();
            result += c;
        }
    }
    
    Serial.print("Response: ");
    Serial.println(result);
    return result;
}

bool LoRaModule::sendData(uint8_t destAddress, String hexData) {
    char cmd[100];
    sprintf(cmd, "AT+SEND=%d,%d,%s", destAddress, hexData.length(), hexData.c_str());
    String response = sendATCommand(cmd, 2000);
    return response.indexOf("OK") != -1;
}

bool LoRaModule::receiveData(String& hexData) {
    if (!loraSerial->available()) {
        return false;
    }
    
    String incomingString = "";
    
    // Read with timeout
    unsigned long startTime = millis();
    unsigned long lastCharTime = millis();
    while (millis() - startTime < 1000) {
        if (loraSerial->available()) {
            char c = loraSerial->read();
            if (c == '\n') break;
            incomingString += c;
            lastCharTime = millis();
        } else if (millis() - lastCharTime > 50) {
            // No data for 50ms, likely complete
            break;
        }
    }
}