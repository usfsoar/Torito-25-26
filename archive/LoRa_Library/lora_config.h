#ifndef LORA_CONFIG_H
#define LORA_CONFIG_H

// LoRa Module Baud Configuration
#define LORA_BAUD 115200

// LoRa Network Configuration
#define LORA_BAND 915000000      // 915 MHz for US
#define LORA_NETWORK_ID 5        // Must match on all devices
#define LORA_SENDER_ADDRESS 2    // Address for sender
#define LORA_RECEIVER_ADDRESS 7  // Address for receiver

// AT Command Templates
#define AT_TEST "AT"
#define AT_SET_ADDRESS_FMT "AT+ADDRESS=%d"
#define AT_SET_BAND_FMT "AT+BAND=%lu"
#define AT_SET_NETWORK_FMT "AT+NETWORKID=%d"
#define AT_SEND_FMT "AT+SEND=%d,%d,%s"  // dest, length, data

// Timing Configuration
#define AT_COMMAND_TIMEOUT 1000
#define LORA_INIT_DELAY 1000
#define LORA_CONFIG_DELAY 100

// Relay Configuration (for receiver)
#define RELAY_MSB_BIT 0x8000     // Bit 15 must be set for valid command
#define RELAY_BIT_START 9        // Relays start at bit 9

#endif
