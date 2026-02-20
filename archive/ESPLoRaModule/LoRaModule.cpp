#include "LoRaModule.h"
#include "lora_config.h"

LoRaModule::LoRaModule(uint8_t rxPin, uint8_t txPin, uint8_t address) 
    : _rxPin(rxPin), _txPin(txPin), _address(address) {
    // ESP32: use Serial1 instance; pins will be configured in begin()
    loraSerial = &Serial1;
}

bool LoRaModule::begin() {
    // Configure Serial1 for the LoRa UART using the pins supplied to the constructor
    loraSerial->begin(115200, SERIAL_8N1, _rxPin, _txPin);
    delay(1000);
    Serial.println("Initializing LoRa Module...");

    if (sendATCommand("AT").indexOf("OK") != -1) {
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
    delay(LORA_CONFIG_DELAY);

    // Apply default radio parameters (SF, BW, CR, preamble)
    if (setParameter(LORA_PARAMETER_SF, LORA_PARAMETER_BW, LORA_PARAMETER_CR, LORA_PARAMETER_PREAMBLE)) {
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

String LoRaModule::sendATCommand(const char* command, unsigned long timeout) {
    String result = "";
    while (loraSerial->available()) {
        loraSerial->read();
    }

    // record the AT command so any later "+ERR=" lines can be correlated
    _lastATCommand = String(command);
    loraSerial->println(command);

    unsigned long startTime = millis();
    unsigned long lastCharTime = millis();
    while (millis() - startTime < timeout) {
        if (loraSerial->available()) {
            char c = loraSerial->read();
            result += c;
            lastCharTime = millis();
            if ((result.indexOf("OK") != -1 || result.indexOf("ERROR") != -1) && 
                millis() - lastCharTime > 50) {
                break;
            }
        }
    }
    Serial.print("Response: ");
    Serial.println(result);
    return result;
}

bool LoRaModule::sendData(uint8_t destAddress, String hexData) {
    char cmd[100];
    sprintf(cmd, "AT+SEND=%d,%d,%s", destAddress, hexData.length() / 2, hexData.c_str());
    String response = sendATCommand(cmd, 2000);
    return response.indexOf("OK") != -1;
}

bool LoRaModule::setParameter(uint8_t sf, uint8_t bw, uint8_t cr, uint8_t preamble) {
    char cmd[64];
    sprintf(cmd, AT_SET_PARAMETER_FMT, sf, bw, cr, preamble);
    String resp = sendATCommand(cmd, AT_COMMAND_TIMEOUT);
    delay(LORA_CONFIG_DELAY);
    return resp.indexOf("OK") != -1;
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

    // Always expose the raw incoming line to the USB serial for debugging
    incomingString.trim();
    if (incomingString.length() > 0) {
        Serial.print("LoRa raw: ");
        Serial.println(incomingString);
    }

    // If module reported an error code (e.g. "+ERR=12"), show context and hints
    int errPos = incomingString.indexOf("+ERR=");
    if (errPos != -1) {
        int codeStart = errPos + 5;
        int codeEnd = codeStart;
        while (codeEnd < incomingString.length() && isDigit(incomingString.charAt(codeEnd))) codeEnd++;
        int errCode = incomingString.substring(codeStart, codeEnd).toInt();
        Serial.print("LoRa module error code: "); Serial.println(errCode);

        if (_lastATCommand.length() > 0) {
            Serial.print("Last AT command: "); Serial.println(_lastATCommand);

            // Heuristic: if last command was AT+SEND, check for length/payload mismatch
            if (_lastATCommand.startsWith("AT+SEND=")) {
                int eq = _lastATCommand.indexOf('=');
                String params = _lastATCommand.substring(eq + 1);
                int c1 = params.indexOf(',');
                int c2 = params.indexOf(',', c1 + 1);
                if (c1 > 0 && c2 > 0) {
                    String lenStr = params.substring(c1 + 1, c2);
                    String dataStr = params.substring(c2 + 1);
                    int lenParam = lenStr.toInt();
                    int actualBytes = dataStr.length() / 2;
                    if (lenParam != actualBytes) {
                        Serial.print("Length mismatch in AT+SEND: specified=");
                        Serial.print(lenParam);
                        Serial.print(" actualBytes=");
                        Serial.println(actualBytes);
                        Serial.println("Hint: when sending hex payloads 'length' must be number of bytes (hexChars/2).");
                    }
                }
            }
        }

        Serial.println("Hints: check payload length, destination address, network ID, module mode and antenna/power.");
    }

    // Parse format: +RCV=<address>,<length>,<data>,<RSSI>,<SNR>
    int firstComma = incomingString.indexOf(',');
    int secondComma = incomingString.indexOf(',', firstComma + 1);
    
    if (firstComma > 0 && secondComma > 0) {
        hexData = incomingString.substring(secondComma + 1);
        // Remove RSSI and SNR if present
        int thirdComma = hexData.indexOf(',');
        if (thirdComma > 0) {
            hexData = hexData.substring(0, thirdComma);
        }
        hexData.trim();
        return true;
    }
    
    return false;
}
