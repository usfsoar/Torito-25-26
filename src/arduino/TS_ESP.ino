#include "lora_config.h"
#include "LoRaModule.h"
#include<HardwareSerial.h>
#include <Wire.h>

#define RX_PIN 8  
#define TX_PIN 7 
#define SLAVE_ADDR 0x08

#define RELAY1 1
#define RELAY2 2
#define RELAY3 3
#define RELAY4 4
#define RELAY5 43
#define RELAY6 44

// Array of relay pins - using GPIO numbers that correspond to D0-D5 on XIAO
const uint8_t relayPins[6] = {RELAY1, RELAY2, RELAY3, RELAY4, RELAY5, RELAY6};
int bitArray[16];

// Create LoRa module instance

HardwareSerial loraSerial(1);
// Function Prototypes
//void setRelays(uint16_t state);

void setup() {
  Serial.begin(115200);           // USB debug serial
  delay(2000);  // Wait for serial to initialize
  
  Serial.println("STARTING RECEIVER...");
  Serial.flush();
  
  // Initialize relay pins as outputs
  for (uint8_t i = 0; i < 6; i++) {
    pinMode(relayPins[i], OUTPUT);
    digitalWrite(relayPins[i], LOW);  // Start with all relays off
  }
  
  Serial.println("=== LoRa Relay Receiver ===");
  
  // Initialize and configure LoRa module
  loraSerial.begin(115200, SERIAL_8N1, RX_PIN, TX_PIN);
  delay(1000);
  
  Serial.println("Initializing LoRa Receiver...");
  
  // Verify module is responding
  if (sendATcommand("AT", 1000).indexOf("OK") != -1) {
    Serial.println("LoRa module responding");
  } else {
    Serial.println("WARNING: LoRa module not responding!");
  }
  
  // Configure this module as receiver (address 7)
  sendATcommand("AT+ADDRESS=7", 1000);
  delay(100);
  sendATcommand("AT+BAND=915000000", 1000);
  delay(100);
  sendATcommand("AT+NETWORKID=18", 1000);
  delay(1000);
  sendATcommand("AT+PARAMETER=11,9,4,24",1000);
  delay(200);
  Serial.println("Setup complete. Listening for messages...");
  Serial.println("This module is Address 7");
  sendATcommand("AT+PARAMETER=11,9,4,24",1000);

  Serial.println("Listening for relay commands...");

  Wire.begin(5, 6); 

}
void sendInt16(uint16_t val) {
  Wire.beginTransmission(SLAVE_ADDR);
  
  uint8_t high = highByte(val); // or (val >> 8) & 0xFF
  uint8_t low = lowByte(val);   // or val & 0xFF
  
  Wire.write(high);
  Wire.write(low);
  
  byte error = Wire.endTransmission();
  if (error == 0) {
    Serial.print("Sent successfully: ");
    Serial.print(val);
    Serial.print(" (0x");
    Serial.print(val, HEX);
    Serial.println(")");
  } else {
    Serial.print("Error: ");
    Serial.println(error);
  }
}

void loop() {
  String hexData;
  String data;
  // Use LoRa module to receive data
  if (loraSerial.available()) {
    String data = "";
    unsigned long startTime = millis();

    while (millis() - startTime < 1000) {
      if (loraSerial.available()) {
        char c = loraSerial.read();
        if (c == '\n') break;
        data += c;
      }
    }
    if (data.indexOf("+RCV=") == -1) return;
    Serial.print("Received data: ");
    Serial.println(data);    
    // Extract hex part from received data 
    int firstComma = data.indexOf(',');
    int secondComma = data.indexOf(',', firstComma + 1);
    int thirdComma = data.indexOf(',', secondComma + 1);

    if (thirdComma != -1) {
        hexData = data.substring(secondComma + 1, thirdComma);
    }
    // Convert hex to uint16_t
    uint16_t receivedBytes = (uint16_t)strtol(hexData.c_str(), NULL, 16);
    Serial.print("Binary: ");
    Serial.println(String(receivedBytes, BIN));
    for (int i = 0; i < 16; i++) {
    // We check bits from most significant (15) to least significant (0)
    // (value >> (15 - i)) moves the target bit to the first position
    // & 1 masks everything else out
      bitArray[i] = (receivedBytes >> (15 - i)) & 1;
    }
    sendInt16(receivedBytes);
    
    // Check MSB and process
    if (receivedBytes & RELAY_MSB_BIT) {
      Serial.println("Valid relay command - updating relays");
      setRelays(receivedBytes);
    } else {
      Serial.println("Invalid: MSB not set");
    }
  }
  
  delay(50);
}

// write all relays at once from a 6-bit value
void setRelays(uint16_t state) {
    for (uint8_t i = 0; i <6; i++) {
        bool off = state & (1u << (14-i));  // check bits 9-14 for relays
        if(off)digitalWrite(relayPins[i],LOW);
        else digitalWrite(relayPins[i],HIGH);
        //digitalWrite(relayPins[i], on ? HIGH : LOW);

        Serial.println("Setting relay " + String(i+1) + " to " + String(off ? "ON" : "OFF"));
    }
    // String convert = String(state, HEX);
    // Wire.beginTransmission(SLAVE_ADDR);
    // // Send the string directly
    // Wire.write((const uint8_t*)convert.c_str(), convert.length());
    // Wire.endTransmission();
    // if (error == 0) {
    // Serial.println("Sent successfully");
    // } else {
    // Serial.print("Error: ");
    // Serial.println(error);
    // } 
    delay(20);
  
}
String sendATcommand(const char *toSend, unsigned long milliseconds) {
  String result = "";
  
  Serial.print("Sending: ");
  Serial.println(toSend);
  
  // Clear any pending data in buffer
  while (loraSerial.available()) {
    loraSerial.read();
  }
  
  loraSerial.println(toSend);
  
  unsigned long startTime = millis();
  Serial.print("Received: ");
  
  while (millis() - startTime < milliseconds) {
    if (loraSerial.available()) {
      char c = loraSerial.read();
      Serial.write(c);
      result += c;
    }
  }
  
  Serial.println();
  return result;
}