//#include <SoftwareSerial.h>
#include <HardwareSerial.h>
#define RX 3  // Connect to LoRa TX
#define TX 2  // Connect to LoRa RX

HardwareSerial lora(1);
//SoftwareSerial lora(RX, TX);
bool reporting_lock = false;
String lora_input = "";
String address = "";

void setup() {
  Serial.begin(9600);
  delay(100);
  
  lora.begin(115200, SERIAL_8N1, RX, TX);
  delay(1000);
  
  Serial.println("Initializing LoRa Sender...");
  
  // Verify module is responding
  if (sendATcommand("AT", 1000).indexOf("OK") != -1) {
    Serial.println("LoRa module responding");
  } else {
    Serial.println("WARNING: LoRa module not responding!");
  }
  
  sendATcommand("AT+ADDRESS=2", 1000);
  delay(100);
  sendATcommand("AT+BAND=915000000", 1000);
  delay(100);
  sendATcommand("AT+NETWORKID=5", 1000);
  delay(1000);
  
  Serial.println("Setup complete. Format: <command>,<address> or <command>,<address>:repeat");
  Serial.println("Example: Hello,7");
}

void loop() {
  checkUserInput();
  
  // Send message if we have input and address
  if (lora_input != "" && address != "") {
    send_command(lora_input, address);
    
    // Clear after sending unless in repeat mode
    if (!reporting_lock) {
      lora_input = "";
      address = "";
    }
  }
  
  delay(10);
}

void checkUserInput() {
  if (Serial.available() > 0) {
    String userInput = Serial.readStringUntil('\n');
    userInput.trim();
    
    if (userInput.length() > 0) {
      // Check if the input ends with ":repeat"
      bool isRepeat = userInput.endsWith(":repeat");
      
      if (isRepeat) {
        int colonIndex = userInput.indexOf(':');
        if (colonIndex != -1) {
          userInput = userInput.substring(0, colonIndex);
          reporting_lock = true;
          Serial.println("Repeat mode enabled");
        }
      } else {
        reporting_lock = false;
      }
      
      // Parse command and address
      int commaIndex = userInput.indexOf(",");
      if (commaIndex != -1) {
        lora_input = userInput.substring(0, commaIndex);
        address = userInput.substring(commaIndex + 1);
        
        Serial.print("Command: ");
        Serial.print(lora_input);
        Serial.print(" | Address: ");
        Serial.println(address);
      } else {
        Serial.println("ERROR: Invalid format. Use: <command>,<address>");
      }
    }
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
    if (!reporting_lock) {  
      lora_input = "";
      address = "";
    }
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