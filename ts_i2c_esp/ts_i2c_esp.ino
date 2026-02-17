#include <Wire.h>
#include <HardwareSerial.h>

#define I2C_DEV_ADDR 0x08

// LORA 1 AND 2 RESPECTIVELY

#define RXLORA_RX 3 // TX on LORA; ESP IS RECEIVING
#define RXLORA_TX 2 // RX on LORA; ESP IS TRANSMITTING

#define TXLORA_RX 5 // TX on LORA; ESP IS RECEIVING
#define TXLORA_TX 4 // RX on LORA; ESP IS TRANSMITTING

HardwareSerial lora1(1); // Hardware UART 1
HardwareSerial lora2(2); // Hardware UART 2

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

  lora1.begin(115200, SERIAL_8N1, RXLORA_RX, RXLORA_TX); // LoRa 1 is RXLORA
  lora2.setPins(TXLORA_RX, TXLORA_TX);
  lora2.begin(115200);
//  lora2.begin(115200, SERIAL_8N1, TXLORA_RX, TXLORA_TX); // LoRa 2 is TXLORA

  delay(1000);

  Serial.println("Initializing LoRa's...");

  String res1 = sendATcommand("AT", 1000, lora1);
  String res2 = sendATcommand("AT", 1000, lora2);

  Serial.println(res1);
  Serial.println("---");
  Serial.println(res2);
  
  // Verify module is responding
  if (res1.indexOf("OK") != -1) {
    Serial.println("LoRa 1 module responding");
  } else {
    Serial.println("WARNING: LoRa 1 module not responding!");
  }

  if (res2.indexOf("OK") != -1) {
    Serial.println("LoRa 2 module responding");
  } else {
    Serial.println("WARNING: LoRa 2 module not responding!");
  }

  // Configure this module as receiver (address 3)
  sendATcommand("AT+ADDRESS=3", 1000, lora1);
  sendATcommand("AT+ADDRESS=3", 1000, lora2);
  delay(100);
  sendATcommand("AT+BAND=928000000", 1000, lora1);
  sendATcommand("AT+BAND=928000000", 1000, lora2);
  delay(100);
  sendATcommand("AT+NETWORKID=18", 1000, lora1);
  sendATcommand("AT+NETWORKID=18", 1000, lora2);
  delay(100);
  sendATcommand("AT+PARAMETER=11,9,4,24", 1000, lora1);
  sendATcommand("AT+PARAMETER=11,9,4,24", 1000, lora2);
  delay(1000);

  Serial.println("Setup complete. Listening for messages...");
  Serial.println("This module is Address 3");


  // Join I2C bus as slave with address 0x08
  Wire.begin(I2C_DEV_ADDR); 
  Wire.onRequest(onRequest);
}

void loop() {
  if (lora1.available()){
    String incoming = lora1.readStringUntil('\n');
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

String sendATcommand(const char *toSend, unsigned long ms, HardwareSerial &name) {
  name.println(toSend);
  unsigned long start = millis();
  String res = "";
  while (millis() - start < ms) {
    if (name.available()) res += (char)name.read();
  }
  return res;
}