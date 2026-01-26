#include <HardwareSerial.h>

// XIAO ESP32S3 uses Hardware Serial for LoRa
// Using Serial1 (UART1)
#define RX_PIN 3  // D7 on XIAO ESP32S3
#define TX_PIN 2  // D6 on XIAO ESP32S3

HardwareSerial loraSerial(1);  // Use UART1

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
void loop(){
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
        int firstComma = incomingString.indexOf(',');
        int secondComma = incomingString.indexOf(',', firstComma + 1);
        if(incomingString[secondComma+1]=='C'){
            Serial.print("C recieved!");
            //AT+SEND=<address>,<length>,<data>
            loraSerial.println("AT+SEND=2,1,C");
            unsigned long startTime = millis();
            Serial.print("Received: ");
        }   

    }
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
