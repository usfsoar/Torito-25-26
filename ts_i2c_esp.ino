#include <Wire.h>
#include <HardwareSerial.h>

#define I2C_DEV_ADDR 0x08

#define RX_PIN 3  // D7 on XIAO ESP32S3
#define TX_PIN 2  // D6 on XIAO ESP32S3

HardwareSerial lora(1);
String toJetson;

void onRequest() {
  Wire.print(toJetson);
}

void onReceive(int len) {
  while (Wire.available()) {
    char c = Wire.read();
    if (c >= 32 && c <= 126) { 
      Serial.print(c);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(100);

  lora.begin(115200, SERIAL_8N1, RX_PIN, TX_PIN);
  delay(1000);

  Serial.println("Initializing LoRa Receiver...");
  
  // Verify module is responding
  if (sendATcommand("AT", 1000).indexOf("OK") != -1) {
    Serial.println("LoRa module responding");
  } else {
    Serial.println("WARNING: LoRa module not responding!");
  }

  // Configure this module as receiver (address 3)
  sendATcommand("AT+ADDRESS=3", 1000);
  delay(100);
  sendATcommand("AT+BAND=928000000", 1000);
  delay(100);
  sendATcommand("AT+NETWORKID=18", 1000);
  delay(100);
  sendATcommand("AT+PARAMETER=11,9,4,24", 1000);
  delay(1000);

  Serial.println("Setup complete. Listening for messages...");
  Serial.println("This module is Address 3");


  // Join I2C bus as slave with address 0x08
  Wire.begin(I2C_DEV_ADDR); 
  Wire.onRequest(onRequest);
}

void loop() {
  if (lora.available()){
    String incoming = lora.readStringUntil('\n');
    Serial.print("Raw Lora: ");
    Serial.println(incoming);

    if (incoming.startsWith("+RCV")){
      parseAndSendToJetson(incoming);
    }
  }
}

void parseAndSendToJetson(String rcvData){
  int firstComma = rcvData.indexOf(',');
  int secondComma = rcvData.indexOf(',', firstComma + 1);
  int thirdComma = rcvData.indexOf(',', secondComma + 1);

  String payload = rcvData.substring(secondComma + 1, thirdComma);
  
  Serial.print("Payload detected: ");
  Serial.println(payload);
  toJetson = payload.c_str();
  Serial.print(toJetson);
}

String sendATcommand(const char *toSend, unsigned long ms) {
  lora.println(toSend);
  unsigned long start = millis();
  String res = "";
  while (millis() - start < ms) {
    if (lora.available()) res += (char)lora.read();
  }
  return res;
}