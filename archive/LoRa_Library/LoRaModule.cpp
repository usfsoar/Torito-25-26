#if !defined(ESP32) && !defined(ESP8266)
#error "LoRa_Library supports only ESP32 and ESP8266"
#endif

#include "LoRaModule.h"
#include "lora_config.h"
#include <HardwareSerial.h>

LoRaModule::LoRaModule(uint8_t rxPin, uint8_t txPin, uint8_t address, HardwareSerial &serial)
    : _rxPin(rxPin), _txPin(txPin), _address(address), loraSerial(serial) {
    // serial reference must be provided by caller
}

bool LoRaModule::begin() {

    // Ensure the HardwareSerial is configured for the LoRa module pins/baud
    if (!loraSerial) {
        Serial.println("ERROR: loraSerial not configured");
        return false;
    }

#if defined(ESP32)
    loraSerial.begin(LORA_BAUD, SERIAL_8N1, _rxPin, _txPin);
#else
    // ESP8266 / other: caller must ensure Serial is configured appropriately
    loraSerial.begin(LORA_BAUD);
#endif

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

    // Apply default radio parameters (SF, BW, CR, preamble)
    if (set_parameter(LORA_PARAMETER_SF, LORA_PARAMETER_BW, LORA_PARAMETER_CR, LORA_PARAMETER_PREAMBLE)) {
        Serial.print("LoRa parameters set: ");
        Serial.println(LORA_PARAMETER_DEFAULT_STR);
    } else {
        Serial.println("Warning: failed to set LoRa parameters");
    }
    
    Serial.println("LoRa configured: Address=" + String(address) + 
                   ", Band=" + String(band) + 
                   ", NetworkID=" + String(networkId));
    return true;
}

bool LoRaModule::set_parameter(uint8_t sf, uint8_t bw, uint8_t cr, uint8_t preamble) {
    char cmd[64];
    sprintf(cmd, AT_SET_PARAMETER_FMT, sf, bw, cr, preamble);
    String resp = sendATCommand(cmd, AT_COMMAND_TIMEOUT);
    delay(LORA_CONFIG_DELAY);
    return resp.indexOf("OK") != -1;
}

String LoRaModule::sendATCommand(const char* command, unsigned long timeout) {
    String result = "";

    if (!loraSerial) {
        Serial.println("ERROR: loraSerial not initialized — cannot send AT command");
        return result;
    }
    
    Serial.print("Sending: ");
    Serial.println(command);
    
    // Clear buffer
    while (loraSerial.available()) {
        loraSerial.read();
        Serial.println("Clearing");
    }
    
    loraSerial.println(command);
    
    unsigned long startTime = millis();
    while (millis() - startTime < timeout) {
        if (loraSerial.available()) {
            char c = (char)loraSerial.read();
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
    if (!loraSerial.available()) {
        return false;
    }

    String incomingString = "";

    // Read with timeout
    unsigned long startTime = millis();
    unsigned long lastCharTime = millis();
    while (millis() - startTime < 1000) {
        if (loraSerial.available()) {
            char c = (char)loraSerial.read();
            if (c == '\n') break;
            incomingString += c;
            lastCharTime = millis();
        } else if (millis() - lastCharTime > 50) {
            // No data for 50ms, likely complete
            break;
        }
    }

    incomingString.trim();
    if (incomingString.length() == 0) return false;

    // Extract first contiguous hex token (0-9A-Fa-f)
    String hex = "";
    for (size_t i = 0; i < incomingString.length(); ++i) {
        char c = incomingString.charAt(i);
        bool isHex = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f');
        if (isHex) {
            hex += c;
        } else if (hex.length() > 0) {
            break; // stop after token
        }
    }

    if (hex.length() == 0) return false;
    hexData = hex;
    return true;
}