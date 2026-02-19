// Receiver: LoRa2 -> MCU2 -> Relays
// Receives hex relay commands from LoRa and controls relays

#include <Arduino.h>
#include <Wire.h>
#include "lora_config.h"
#include "LoRaModule.h"

#define RX_PIN 44  // GPIO44 D7 (RX on XIAO) - connects to LoRa TX
#define TX_PIN 43  // GPIO43 D6 (TX on XIAO) - connects to LoRa RX

// I2C address for the Teensy that receives the binary relay command (change if needed)
#define TEENSY_I2C_ADDRESS 0x08
#define OPEN_ALL_VALVES 0xFE00  // binary 1111111000000000 (bits 15..9 = 1)
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
bool parseHexToUint16(const String &hex, uint16_t &out); // parse hex string to uint16_t
void sendToTeensy(uint16_t value);                       // send 16-bit value to Teensy over I2C

void setup() {
  Serial.begin(115200);           // USB debug serial
  delay(2000);  // Wait for serial to initialize
  
  Serial.println("STARTING RECEIVER...");
  Serial.flush();
  
  // Start I2C (Wire) as master
  Wire.begin();
  Serial.println("Wire (I2C) initialized");
  
  // Initialize relay pins as outputs
  for (uint8_t i = 0; i < 6; i++) {
    pinMode(relayPins[i], OUTPUT);
    digitalWrite(relayPins[i], HIGH);  // Start with no power (solenoids closed)
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
    
    // Parse hex string into uint16_t
    uint16_t receivedBytes;
    if (!parseHexToUint16(hexData, receivedBytes)) {
      Serial.println("✗ Failed to parse hex string");
      delay(50);
      continue;
    }
    Serial.print("Binary: ");
    Serial.println(String(receivedBytes, BIN));

    // If this exact pattern was received, it's the "open all valves" command
    if (receivedBytes == OPEN_ALL_VALVES) {
      Serial.println("Command: OPEN ALL VALVES (0xFE00 / 1111111000000000)");
    }

    // forward raw binary to Teensy over I2C (MSB first)
    sendToTeensy(receivedBytes);

    // Only update relays when the MSB validation bit is set
    bool validCommand = (receivedBytes & RELAY_MSB_BIT);
    if (validCommand) {
      Serial.println("Valid relay command - updating relays");
      setRelays(receivedBytes);
    } else {
      Serial.println("MSB not set — relays will NOT be updated");
    }
  }
  
  delay(50);
}

// write all relays at once from a 6-bit value
// Relays are mapped to bits 9..14 (relay1 = bit14 ... relay6 = bit9)
// Bit==1 indicates valve OPEN. Hardware expects active-low signal (LOW energizes relay/solenoid).
void setRelays(uint16_t state) {
    for (uint8_t i = 0; i < 6; ++i) {
        // map relay i (0..5) to bit index 14..9 so bit14 opens relay1
        bool on = state & (1u << (RELAY_BIT_START + 5 - i));  // check bits 14-9 for relays
        // 1 => OPEN => drive pin LOW (active-low)
        digitalWrite(relayPins[i], on ? LOW : HIGH);
        Serial.println("Setting relay " + String(i+1) + " to " + String(on ? "OPEN" : "CLOSED"));
    }
}

// Parse a hex string (e.g. "1A3F") into a uint16_t. Returns true on success.
bool parseHexToUint16(const String &hex, uint16_t &out) {
  char *endptr = nullptr;
  long val = strtol(hex.c_str(), &endptr, 16);
  if (endptr == hex.c_str() || val < 0 || val > 0xFFFF) {
    return false;
  }
  out = (uint16_t)val;
  return true;
}

// Send a 16-bit value to the Teensy over I2C (MSB first).
void sendToTeensy(uint16_t value) {
  uint8_t buf[2];
  buf[0] = (uint8_t)(value >> 8);   // MSB
  buf[1] = (uint8_t)(value & 0xFF); // LSB

  Wire.beginTransmission(TEENSY_I2C_ADDRESS);
  Wire.write(buf, 2);
  uint8_t err = Wire.endTransmission();

  Serial.print("I2C -> Teensy (0x");
  Serial.print(TEENSY_I2C_ADDRESS, HEX);
  Serial.print(") sent 0x");
  Serial.println(value, HEX);
  if (err != 0) {
    Serial.print("I2C error code: ");
    Serial.println(err);
  }
}