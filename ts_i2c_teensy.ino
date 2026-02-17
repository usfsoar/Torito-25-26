#include "lora_config.h"
#include "LoRaModule.h"
#include <Wire.h>
#define RX_PIN 0  
#define TX_PIN 1 


HardwareSerial &lora = Serial1;
String data = "";

void setup() {
  Serial.begin(115200);
  delay(2000);

  Serial.println("=== LoRa Relay Receiver ===");

  lora.begin(115200);

  if (sendATcommand("AT", 1000).indexOf("OK") != -1) {
    Serial.println("LoRa module responding");
  } else {
    Serial.println("WARNING: LoRa module not responding!");
  }
  

  sendATcommand("AT+ADDRESS=6", 1000);
  delay(100);
  sendATcommand("AT+BAND=928000000", 1000);
  delay(100);
  sendATcommand("AT+NETWORKID=18", 1000);
  delay(100);
  sendATcommand("AT+PARAMETER=11,9,4,24", 1000);
  delay(1000);
  pinMode(18, INPUT_PULLUP);
  pinMode(19, INPUT_PULLUP);
  Wire.begin(0x08);           // Join i2c bus with address #8
  Wire.onReceive(receiveEvent); // Register event
  Serial.print("Started");
}

void loop() {
  if(data.length()>1){
    send_command(data,3);
  }
  delay(2000);
}

// Function that executes whenever data is received from master
void receiveEvent(int howMany) {
  if (howMany >= 2) {
    uint8_t high = Wire.read();
    uint8_t low = Wire.read();
    uint16_t value = (high << 8) | low;
    
    // Convert to hex string
    char hexStr[5];  // 4 hex digits + null terminator
    sprintf(hexStr, "%04X", value);  // Format as 4-digit uppercase hex
    data = String(hexStr);
    
    Serial.println(data);
    send_command(data, 3);
  }
}

void send_command(String inputString, String address) {
  int len = inputString.length();
  int addressInt = address.toInt();
  
  if (addressInt < 0 || addressInt > 65535) {
    Serial.println("ERROR: Invalid address");
    return;
  }
  
  Serial.print("Preparing to send: '");
  Serial.print(inputString);
  Serial.print("' (length: ");
  Serial.print(len);
  Serial.print(") to address ");
  Serial.println(addressInt);
  
  // Build the AT command
  // Format: AT+SEND=<address>,<length>,<data>
  char atCommand[100];
  
  snprintf(atCommand, sizeof(atCommand), "AT+SEND=%d,%d,%s", 
           addressInt, len, inputString.c_str());
  
  Serial.print("AT Command: ");
  Serial.println(atCommand);
  
  String response = sendATcommand(atCommand, 2000);
  
  // Check if send was successful
  if (response.indexOf("OK") != -1) {
    Serial.println("✓ Send successful");
  } else if (response.indexOf("+ERR") != -1) {
    Serial.println("✗ Send failed - Error response");
  } else {
    Serial.println("? Unknown response");
  }
  
  delay(100);  // Small delay between sends
}

String sendATcommand(const char *toSend, unsigned long milliseconds) {
  String result = "";
  
  Serial.print("Sending: ");
  Serial.println(toSend);
  
  // Clear any pending data in buffer
  while (lora.available()) {
    lora.read();
  }
  
  lora.println(toSend);
  
  unsigned long startTime = millis();
  Serial.print("Received: ");
  
  while (millis() - startTime < milliseconds) {
    if (lora.available()) {
      char c = lora.read();
      Serial.write(c);
      result += c;
    }
  }
  
  Serial.println();
  return result;
}