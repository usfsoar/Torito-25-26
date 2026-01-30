#include <HardwareSerial.h>


#define RX_PIN 3  // 
#define TX_PIN 2  // 

HardwareSerial loraSerial(1);  // Use UART2

void setup() {
  Serial.begin(115200);  // USB Serial for debugging
  delay(100);
  
  // Initialize Hardware Serial for LoRa at 115200 baud
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
  sendATcommand("AT+NETWORKID=5", 1000);
  delay(1000);
  
  Serial.println("Setup complete. Listening for messages...");
  Serial.println("This module is Address 7");
}

void loop() {
  // Check for incoming LoRa messages
  if (loraSerial.available()) {
    String incomingString = "";
    
    // Read with timeout
    unsigned long startTime = millis();
    while (millis() - startTime < 1000) {
      if (loraSerial.available()) {
        char c = loraSerial.read();
        if (c == '\n') break;
        incomingString += c;
      }
    }
    
    if (incomingString.length() > 0) {
      Serial.println("========================================");
      Serial.print("RAW: ");
      Serial.println(incomingString);
      
      // Parse the received data
      // Expected format: +RCV=<address>,<length>,<data>,<RSSI>,<SNR>
      // Example: +RCV=1,5,Hello,-50,10
      if (incomingString.indexOf("+RCV=") != -1) {
        parseReceivedMessage(incomingString);
      } else {
        Serial.println("Unknown format");
      }
      Serial.println("========================================\n");
    }
  }
  
  delay(10);
}

void parseReceivedMessage(String rawMessage) {
  // Remove +RCV= prefix
  int startIdx = rawMessage.indexOf("+RCV=");
  if (startIdx == -1) return;
  
  String data = rawMessage.substring(startIdx + 5);  // Skip "+RCV="
  
  // Parse comma-separated values
  int firstComma = data.indexOf(',');
  int secondComma = data.indexOf(',', firstComma + 1);
  int thirdComma = data.indexOf(',', secondComma + 1);
  int fourthComma = data.indexOf(',', thirdComma + 1);
  
  if (firstComma == -1 || secondComma == -1) {
    Serial.println("Parse error");
    return;
  }
  
  // Extract fields
  String senderAddr = data.substring(0, firstComma);
  String length = data.substring(firstComma + 1, secondComma);
  
  String message;
  String rssi = "N/A";
  String snr = "N/A";
  
  if (thirdComma != -1) {
    message = data.substring(secondComma + 1, thirdComma);
    if (fourthComma != -1) {
      rssi = data.substring(thirdComma + 1, fourthComma);
      snr = data.substring(fourthComma + 1);
    } else {
      rssi = data.substring(thirdComma + 1);
    }
  } else {
    message = data.substring(secondComma + 1);
  }
  
  // Display parsed data
  Serial.print("From Address: ");
  Serial.println(senderAddr);
  Serial.print("Length: ");
  Serial.println(length);
  Serial.print("Message: ");
  Serial.println(message);
  Serial.print("RSSI: ");
  Serial.print(rssi);
  Serial.println(" dBm");
  Serial.print("SNR: ");
  Serial.println(snr);
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