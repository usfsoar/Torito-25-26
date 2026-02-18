// Receiver: LoRa2 -> MCU2 -> Relays
// Receives hex relay commands from LoRa and controls relays

#include <Arduino.h>
#include "lora_config.h"
#include "LoRaModule.h"

#define RX_PIN 44  // GPIO44 D7 (RX on XIAO) - connects to LoRa TX
#define TX_PIN 43  // GPIO43 D6 (TX on XIAO) - connects to LoRa RX

#define RELAY1 1
#define RELAY2 2
#define RELAY3 3
#define RELAY4 4
#define RELAY5 5
#define RELAY6 6

// Array of relay pins - using GPIO numbers that correspond to D0-D5 on XIAO
const uint8_t relayPins[6] = {RELAY1, RELAY2, RELAY3, RELAY4, RELAY5, RELAY6};

// Create LoRa module instance
LoRaModule lora(RX_PIN, TX_PIN, LORA_RECEIVER_ADDRESS);

// Function Prototypes
void setRelays(uint16_t state);

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
  if (lora.begin()) {
    lora.configure(LORA_RECEIVER_ADDRESS, LORA_BAND, LORA_NETWORK_ID);
    Serial.println("✓ LoRa configured successfully");
  } else {
    Serial.println("✗ Failed to initialize LoRa!");
  }
  
  Serial.println("Listening for relay commands...");
}

void loop() {
  String hexData;
  
  // Use LoRa module to receive data
  if (lora.receiveData(hexData)) {
    Serial.print("Received hex data: ");
    Serial.println(hexData);
    
    // Convert hex to uint16_t
    uint16_t receivedBytes = (uint16_t)strtol(hexData.c_str(), NULL, 16);
    Serial.print("Binary: ");
    Serial.println(String(receivedBytes, BIN));
    
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
    for (uint8_t i = 0; i < 6; ++i) {
        bool on = state & (1u << (i + RELAY_BIT_START));  // check bits 9-14 for relays
        digitalWrite(relayPins[i], on ? HIGH : LOW);
        Serial.println("Setting relay " + String(i+1) + " to " + String(on ? "ON" : "OFF"));
    }
}