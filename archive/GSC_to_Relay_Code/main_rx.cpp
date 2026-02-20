// Receiver: LoRa2 -> MCU2 -> Relays
// Receives hex relay commands from LoRa and controls relays

#include <Arduino.h>
#include <Wire.h>
#include "lora_config.h"
#include "LoRaModule.h"

#define RX_PIN 8  // GPIO44 D7 (RX on XIAO) - connects to LoRa TX
#define TX_PIN 7  // GPIO43 D6 (TX on XIAO) - connects to LoRa RX

// I2C address for this ESP when acting as a slave (change if needed)
#define I2C_SLAVE_ADDR 0x09
#define OPEN_ALL_VALVES 0xFE00  // binary 1111111000000000 (bits 15..9 = 1)
#define RELAY1 1
#define RELAY2 2
#define RELAY3 3
#define RELAY4 4
#define RELAY5 6
#define RELAY6 7

// Array of relay pins - using GPIO numbers that correspond to D0-D5 on XIAO
const uint8_t relayPins[6] = {RELAY1, RELAY2, RELAY3, RELAY4, RELAY5, RELAY6};

// Create LoRa module instance
LoRaModule lora(RX_PIN, TX_PIN, LORA_RECEIVER_ADDRESS);

// I2C state (updated by master writes)
volatile uint16_t lastI2CValue = RELAY_MSB_BIT; // MSB set by default so master reads are valid

// Function Prototypes
void setRelays(uint16_t state);
bool parseHexToUint16(const String &hex, uint16_t &out); // parse hex string to uint16_t
void receiveEvent(int howMany);                          // I2C receive handler (Wire.onReceive)
void requestEvent();                                     // I2C request handler (Wire.onRequest)

void setup() {
  Serial.begin(115200);           // USB debug serial
  delay(2000);  // Wait for serial to initialize
  
  Serial.println("STARTING RECEIVER...");
  Serial.flush();
  
  // Start I2C (Wire) as slave
  Wire.begin(I2C_SLAVE_ADDR);
  Wire.onReceive(receiveEvent);
  Wire.onRequest(requestEvent);
  Serial.print("Wire (I2C) initialized as SLAVE @ 0x");
  Serial.println(I2C_SLAVE_ADDR, HEX);

  // ensure local state has MSB validation bit set so masters reading this device see a valid state
  lastI2CValue = RELAY_MSB_BIT;
  
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
      return;
    }
    Serial.print("Binary: ");
    Serial.println(String(receivedBytes, BIN));

    // If this exact pattern was received, it's the "open all valves" command
    if (receivedBytes == OPEN_ALL_VALVES) {
      Serial.println("Command: OPEN ALL VALVES (0xFE00 / 1111111000000000)");
    }

    // update local I2C state so an I2C master can read the latest value
    lastI2CValue = receivedBytes;

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

// I2C receive handler — called when an I2C master writes to this device
void receiveEvent(int howMany) {
  // Expect exactly 2 bytes; anything else is unexpected and will be logged/drained.
  if (howMany != 2) {
    Serial.print("I2C receiveEvent: unexpected write length ");
    Serial.print(howMany);
    Serial.print(" bytes — draining (hex):");

    // drain & print all available bytes in hex
    while (Wire.available()) {
      int b = Wire.read();
      if (b < 0) break;
      Serial.print(' ');
      if ((uint8_t)b < 16) Serial.print('0');
      Serial.print((uint8_t)b, HEX);
    }
    Serial.println();
    return;               // ignore the entire write
  }

  // Normal 2-byte path
  uint8_t high = Wire.read();
  uint8_t low  = Wire.read();
  uint16_t value = (uint16_t(high) << 8) | low;

  lastI2CValue = value;
  Serial.print("I2C <- master wrote 0x");
  Serial.println(value, HEX);

  if (value & RELAY_MSB_BIT) {
    setRelays(value);
  } else {
    Serial.println("MSB not set — write ignored for relay update");
  }
}

// I2C request handler — called when an I2C master requests data from this slave
void requestEvent() {
  uint8_t high = (uint8_t)(lastI2CValue >> 8);
  uint8_t low = (uint8_t)(lastI2CValue & 0xFF);
  Wire.write(high);
  Wire.write(low);
}
